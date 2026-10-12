"""
科技金融指数研究 · ③验 数据点质量报告（只读语料 → 写报告，仿 verify_corpus.py 的解耦模式）

职责：读 ③ 产出的数据点语料（证据级累积总库或某轮 points_delta），生成 Markdown 报告：
  - 总量与维度分布（context_type / extract_method / authority / 指标）
  - 置信度分布
  - 地区覆盖（124 个县级单元里有多少产出了数据点）
  - **多源冲突组**（同一 地区×年份×指标 出现不同数值）→ 供人工仲裁
  - 缺口：正文为空 / 扫描件被跳过的篇数（读 body_cache 索引）——让缺口始终可见
  - 随机抽检样本（含原文证据片段）→ 供人工核对精度

本脚本不发任何请求、不写数据，只读已经存在的缓存与 CSV。

用法：
  python verify_points.py                 # 读累积总库，报告写进最新运行目录
  python verify_points.py --sample 30     # 抽检 30 条
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

for _p in Path(__file__).resolve().parents:
    if (_p / "crawler_common.py").exists():
        sys.path.insert(0, str(_p))
        break
else:
    raise RuntimeError("找不到 crawler_common.py，请确认它位于仓库根目录")

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import topic_config as cfg      # noqa: E402
from crawler_common import (    # noqa: E402
    resolve_output_root,
    resolve_run_dir,
    run_logging,
)


def _read_points(out_root, points_path=None):
    """优先用指定的数据点文件，否则用累积总库。"""
    path = points_path or os.path.join(out_root, cfg.POINTS_ACCUMULATOR)
    if not os.path.exists(path):
        return pd.DataFrame(), path
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna(""), path


def _dist(series):
    vc = series.value_counts()
    return "，".join(f"{k} {v}" for k, v in vc.items()) if len(vc) else "（无）"


def _county_coverage(df):
    """有数据点的县级单元数 / 广东省 124 个县级单元。"""
    total = sum(len(units) for units in cfg.GUANGDONG_REGIONS.values())
    units = df["region_code"][df["region_level"] == "county"].nunique()
    return units, total


def _conflict_groups(df):
    """同一 (region_code, level3_code, stat_year, metric) 下 actual 口径出现多个不同数值。"""
    act = df[(df["context_type"] == "actual") &
             (df["value_num"].astype(str).str.strip() != "")]
    if act.empty:
        return []
    key = ["region_code", "level3_code", "stat_year", "metric"]
    out = []
    for k, g in act.groupby(key, dropna=False):
        vals = pd.to_numeric(g["value_num"], errors="coerce").dropna()
        if vals.nunique() > 1:
            out.append((k, sorted(set(g["region_name"])), int(vals.min()),
                        int(vals.max()), len(g)))
    return out


def _gap_stats(out_root):
    """读 body_cache 各处索引，统计正文为空/扫描件缺口。"""
    base = Path(out_root) / cfg.BODY_CACHE_DIR
    no_body, scanned, total = 0, 0, 0
    for bucket in base.glob("*"):
        if not bucket.is_dir() or bucket.name == "attach":
            continue
        for jf in bucket.glob("*.json"):
            total += 1
            try:
                obj = json.load(open(jf, encoding="utf-8"))
            except Exception:
                continue
            if obj.get("status") != "ok":
                no_body += 1
            for a in obj.get("attachments", []) or []:
                if a.get("is_scanned"):
                    scanned += 1
    return total, no_body, scanned


def build_report(df, out_root, sample_n=20):
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("# A101 数据点质量报告\n")
    lines.append(f"生成时间：{now}\n")

    if df.empty:
        lines.append("\n> 未发现任何数据点。请先运行 techfin_fetch_body.py 与 techfin_extract_points.py。\n")
        return "\n".join(lines)

    lines.append("## 1. 概览\n")
    lines.append(f"- 数据点总数：{len(df)}")
    lines.append(f"- 覆盖文章数：{df['article_id'].nunique()}")
    lines.append(f"- 涉及县级单元：{df['region_code'][df['region_level']=='county'].nunique()}")
    lines.append(f"- 指标编码：{_dist(df['level3_code'])}")
    lines.append("")

    lines.append("## 2. 维度分布\n")
    lines.append(f"- 语境类型 context_type：{_dist(df['context_type'])}")
    lines.append(f"- 抽取方式 extract_method：{_dist(df['extract_method'])}")
    lines.append(f"- 权威性 authority：{_dist(df['authority'])}")
    lines.append(f"- 地区级别 region_level：{_dist(df['region_level'])}")
    lines.append("")

    lines.append("## 3. 置信度分布\n")
    conf = pd.to_numeric(df["confidence"], errors="coerce")
    lines.append(f"- 平均 {conf.mean():.2f} | 中位 {conf.median():.2f} | "
                 f"最低 {conf.min():.2f} | 最高 {conf.max():.2f}")
    for lo, hi in [(0.9, 1.01), (0.7, 0.9), (0.5, 0.7), (0.0, 0.5)]:
        n = int(((conf >= lo) & (conf < hi)).sum())
        lines.append(f"  - [{lo:.1f}, {hi:.1f})：{n}")
    lines.append("")

    lines.append("## 4. 地区覆盖与缺口\n")
    got, total_units = _county_coverage(df)
    lines.append(f"- 有数据点的县级单元：{got}/{total_units}"
                 f"（县级新闻语料天然稀疏，需结合①覆盖率一起看）")
    try:
        body_total, no_body, scanned = _gap_stats(out_root)
        lines.append(f"- 正文缓存文章 {body_total} 篇，其中无正文 {no_body} 篇")
        lines.append(f"- 扫描件 PDF（未 OCR，已跳过正文抽取）：{scanned} 个")
    except Exception as e:
        lines.append(f"- （缺口统计失败：{e}）")
    lines.append("")

    lines.append("## 5. 多源冲突组（同一 地区×年份×指标 出现不同数值，需人工仲裁）\n")
    conflicts = _conflict_groups(df)
    if not conflicts:
        lines.append("- 无冲突（或不足两个来源）。\n")
    else:
        lines.append(f"共 {len(conflicts)} 组：\n")
        lines.append("| 地区 | 指标 | 年份 | 口径 | 值范围 | 来源数 |")
        lines.append("|---|---|---|---|---|---|")
        for k, names, vmin, vmax, n in conflicts[:50]:
            lines.append(f"| {'/'.join(names)} | {k[1]} | {k[2]} | {k[3]} | "
                         f"{vmin}~{vmax} | {n} |")
        lines.append("")

    lines.append(f"## 6. 随机抽检样本（{sample_n} 条，供人工核对精度）\n")
    sample = df.sample(min(sample_n, len(df)), random_state=42)
    for i, (_, r) in enumerate(sample.iterrows(), 1):
        lines.append(f"{i}. **{r['region_name'] or '(无地区)'}** "
                     f"{r['context_type']} {r['value_text']}（置信 {r['confidence']}）")
        lines.append(f"   - 证据：{r['evidence_snippet']}")
        lines.append(f"   - 来源：{r['publisher']} | {r['publish_date']} | {r['source_url']}")
    lines.append("")

    return "\n".join(lines)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · ③验 数据点质量报告")
    ap.add_argument("--output-dir", help="输出根目录（覆盖 topic_config.OUTPUT_ROOT）")
    ap.add_argument("--points", help="指定数据点 CSV（默认用累积总库）")
    ap.add_argument("--sample", type=int, default=20, help="抽检样本条数（默认 20）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    topic_dir = Path(__file__).resolve().parent
    out_root = resolve_output_root(topic_dir, cfg.OUTPUT_ROOT, args.output_dir)

    df, path = _read_points(out_root, args.points)
    run_dir = resolve_run_dir(out_root)
    report_path = os.path.join(run_dir, "points_validation.md")
    with run_logging(os.path.join(run_dir, cfg.POINTS_PREFIX + "verify_log")):
        print(f"[输入] 数据点文件：{path}（{len(df)} 条）")
        report = build_report(df, out_root, sample_n=args.sample)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[输出] 质量报告 → {report_path}")
    print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
