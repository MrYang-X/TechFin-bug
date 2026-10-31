"""
科技金融指数研究 · CSV → MySQL 数据迁移/入库（techfin_load_to_db.py，可选后处理）

职责：读取流水线数据根 `A/A1/data/` 中的聚合级数据（默认最新一轮
`*/points_resolved.csv`），用证据级 `techfin_a101_points.csv` / `points_delta.csv`
补齐单位、语境、发布日期与证据片段，经严格校验后写入 MySQL 的
`fact_index_value` 表。入库报告默认写到独立的 `A/A1/reports/db_load/<YYYY.M.D-N>/`，
不与爬虫运行目录混放。

设计原则：
  - 系统记录只写 `fact_index_value`，不新增文章/证据表；article_id 存 `evidence_ref`。
  - 默认严格过滤：只入 actual + 有值 + 指标/单位匹配 + 非假阳性 + 地区一致的行。
  - 地区/年份/拆分口径/指标状态均有门禁，详见 `--help` 与 README。
  - A103 默认阻断（抽取层已强制规则层只产 A101，loader 作最后一道闸门）。
  - 单位存实际观测单位，不做跨单位换算；value_text 保留原文。
  - 幂等：以 `(region_code, year, level3_code, measure_code, context_type)` 为键 upsert；
    `--skip-imported` 再按文件 SHA-256 做文件级幂等。
  - 所有可疑行写入 `load_errors.csv`，不静默丢弃；默认 transaction 模式整体回滚。

用法（在仓库根或 A/A1 下均可）：
  # 预演，不写库
  python A/A1/load/techfin_load_to_db.py --dry-run

  # 写入默认库 techfin（密码从 TECHFIN_DB_PASSWORD 读取）
  python A/A1/load/techfin_load_to_db.py

  # 指定数据库/主机/用户
  python A/A1/load/techfin_load_to_db.py --database techfin_test \
      --host 127.0.0.1 --port 3306 --user root --ask-password

  # 指定输入文件 / 合并所有轮次
  python A/A1/load/techfin_load_to_db.py \
      --input-file <数据根>/<运行目录>/points_resolved.csv
  python A/A1/load/techfin_load_to_db.py --all-runs

  # 放行“补充公布/更名/搬迁”等批次值
  python A/A1/load/techfin_load_to_db.py --allow-partial-batch

  # 纯函数自测 / 查看口径映射
  python A/A1/load/techfin_load_to_db.py --self-test
  python A/A1/load/techfin_load_to_db.py --dump-mapping

依赖：pandas、PyMySQL（requirements.txt 已声明）。
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pandas as pd

# ---- 引导：通用工具在仓库根，本脚本在 A/A1/load/ ----
_REPO_ROOT = _TOPIC_DIR = None
for _p in Path(__file__).resolve().parents:
    if _REPO_ROOT is None and (_p / "crawler_common.py").exists():
        _REPO_ROOT = _p
    if _TOPIC_DIR is None and (_p / "config" / "topic_config.py").exists():
        _TOPIC_DIR = _p
    if _REPO_ROOT and _TOPIC_DIR:
        break
if not (_REPO_ROOT and _TOPIC_DIR):
    raise RuntimeError("找不到 crawler_common.py（仓库根）或 config/topic_config.py（主题目录）")
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_TOPIC_DIR / "config"))

# noinspection PyUnresolvedReferences
import topic_config as cfg  # noqa: E402
# noinspection PyUnresolvedReferences
from paths import (  # noqa: E402
    file_fingerprint,
    finish_run_manifest,
    new_load_run,
    pipeline_root,
    write_json_atomic,
    write_run_manifest,
)
from crawler_common import (  # noqa: E402
    RUN_DIR_RE,
    parse_date,
    run_logging,
)

try:
    import pymysql  # noqa: E402
except ImportError:  # pragma: no cover - 运行环境缺依赖时给出中文提示
    pymysql = None


# ==================== 本脚本配置 ====================

LOADER_VERSION = "load-v1"
DEFAULT_DATABASE = "techfin"
DEFAULT_BLOCK_LEVEL3 = "A103"
DEFAULT_BATCH_SIZE = 500

RESOLVED_REQUIRED = [
    "region_code", "region_name", "city_name", "level3_code", "level3_name",
    "metric", "stat_year", "stat_year_src", "canonical_value",
    "n_sources", "n_support", "source_article_ids",
    "value_min", "value_max", "agreement", "chosen_reason",
    "authority", "data_quality", "data_source",
]

POINT_REQUIRED = [
    "point_id", "article_id", "level3_code", "region_code", "stat_year", "metric",
    "value_num", "value_text", "unit", "context_type", "evidence_snippet",
    "publish_date", "authority", "quality_grade", "confidence", "title",
]

CURATED_REQUIRED = ["article_id", "title", "source", "date_final", "quality_grade", "kind"]

ERROR_COLS = [
    "row_no", "input_file", "run_dir", "region_code", "region_name",
    "level3_code", "metric", "stat_year", "stat_year_src", "canonical_value",
    "context_type", "action", "severity", "error_code", "error_codes_all",
    "error_detail", "evidence_ref", "evidence_snippet", "suggestion", "row_json",
]

# 数值与单位解析
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT_TOKENS = [
    "家/万人", "家每万人", "户/万人", "个/万人", "件/万人",
    "万亿元", "亿元", "万元", "元",
    "家", "户", "个", "所", "件", "起", "项", "笔", "%", "％",
    "分", "人年", "指数值",
]
_UNIT_ORDER = sorted(set(_UNIT_TOKENS), key=len, reverse=True)

VALUE_RE = re.compile(rf"^\s*(?P<num>{_NUM})\s*(?P<rest>.*)$")
LEADING_APPROX_RE = re.compile(r"^(约|近|超过|超|逾|突破|首次突破|达到)\s*")
APPROX_RE = re.compile(r"余|多|近|超|逾|约|突破|超过")
SUBJECT_RE = re.compile(r"(服务企业|服务机构|孵化器|平台|项目|名单|批次|活动|授牌|试点服务机构)\s*$")
BATCH_RE = re.compile(r"第[一二三四五六七八九十\d]+批|补充公布|异地搬迁|更名|取消.{0,6}资格|认定报备")
SCOPE_RE = re.compile(r"全市|全市域|全省|全省域|全国")
CITY_SCOPE_RE = re.compile(r"全省|全省域|全国")
AGG_RE = re.compile(r"四区|各区|之和|合计|总计|分别有|分别为")
SUBUNIT_RE = re.compile(r"(试验区|开发区|高新区|园区|街道|镇|片区|新城|基地|社区|商圈)")
CHOSEN_RE = re.compile(r"→\s*(actual|planned|target|decline|unknown)\s*$")

# 存量型指标：批次公告里的数字通常不是存量，严格模式拒绝
STOCK_INDICATORS = {
    "A101", "A102", "A201", "A202", "A203", "A204",
    "B101", "B103", "B201", "B202", "B203", "B204",
    "B301", "B302", "B401", "C101", "C102", "C103",
    "C202", "C301", "D102", "D201", "D202",
}

AUTHORITY_MAP = {
    "官方": "official", "official": "official",
    "媒体": "media", "media": "media",
    "第三方": "third_party", "third_party": "third_party",
    "专家": "expert", "expert": "expert",
    "": "unknown", "unknown": "unknown",
}

UPSERT_SQL = """
INSERT INTO fact_index_value (
    region_code, year, level3_code, measure_code, context_type,
    source_channel, source_authority, source_ref, obs_status,
    value_num, value_text, value_unit, data_quality,
    n_sources, value_min, value_max, agreement,
    evidence_ref, source_publish_date, chosen_reason, remark
) VALUES (
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
    source_channel      = VALUES(source_channel),
    source_authority    = VALUES(source_authority),
    source_ref          = VALUES(source_ref),
    obs_status          = VALUES(obs_status),
    value_num           = VALUES(value_num),
    value_text          = VALUES(value_text),
    value_unit          = VALUES(value_unit),
    data_quality        = VALUES(data_quality),
    n_sources           = VALUES(n_sources),
    value_min           = VALUES(value_min),
    value_max           = VALUES(value_max),
    agreement           = VALUES(agreement),
    evidence_ref        = VALUES(evidence_ref),
    source_publish_date = VALUES(source_publish_date),
    chosen_reason       = VALUES(chosen_reason),
    remark              = VALUES(remark),
    updated_at          = CURRENT_TIMESTAMP
"""

# missing 行：存在则不动，绝不用 missing 覆盖 present
MISSING_SQL = UPSERT_SQL.replace(
    "ON DUPLICATE KEY UPDATE\n"
    "    source_channel      = VALUES(source_channel),\n"
    "    source_authority    = VALUES(source_authority),\n"
    "    source_ref          = VALUES(source_ref),\n"
    "    obs_status          = VALUES(obs_status),\n"
    "    value_num           = VALUES(value_num),\n"
    "    value_text          = VALUES(value_text),\n"
    "    value_unit          = VALUES(value_unit),\n"
    "    data_quality        = VALUES(data_quality),\n"
    "    n_sources           = VALUES(n_sources),\n"
    "    value_min           = VALUES(value_min),\n"
    "    value_max           = VALUES(value_max),\n"
    "    agreement           = VALUES(agreement),\n"
    "    evidence_ref        = VALUES(evidence_ref),\n"
    "    source_publish_date = VALUES(source_publish_date),\n"
    "    chosen_reason       = VALUES(chosen_reason),\n"
    "    remark              = VALUES(remark),\n"
    "    updated_at          = CURRENT_TIMESTAMP\n",
    "ON DUPLICATE KEY UPDATE id = id\n",
)

# 列顺序：与 UPSERT_SQL 的 VALUES 顺序完全一致
PARAM_KEYS = [
    "region_code", "year", "level3_code", "measure_code", "context_type",
    "source_channel", "source_authority", "source_ref", "obs_status",
    "value_num", "value_text", "value_unit", "data_quality",
    "n_sources", "value_min", "value_max", "agreement",
    "evidence_ref", "source_publish_date", "chosen_reason", "remark",
]


# ==================== 数据结构 ====================

@dataclass
class Issue:
    row_no: int
    input_file: str
    run_dir: str = ""
    region_code: str = ""
    region_name: str = ""
    level3_code: str = ""
    metric: str = ""
    stat_year: str = ""
    stat_year_src: str = ""
    canonical_value: str = ""
    context_type: str = ""
    action: str = "reject"
    severity: str = "hard"
    code: str = ""
    detail: str = ""
    evidence_ref: str = ""
    evidence_snippet: str = ""
    suggestion: str = ""
    row_json: str = ""
    codes_all: list[str] = field(default_factory=list)


@dataclass
class LoadStats:
    input_rows: int = 0
    passed: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    skipped: int = 0
    warnings: int = 0
    db_errors: int = 0
    hard_rejects: int = 0
    truncated_fields: dict[str, int] = field(default_factory=dict)


@dataclass
class InputFile:
    path: Path
    run_dir: str
    sha256: str
    run_order: tuple[int, int, int, int]


@dataclass
class ParsedValue:
    num: float
    unit: str
    approx: bool
    tail: str
    raw: str


@dataclass
class Dims:
    regions: dict[str, dict[str, Any]]
    measures: dict[tuple[str, str], dict[str, Any]]
    primary: dict[str, dict[str, Any]]
    channels: dict[str, dict[str, Any]]
    indexes: dict[str, dict[str, Any]]
    other_county_names: dict[str, list[tuple[str, str]]]
    region_names: dict[str, set[str]]


@dataclass
class WriteStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    db_errors: int = 0


# ==================== 通用小工具 ====================

def _clip(text: Any, limit: int, field_name: str, ctx: dict[str, Any] | None = None) -> str:
    """按数据库字段长度截断；被截断时计入 LoadStats.truncated_fields。"""
    value = str(text or "")
    if len(value) <= limit:
        return value
    if ctx is not None:
        stats = ctx.get("load_stats")
        if stats is not None:
            stats.truncated_fields[field_name] = stats.truncated_fields.get(field_name, 0) + 1
    return value[:limit]


def s(row: Any, key: str, default: str = "") -> str:
    """从 pandas Series / dict 取字符串，NaN/None → 空串。"""
    try:
        value = row.get(key, default)
    except Exception:
        value = default
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() == "nan":
        return default
    return text


def parse_int(text: Any) -> int | None:
    if text is None:
        return None
    m = re.search(r"[-+]?\d+", str(text).replace(",", ""))
    if not m:
        return None
    try:
        return int(m.group())
    except ValueError:
        return None


def parse_float(text: Any) -> float | None:
    if text is None:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(text).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def to_bool_int(text: Any) -> int | None:
    value = str(text or "").strip().lower()
    if value in ("true", "1", "yes", "y", "是"):
        return 1
    if value in ("false", "0", "no", "n", "否"):
        return 0
    return None


def format_number(num: float) -> str:
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num:g}"


def split_ids(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[,，;；\s]+", str(text))
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def normalize_unit(unit: str) -> str:
    unit = (unit or "").strip().replace("％", "%")
    return {
        "家每万人": "家/万人",
        "个每万人": "个/万人",
        "件每万人": "件/万人",
        "项": "件",
        "笔": "起",
    }.get(unit, unit)


def decimal_or_none(num: float | None) -> Decimal | None:
    if num is None:
        return None
    try:
        return Decimal(str(num)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def safe_date(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    parsed = parse_date(text)
    return parsed or None


# ==================== 数值/单位解析 ====================

def parse_value_text(text: str) -> ParsedValue | None:
    """解析“数字 + 可选量级 + 可选单位”的原始文本。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    approx = bool(APPROX_RE.search(raw))
    m_lead = LEADING_APPROX_RE.match(raw)
    if m_lead:
        approx = True
        raw = raw[m_lead.end():]
    m = VALUE_RE.match(raw)
    if not m:
        return None
    try:
        num = float(m.group("num").replace(",", ""))
    except ValueError:
        return None
    rest = m.group("rest").strip()
    mag = 1.0
    unit = ""

    # 先处理“万亿元”这类长单位，避免“亿”被当成量级
    if rest.startswith("万亿元"):
        mag = 10000.0
        unit = "亿元"
        rest = rest[3:]
    elif rest.startswith("亿元"):
        unit = "亿元"
        rest = rest[2:]
    elif rest.startswith("万元"):
        unit = "万元"
        rest = rest[2:]
    elif rest.startswith("万亿"):
        mag = 1e12
        rest = rest[2:]
    else:
        if rest.startswith("万余") or rest.startswith("亿余"):
            mag = 10000.0 if rest[0] == "万" else 1e8
            approx = True
            rest = rest[2:]
        elif rest.startswith("万") or rest.startswith("亿"):
            mag = 10000.0 if rest[0] == "万" else 1e8
            rest = rest[1:]
        elif rest.startswith("余") or rest.startswith("多"):
            approx = True
            rest = rest[1:]

    if not unit:
        for candidate in _UNIT_ORDER:
            if rest.startswith(candidate):
                unit = candidate
                break
    tail = rest[len(unit):] if unit and rest.startswith(unit) else rest
    return ParsedValue(num * mag, normalize_unit(unit), approx, tail, str(text))


def parse_value(value_text: str, canonical_value: str, evidence_unit: str) -> ParsedValue | None:
    """优先解析 value_text；失败再解析 canonical_value。"""
    for candidate in (value_text, canonical_value):
        if not candidate:
            continue
        parsed = parse_value_text(candidate)
        if parsed:
            if not parsed.unit and evidence_unit:
                parsed.unit = normalize_unit(evidence_unit)
            return parsed
    return None


def unit_compatible(actual: str, standard: str) -> tuple[bool, str]:
    """返回 (是否兼容, 错误码/提示码)。只做单位族兼容，不做跨单位换算。"""
    a = normalize_unit(actual)
    st = normalize_unit(standard)

    if not a:
        if st == "%":
            return False, "PERCENT_UNIT_MISSING"
        return True, "UNIT_ASSUMED"

    if st == "%":
        return (a == "%", "" if a == "%" else "UNIT_MISMATCH")

    if st == "家":
        if a in ("家", "户"):
            return True, ""
        if a == "个":
            return False, "UNIT_WEAK"
        return False, "UNIT_MISMATCH"

    if st == "个":
        return (a == "个", "" if a == "个" else "UNIT_MISMATCH")

    if st in ("家/万人", "件/万人"):
        allowed = {st, st.replace("/万人", "每万人")}
        if st == "家/万人":
            allowed.add("户/万人")
        return (a in allowed, "" if a in allowed else "UNIT_MISMATCH")

    if st in ("元", "万元", "亿元"):
        allowed = {"元", "万元", "亿元"}
        return (a in allowed, "" if a in allowed else "UNIT_MISMATCH")

    return (a == st, "" if a == st else "UNIT_MISMATCH")


# ==================== 语境/假阳性/地区/年份检测 ====================

def parse_chosen_context(reason: str) -> tuple[str, bool]:
    """从 chosen_reason 解析 context_type；返回 (context, 是否前后矛盾)。"""
    reason = str(reason or "").strip()
    m = CHOSEN_RE.search(reason)
    context = m.group(1) if m else "unknown"
    if not m:
        for candidate in ("actual", "planned", "target", "decline", "unknown"):
            if candidate in reason:
                context = candidate
                break
    inconsistent = False
    if "取实际口径" in reason and context != "actual":
        inconsistent = True
    if "无实际口径" in reason and context == "actual":
        inconsistent = True
    return context, inconsistent


def evidence_window(snippet: str, value_text: str, value_num: float | None) -> str:
    snippet = str(snippet or "").strip()
    if not snippet:
        return ""
    needles: list[str] = []
    if value_text:
        needles.append(str(value_text).strip())
    if value_num is not None:
        needles.append(format_number(value_num))
        if abs(value_num - round(value_num)) < 1e-9:
            needles.append(f"{int(round(value_num)):,}")
    for needle in needles:
        if needle and needle in snippet:
            i = snippet.index(needle)
            return snippet[max(0, i - 60): i + len(needle) + 60]
    return snippet[:300]


def detect_workday(value_text: str, window: str, unit: str) -> bool:
    if normalize_unit(unit) != "个":
        return False
    if "工作日" in str(value_text or ""):
        return True
    # 在证据窗口里找“数字 + 可选个 + 时间单位”，而不是要求窗口以该词开头
    return bool(re.search(
        r"\d[\d,]*(?:\.\d+)?\s*(?:个)?\s*(工作日|个月|月|年|天|小时|分钟|人次|百分点)",
        str(window or ""),
    ))


def detect_subject(window: str, value_num: float | None) -> bool:
    if value_num is None:
        return False
    for needle in (format_number(value_num), f"{int(round(value_num)):,}" if abs(value_num - round(value_num)) < 1e-9 else ""):
        if not needle:
            continue
        i = window.find(needle)
        if i >= 0:
            before = window[max(0, i - 15):i]
            if SUBJECT_RE.search(before):
                return True
    return False


def detect_partial_batch(title: str, window: str, level3_code: str) -> bool:
    if level3_code not in STOCK_INDICATORS:
        return False
    return bool(BATCH_RE.search(f"{title or ''} {window or ''}"))


def number_prefix(window: str, value_num: float | None, length: int = 40) -> str:
    """取数字前的局部前缀，用于判断“全市/全国/其他区县”等口径是否真正修饰该数字。"""
    if value_num is None:
        return window[:length]
    needles = [format_number(value_num)]
    if abs(value_num - round(value_num)) < 1e-9:
        needles.append(f"{int(round(value_num)):,}")
    for needle in needles:
        i = window.find(needle)
        if i >= 0:
            return window[max(0, i - length):i]
    return window[:length]


def detect_region_issue(prefix: str, region: dict[str, Any], dims: Dims) -> list[tuple[str, str]]:
    """返回 [(错误码, 说明)]；口径词只在数字前缀里判断，避免程序性“全国……办公室”误杀。"""
    issues: list[tuple[str, str]] = []
    level = int(region.get("region_level") or 0)

    if level == 3:
        if SCOPE_RE.search(prefix):
            issues.append(("REGION_SCOPE_MISMATCH", "区县行的数字前缀出现全市/全省/全国口径"))
        if AGG_RE.search(prefix):
            issues.append(("REGION_AGGREGATE", "数字前缀出现四区/各区/合计/分别有等汇总口径"))
        if SUBUNIT_RE.search(prefix):
            match = SUBUNIT_RE.search(prefix)
            issues.append(("REGION_SUBUNIT", f"数字前缀出现子区域：{match.group(0)}"))
        other = None
        for name, code in dims.other_county_names.get(region.get("province_code", ""), []):
            if code == region.get("region_code"):
                continue
            if name and name in prefix:
                other = name
                break
        if other:
            issues.append(("REGION_OTHER_MENTION", f"数字前缀出现其他区县：{other}"))
    elif level == 2:
        if CITY_SCOPE_RE.search(prefix):
            issues.append(("REGION_SCOPE_MISMATCH", "地级市行的数字前缀出现全省/全国口径"))
    else:
        if re.search(r"全国(?:共|累计|范围|总计|有|排名)", prefix):
            issues.append(("REGION_SCOPE_MISMATCH", "省级行的数字前缀出现全国口径"))

    return issues


def region_verified(window: str, title: str, region: dict[str, Any], dims: Dims) -> str:
    names = dims.region_names.get(region.get("region_code", ""), set())
    if any(name and name in window for name in names):
        return "text"
    if any(name and name in title for name in names):
        return "title"
    return "metadata"


# ==================== CSV / 输入发现 ====================

def read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    df.columns = [str(c).strip().lstrip("﻿") for c in df.columns]
    return df.fillna("")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_order_from_name(name: str) -> tuple[int, int, int, int]:
    m = RUN_DIR_RE.match(name)
    if not m:
        return (0, 0, 0, 0)
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def resolve_user_path(text: str) -> Path:
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    # 先按当前工作目录找，找不到再锚定主题目录 A/A1
    if path.exists():
        return path.resolve()
    return _TOPIC_DIR / path


def discover_inputs(args: argparse.Namespace, out_root: Path) -> list[InputFile]:
    paths: list[Path] = []
    if args.input_file:
        paths = [resolve_user_path(p) for p in args.input_file]
    else:
        base = Path(args.input_dir).expanduser() if args.input_dir else out_root
        if not base.is_absolute():
            base = _TOPIC_DIR / base
        if (base / "points_resolved.csv").exists():
            paths = [base / "points_resolved.csv"]
        else:
            candidates = []
            if base.exists():
                for child in base.iterdir():
                    if child.is_dir() and RUN_DIR_RE.match(child.name):
                        p = child / "points_resolved.csv"
                        if p.exists():
                            candidates.append(p)
            candidates.sort(key=lambda p: run_order_from_name(p.parent.name))
            if not candidates:
                print(f"[错误] 在 {base} 下找不到任何 points_resolved.csv")
                return []
            paths = candidates if args.all_runs else [candidates[-1]]

    result: list[InputFile] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        if not path.exists():
            print(f"[错误] 输入文件不存在：{path}")
            return []
        result.append(InputFile(
            path=path,
            run_dir=path.parent.name if RUN_DIR_RE.match(path.parent.name) else "",
            sha256=file_sha256(path),
            run_order=run_order_from_name(path.parent.name),
        ))
    return result


def load_points(args: argparse.Namespace, out_root: Path, inputs: list[InputFile]) -> dict[str, list[dict[str, Any]]]:
    paths: list[Path] = []
    if args.points_file:
        paths.append(resolve_user_path(args.points_file))
    else:
        # 先放根累积库，再放同运行目录的 points_delta，后者去重时优先
        root_points = out_root / cfg.POINTS_ACCUMULATOR
        if root_points.exists():
            paths.append(root_points)
        for inp in inputs:
            p = inp.path.parent / "points_delta.csv"
            if p.exists():
                paths.append(p)

    frames = []
    for path in paths:
        if not path.exists():
            print(f"[提示] 证据级文件不存在，跳过：{path}")
            continue
        df = read_csv(path)
        missing = [c for c in POINT_REQUIRED if c not in df.columns]
        if missing:
            print(f"[错误] 证据级文件缺少列 {missing}：{path}")
            return {}
        frames.append(df)

    if not frames:
        print("[提示] 没有可用的证据级文件；unit/context 将无法补齐，严格模式会拒绝相关行。")
        return {}

    points = pd.concat(frames, ignore_index=True, sort=False).fillna("")
    if "point_id" in points.columns:
        points = points.drop_duplicates(subset=["point_id"], keep="last")
    else:
        points = points.drop_duplicates(keep="last")

    by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _, row in points.iterrows():
        record = {str(k): s(row, str(k)) for k in points.columns}
        by_article[record.get("article_id", "")].append(record)
    return dict(by_article)


def load_curated(args: argparse.Namespace, out_root: Path) -> dict[str, dict[str, Any]]:
    path = resolve_user_path(args.curated_file) if args.curated_file else out_root / cfg.CURATED_ACCUMULATOR
    if not path.exists():
        print(f"[提示] 文章级文件不存在，溯源字段将留空：{path}")
        return {}
    df = read_csv(path)
    missing = [c for c in CURATED_REQUIRED if c not in df.columns]
    if missing:
        print(f"[提示] 文章级文件缺少列 {missing}，按空值处理：{path}")
    out: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        article_id = s(row, "article_id")
        if not article_id:
            continue
        out[article_id] = {
            "title": s(row, "title"),
            "source": s(row, "source"),
            "date_final": s(row, "date_final"),
            "quality_grade": s(row, "quality_grade"),
            "kind": s(row, "kind"),
        }
    return out


# ==================== 数据库 ====================

def connect_db(args: argparse.Namespace):
    if pymysql is None:
        print("[错误] 缺少 PyMySQL。请先执行：pip install \"PyMySQL[rsa]==1.1.2\"")
        return None
    password = os.getenv(args.password_env, "")
    if not password and args.ask_password:
        password = getpass.getpass("MySQL 密码：")
    try:
        conn = pymysql.connect(
            host=args.host,
            port=args.port,
            user=args.user,
            password=password,
            database=args.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
            init_command="SET time_zone = '+08:00'",
        )
    except Exception as exc:
        print(f"[错误] 无法连接 MySQL {args.user}@{args.host}:{args.port}/{args.database}：{type(exc).__name__}: {exc}")
        print("[提示] 可用 --host/--port/--user/--database 指定目标；密码从环境变量或 --ask-password 读取。")
        return None
    return conn


def load_dimensions(conn) -> Dims | None:
    queries = {
        "regions": "SELECT region_code, region_name, region_full_name, region_level, parent_code, city_code, province_code, region_type, is_active FROM dim_region",
        "measures": "SELECT level3_code, measure_code, measure_name, standard_unit, value_kind, is_primary FROM dim_index_measure",
        "channels": "SELECT channel_code, channel_name, default_quality FROM dim_source_channel",
        "indexes": "SELECT level3_code, level3_name, level1_code, level2_code, status FROM dim_index",
    }
    data: dict[str, list[dict[str, Any]]] = {}
    try:
        with conn.cursor() as cur:
            for key, sql in queries.items():
                cur.execute(sql)
                data[key] = list(cur.fetchall())
    except Exception as exc:
        print(f"[错误] 读取维度表失败：{type(exc).__name__}: {exc}")
        print("[提示] 请先在目标库执行 db/00_database.sql → 10_schema.sql → 20_seed_region.sql → 21_seed_index.sql。")
        return None

    regions = {str(r["region_code"]): r for r in data["regions"]}
    measures = {(str(m["level3_code"]), str(m["measure_code"])): m for m in data["measures"]}
    primary = {str(m["level3_code"]): m for m in data["measures"] if int(m.get("is_primary") or 0) == 1}
    channels = {str(c["channel_code"]): c for c in data["channels"]}
    indexes = {str(i["level3_code"]): i for i in data["indexes"]}

    other_county_names: dict[str, list[tuple[str, str]]] = defaultdict(list)
    region_names: dict[str, set[str]] = defaultdict(set)

    for code, region in regions.items():
        names = {
            str(region.get("region_name") or "").strip(),
            str(region.get("region_full_name") or "").strip(),
        }
        name = str(region.get("region_name") or "").strip()
        short = re.sub(r"(自治县|区|县|市)$", "", name)
        names.add(short)
        names = {n for n in names if n and len(n) >= 2}
        region_names[code].update(names)

        if int(region.get("region_level") or 0) == 3:
            prov = str(region.get("province_code") or "")
            for n in names:
                if n == "城区":
                    continue
                other_county_names[prov].append((n, code))

    if not regions or not measures or not channels:
        print("[错误] 维度表为空或缺少指标口径，请先执行 db/20_seed_region.sql 与 db/21_seed_index.sql。")
        return None

    return Dims(
        regions=regions,
        measures=measures,
        primary=primary,
        channels=channels,
        indexes=indexes,
        other_county_names=dict(other_county_names),
        region_names=dict(region_names),
    )


def load_existing(conn) -> dict[tuple[str, int, str, str, str], dict[str, Any]] | None:
    sql = """
        SELECT region_code, year, level3_code, measure_code, context_type,
               obs_status, data_quality
        FROM fact_index_value
    """
    out: dict[tuple[str, int, str, str, str], dict[str, Any]] = {}
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                key = (
                    str(row["region_code"]), int(row["year"]),
                    str(row["level3_code"]), str(row["measure_code"]),
                    str(row["context_type"]),
                )
                out[key] = dict(row)
    except Exception as exc:
        print(f"[错误] 读取 fact_index_value 失败：{type(exc).__name__}: {exc}")
        # 不能把"读取失败"当成"表为空"——否则会按全新数据覆盖，风险极高
        return None
    return out


def existing_batch_hashes(conn) -> set[str]:
    """已成功导入的文件 SHA-256 集合（用于 --skip-imported）。"""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_sha256 FROM import_batch "
                "WHERE file_sha256 IS NOT NULL AND file_sha256 <> ''"
            )
            return {str(r["file_sha256"]) for r in cur.fetchall()}
    except Exception as exc:
        print(f"[警告] 读取 import_batch 失败（--skip-imported 不生效）：{exc}")
        return set()


