"""
科技金融指数研究 · ⑤ OCR 补全（扫描件附件）

职责：读 ② 的正文缓存 body_cache/，把【被判为扫描件（is_scanned=True）的 PDF 附件】
用**本地 PaddleOCR**（PP-OCRv5）补出文本，写回缓存（同名 .txt + 更新 JSON 字段）。
**纯本地、不发任何网络请求**；**不 import 任何业务脚本**（只读 crawler_common / topic_config）。

为什么独立成 pass：② 会跳过"已成功抓取"的条目（含已下载的扫描件），故补 OCR 必须在
② 之外单独跑一遍；② 也因此不必依赖 paddle。

顺序：①采集 → ②抓取 → ⑤OCR → ④清洗 → ③抽取（写回缓存后，③ 读附件 .txt 会自动吃到 OCR 文本）。

前置（一次性）：PaddleOCR 模型需先下载——
  set PADDLE_PDX_MODEL_SOURCE=BOS
  python -c "from paddleocr import PaddleOCR; PaddleOCR(lang='ch', ocr_version='PP-OCRv5', device='cpu', use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)"

用法：
  python techfin_ocr.py                # 补全部待办的扫描件附件
  python techfin_ocr.py --limit 2      # 先跑前 2 个附件看效果
  python techfin_ocr.py --refresh      # 忽略 ocr_applied 标记，重新 OCR
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

# ---- 引导：通用工具在仓库根，本脚本在 A/A1/ ----
# 向上找两个锚点：仓库根（crawler_common.py）与主题目录（config/topic_config.py）。
# 脚本进了子目录后，"脚本自身目录"不再是配置所在，故必须显式定位这两处。
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
sys.path.insert(0, str(_REPO_ROOT))             # → crawler_common
sys.path.insert(0, str(_TOPIC_DIR / "config"))  # → topic_config / extract_config

# （配置目录已由上面的主题目录引导加入 sys.path）

# noinspection PyUnresolvedReferences
import topic_config as cfg  # noqa: E402
# noinspection PyUnresolvedReferences
from paths import (  # noqa: E402
    finish_run_manifest,
    new_pipeline_run,
    pipeline_root,
    resolve_cache_path,
)
from crawler_common import (  # noqa: E402
    run_logging,
)

INDEX_COLS = ["article_id", "att_index", "filename", "ocr_pages", "total_pages",
              "text_chars", "avg_conf", "low_conf_pages", "truncated", "seconds", "status"]


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _try_import(name):
    """尝试导入可选依赖，失败返回 None。"""
    try:
        import importlib
        return importlib.import_module(name)
    except Exception:
        return None


def _norm_block(text):
    """规整多行文本：压缩行内空白、合并多余空行。"""
    import re
    if not text:
        return ""
    text = text.replace("　", " ").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def atomic_write_text(path, text):
    """先写 .tmp 再 os.replace，避免中断留下半个文件。"""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text or "")
    os.replace(tmp, path)


# ==================== PaddleOCR 惰性单例 ====================

_OCR = None
_OCR_FAILED = False


def get_ocr():
    """惰性构造 PaddleOCR（模型加载昂贵，全程复用一次）。不可用返回 None。"""
    global _OCR, _OCR_FAILED
    if _OCR is not None:
        return _OCR
    if _OCR_FAILED:
        return None
    try:
        from paddleocr import PaddleOCR
    except Exception as e:
        print(f"[OCR] 无法导入 paddleocr：{e}")
        print("      请确认已安装 paddleocr + paddlepaddle，且模型已下载（见脚本头 Step 0）。")
        _OCR_FAILED = True
        return None
    try:
        t = time.time()
        print(f"[OCR] 正在加载 {cfg.OCR_VERSION}({cfg.OCR_DET_MODEL}/{cfg.OCR_REC_MODEL})"
              f"（{cfg.OCR_DEVICE}）模型……")
        _OCR = PaddleOCR(lang=cfg.OCR_LANG, ocr_version=cfg.OCR_VERSION,
                         device=cfg.OCR_DEVICE,
                         text_detection_model_name=cfg.OCR_DET_MODEL,
                         text_recognition_model_name=cfg.OCR_REC_MODEL,
                         use_doc_orientation_classify=False,
                         use_doc_unwarping=False,
                         use_textline_orientation=False)
        print(f"[OCR] 模型就绪（{time.time() - t:.1f}s）")
    except Exception as e:
        print(f"[OCR] 初始化失败：{e}")
        _OCR_FAILED = True
        return None
    return _OCR


# ==================== PDF 渲染 / OCR ====================

def render_pdf_pages(path, dpi=None, max_pages=None):
    """用 pypdfium2 把 PDF 每页渲染成 RGB numpy 数组。返回 (pages, total_pages, err)。"""
    dpi = dpi or cfg.OCR_DPI
    max_pages = max_pages or cfg.OCR_MAX_PAGES
    pypdfium2 = _try_import("pypdfium2")
    numpy = _try_import("numpy")
    if pypdfium2 is None or numpy is None:
        return [], 0, "缺 pypdfium2/numpy"
    try:
        pdf = pypdfium2.PdfDocument(path)
    except Exception as e:
        return [], 0, f"打开失败:{type(e).__name__}"
    total = len(pdf)
    pages, scale = [], dpi / 72.0
    try:
        for i in range(min(total, max_pages)):
            page = pdf[i]
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil().convert("RGB")
            pages.append(numpy.asarray(pil))
            page.close()
    except Exception as e:
        pdf.close()
        return pages, total, f"渲染失败:{type(e).__name__}"
    pdf.close()
    return pages, total, ""


def _res_scope(r):
    """取 Result 对象的 dict 视图（优先 .json，其次 dict 化）。"""
    d = getattr(r, "json", None)
    if isinstance(d, dict):
        return d
    if isinstance(r, dict):
        return r
    try:
        return dict(r)
    except Exception:
        return {}


def _extract_list(r, key):
    """从结果里取某个列表字段，兼容 {..'res':{key:..}} 与扁平两种形态。"""
    d = _res_scope(r)
    inner = d.get("res") if isinstance(d.get("res"), dict) else None
    for scope in (inner, d):
        if isinstance(scope, dict) and scope.get(key) is not None:
            try:
                return list(scope[key])
            except TypeError:
                return []
    return []


def ocr_pages(ocr, pages):
    """逐页 OCR，返回 (每页文本列表, 所有置信度列表, 每页平均置信度列表)。"""
    page_texts, confs, page_confs = [], [], []
    for arr in pages:
        try:
            res = ocr.predict(arr)
        except Exception as e:
            print(f"    [OCR异常] {type(e).__name__}: {e}")
            page_texts.append("")
            page_confs.append(0.0)
            continue
        lines, page_conf = [], []
        for r in res:
            lines.extend(t for t in _extract_list(r, "rec_texts") if t)
            page_conf.extend(c for c in _extract_list(r, "rec_scores") if c is not None)
        page_texts.append("\n".join(lines))
        page_confs.append(round(sum(page_conf) / len(page_conf), 3) if page_conf else 0.0)
        confs.extend(page_conf)
    return page_texts, confs, page_confs


# ==================== 缓存扫描 ====================

def load_cache(cache_dir):
    """{article_id: (json_path, obj)}，跳过 attach/ 子目录。"""
    out = {}
    base = Path(cache_dir)
    if not base.exists():
        return out
    for bucket in base.iterdir():
        if not bucket.is_dir() or bucket.name == "attach":
            continue
        for jf in bucket.glob("*.json"):
            try:
                obj = json.load(open(jf, encoding="utf-8"))
                out[obj.get("article_id", "")] = (str(jf), obj)
            except Exception:
                continue
    return out


def build_targets(cache, refresh):
    """挑出待 OCR 的附件：PDF 且 is_scanned 且未 ocr_applied（或 --refresh）。"""
    targets = []
    for aid, (jp, obj) in cache.items():
        for i, att in enumerate(obj.get("attachments", []) or []):
            if att.get("status") != "ok" or att.get("ext") != ".pdf":
                continue
            if not att.get("is_scanned"):
                continue
            if att.get("ocr_applied") and not refresh:
                continue
            targets.append((aid, i))
    return targets


def process_one(cache, aid, att_index, cache_dir=None):
    """对单个扫描件附件做 OCR 并写回缓存。返回索引行 dict。"""
    ocr = get_ocr()
    jp, obj = cache[aid]
    att = obj["attachments"][att_index]
    path = (str(resolve_cache_path(cache_dir, att.get("path", ""), att.get("path_rel")))
            if cache_dir else att.get("path", ""))
    row = {"article_id": aid, "att_index": att_index,
           "filename": att.get("filename", ""), "ocr_pages": 0, "total_pages": 0,
           "text_chars": 0, "avg_conf": "", "low_conf_pages": 0,
           "truncated": 0, "seconds": 0, "status": ""}
    if not path or not os.path.exists(path):
        row["status"] = "附件文件缺失"
        return row

    t0 = time.time()
    pages, total, err = render_pdf_pages(path)
    row["total_pages"] = total
    if err or not pages:
        row["status"] = f"渲染失败:{err or '无页'}"
        row["seconds"] = round(time.time() - t0, 1)
        return row

    page_texts, confs, page_confs = ocr_pages(ocr, pages)
    text = _norm_block("\n\n".join(page_texts))
    text_path = att.get("text_path") or os.path.join(
        os.path.dirname(path), (att.get("sha1", "")[:12] or "ocr") + ".txt")
    if cache_dir:
        text_path = str(resolve_cache_path(cache_dir, text_path, att.get("text_rel")))
    atomic_write_text(text_path, text)
    low_conf_pages = [i + 1 for i, c in enumerate(page_confs) if c < 0.8]
    truncated = 1 if total > len(pages) else 0
    if truncated:
        print(f"    [警告] {att.get('filename', '')} 共 {total} 页，"
              f"按 OCR_MAX_PAGES={cfg.OCR_MAX_PAGES} 只处理前 {len(pages)} 页")

    att.update({
        "text_path": text_path, "text_chars": len(text),
        "text_rel": (os.path.relpath(text_path, cache_dir) if cache_dir else ""),
        "text_method": "paddleocr", "is_scanned": False,
        "ocr_applied": True, "ocr_pages": len(pages), "ocr_total_pages": total,
        "ocr_dpi": cfg.OCR_DPI,
        "ocr_avg_conf": (round(sum(confs) / len(confs), 3) if confs else ""),
        "ocr_page_conf": page_confs,
        "ocr_low_conf_pages": low_conf_pages,
        "ocr_truncated": bool(truncated),
    })
    atomic_write_text(jp, json.dumps(obj, ensure_ascii=False, indent=1))

    row.update({"ocr_pages": len(pages), "text_chars": len(text),
                "avg_conf": att["ocr_avg_conf"],
                "low_conf_pages": len(low_conf_pages), "truncated": truncated,
                "seconds": round(time.time() - t0, 1), "status": "ok"})
    return row


# ==================== 主流程 ====================

def run_once(run_dir, paths, cache_dir, refresh=False, limit=None):
    print("=" * 60)
    print(f"{cfg.TOPIC_LABEL} · ⑤ OCR 补全（扫描件附件，{cfg.OCR_VERSION}）")
    print("=" * 60)

    cache = load_cache(cache_dir)
    print(f"正文缓存：{len(cache)} 篇")
    targets = build_targets(cache, refresh)
    if limit:
        targets = targets[:limit]
    print(f"待 OCR 扫描件附件：{len(targets)} 个")
    if not targets:
        print("[中止] 没有待 OCR 的扫描件（或已全部处理；--refresh 可重跑）。")
        return

    if get_ocr() is None:            # 提前失败，避免空跑
        return

    rows, interrupted = [], False
    stats = {"ok": 0, "text": 0, "pages": 0, "sec": 0.0}

    def flush():
        pd.DataFrame(rows, columns=INDEX_COLS).to_csv(
            paths["index"], index=False, encoding="utf-8-sig")

    try:
        for n, (aid, i) in enumerate(targets, 1):
            row = process_one(cache, aid, i, cache_dir)
            rows.append(row)
            flush()                                   # 先落盘
            if row["status"] == "ok":
                stats["ok"] += 1
                stats["text"] += row["text_chars"]
                stats["pages"] += row["ocr_pages"]
                stats["sec"] += row["seconds"]
            print(f"[{n}/{len(targets)}] {row['status']:10s} "
                  f"页 {row['ocr_pages']}/{row['total_pages']} | "
                  f"字数 {row['text_chars']:>6} | 置信 {row['avg_conf']} | "
                  f"{row['seconds']}s | {aid}")
    except KeyboardInterrupt:
        interrupted = True
        print("\n[中断] 收到 Ctrl-C；已处理的附件已写回缓存。")

    flush()
    print("\n" + "-" * 60)
    print(f"[汇总] 成功 {stats['ok']}/{len(targets)}，共 {stats['pages']} 页，"
          f"{stats['text']} 字，用时 {stats['sec']:.0f}s")
    if stats["pages"]:
        print(f"  平均 {stats['sec'] / stats['pages']:.1f}s/页")
    if interrupted:
        print("  [注意] 本轮中断；重跑会跳过已 ocr_applied 的附件。")
    print("  [提示] OCR 文本已写回各自 .txt，并更新缓存 JSON（ocr_applied=True）；")
    print("         接着跑 techfin_curate.py → techfin_extract_points.py 即会被使用。")
    print("-" * 60)


def artifact_paths(run_dir):
    p = cfg.OCR_PREFIX
    return {"log": os.path.join(run_dir, p + "log"),
            "index": os.path.join(run_dir, p + "index.csv")}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · ⑤ OCR 补全（扫描件附件）")
    ap.add_argument("--output-dir", help="数据根目录（覆盖 topic_config.DATA_ROOT）")
    ap.add_argument("--refresh", action="store_true",
                    help="忽略 ocr_applied 标记，重新 OCR")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 个附件（试验用）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    topic_dir = _TOPIC_DIR
    out_root = pipeline_root(topic_dir, args.output_dir)
    cache_dir = str(out_root / cfg.BODY_CACHE_DIR)
    run_dir = new_pipeline_run("ocr", override=args.output_dir, topic_dir=topic_dir)
    paths = artifact_paths(run_dir)
    status = "failed"
    try:
        with run_logging(paths["log"]):
            print(f"数据根目录：{out_root}")
            if not cfg.OCR_ENABLED:
                print("[中止] topic_config.OCR_ENABLED=False，未启用 OCR。")
                return
            run_once(run_dir, paths, cache_dir, refresh=args.refresh, limit=args.limit)
        status = "ok"
    finally:
        finish_run_manifest(run_dir, status=status)
        print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
