"""
科技金融指数研究 · ⑥ 评估闭环（人工标注 → precision/recall）

职责：把"精确"变成**可度量、可复现**的闭环。纯本地、只读数据点 CSV + 正文缓存，
产物落在 `<OUT_ROOT>/eval/`（**固定目录，不随运行轮次变**，因为标注文件要跨轮复用）。

两步走：
  1) 生成标注模板：`--make-template N` → `eval/label_precision.csv`（机器字段 + 空列"人工标签/人工年限"）。
     人工在 CSV 里把 `人工标签` 填 correct / wrong / partial，`人工年限` 填你认为正确的年份。
  2) 评分：`--score` → `eval/eval_report.md`（整体/分组 precision、年份正确率、标注覆盖率）。
  召回（可选）：`--make-recall N` / `--score-recall`（抽文章通读，估漏检）。

改 prompt/规则后，用**同一抽样**（seed 固定）重跑 `--make-template` 与 `--score` 即可对比好坏。

用法：
  python techfin_eval.py --make-template 20
  python techfin_eval.py --score
  python techfin_eval.py --make-recall 10
  python techfin_eval.py --score-recall
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

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

import topic_config as cfg       # noqa: E402
from crawler_common import resolve_output_root  # noqa: E402

SEED = 42
LABEL_COLS = ["point_id", "article_id", "extract_method", "context_type", "authority",
              "region_name", "value_text", "unit", "stat_year", "stat_year_src",
              "confidence", "evidence_snippet", "context_excerpt", "source_url",
              "人工标签", "人工年限", "备注"]
RECALL_COLS = ["article_id", "kind", "title", "source_url", "n_machine_points",
               "machine_points", "正文相关片段", "漏检点数", "备注"]
VALID_LABELS = ("correct", "wrong", "partial")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_points(out_root):
    p = Path(out_root) / cfg.POINTS_ACCUMULATOR
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, dtype=str, encoding="utf-8-sig").fillna("")


def _load_cache(out_root):
    base = Path(out_root) / cfg.BODY_CACHE_DIR
    out = {}
    if not base.exists():
        return out
    import json
    for bucket in base.iterdir():
        if not bucket.is_dir() or bucket.name == "attach":
            continue
        for jf in bucket.glob("*.json"):
            try:
                obj = json.load(open(jf, encoding="utf-8"))
                out[obj.get("article_id", "")] = obj
            except Exception:
                continue
    return out


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _excerpt(obj, p):
    """取证据片段±200字上下文（正文按 evidence_locator 定位；附件/无定位则用 evidence_snippet）。"""
    if not obj:
        return _norm(p.get("evidence_snippet", ""))
    es = str(p.get("evidence_source", ""))
    txt = ""
    if es == "body":
        txt = obj.get("body_text", "") or ""
    elif es.startswith("attachment:"):
        name = es.split(":", 1)[1]
        for a in obj.get("attachments", []) or []:
            if a.get("filename") == name:
                tp = a.get("text_path", "")
                if tp and os.path.exists(tp):
                    try:
                        txt = open(tp, encoding="utf-8").read()
                    except OSError:
                        txt = ""
                break
    if not txt:
        txt = obj.get("body_text", "") or ""
    m = re.match(r"char:(\d+)", str(p.get("evidence_locator", "")))
    if m and txt:
        c = int(m.group(1))
        return _norm(txt[max(0, c - 200):c + 200])
    return _norm(p.get("evidence_snippet", ""))


# ==================== 生成标注模板 ====================

def make_template(out_root, n):
    pts = _load_points(out_root)
    if pts.empty:
        print("[中止] 没有数据点可抽样（先跑 techfin_extract_points.py）。")
        return
    cache = _load_cache(out_root)
    sample = pts.sample(min(n, len(pts)), random_state=SEED).copy()
    rows = []
    for _, r in sample.iterrows():
        d = {c: r.get(c, "") for c in LABEL_COLS[:14]}
        d["context_excerpt"] = _excerpt(cache.get(r.get("article_id", "")), r)
        d["人工标签"] = ""
        d["人工年限"] = ""
        d["备注"] = ""
        rows.append(d)
    out = pd.DataFrame(rows, columns=LABEL_COLS)
    path = Path(out_root) / "eval" / "label_precision.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[输出] 标注模板 {len(out)} 行 → {path}")
    print(f"       请在「人工标签」列填 {VALID_LABELS}（可留空=未标），「人工年限」填你认为正确的年份，再跑 --score。")


def score(out_root):
    path = Path(out_root) / "eval" / "label_precision.csv"
    if not path.exists():
        print(f"[中止] 找不到标注文件：{path}（先跑 --make-template）")
        return
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    lab = df["人工标签"].str.strip().str.lower()
    labeled = lab.isin(VALID_LABELS)
    n_total, n_lab = len(df), int(labeled.sum())

    def prec(sub_lab):
        c = (sub_lab == "correct").sum()
        w = (sub_lab == "wrong").sum()
        p = (sub_lab == "partial").sum()
        denom = c + w + p
        return ((c + 0.5 * p) / denom) if denom else None

    lines = ["# A101 抽取评估报告\n", f"生成时间：{now_str()}\n",
             "## 1. 总览\n",
             f"- 抽样 {n_total} 条，已标注 {n_lab} 条（覆盖率 {n_lab / n_total * 100:.0f}%）"]
    overall = prec(lab[labeled])
    lines.append(f"- **整体 precision：{overall:.2f}**" if overall is not None else "- 整体 precision：无标注")
    lines.append("")

    lines.append("## 2. 分组 precision\n")
    for col in ("extract_method", "context_type", "authority", "stat_year_src"):
        if col not in df.columns:
            continue
        lines.append(f"- 按 `{col}`：")
        for g, sub in df[labeled].groupby(col, dropna=False):
            sub_lab = sub["人工标签"].str.strip().str.lower()
            pr = prec(sub_lab)
            if pr is not None:
                lines.append(f"  - {g or '(空)'}: {pr:.2f}（n={len(sub)}）")
        lines.append("")

    # 年份正确率
    yr = df[(df["人工年限"].str.strip() != "") & labeled]
    if len(yr):
        ok = (yr["stat_year"].str.strip() == yr["人工年限"].str.strip())
        lines.append(f"## 3. 年份正确率\n- {int(ok.sum())}/{len(yr)} = {ok.sum() / len(yr) * 100:.0f}%"
                     f"（对比 stat_year vs 人工年限；矛盾项见下）\n")
        bad = yr[~ok]
        for _, r in bad.head(20).iterrows():
            lines.append(f"  - 机器 {r['stat_year']}（{r['stat_year_src']}） vs 人工 {r['人工年限']}"
                         f" | {str(r['evidence_snippet'])[:40]}")
        lines.append("")
    else:
        lines.append("## 3. 年份正确率\n- 未填「人工年限」，跳过。\n")

    rpt = Path(out_root) / "eval" / "eval_report.md"
    with open(rpt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[输出] 评估报告 → {rpt}")
    if overall is not None:
        print(f"[结果] 整体 precision = {overall:.2f}（已标 {n_lab}/{n_total}）")


# ==================== 召回抽测（近似）====================

def make_recall(out_root, n):
    pts = _load_points(out_root)
    cache = _load_cache(out_root)
    aids = [a for a in cache if (cache[a].get("body_text") or "").strip()]
    if not aids:
        print("[中止] 没有含正文的缓存文章。")
        return
    import random
    random.seed(SEED)
    picked = random.sample(aids, min(n, len(aids)))
    rows = []
    for aid in picked:
        obj = cache[aid]
        meta = obj.get("meta", {}) or {}
        sub = pts[pts["article_id"] == aid] if not pts.empty else pd.DataFrame()
        mpts = "；".join(f"{r['region_name']}/{r['context_type']}/{r['value_text']}"
                        for _, r in sub.iterrows()) or "（机器未抽出）"
        # 正文里含指标词的句窗口（供人工找漏检）
        rel = _norm(obj.get("body_text", ""))[:1500]
        rows.append({"article_id": aid, "kind": meta.get("kind", ""),
                     "title": meta.get("title", ""), "source_url": meta.get("url", ""),
                     "n_machine_points": len(sub), "machine_points": mpts,
                     "正文相关片段": rel, "漏检点数": "", "备注": ""})
    path = Path(out_root) / "eval" / "label_recall.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=RECALL_COLS).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[输出] 召回抽测模板 {len(rows)} 篇 → {path}")
    print("       请通读「正文相关片段」，把机器漏掉的数据点数量填进「漏检点数」，再跑 --score-recall。")


def score_recall(out_root):
    path = Path(out_root) / "eval" / "label_recall.csv"
    if not path.exists():
        print(f"[中止] 找不到 {path}（先跑 --make-recall）")
        return
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    df = df[df["漏检点数"].str.strip() != ""]
    if df.empty:
        print("[中止] 未填「漏检点数」。")
        return
    tp = pd.to_numeric(df["n_machine_points"], errors="coerce").fillna(0).sum()
    fn = pd.to_numeric(df["漏检点数"], errors="coerce").fillna(0).sum()
    recall = tp / (tp + fn) if (tp + fn) else None
    rpt = Path(out_root) / "eval" / "eval_report.md"
    line = (f"\n## 4. 召回（近似，基于 {len(df)} 篇人工通读）\n- 机器命中 {int(tp)}，人工新增漏检 {int(fn)}"
            f" → **recall ≈ {recall:.2f}**\n" if recall is not None else "")
    with open(rpt, "a", encoding="utf-8") as f:
        f.write(line)
    print(f"[输出] 召回已追加到 {rpt}")
    if recall is not None:
        print(f"[结果] recall ≈ {recall:.2f}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=f"{cfg.TOPIC_LABEL} · ⑥ 评估闭环")
    ap.add_argument("--output-dir")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--make-template", type=int, metavar="N", help="生成标注模板（抽 N 条数据点）")
    g.add_argument("--score", action="store_true", help="读标注算 precision")
    g.add_argument("--make-recall", type=int, metavar="N", help="生成召回抽测模板（抽 N 篇文章）")
    g.add_argument("--score-recall", action="store_true", help="算召回")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out_root = resolve_output_root(_TOPIC_DIR, cfg.OUTPUT_ROOT, args.output_dir)
    if args.make_template is not None:
        make_template(out_root, args.make_template)
    elif args.score:
        score(out_root)
    elif args.make_recall is not None:
        make_recall(out_root, args.make_recall)
    elif args.score_recall:
        score_recall(out_root)


if __name__ == "__main__":
    main()