def record_import_batch(
    conn,
    inputs: list[InputFile],
    stats: LoadStats,
    write_stats: WriteStats,
    args: argparse.Namespace,
) -> None:
    """成功写库后登记 import_batch（dry-run 或 DB 错误时不登记）。"""
    if args.dry_run or write_stats.db_errors:
        return
    try:
        sha = (inputs[0].sha256 if len(inputs) == 1 else
               hashlib.sha256("".join(sorted(i.sha256 for i in inputs)).encode()).hexdigest())
        names = ",".join(i.path.name for i in inputs)[:255]
        note = (
            f"loader={LOADER_VERSION}; input={stats.input_rows}; passed={stats.passed}; "
            f"inserted={write_stats.inserted}; updated={write_stats.updated}; "
            f"rejected={stats.rejected}; skipped={stats.skipped}; "
            f"write_mode={args.write_mode}"
        )[:500]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO import_batch "
                "(batch_name, file_name, file_sha256, default_source_channel, "
                " default_source_authority, default_source_ref, note) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (f"loader {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", names,
                 sha, "news_corpus", "unknown", "", note),
            )
        conn.commit()
        print(f"[批次] 已登记 import_batch（sha256={sha[:12]}…）")
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[警告] 写 import_batch 失败（不影响入库）：{exc}")



