"""
科技金融指数研究 · 数据点质量报告（verify_points.py；只读语料 → 写报告，仿 verify_corpus.py 的解耦模式）

职责：读 techfin_extract_points.py 产出的数据点语料（证据级累积总库或某轮 points_delta），生成 Markdown 报告：
  - 总量与维度分布（context_type / extract_method / authority / 指标）
  - 置信度分布
  - 地区覆盖（124 个采集单元里有多少产出了数据点）
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
import topic_config as cfg      # noqa: E402
# noinspection PyUnresolvedReferences
from paths import finish_run_manifest, new_pipeline_run, pipeline_root  # noqa: E402
from crawler_common import (    # noqa: E402
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

    cov_path = Path(out_root) / "coverage_latest.json"
    if cov_path.exists():
        try:
            cov = json.loads(cov_path.read_text(encoding="utf-8"))
            lines.append("## 0. 采集覆盖率（区分“没采到”与“确实没有”）\n")
            lines.append(f"- 最近一轮：{cov.get('generated_at', '')}")
            lines.append(f"- 检索计划总数：{cov.get('total_plans', '')}")
            lines.append(f"- 逐检索式状态：{cov.get('status_counts', {})}")
            lines.append(f"- 按源：{cov.get('by_source', {})}")
            lines.append("- **未执行/失败/tripped 导致的空值不等于该地区没有数据。**\n")
        except Exception as e:
            lines.append(f"> 覆盖率文件读取失败：{e}\n")

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
    for col in ("region_from", "aggregation_type"):
        if col in df.columns:
            lines.append(f"- {col}：{_dist(df[col])}")
    if "aggregation_type" in df.columns:
        n_break = int((df["aggregation_type"] == "breakdown").sum())
        lines.append(f"- 拆分口径 breakdown：{n_break}（保留证据，不参与主值）")
    if "level3_code" in df.columns and "metric" in df.columns:
        bad = df[(df["level3_code"] == "A103") & (df["metric"] == "count")]
        lines.append(f"- A103+count 错配：{len(bad)}（应为 0）")
    lines.append("")

    lines.append("## 2b. 数据年份\n")
    if "stat_year" in df.columns:
        filled = df["stat_year"].astype(str).str.strip().ne("")
        lines.append(f"- stat_year 非空：{int(filled.sum())}/{len(df)}（{filled.mean() * 100:.0f}%）")
        if "stat_year_src" in df.columns:
            lines.append(f"- 年份来源 stat_year_src：{_dist(df['stat_year_src'])}")
        if "year_confidence" in df.columns:
            yc = pd.to_numeric(df["year_confidence"], errors="coerce").fillna(0)
            lines.append(f"- year_confidence<0.5（不参与主值）：{int((yc < 0.5).sum())}/{len(df)}")
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
                 f"（县级新闻语料天然稀疏，需结合 collect/TechFin-bug.py 的采集覆盖率一起看）")
    if "region_from" in df.columns:
        n_query = int((df["region_from"] == "query").sum())
        lines.append(f"- region_from=query（无文本证据，loader 默认拒绝）：{n_query}")
        n_scope = int(df["region_from"].astype(str).str.startswith("scope_").sum())
        lines.append(f"- region_from=scope_*（全市/全省/全国口径）：{n_scope}")
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
        description=f"{cfg.TOPIC_LABEL} · 数据点质量报告")
    ap.add_argument("--output-dir", help="数据根目录（覆盖 topic_config.DATA_ROOT）")
    ap.add_argument("--points", help="指定数据点 CSV（默认用累积总库）")
    ap.add_argument("--sample", type=int, default=20, help="抽检样本条数（默认 20）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    topic_dir = _TOPIC_DIR
    out_root = pipeline_root(topic_dir, args.output_dir)

    df, path = _read_points(out_root, args.points)
    run_dir = new_pipeline_run("verify-points", override=args.output_dir, topic_dir=topic_dir)
    report_path = os.path.join(run_dir, "points_validation.md")
    status = "failed"
    try:
        with run_logging(os.path.join(run_dir, cfg.POINTS_PREFIX + "verify_log")):
            print(f"[输入] 数据点文件：{path}（{len(df)} 条）")
            report = build_report(df, out_root, sample_n=args.sample)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"[输出] 质量报告 → {report_path}")
        status = "ok"
    finally:
        finish_run_manifest(run_dir, status=status, outputs=[report_path])
        print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
