"""
主题级路径契约（A/A1 唯一路径构造入口）

目的：
  - 流水线数据根（data/）与入库报告根（reports/db_load/）彻底分离；
  - 所有脚本不再各自拼路径，而是统一走本模块；
  - 每个运行目录写 `_run.json`，让目录自描述（阶段、脚本、参数、输入输出、状态）。

设计约束：
  - 本模块属于主题配置层，可以 import `topic_config` 与 `crawler_common`；
    `crawler_common` 不允许反向 import 本模块（保持主题无关）。
  - 写清单失败只打印中文警告，绝不阻断采集/抽取/入库主流程。
  - 目录分配仍复用 `crawler_common.resolve_run_dir()`（YYYY.M.D-N，永远新建下一个序号）。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import socket
import sys
from datetime import datetime
from pathlib import Path

# noinspection PyUnresolvedReferences
import topic_config as cfg

# paths.py 位于 A/A1/config/，上一级即主题目录 A/A1
CONFIG_DIR = Path(__file__).resolve().parent
TOPIC_DIR = CONFIG_DIR.parent

# 仓库根：向上找 crawler_common.py，找不到则为 None（只影响 manifest 里的相对路径）
REPO_ROOT = next(
    (p for p in CONFIG_DIR.parents if (p / "crawler_common.py").exists()),
    None,
)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cc():
    """延迟导入 crawler_common，避免 paths 被 import 时 sys.path 尚未就绪。"""
    from crawler_common import resolve_output_root, resolve_run_dir

    return resolve_output_root, resolve_run_dir


# ==================== 根目录 ====================

def _base_dir(topic_dir=None) -> Path:
    """未显式传主题目录时，默认用 paths.py 所在的 A/A1。"""
    return Path(topic_dir) if topic_dir is not None else TOPIC_DIR


def pipeline_root(topic_dir=None, override=None) -> Path:
    """解析流水线数据根（默认 A/A1/data/，可用 --output-dir 覆盖）。"""
    resolve_output_root, _ = _cc()
    return Path(resolve_output_root(_base_dir(topic_dir), cfg.DATA_ROOT, override))


def load_report_root(topic_dir=None, override=None) -> Path:
    """解析入库报告根（techfin_load_to_db.py 的产物；默认 A/A1/reports/db_load/，可用 --report-root 覆盖）。"""
    resolve_output_root, _ = _cc()
    return Path(resolve_output_root(_base_dir(topic_dir), cfg.LOAD_REPORT_ROOT, override))


def legacy_root(topic_dir=None) -> Path:
    """旧产物根（只读）。仅用于启动提示与 --input-dir 兼容，不在此写入。"""
    path = Path(cfg.LEGACY_OUTPUT_ROOT)
    if not path.is_absolute():
        path = _base_dir(topic_dir) / path
    return path


# ==================== 运行目录 ====================

def new_pipeline_run(stage, override=None, argv=None, now=None, topic_dir=None) -> Path:
    """在流水线数据根下分配下一个 YYYY.M.D-N 目录，并写 _run.json。"""
    _, resolve_run_dir = _cc()
    root = pipeline_root(topic_dir, override)
    run_dir = Path(resolve_run_dir(root, now=now))
    write_run_manifest(
        run_dir,
        stage=stage,
        argv=argv,
        data_root=root,
    )
    return run_dir


def new_load_run(stage="load", override=None, argv=None, now=None, topic_dir=None) -> Path:
    """在入库报告根下分配独立的 YYYY.M.D-N 目录，并写 _run.json。"""
    _, resolve_run_dir = _cc()
    root = load_report_root(topic_dir, override)
    run_dir = Path(resolve_run_dir(root, now=now))
    write_run_manifest(
        run_dir,
        stage=stage,
        argv=argv,
        data_root=pipeline_root(topic_dir),
        report_root=root,
    )
    return run_dir


# ==================== 运行清单 ====================

def _script_info() -> tuple[str, str]:
    """返回 (脚本相对路径, 脚本 SHA-256)；无法定位时返回空串。"""
    raw = sys.argv[0] if sys.argv else ""
    if not raw:
        return "", ""
    path = Path(raw)
    try:
        path = path.resolve()
    except OSError:
        return str(path), ""
    shown = str(path)
    if REPO_ROOT is not None:
        try:
            shown = str(path.relative_to(REPO_ROOT))
        except ValueError:
            pass
    return shown, sha256_file(path)


def _write_json_atomic(path: Path, obj) -> None:
    """原子写 JSON，避免中途崩溃留下半个文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_json_atomic(path, obj) -> None:
    """公开的原子 JSON 写入（供 loader 写 manifest 使用）。"""
    _write_json_atomic(Path(path), obj)