def point_score(point: dict[str, Any], context: str, first_article: str) -> int:
    score = 0
    if s(point, "context_type") == context:
        score += 3
    if s(point, "article_id") == first_article:
        score += 2
    if s(point, "authority") == "官方":
        score += 1
    try:
        if float(s(point, "confidence") or 0) >= 0.8:
            score += 1
    except ValueError:
        pass
    return score


def match_evidence(
    row: Any,
    points_by_article: dict[str, list[dict[str, Any]]],
    context: str,
) -> tuple[dict[str, Any] | None, str]:
    ids = split_ids(s(row, "source_article_ids"))
    level3 = s(row, "level3_code")
    region_code = s(row, "region_code")
    # 年份按数值比较：上游可能写 "2025"，旧累积库可能仍是 "2025.0"
    year = parse_int(s(row, "stat_year"))
    metric = s(row, "metric")
    value = parse_float(s(row, "canonical_value"))

    def collect(relax_metric: bool, relax_value: bool) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for article_id in ids:
            for point in points_by_article.get(article_id, []):
                if s(point, "level3_code") != level3:
                    continue
                if s(point, "region_code") != region_code:
                    continue
                if year is not None and parse_int(s(point, "stat_year")) != year:
                    continue
                if metric and not relax_metric and s(point, "metric") != metric:
                    continue
                pv = parse_float(s(point, "value_num"))
                if value is not None and not relax_value and pv is not None and abs(pv - value) > 1e-6:
                    continue
                found.append(point)
        return found

    candidates = collect(False, False)
    if not candidates:
        candidates = collect(True, False)
    if not candidates:
        candidates = collect(False, True)
    if not candidates:
        candidates = collect(True, True)
    if not candidates:
        return None, "ENRICH_NO_EVIDENCE"

    first_article = ids[0] if ids else ""
    candidates.sort(key=lambda p: (point_score(p, context, first_article), s(p, "publish_date")), reverse=True)
    best = candidates[0]
    top_score = point_score(best, context, first_article)
    top = [p for p in candidates if point_score(p, context, first_article) == top_score]
    signatures = {(s(p, "unit"), s(p, "value_text")) for p in top}
    if len(signatures) > 1:
        return None, "ENRICH_AMBIGUOUS"
    return best, ""