def write_run_manifest(
    run_dir,
    stage,
    argv=None,
    data_root=None,
    report_root=None,
    inputs=None,
    outputs=None,
    counts=None,
    warnings=None,
    errors=None,
    status="running",
) -> Path | None:
    """
    写 `_run.json`。失败只警告，不阻断主流程。

    字段设计参考 OpenLineage/DVC 的最小集：阶段、脚本、参数、输入输出哈希、状态。
    """
    try:
        script, script_sha = _script_info()
        payload = {
            "schema_version": 1,
            "stage": stage or "",
            "script": script,
            "script_sha256": script_sha,
            "argv": list(argv if argv is not None else sys.argv[1:]),
            "started_at": now_str(),
            "finished_at": None,
            "data_root": str(data_root) if data_root is not None else "",
            "report_root": str(report_root) if report_root is not None else "",
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "status": status or "running",
            "inputs": _normalize_items(inputs),
            "outputs": _normalize_items(outputs),
            "counts": dict(counts or {}),
            "warnings": list(warnings or []),
            "errors": list(errors or []),
        }
        path = Path(run_dir) / cfg.RUN_MANIFEST
        _write_json_atomic(path, payload)
        return path
    except Exception as exc:  # 清单失败不影响业务
        print(f"[警告] 写运行清单失败（不影响主流程）：{exc}")
        return None


def finish_run_manifest(
    run_dir,
    status="ok",
    outputs=None,
    counts=None,
    warnings=None,
    errors=None,
) -> Path | None:
    """结束运行：更新 `_run.json` 的 finished_at/status，并合并输出与计数。"""
    try:
        path = Path(run_dir) / cfg.RUN_MANIFEST
        payload = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        payload.update({
            "finished_at": now_str(),
            "status": status,
        })
        if outputs is not None:
            payload["outputs"] = _normalize_items(outputs)
        if counts is not None:
            payload["counts"] = dict(counts)
        if warnings is not None:
            payload["warnings"] = list(warnings)
        if errors is not None:
            payload["errors"] = list(errors)
        _write_json_atomic(path, payload)
        return path
    except Exception as exc:
        print(f"[警告] 更新运行清单失败（不影响主流程）：{exc}")
        return None


# ==================== 文件指纹与缓存路径 ====================

def sha256_file(path) -> str:
    """文件 SHA-256；不存在或读取失败返回空串。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def count_csv_rows(path) -> int:
    """CSV 数据行数（不含表头）；失败返回 0。"""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            return sum(1 for _ in reader)
    except (OSError, UnicodeError, csv.Error):
        return 0


def file_fingerprint(path) -> dict:
    """生成清单用的输入/输出指纹：路径、SHA-256、行数（CSV 时）。"""
    p = Path(path)
    return {
        "path": str(p),
        "sha256": sha256_file(p),
        "rows": count_csv_rows(p) if p.suffix.lower() == ".csv" else None,
    }


def _normalize_items(items) -> list:
    """把字符串/Path 自动转成文件指纹，dict 原样保留。"""
    out = []
    for item in items or []:
        if isinstance(item, (str, os.PathLike)):
            out.append(file_fingerprint(item))
        elif isinstance(item, dict):
            out.append(item)
    return out


def resolve_cache_path(base_dir, stored, rel=None) -> Path:
    """
    解析正文/附件缓存路径：
      1. 优先相对路径 rel（新缓存写法）；
      2. 其次兼容旧绝对路径 stored；
      3. 最后按 base_dir / stored 解释。
    """
    base = Path(base_dir)
    if rel:
        candidate = base / rel
        if candidate.exists():
            return candidate
    if stored:
        candidate = Path(stored)
        if candidate.is_absolute():
            return candidate
        return base / candidate
    return base