# ==================== 行评估 ====================

def make_issue(
    ctx: dict[str, Any],
    action: str,
    severity: str,
    code: str,
    detail: str,
    suggestion: str = "",
    point: dict[str, Any] | None = None,
) -> Issue:
    row = ctx["row"]
    return Issue(
        row_no=ctx["row_no"],
        input_file=ctx["input_file"],
        run_dir=ctx["run_dir"],
        region_code=s(row, "region_code"),
        region_name=s(row, "region_name"),
        level3_code=s(row, "level3_code"),
        metric=s(row, "metric"),
        stat_year=s(row, "stat_year"),
        stat_year_src=s(row, "stat_year_src"),
        canonical_value=s(row, "canonical_value"),
        context_type=ctx.get("context", ""),
        action=action,
        severity=severity,
        code=code,
        detail=detail,
        evidence_ref=s(row, "source_article_ids"),
        evidence_snippet=s(point, "evidence_snippet") if point else "",
        suggestion=suggestion,
        row_json=json.dumps(
            {str(k): v for k, v in dict(row).items()},
            ensure_ascii=False, default=str,
        )[:2000],
        codes_all=[code],
    )


def build_remark(
    row: Any,
    point: dict[str, Any] | None,
    measure: dict[str, Any],
    actual_unit: str,
    verified: str,
    assumed_unit: bool,
    approx: bool,
    partial_batch: bool,
) -> str:
    parts = [
        f"stat_year_src={s(row, 'stat_year_src')}",
        f"year_confidence={s(row, 'year_confidence')}",
        f"region_from={s(row, 'region_from')}",
        f"aggregation_type={s(row, 'aggregation_type')}",
        f"point_id={s(point, 'point_id') if point else ''}",
        f"measure={measure['measure_code']}",
        f"unit={actual_unit}",
        f"standard_unit={measure['standard_unit']}",
        f"region_verified={verified}",
    ]
    if assumed_unit:
        parts.append("unit_assumed=1")
    if approx:
        parts.append("approx=1")
    if partial_batch:
        parts.append("partial_batch=1")
    return "; ".join(parts)[:500]


def map_channel(curated: dict[str, Any], point: dict[str, Any], row: Any) -> str:
    kind = s(curated, "kind")
    authority = s(row, "authority") or s(point, "authority")
    if kind == "official":
        return "bulletin"
    if kind == "search":
        title = s(curated, "title")
        if "年鉴" in title or "统计公报" in title:
            return "yearbook"
        return "news_corpus"
    if authority == "官方":
        return "bulletin"
    if authority == "媒体":
        return "news_corpus"
    if authority in ("专家", "expert"):
        return "expert"
    if authority == "第三方":
        return "third_party"
    return "news_corpus"


def build_present_record(
    row: Any,
    point: dict[str, Any],
    measure: dict[str, Any],
    parsed: ParsedValue,
    actual_unit: str,
    context: str,
    verified: str,
    curated: dict[str, Any],
    dims: Dims,
    assumed_unit: bool,
    partial_batch: bool,
    year: int | None = None,
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    authority = s(row, "authority") or s(point, "authority")
    source_authority = AUTHORITY_MAP.get(authority, "unknown")
    source_channel = map_channel(curated, point, row)
    channel_info = dims.channels.get(source_channel, {})
    quality = parse_int(s(row, "data_quality")) or parse_int(s(point, "quality_grade"))
    if quality is None:
        quality = int(channel_info.get("default_quality") or 4)

    ids = split_ids(s(row, "source_article_ids"))
    chosen_id = s(point, "article_id")
    ordered_ids = ([chosen_id] if chosen_id else []) + [x for x in ids if x != chosen_id]
    evidence_ref = ",".join(ordered_ids)

    value_text = s(point, "value_text") or f"{format_number(parsed.num)}{actual_unit}"
    source_ref = _clip(s(curated, "title") or s(row, "data_source") or source_channel,
                       200, "source_ref", ctx)
    publish_date = safe_date(s(point, "publish_date")) or safe_date(s(curated, "date_final"))
    n_sources = parse_int(s(row, "n_sources"))
    if n_sources is None:
        n_sources = len(ids)
    value_min = parse_float(s(row, "value_min"))
    value_max = parse_float(s(row, "value_max"))
    if value_min is None:
        value_min = parsed.num
    if value_max is None:
        value_max = parsed.num

    return {
        "_row_no": 0,
        "region_code": s(row, "region_code"),
        "year": year if year is not None else parse_int(s(row, "stat_year")),
        "level3_code": s(row, "level3_code"),
        "measure_code": measure["measure_code"],
        "context_type": context,
        "source_channel": source_channel,
        "source_authority": source_authority,
        "source_ref": source_ref,
        "obs_status": "present",
        "value_num": decimal_or_none(parsed.num),
        "value_text": _clip(value_text, 255, "value_text", ctx),
        "value_unit": actual_unit,
        "data_quality": quality,
        "n_sources": n_sources,
        "value_min": decimal_or_none(value_min),
        "value_max": decimal_or_none(value_max),
        "agreement": to_bool_int(s(row, "agreement")),
        "evidence_ref": evidence_ref,
        "source_publish_date": publish_date,
        "chosen_reason": _clip(s(row, "chosen_reason"), 255, "chosen_reason", ctx),
        "remark": _clip(build_remark(
            row, point, measure, actual_unit, verified,
            assumed_unit, parsed.approx, partial_batch,
        ), 500, "remark", ctx),
    }


def build_missing_record(
    row: Any,
    measure: dict[str, Any],
    context: str,
    ctx: dict[str, Any] | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    ids = split_ids(s(row, "source_article_ids"))
    return {
        "region_code": s(row, "region_code"),
        "year": year if year is not None else parse_int(s(row, "stat_year")),
        "level3_code": s(row, "level3_code"),
        "measure_code": measure["measure_code"],
        "context_type": context,
        "source_channel": "none",
        "source_authority": "unknown",
        "source_ref": "",
        "obs_status": "missing",
        "value_num": None,
        "value_text": None,
        "value_unit": None,
        "data_quality": None,
        "n_sources": parse_int(s(row, "n_sources")) or len(ids),
        "value_min": None,
        "value_max": None,
        "agreement": to_bool_int(s(row, "agreement")),
        "evidence_ref": ",".join(ids),
        "source_publish_date": None,
        "chosen_reason": _clip(s(row, "chosen_reason"), 255, "chosen_reason", ctx),
        "remark": _clip(
            f"resolved 无实际口径; stat_year_src={s(row, 'stat_year_src')}; "
            f"agreement={s(row, 'agreement')}; n_support={s(row, 'n_support')}",
            500, "remark", ctx),
    }


def evaluate_row(row: Any, ctx: dict[str, Any]) -> tuple[dict[str, Any] | None, list[Issue]]:
    args: argparse.Namespace = ctx["args"]
    dims: Dims = ctx["dims"]
    issues: list[Issue] = []

    region_code = s(row, "region_code")
    level3 = s(row, "level3_code")
    metric = s(row, "metric")
    stat_year = s(row, "stat_year")
    canonical = s(row, "canonical_value")
    context, inconsistent = parse_chosen_context(s(row, "chosen_reason"))
    ctx["context"] = context

    # 1) CLI 过滤
    if args.only_level3 and level3 not in args.only_level3:
        issues.append(make_issue(ctx, "skip", "policy", "FILTERED", f"level3={level3} 不在 --only-level3"))
        return None, issues
    region = dims.regions.get(region_code)
    if args.province_code:
        province = str(region.get("province_code") or "") if region else region_code[:2] + "0000"
        if province != args.province_code:
            issues.append(make_issue(ctx, "skip", "policy", "FILTERED", f"province={province} 不在 --province-code"))
            return None, issues

    # 2) 维度
    if region is None:
        issues.append(make_issue(ctx, "reject", "hard", "REGION_UNKNOWN", f"region_code={region_code} 不在 dim_region"))
        return None, issues
    if int(region.get("is_active") or 0) != 1:
        issues.append(make_issue(ctx, "reject", "hard", "REGION_INACTIVE", f"region_code={region_code} 已失效"))
        return None, issues

    # 3) 指标阻断 + 维度状态
    if level3 in args.block_level3:
        issues.append(make_issue(
            ctx, "reject", "policy", "LEVEL3_BLOCKED",
            f"{level3} 被 --block-level3 阻断（当前 A103 存在系统性 count/家 错配）",
            "如确认数据合法，使用 --allow-level3 A103 显式放开。",
        ))
        return None, issues
    index_info = dims.indexes.get(level3) or {}
    index_status = str(index_info.get("status", "") or "")
    if index_status and index_status != "active" and not args.allow_inactive_index:
        issues.append(make_issue(
            ctx, "reject", "policy", "INDEX_INACTIVE",
            f"{level3} 的 dim_index.status={index_status}，不是 active",
            "如确认仍要写入，使用 --allow-inactive-index。",
        ))
        return None, issues

    # 3.5) 年份先校验：放在空值分支之前，避免 --include-missing 对空年份 int("") 崩溃
    year_value = parse_int(stat_year)
    if year_value is None or not (1990 <= year_value <= 2100):
        issues.append(make_issue(
            ctx, "reject", "hard", "YEAR_INVALID", f"stat_year={stat_year!r}"))
        return None, issues

    # 4) 口径
    measure = None
    if metric:
        measure = dims.measures.get((level3, metric))
        if measure is None:
            issues.append(make_issue(
                ctx, "reject", "hard", "MEASURE_NOT_FOUND",
                f"({level3}, {metric}) 不在 dim_index_measure",
                "检查 metric 是否与指标口径一致；A103 应为 share/%，不是 count/家。",
            ))
            return None, issues
    else:
        measure = dims.primary.get(level3)
        if measure is None:
            issues.append(make_issue(ctx, "reject", "hard", "MEASURE_NOT_FOUND", f"{level3} 没有主口径"))
            return None, issues

    if inconsistent:
        issues.append(make_issue(
            ctx, "reject", "policy", "CONTEXT_INCONSISTENT",
            f"chosen_reason 前后矛盾：{s(row, 'chosen_reason')}",
        ))
        return None, issues

    # 5) 语境：先于空值判断，避免 --include-missing 绕过 --include-non-actual
    if context != "actual" and not args.include_non_actual:
        issues.append(make_issue(
            ctx, "skip", "policy", "NON_ACTUAL_CONTEXT",
            f"context_type={context}；默认只入 actual",
            "如需入库，使用 --include-non-actual。",
        ))
        return None, issues

    # 6) 空值
    if not canonical:
        if args.include_missing:
            record = build_missing_record(row, measure, context, ctx=ctx, year=year_value)
            issues.append(make_issue(ctx, "warn", "soft", "EMPTY_VALUE_MISSING", "主值为空，按 --include-missing 写入 missing"))
            return record, issues
        issues.append(make_issue(
            ctx, "skip", "policy", "EMPTY_VALUE",
            f"canonical_value 为空；n_sources={s(row, 'n_sources')}，agreement={s(row, 'agreement')}",
            "空值不能写 0；确认有实际值后重跑，或用 --include-missing 写入缺失占位。",
        ))
        return None, issues

    # 7) 证据补齐
    point, enrich_err = match_evidence(row, ctx["points"], context)
    if point is None:
        issues.append(make_issue(
            ctx, "reject", "hard", enrich_err or "ENRICH_NO_EVIDENCE",
            f"无法从 points_delta/techfin_a101_points 匹配到唯一证据点（{enrich_err}）",
            "检查 source_article_ids、level3_code、region_code、stat_year、value_num 是否与证据级一致。",
        ))
        return None, issues

    # 7.1) LLM 证据可验证性：evidence_snippet 必须在正文/附件中能定位到
    if s(point, "extract_method") == "llm" and s(point, "evidence_verified") not in (
            "1", "True", "true"):
        if args.allow_unverified_evidence:
            issues.append(make_issue(
                ctx, "warn", "soft", "EVIDENCE_UNVERIFIED",
                "LLM 证据片段未在正文/附件中定位到（已由 --allow-unverified-evidence 放行）",
                "建议人工核对 evidence_snippet。", point=point,
            ))
        else:
            issues.append(make_issue(
                ctx, "reject", "hard", "EVIDENCE_UNVERIFIED",
                "LLM 证据片段未在正文/附件中定位到",
                "核实证据原文；或使用 --allow-unverified-evidence 显式放行。", point=point,
            ))
            return None, issues

    # 8) 数值/单位
    parsed = parse_value(s(point, "value_text"), canonical, s(point, "unit"))
    if parsed is None:
        issues.append(make_issue(
            ctx, "reject", "hard", "VALUE_NOT_NUMERIC",
            f"无法解析数值：canonical_value={canonical!r}, value_text={s(point, 'value_text')!r}",
            point=point,
        ))
        return None, issues

    actual_unit = parsed.unit or s(point, "unit")
    ok, unit_code = unit_compatible(actual_unit, measure["standard_unit"])
    assumed_unit = (unit_code == "UNIT_ASSUMED")
    if not ok:
        issues.append(make_issue(
            ctx, "reject", "hard", unit_code or "UNIT_MISMATCH",
            f"单位 {actual_unit!r} 与标准单位 {measure['standard_unit']!r} 不兼容",
            "A103 应为 %；A101 的“个”可能是“工作日”假阳性，需核实原文。",
            point=point,
        ))
        return None, issues
    if assumed_unit:
        actual_unit = measure["standard_unit"]

    # 9) 范围
    if measure["standard_unit"] == "%":
        if measure["measure_code"] != "growth" and not (0 <= parsed.num <= 100):
            issues.append(make_issue(
                ctx, "reject", "hard", "PERCENT_RANGE",
                f"百分比值 {parsed.num} 不在 0—100",
                point=point,
            ))
            return None, issues
    if measure["measure_code"] in ("count", "amount", "density", "share", "coverage") and parsed.num < 0:
        issues.append(make_issue(ctx, "reject", "hard", "VALUE_NEGATIVE", f"负值 {parsed.num}", point=point))
        return None, issues

    # 10) 假阳性
    window = evidence_window(s(point, "evidence_snippet"), s(point, "value_text"), parsed.num)
    title = s(point, "title") or s(ctx["curated"].get(s(point, "article_id"), {}), "title")
    if detect_workday(s(point, "value_text"), window, actual_unit):
        issues.append(make_issue(
            ctx, "reject", "hard", "FP_WORKDAY",
            "数字后紧跟“工作日/个月/人/人次”等非指标单位",
            "这是公示期等时间表述，不是指标值。", point=point,
        ))
        return None, issues
    if detect_subject(window, parsed.num):
        issues.append(make_issue(
            ctx, "reject", "hard", "FP_SUBJECT",
            "数字左侧主语是服务企业/服务机构/孵化器/平台/项目/名单/批次等",
            "该数字不是目标指标值。", point=point,
        ))
        return None, issues
    if parsed.approx and not args.allow_approx:
        issues.append(make_issue(
            ctx, "reject", "policy", "VALUE_APPROX",
            f"数值含约数/下界词：{s(point, 'value_text')}",
            "如需入库，使用 --allow-approx。", point=point,
        ))
        return None, issues
    partial_batch = detect_partial_batch(title, window, level3)
    if partial_batch and not args.allow_partial_batch:
        issues.append(make_issue(
            ctx, "reject", "policy", "PARTIAL_BATCH",
            "标题/片段含“第 X 批/补充公布/异地搬迁/更名/取消资格/认定报备”，存量型指标不能直接用批次值",
            "如研究口径接受年度认定批次，使用 --allow-partial-batch。", point=point,
        ))
        return None, issues

    # 11) 地区
    prefix = number_prefix(window, parsed.num)
    region_issues = detect_region_issue(prefix, region, dims)
    for code, detail in region_issues:
        issues.append(make_issue(
            ctx, "reject", "hard", code, detail,
            "检查原文中的地区主语；全市/其他区县/子区域的值不能归到本行地区。",
            point=point,
        ))
    if region_issues:
        return None, issues

    # 11.1) 地区置信与拆分口径：默认硬拒绝，避免"没采到/全文回退"污染事实表
    level = int(region.get("region_level") or 0)
    region_from = s(row, "region_from")
    agg_type = s(row, "aggregation_type")
    if level == 3 and region_from in ("query", "none", "scope_national"):
        if args.allow_region_query:
            issues.append(make_issue(
                ctx, "warn", "soft", "REGION_UNVERIFIED",
                f"region_from={region_from}，县级地区无文本证据（已由 --allow-region-query 放行）",
                "建议人工核对地区归属。", point=point,
            ))
        else:
            issues.append(make_issue(
                ctx, "reject", "hard", "REGION_UNVERIFIED",
                f"region_from={region_from}，县级地区无文本证据",
                "确认原文明确写有该地区；或使用 --allow-region-query 显式放行。", point=point,
            ))
            return None, issues
    if agg_type == "breakdown" and not args.allow_breakdown:
        issues.append(make_issue(
            ctx, "reject", "hard", "AGGREGATE_VALUE",
            "aggregation_type=breakdown（分别有/各区/四区/其中），拆分值不能代表地区总量",
            "如研究口径接受拆分值，使用 --allow-breakdown。", point=point,
        ))
        return None, issues

    verified = region_verified(window, title, region, dims)
    if verified == "metadata" and s(row, "region_from") == "query":
        issues.append(make_issue(
            ctx, "warn", "soft", "REGION_UNVERIFIED",
            "证据窗口和标题都没有出现本行地区名（region_from=query）",
            "建议人工核对地区归属。", point=point,
        ))

    # 12) 年份
    year = parse_int(stat_year)
    if year is None or not (1990 <= year <= 2100):
        issues.append(make_issue(ctx, "reject", "hard", "YEAR_INVALID", f"stat_year={stat_year!r}", point=point))
        return None, issues
    year_src = s(row, "stat_year_src")
    year_mark = f"{year}年"
    if year_src in ("doc", "article", "none"):
        if year_mark not in window:
            if args.allow_low_confidence_year:
                issues.append(make_issue(ctx, "warn", "soft", "YEAR_LOW_CONFIDENCE", f"stat_year_src={year_src}，证据中无 {year_mark}", point=point))
            else:
                issues.append(make_issue(
                    ctx, "reject", "policy", "YEAR_LOW_CONFIDENCE",
                    f"stat_year_src={year_src} 且证据中无 {year_mark}",
                    "文档日期不等于统计年；核实年份或使用 --allow-low-confidence-year。", point=point,
                ))
                return None, issues
        else:
            issues.append(make_issue(ctx, "warn", "soft", "YEAR_LOW_CONFIDENCE_RESOLVED", f"stat_year_src={year_src}，但证据含 {year_mark}", point=point))
    elif year_src == "window" and year_mark not in window:
        issues.append(make_issue(ctx, "warn", "soft", "YEAR_WINDOW_UNVERIFIED", f"stat_year_src=window，证据中无 {year_mark}", point=point))

    year_conf = parse_float(s(row, "year_confidence"))
    if year_conf is not None and year_conf < 0.5:
        if args.allow_low_confidence_year:
            issues.append(make_issue(
                ctx, "warn", "soft", "YEAR_LOW_CONFIDENCE",
                f"year_confidence={year_conf:.2f} < 0.5（已放行）", point=point,
            ))
        else:
            issues.append(make_issue(
                ctx, "reject", "policy", "YEAR_LOW_CONFIDENCE",
                f"year_confidence={year_conf:.2f} < 0.5；统计年证据不足",
                "核实年份，或使用 --allow-low-confidence-year。", point=point,
            ))
            return None, issues

    publish_year = parse_int(s(point, "publish_date")[:4]) if s(point, "publish_date") else None
    if publish_year and year > publish_year:
        issues.append(make_issue(
            ctx, "reject", "hard", "YEAR_AFTER_PUBLISH",
            f"统计年 {year} 晚于来源发布日期 {s(point, 'publish_date')}",
            point=point,
        ))
        return None, issues

    # 13) 记录
    curated = ctx["curated"].get(s(point, "article_id"), {}) or ctx["curated"].get(split_ids(s(row, "source_article_ids"))[0] if split_ids(s(row, "source_article_ids")) else "", {})
    record = build_present_record(
        row, point, measure, parsed, actual_unit, context, verified,
        curated, dims, assumed_unit, partial_batch, year=year, ctx=ctx,
    )

    # 一致性警告
    ids = split_ids(s(row, "source_article_ids"))
    n_sources = parse_int(s(row, "n_sources"))
    if n_sources is not None and len(ids) and n_sources != len(ids):
        issues.append(make_issue(
            ctx, "warn", "soft", "SOURCE_COUNT_MISMATCH",
            f"n_sources={n_sources} 与 source_article_ids 数 {len(ids)} 不一致", point=point,
        ))
    vmin = parse_float(s(row, "value_min"))
    vmax = parse_float(s(row, "value_max"))
    if vmin is not None and vmax is not None and not (vmin <= parsed.num <= vmax):
        issues.append(make_issue(
            ctx, "warn", "soft", "RANGE_INCONSISTENT",
            f"主值 {parsed.num} 不在 value_min={vmin} / value_max={vmax} 之间", point=point,
        ))
    if not canonical and to_bool_int(s(row, "agreement")) == 1:
        issues.append(make_issue(
            ctx, "warn", "soft", "AGREEMENT_WITHOUT_VALUE",
            "agreement=True 但主值为空；agreement 只表示取值一致，不代表有可用主值", point=point,
        ))

    # 汇总本行所有错误码
    codes = [i.code for i in issues]
    for issue in issues:
        issue.codes_all = codes
    return record, issues


# ==================== 写库 ====================

def record_key(record: dict[str, Any]) -> tuple[str, int, str, str, str]:
    return (
        str(record["region_code"]), int(record["year"]),
        str(record["level3_code"]), str(record["measure_code"]),
        str(record["context_type"]),
    )


def record_params(record: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(record.get(key) for key in PARAM_KEYS)


def write_records(
    conn,
    records: list[dict[str, Any]],
    existing: dict[tuple[str, int, str, str, str], dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[WriteStats, list[Issue]]:
    stats = WriteStats()
    issues: list[Issue] = []

    present_to_write: list[dict[str, Any]] = []
    missing_to_write: list[dict[str, Any]] = []

    for record in records:
        key = record_key(record)
        old = existing.get(key)
        is_present = record.get("obs_status") == "present"

        if not is_present:
            # missing 只补空，不覆盖已有行
            if old is None:
                missing_to_write.append(record)
                stats.inserted += 1
            else:
                stats.unchanged += 1
            continue

        if old is None:
            present_to_write.append(record)
            stats.inserted += 1
            continue

        if old.get("obs_status") != "present":
            present_to_write.append(record)
            stats.updated += 1
            continue

        if args.upsert_policy == "skip":
            stats.unchanged += 1
            continue

        if args.upsert_policy == "better":
            old_q = int(old.get("data_quality") or 4)
            new_q = int(record.get("data_quality") or 4)
            if new_q > old_q:
                stats.unchanged += 1
                continue

        present_to_write.append(record)
        stats.updated += 1

    if args.dry_run:
        return stats, issues

    if args.write_mode == "transaction" and args.on_db_error == "abort":
        total = len(present_to_write) + len(missing_to_write)
        try:
            with conn.cursor() as cur:
                if present_to_write:
                    cur.executemany(UPSERT_SQL, [record_params(r) for r in present_to_write])
                if missing_to_write:
                    cur.executemany(MISSING_SQL, [record_params(r) for r in missing_to_write])
            conn.commit()
        except Exception as exc:
            conn.rollback()
            stats.inserted = 0
            stats.updated = 0
            stats.db_errors = total
            issues.append(Issue(
                row_no=0, input_file="", region_code="", level3_code="", stat_year="",
                action="reject", severity="hard", code="DB_TRANSACTION_ROLLBACK",
                detail=f"{type(exc).__name__}: {exc}",
                suggestion="事务已整体回滚；修复后重跑，或使用 --on-db-error isolate / --write-mode chunked。",
            ))
        return stats, issues

    def execute(items: list[dict[str, Any]], sql: str) -> None:
        if not items:
            return
        chunk_size = max(1, int(args.batch_size))
        chunks = [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
        for chunk in chunks:
            try:
                with conn.cursor() as cur:
                    cur.executemany(sql, [record_params(r) for r in chunk])
                conn.commit()
            except Exception:
                conn.rollback()
                # 分批失败 → 单行重试，隔离坏行
                for record in chunk:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(sql, record_params(record))
                        conn.commit()
                    except Exception as one_exc:
                        conn.rollback()
                        stats.db_errors += 1
                        issues.append(Issue(
                            row_no=int(record.get("_row_no") or 0),
                            input_file=str(record.get("_input_file") or ""),
                            region_code=str(record.get("region_code") or ""),
                            level3_code=str(record.get("level3_code") or ""),
                            stat_year=str(record.get("year") or ""),
                            action="reject", severity="hard", code="DB_ERROR",
                            detail=f"{type(one_exc).__name__}: {one_exc}",
                            suggestion="检查字段长度、外键、CHECK 约束。",
                        ))

    execute(present_to_write, UPSERT_SQL)
    execute(missing_to_write, MISSING_SQL)
    return stats, issues


# ==================== 报告 ====================

def write_reports(
    report_dir: Path,
    stats: LoadStats,
    issues: list[Issue],
    records: list[dict[str, Any]],
    inputs: list[InputFile],
    dims: Dims | None,
    args: argparse.Namespace,
    write_stats: WriteStats,
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)

    # load_errors.csv
    rows = []
    for issue in issues:
        item = asdict(issue)
        item["error_code"] = issue.code
        item["error_detail"] = issue.detail
        item["error_codes_all"] = ";".join(issue.codes_all or [issue.code])
        rows.append(item)
    errors = pd.DataFrame(rows, columns=ERROR_COLS)
    errors.to_csv(report_dir / "load_errors.csv", index=False, encoding="utf-8-sig")

    # 统计
    code_counter = Counter((i.action, i.severity, i.code) for i in issues)
    level3_counter = Counter(r.get("level3_code", "") for r in records)
    region_counter = Counter(r.get("region_code", "") for r in records)
    channel_counter = Counter(r.get("source_channel", "") for r in records)
    quality_counter = Counter(str(r.get("data_quality", "")) for r in records)

    lines: list[str] = []
    lines.append("# 科技金融 CSV → MySQL 入库报告")
    lines.append("")
    lines.append(f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- loader 版本：`{LOADER_VERSION}`")
    lines.append(f"- 目标库：`{args.database}` @ `{args.host}:{args.port}`，用户 `{args.user}`")
    lines.append(f"- dry-run：`{bool(args.dry_run)}`")
    lines.append(f"- 数据库写入策略：`{args.upsert_policy}`")
    lines.append(f"- 阻断指标：`{','.join(sorted(args.block_level3)) or '（无）'}`")
    lines.append("")
    lines.append("## 1. 输入文件")
    lines.append("")
    lines.append("| 文件 | 运行目录 | SHA-256 |")
    lines.append("|---|---|---|")
    for inp in inputs:
        lines.append(f"| `{inp.path}` | `{inp.run_dir}` | `{inp.sha256[:16]}…` |")
    lines.append("")
    lines.append("## 2. 漏斗")
    lines.append("")
    lines.append("| 阶段 | 行数 | 说明 |")
    lines.append("|---|---:|---|")
    lines.append(f"| 输入 | {stats.input_rows} | 读取到的 resolved 行 |")
    lines.append(f"| 通过 | {stats.passed} | 通过严格校验的记录 |")
    write_note = "dry-run 预测：" if args.dry_run else ""
    lines.append(f"| 写入 | {write_stats.inserted + write_stats.updated} | {write_note}新增 {write_stats.inserted} / 更新 {write_stats.updated} / 不变 {write_stats.unchanged} |")
    lines.append(f"| 拒绝 | {stats.rejected} | 其中 hard 拒绝 {stats.hard_rejects} |")
    lines.append(f"| 跳过 | {stats.skipped} | 空值/非 actual/CLI 过滤 |")
    lines.append(f"| 警告 | {stats.warnings} | 不阻断写入，见第 5 节 |")
    lines.append(f"| DB 错误 | {write_stats.db_errors} | 失败行见 `load_errors.csv` |")
    lines.append("")
    lines.append("## 3. 拒绝/跳过原因")
    lines.append("")
    lines.append("| action | severity | error_code | 数量 | 示例 |")
    lines.append("|---|---|---|---:|---|")
    examples: dict[tuple[str, str, str], str] = {}
    for issue in issues:
        examples.setdefault((issue.action, issue.severity, issue.code), issue.detail)
    for (action, severity, code), count in code_counter.most_common():
        example = examples.get((action, severity, code), "").replace("|", "/")[:120]
        lines.append(f"| {action} | {severity} | `{code}` | {count} | {example} |")
    lines.append("")
    lines.append("## 4. 写入明细")
    lines.append("")
    if records:
        lines.append("| region | year | level3 | measure | context | value | unit | quality | channel | evidence_ref |")
        lines.append("|---|---:|---|---|---|---:|---|---:|---|---|")
        for r in records[:200]:
            lines.append(
                f"| {r.get('region_code','')} | {r.get('year','')} | {r.get('level3_code','')} "
                f"| {r.get('measure_code','')} | {r.get('context_type','')} "
                f"| {r.get('value_num','')} | {r.get('value_unit','')} "
                f"| {r.get('data_quality','')} | {r.get('source_channel','')} "
                f"| {r.get('evidence_ref','')} |"
            )
        if len(records) > 200:
            lines.append(f"| … | | | | | | | | | 其余 {len(records) - 200} 行 |")
    else:
        lines.append("本次没有通过严格校验的记录。")
    lines.append("")
    lines.append("## 5. 警告")
    lines.append("")
    warning_issues = [i for i in issues if i.action == "warn"]
    if warning_issues:
        for issue in warning_issues[:100]:
            lines.append(f"- 行 {issue.row_no} `{issue.code}`：{issue.detail}")
        if len(warning_issues) > 100:
            lines.append(f"- … 其余 {len(warning_issues) - 100} 条见 `load_errors.csv`")
    else:
        lines.append("无。")
    lines.append("")
    lines.append("## 6. 分布")
    lines.append("")
    lines.append(f"- 指标：{dict(level3_counter)}")
    lines.append(f"- 地区：{dict(region_counter)}")
    lines.append(f"- 来源渠道：{dict(channel_counter)}")
    lines.append(f"- 质量等级：{dict(quality_counter)}")
    lines.append("")
    lines.append("## 6.1 字段截断")
    lines.append("")
    if stats.truncated_fields:
        for field_name, count in sorted(stats.truncated_fields.items()):
            lines.append(f"- `{field_name}` 截断 {count} 次")
    else:
        lines.append("无。")
    lines.append("")
    lines.append("## 7. 维度自检")
    lines.append("")
    if dims:
        lines.append(f"- dim_region：{len(dims.regions)} 行")
        lines.append(f"- dim_index_measure：{len(dims.measures)} 行")
        lines.append(f"- 主口径：{len(dims.primary)} 个指标")
        lines.append(f"- dim_source_channel：{len(dims.channels)} 行")
    else:
        lines.append("- 未加载维度。")
    lines.append("")
    lines.append("## 8. 复现命令")
    lines.append("")
    lines.append("```bash")
    lines.append(" ".join([sys.executable, *sys.argv]))
    lines.append("```")
    lines.append("")
    lines.append("## 9. 重要提示")
    lines.append("")
    lines.append("- `agreement=True` 不代表有可用主值；空值仍按 `EMPTY_VALUE` 跳过。")
    lines.append("- A103 默认阻断：A103 证据点通常为 `count/家`，与 `share/%` 定义不符。")
    lines.append("- 单位存实际观测单位，不做跨单位换算；`value_text` 保留原文。")
    lines.append("- `fact_index_value` 唯一键为 `(region_code, year, level3_code, measure_code, context_type)`，重复执行是幂等的。")
    (report_dir / "load_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_load_manifest(
    report_dir: Path,
    out_root: Path,
    started_at: str,
    stats: LoadStats,
    inputs: list[InputFile],
    dims: Dims | None,
    args: argparse.Namespace,
    write_stats: WriteStats,
) -> None:
    """写 load_manifest.json：输入指纹、参数快照、目标库与漏斗统计（不含密码）。"""
    try:
        payload = {
            "schema_version": 1,
            "loader_version": LOADER_VERSION,
            "started_at": started_at,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_root": str(out_root),
            "report_dir": str(report_dir),
            "database": {
                "name": args.database, "host": args.host, "port": args.port,
                "user": args.user, "dry_run": bool(args.dry_run),
            },
            "policy": {
                "upsert": args.upsert_policy,
                "write_mode": args.write_mode,
                "on_db_error": args.on_db_error,
                "skip_imported": bool(args.skip_imported),
                "block_level3": sorted(args.block_level3),
                "include_non_actual": bool(args.include_non_actual),
                "include_missing": bool(args.include_missing),
                "allow_partial_batch": bool(args.allow_partial_batch),
                "allow_approx": bool(args.allow_approx),
                "allow_low_confidence_year": bool(args.allow_low_confidence_year),
            },
            "inputs": [
                {**file_fingerprint(inp.path), "run_dir": inp.run_dir,
                 "run_order": list(inp.run_order)}
                for inp in inputs
            ],
            "dimensions": {
                "regions": len(dims.regions) if dims else 0,
                "measures": len(dims.measures) if dims else 0,
                "primary": len(dims.primary) if dims else 0,
                "channels": len(dims.channels) if dims else 0,
            },
            "stats": asdict(stats),
            "write_stats": asdict(write_stats),
        }
        write_json_atomic(Path(report_dir) / "load_manifest.json", payload)
    except Exception as exc:
        print(f"[警告] 写 load_manifest.json 失败（不影响入库）：{exc}")


# ==================== 主流程 ====================

def run_once(args: argparse.Namespace) -> int:
    """包装 _run_once：无论成功/失败/异常，都更新报告目录的 _run.json。"""
    state = {"dir": None, "status": "failed"}
    try:
        return _run_once(args, state)
    finally:
        report_dir = state.get("dir")
        if report_dir:
            finish_run_manifest(Path(report_dir), status=state.get("status", "failed"))


def _run_once(args: argparse.Namespace, state: dict[str, Any]) -> int:
    out_root = Path(pipeline_root(_TOPIC_DIR, args.output_dir))
    if args.report_dir:
        report_dir = resolve_user_path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        write_run_manifest(
            report_dir, stage="load", argv=sys.argv[1:],
            data_root=out_root, report_root=report_dir.parent,
        )
    else:
        report_dir = Path(new_load_run(
            "load", override=args.report_root, topic_dir=_TOPIC_DIR, argv=sys.argv[1:]))
    state["dir"] = report_dir
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with run_logging(report_dir / "load_log"):
        print("=" * 60)
        print(f"{cfg.TOPIC_LABEL} · CSV → MySQL 入库")
        print("=" * 60)
        print(f"数据根目录：{out_root}")
        print(f"报告目录：{report_dir}")
        legacy = Path(_TOPIC_DIR) / cfg.LEGACY_OUTPUT_ROOT
        try:
            empty_new = not any(out_root.iterdir())
        except OSError:
            empty_new = True
        if empty_new and legacy.exists():
            print(f"[提示] 新数据根为空；旧产物保留在 {legacy}（只读）。"
                  f"如需读取旧运行目录，使用 --input-dir。")

        inputs = discover_inputs(args, out_root)
        if not inputs:
            print("[中止] 没有可用的 points_resolved.csv")
            return 2
        print(f"[输入] {len(inputs)} 个文件：")
        for inp in inputs:
            print(f"  - {inp.path}  (run={inp.run_dir or '-'}, sha256={inp.sha256[:12]}…)")

        points = load_points(args, out_root, inputs)
        curated = load_curated(args, out_root)
        print(f"[证据] 覆盖文章数：{len([k for k, v in points.items() if v])}")
        print(f"[文章] 溯源 lookup：{len(curated)} 篇")

        conn = connect_db(args)
        if conn is None:
            return 2
        try:
            dims = load_dimensions(conn)
            if dims is None:
                return 2
            print(f"[维度] region={len(dims.regions)} measure={len(dims.measures)} "
                  f"primary={len(dims.primary)} channel={len(dims.channels)}")

            # 文件级幂等：按 SHA-256 跳过已成功导入的文件
            if args.skip_imported and not args.force:
                imported = existing_batch_hashes(conn)
                before = len(inputs)
                inputs = [i for i in inputs if i.sha256 not in imported]
                skipped = before - len(inputs)
                if skipped:
                    print(f"[幂等] --skip-imported：跳过已导入文件 {skipped} 个")
                if not inputs:
                    print("[完成] 所有输入文件均已导入，无需重复写库。")
                    return 0

            frames = []
            for inp in inputs:
                df = read_csv(inp.path)
                missing = [c for c in RESOLVED_REQUIRED if c not in df.columns]
                if missing:
                    print(f"[中止] {inp.path} 缺少列：{missing}")
                    return 2
                df = df.copy()
                df["_input_file"] = str(inp.path)
                df["_run_dir"] = inp.run_dir
                df["_run_order"] = [inp.run_order] * len(df)
                df["_row_no"] = range(1, len(df) + 1)
                frames.append(df)

            data = pd.concat(frames, ignore_index=True, sort=False).fillna("")

            stats = LoadStats()

            if args.all_runs:
                # 同一格保留最新一轮；冲突写入警告
                best: dict[tuple, pd.Series] = {}
                duplicate_conflicts = 0
                for _, row in data.iterrows():
                    context, _ = parse_chosen_context(s(row, "chosen_reason"))
                    # 年份按数值归一，避免 "2025" 与 "2025.0" 被当成不同格
                    year_key = parse_int(s(row, "stat_year"))
                    key = (s(row, "region_code"),
                           year_key if year_key is not None else s(row, "stat_year"),
                           s(row, "level3_code"), s(row, "metric"), context)
                    old = best.get(key)
                    if old is None:
                        best[key] = row
                    else:
                        duplicate_conflicts += 1
                        if tuple(row["_run_order"]) >= tuple(old["_run_order"]):
                            best[key] = row
                data = pd.DataFrame(list(best.values())).fillna("")
                if duplicate_conflicts:
                    print(f"[去重] --all-runs 合并后去掉 {duplicate_conflicts} 行重复格（保留最新轮）")

            if args.limit:
                data = data.head(max(0, int(args.limit)))
            stats.input_rows = len(data)

            records: list[dict[str, Any]] = []
            issues: list[Issue] = []
            for _, row in data.iterrows():
                ctx = {
                    "row": row,
                    "row_no": int(row.get("_row_no") or 0),
                    "input_file": s(row, "_input_file"),
                    "run_dir": s(row, "_run_dir"),
                    "args": args,
                    "dims": dims,
                    "points": points,
                    "curated": curated,
                    "load_stats": stats,
                }
                try:
                    record, row_issues = evaluate_row(row, ctx)
                except Exception as exc:
                    # 单行格式/解析异常隔离为 hard 拒绝，避免整批入库中断
                    record = None
                    row_issues = [make_issue(
                        ctx, "reject", "hard", "ROW_EXCEPTION",
                        f"行处理异常 {type(exc).__name__}: {exc}",
                        "已隔离该行；请检查原始 CSV 字段格式。",
                    )]
                issues.extend(row_issues)
                reject = any(i.action == "reject" for i in row_issues)
                skip = any(i.action == "skip" for i in row_issues)
                if any(i.action == "warn" for i in row_issues):
                    stats.warnings += 1
                if reject:
                    stats.rejected += 1
                    if any(i.severity == "hard" for i in row_issues):
                        stats.hard_rejects += 1
                elif skip:
                    stats.skipped += 1
                else:
                    stats.passed += 1
                    if record is not None:
                        record["_row_no"] = int(row.get("_row_no") or 0)
                        record["_input_file"] = s(row, "_input_file")
                        records.append(record)

            existing = load_existing(conn)
            if existing is None:
                print("[中止] 无法读取现有事实表，为避免误覆盖，本次不写库。")
                return 2
            write_stats, db_issues = write_records(conn, records, existing, args)
            issues.extend(db_issues)
            stats.inserted = write_stats.inserted
            stats.updated = write_stats.updated
            stats.unchanged = write_stats.unchanged
            stats.db_errors = write_stats.db_errors

            write_reports(report_dir, stats, issues, records, inputs, dims, args, write_stats)
            write_load_manifest(report_dir, out_root, started_at, stats, inputs, dims, args, write_stats)
            record_import_batch(conn, inputs, stats, write_stats, args)

            print("\n" + "-" * 60)
            print(
                f"[汇总] 输入 {stats.input_rows} → 通过 {stats.passed} → "
                f"写入 {write_stats.inserted + write_stats.updated} "
                f"（新增 {write_stats.inserted} / 更新 {write_stats.updated} / 不变 {write_stats.unchanged}）→ "
                f"拒绝 {stats.rejected} / 跳过 {stats.skipped} / 警告 {stats.warnings} / DB错误 {stats.db_errors}"
            )
            print(f"[报告] {report_dir / 'load_report.md'}")
            print(f"[错误] {report_dir / 'load_errors.csv'}")
            print("-" * 60)

            if args.fail_on_error and (stats.hard_rejects or stats.db_errors):
                return 1
            state["status"] = "ok" if write_stats.db_errors == 0 else "failed"
            return 0
        finally:
            try:
                conn.close()
            except Exception:
                pass
            print(f"[归档] 本轮入库产物：{report_dir}")


def dump_mapping(args: argparse.Namespace) -> int:
    conn = connect_db(args)
    if conn is None:
        return 2
    try:
        dims = load_dimensions(conn)
        if dims is None:
            return 2
        print("level3_code\tmeasure_code\tstandard_unit\tis_primary\tmeasure_name")
        for (level3, measure), info in sorted(dims.measures.items()):
            print(f"{level3}\t{measure}\t{info['standard_unit']}\t{info['is_primary']}\t{info['measure_name']}")
        return 0
    finally:
        conn.close()


def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    p = parse_value_text("2355家")
    check("2355家 → 2355/家", p is not None and p.num == 2355 and p.unit == "家" and not p.approx)
    p = parse_value_text("1.2万家")
    check("1.2万家 → 12000/家", p is not None and p.num == 12000 and p.unit == "家")
    p = parse_value_text("800余家")
    check("800余家 → 800/家 + approx", p is not None and p.num == 800 and p.unit == "家" and p.approx)
    p = parse_value_text("4.2%")
    check("4.2% → 4.2/%", p is not None and p.num == 4.2 and p.unit == "%")
    p = parse_value_text("1.2亿元")
    check("1.2亿元 → 1.2/亿元", p is not None and p.num == 1.2 and p.unit == "亿元")
    p = parse_value_text("10个工作日")
    check("10个工作日 → 10/个 + tail 工作日", p is not None and p.num == 10 and p.unit == "个" and p.tail.startswith("工作日"))

    check("A101 个 与 家 不兼容", unit_compatible("个", "家") == (False, "UNIT_WEAK"))
    check("家 与 家 兼容", unit_compatible("家", "家")[0])
    check("家 与 % 不兼容", not unit_compatible("家", "%")[0])
    check("空单位 + % → PERCENT_UNIT_MISSING", unit_compatible("", "%") == (False, "PERCENT_UNIT_MISSING"))

    check("chosen actual", parse_chosen_context("取实际口径(官方)→actual")[0] == "actual")
    check("chosen planned", parse_chosen_context("无实际口径，仅 planned/target/decline→planned")[0] == "planned")
    check("workday 假阳性", detect_workday("10个", "公示期为10个工作日", "个"))
    check("subject 假阳性", detect_subject("累计服务企业800余家", 800))
    check("全市口径", bool(SCOPE_RE.search("2022年，全市有效期内高新技术企业12340家")))
    check("子区域", bool(SUBUNIT_RE.search("琶洲试验区高新技术企业304家")))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'OK ' if ok else 'FAIL'}  {name}")
    if failed:
        print(f"[自测失败] {len(failed)} 项：{failed}")
        return 1
    print(f"[自测通过] {len(checks)} 项")
    return 0


# ==================== CLI ====================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · CSV → MySQL 数据迁移/入库",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    db = parser.add_argument_group("数据库")
    db.add_argument("--database", default=os.getenv("TECHFIN_DB_NAME", DEFAULT_DATABASE),
                    help="目标数据库名")
    db.add_argument("--host", default=os.getenv("TECHFIN_DB_HOST", "127.0.0.1"), help="MySQL 主机")
    db.add_argument("--port", type=int, default=int(os.getenv("TECHFIN_DB_PORT", "3306")), help="MySQL 端口")
    db.add_argument("--user", default=os.getenv("TECHFIN_DB_USER", "root"), help="MySQL 用户")
    db.add_argument("--password-env", default="TECHFIN_DB_PASSWORD", help="密码所在环境变量名")
    db.add_argument("--ask-password", action="store_true", help="交互输入密码")

    inp = parser.add_argument_group("输入")
    inp.add_argument("--input-dir", default=None, help="输入根或单个运行目录；默认 data 数据根")
    inp.add_argument("--input-file", action="append", default=None, help="显式指定 points_resolved.csv，可重复")
    inp.add_argument("--all-runs", action="store_true", help="合并所有运行目录的 points_resolved.csv")
    inp.add_argument("--points-file", default=None, help="证据级 CSV；默认自动查找 points_delta.csv / techfin_a101_points.csv")
    inp.add_argument("--curated-file", default=None, help="文章级 CSV；默认 curated_corpus.csv")
    inp.add_argument("--output-dir", default=None, help="覆盖流水线数据根（topic_config.DATA_ROOT）")
    inp.add_argument("--limit", type=int, default=None, help="最多处理 N 行，便于试跑")

    policy = parser.add_argument_group("过滤策略")
    policy.add_argument("--only-level3", default=None, help="只处理指定三级指标，逗号分隔，如 A101,A102")
    policy.add_argument("--province-code", default=None, help="只处理指定省，如 440000")
    policy.add_argument("--block-level3", default=DEFAULT_BLOCK_LEVEL3,
                        help="阻断的三级指标，逗号分隔；默认 A103")
    policy.add_argument("--allow-level3", default=None,
                        help="从阻断列表放行的指标，逗号分隔；如 A103")
    policy.add_argument("--include-non-actual", action="store_true", help="写入 planned/target/decline/unknown")
    policy.add_argument("--include-missing", action="store_true", help="空值写 obs_status=missing（不覆盖 present）")
    policy.add_argument("--allow-partial-batch", action="store_true", help="允许补充公布/更名/搬迁等批次值")
    policy.add_argument("--allow-approx", action="store_true", help="允许约数（约/余/突破等）")
    policy.add_argument("--allow-low-confidence-year", action="store_true", help="允许 doc/window 低置信年份")
    policy.add_argument("--allow-region-query", action="store_true",
                        help="允许 region_from=query/none/scope_national 的县级行入库（默认拒绝）")
    policy.add_argument("--allow-breakdown", action="store_true",
                        help="允许 aggregation_type=breakdown 的拆分口径值入库（默认拒绝）")
    policy.add_argument("--allow-inactive-index", action="store_true",
                        help="允许 dim_index.status 非 active（retired/draft）的指标入库（默认拒绝）")
    policy.add_argument("--allow-unverified-evidence", action="store_true",
                        help="允许 evidence_verified=0 的 LLM 数据点入库（默认拒绝）")

    run = parser.add_argument_group("运行")
    run.add_argument("--dry-run", action="store_true", help="只校验和出报告，不写库")
    run.add_argument("--upsert-policy", choices=("overwrite", "better", "skip"), default="overwrite",
                     help="重复键策略")
    run.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每批 executemany 行数")
    run.add_argument("--write-mode", choices=("transaction", "chunked"), default="transaction",
                     help="transaction=全部成功才提交（默认）；chunked=逐批提交（允许部分写入）")
    run.add_argument("--on-db-error", choices=("abort", "isolate"), default="abort",
                     help="transaction 模式下 DB 错误的处理：abort=整批回滚（默认）；isolate=逐行重试")
    run.add_argument("--skip-imported", action="store_true",
                     help="按 file_sha256 跳过 import_batch 中已成功导入的文件")
    run.add_argument("--force", action="store_true",
                     help="与 --skip-imported 配合，强制重新导入")
    run.add_argument("--report-dir", default=None, help="报告目录；指定后直接使用（可能覆盖同名报告）")
    run.add_argument("--report-root", default=None,
                     help="入库报告根；默认 topic_config.LOAD_REPORT_ROOT，报告在其下新建运行目录")
    run.add_argument("--fail-on-error", action="store_true", help="存在 hard 拒绝或 DB 错误时退出码 1")
    run.add_argument("--dump-mapping", action="store_true", help="打印指标口径/单位映射后退出")
    run.add_argument("--self-test", action="store_true", help="运行纯函数自测后退出")

    args = parser.parse_args(argv)

    # 规范化逗号列表
    args.only_level3 = {x.strip() for x in args.only_level3.split(",") if x.strip()} if args.only_level3 else set()
    blocked = {x.strip() for x in str(args.block_level3 or "").split(",") if x.strip()}
    allowed = {x.strip() for x in str(args.allow_level3 or "").split(",") if x.strip()}
    args.block_level3 = blocked - allowed
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.database or ""):
        parser.error("--database 只能包含字母、数字、下划线")
    if args.batch_size < 1:
        parser.error("--batch-size 必须 >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if args.dump_mapping:
        return dump_mapping(args)
    return run_once(args)


if __name__ == "__main__":
    sys.exit(main())
