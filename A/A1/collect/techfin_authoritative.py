"""
科技金融指数研究 —— 官方政策源采集（用于与搜索引擎语料交叉印证）

依赖：pip install requests beautifulsoup4 pandas
通用工具见仓库根 crawler_common.py；本主题配置见同目录 topic_config.py
（官方源清单 SOURCES 也在那里）；依赖关系与用法见同目录 README.md。

与 TechFin-bug.py 的分工：
    TechFin-bug.py           搜索引擎（百度+360），负责广度与历史深度
    techfin_authoritative.py 官方政策源，负责权威口径与时间线的核对

范围与取舍（实测决定，勿删）：
  * 只抓【列表页元数据】（标题/日期/链接），不进正文——请求量小、跑得快。
  * 链接完整保留（url 列 + official_links.txt），便于人工点开核对原文。
  * 源清单见 topic_config.SOURCES。2026-10 已把源换成"真正发布指标内容"的栏目
    （广东省科技厅·通知公告/政策文件 + 科技部·通知通告）；原人行/发改委/科技部工作动态
    指向的是新闻/发布会栏目，标题里没有一条含 A1 指标词，命中 0（原因见那里的注释）。
  * 默认【保存全部条目】而非按关键词过滤，原因见 topic_config.SAVE_ALL_ITEMS。
    打标词 AUTH_KEYWORDS 由 INDEX_KEYWORDS 派生（见 topic_config），故随检索词自动同步。

未采用/已排除的源（都实测过，别再重复踩）：
  * 证监会 csrc.gov.cn —— 可抓的两个栏目都已停更（c100028 停在 2021-12，
    c100029 停在 2024-04），当前内容为 JS 渲染；用它只会引入陈旧切片。
  * 中国政府网政策文件库 —— JSON 接口各种参数组合均 totalCount=0，政策库页 403，
    "最新政策"页为 JS 渲染（0 条可解析）。
  * 金融监管总局 —— SPA，列表页仅 5 个 <a>、解析 0 条。
"""

import argparse
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---- 引导：通用工具在仓库根，而本脚本在 A/A1/（原因见 TechFin-bug.py 同段注释）
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

# 本脚本所在目录同样要在 sys.path 上：正常 `python 脚本.py` 时 Python 会自动把
# 脚本目录放进 sys.path[0]，但用 importlib / IDE runner / 测试框架加载时【不会】，
# 那时 import topic_config 会失败。显式补一次，让脚本对启动方式不敏感。
# （配置目录已由上面的主题目录引导加入 sys.path）

import topic_config as cfg  # noqa: E402  （须在 sys.path 引导之后导入）
from paths import finish_run_manifest, new_pipeline_run, pipeline_root  # noqa: E402
from crawler_common import (  # noqa: E402
    append_progress,
    blocking_reason,
    clean_dataframe,
    clean_text,
    csv_row_count,
    export_link_list,
    generate_article_id,
    get_random_headers,
    guess_encoding,
    is_relevant,
    load_progress,
    make_article,
    merge_with_existing,
    print_quality_report,
    run_logging,
)

# ==================== 配置区 ====================
# 主题相关配置全部来自 topic_config.py：
# 检索词（AUTH_KEYWORDS）、官方源清单（SOURCES）、翻页数与节奏、
# 是否全量保存（SAVE_ALL_ITEMS）、产物契约（输出根/累积总库/前缀）


# ==================== 解析 ====================

# 官方站列表行的日期写法：2026-09-24 / 2026年9月24日 / 2026/09/24
_ROW_DATE_RE = re.compile(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})")

# 行容器文本超过这个长度，说明 find_parent 没定位到"一行"，而是套到了整块
_MAX_ROW_TEXT = 150


def parse_list_rows(html, base_url):
    """
    从官方列表页提取 [(title, date_raw, url)]。

    三个站的 HTML 结构差异很大（table / ul / div 混用），但共同点是：
    【标题链接与它的日期在同一个行容器内】。故用"取链接最近的祖先容器，
    在其中找日期"这一通用启发式，避免逐站写选择器——实测对
    gdstc / most 等均能稳定命中。
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen_urls = set()

    for a in soup.select("a[href]"):
        title = clean_text(a.get_text())
        if len(title) < 8:            # 太短的多为栏目名/导航
            continue

        parent = a.find_parent(["li", "tr", "td", "div", "p"]) or a
        text = clean_text(parent.get_text(" ", strip=True))
        if len(text) > _MAX_ROW_TEXT:  # 容器过大 = 没定位到行
            continue

        m = _ROW_DATE_RE.search(text)
        if not m:
            continue

        url = urljoin(base_url, a.get("href", ""))
        if not url.startswith("http") or url in seen_urls:
            continue
        seen_urls.add(url)
        rows.append((title, m.group(0), url))

    return rows


# ==================== 采集 ====================

def page_urls(source, max_pages):
    """按各站分页规律生成待抓页 URL 列表（第一页用 list_url，其余套模板）"""
    urls = [source["list_url"]]
    start = source.get("first_page_no", 1)
    for i in range(max_pages - 1):
        urls.append(source["page_url"].format(n=start + i))
    return urls


def fetch_page(url, session, label, retries=2):
    """返回 Response 或 None（网络异常/被拦截）。网络异常/429/5xx 重试一次。"""
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=get_random_headers(referer=url),
                               timeout=20)
        except requests.RequestException as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(random.uniform(*cfg.OFFICIAL_DELAY))
                continue
            print(f"      [异常] {label} 请求失败：{e}")
            return None

        # 政府站大量 GBK/GB2312，统一走 guess_encoding（与搜索/正文一致）
        resp.encoding = guess_encoding(resp)
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            time.sleep(random.uniform(*cfg.OFFICIAL_DELAY))
            continue
        if resp.status_code != 200:
            print(f"      [警告] {label} 状态码 {resp.status_code}")
            return None

        reason = blocking_reason(resp)
        if reason:
            print(f"      [警告] {label} {reason}")
            return None
        return resp
    if last_err:
        print(f"      [异常] {label} 请求失败：{last_err}")
    return None


# ==================== 产物落地 ====================

def order_columns(df):
    """按主题配置的列顺序整理（与旧数据合并后可能多出列）"""
    return df[[c for c in cfg.OFFICIAL_COLS if c in df.columns]]


def artifact_paths(run_dir):
    """本轮产物的落地路径。前缀来自主题配置——两个采集脚本共用同一个输出根与
    当日序号，靠前缀区分是谁产出的。"""
    p = cfg.OFFICIAL_PREFIX
    return {
        "log": os.path.join(run_dir, p + "log"),
        "delta": os.path.join(run_dir, p + "delta.csv"),
        "cumulative": os.path.join(run_dir, p + "cumulative.csv"),
        "links": os.path.join(run_dir, p + "links.txt"),
    }


def run_once(run_dir, paths, accumulator, progress_path=None, fresh=False, limit_pages=None):
    print("=" * 60)
    print("科技金融指数研究 · 官方政策源采集（用于交叉印证）")
    print("=" * 60)
    print(f"检索词：{'、'.join(cfg.AUTH_KEYWORDS)}")
    max_pages = int(limit_pages or cfg.MAX_PAGES_PER_SOURCE)
    print(f"每源翻页：{max_pages} 页 | "
          f"保存全部条目：{'是' if cfg.SAVE_ALL_ITEMS else '否（只存命中）'}")
    print(f"产物目录：{run_dir}")

    session = requests.Session()
    seen = set()
    articles = []
    done = set() if fresh or not progress_path else load_progress(progress_path)
    stats = {
        "per_keyword": {kw: 0 for kw in cfg.AUTH_KEYWORDS},
        "per_source": {},
        "matched_total": 0,
        "noise_dropped": 0,
        "irrelevant_dropped": 0,
        "failed_pages": 0,
        "failed_by_source": {},
    }

    for source in cfg.SOURCES:
        label = source["label"]
        print(f"\n-- 来源：{label}（{source['name']}）")
        stats["per_source"][label] = 0

        for page_no, url in enumerate(page_urls(source, max_pages), 1):
            if (source["name"], url) in done:
                print(f"    第 {page_no} 页 [跳过] 台账已成功")
                continue
            resp = fetch_page(url, session, label)
            if resp is None:
                stats["failed_pages"] += 1
                stats["failed_by_source"][label] = \
                    stats["failed_by_source"].get(label, 0) + 1
                print(f"    第 {page_no} 页 [中断] 采集失败，该源停止翻页")
                break

            rows = parse_list_rows(resp.text, url)
            if not rows:
                print(f"    第 {page_no} 页 [停止] 未解析出条目（可能改版）")
                break

            new = 0
            for title, date_raw, link in rows:
                matched = [kw for kw in cfg.AUTH_KEYWORDS
                           if is_relevant(title, kw, cfg.CORE_TERMS,
                                          cfg.QUALIFIER_TERMS,
                                          max_token_gap=cfg.RELEVANCE_MAX_TOKEN_GAP)]
                if not matched and not cfg.SAVE_ALL_ITEMS:
                    stats["irrelevant_dropped"] += 1
                    continue

                art_id = generate_article_id(title, link)
                if art_id in seen:
                    continue
                seen.add(art_id)

                kw = matched[0] if matched else ""
                art = make_article(title, link, "", label, date_raw, kw,
                                   source["name"])
                art["article_id"] = art_id
                art["engines"] = source["name"]
                articles.append(art)
                new += 1
                stats["per_source"][label] += 1
                if kw:
                    stats["per_keyword"][kw] += 1
                    stats["matched_total"] += 1

            print(f"    第 {page_no} 页 解析 {len(rows)} 条，入库 {new} 条"
                  f"（累计命中关键词 {stats['matched_total']} 条）")
            if progress_path:
                append_progress(progress_path, [{
                    "source": source["name"], "plan_key": url, "status": "ok",
                    "n_items": len(rows),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                }])
            time.sleep(random.uniform(*cfg.OFFICIAL_DELAY))

    if not articles:
        print("\n[结果] 未采集到命中条目。")
        print("       官方源标题多为政策/会议口径，命中率天然偏低；"
              "可增补 AUTH_KEYWORDS，或把 SAVE_ALL_ITEMS 设为 True 保存全部条目。")
        return

    # 本轮增量（合并历史之前），与累积快照分开保存
    before = csv_row_count(accumulator)
    delta = clean_dataframe(pd.DataFrame(articles))

    # 累积总库仍在顶层：它是跨轮累积基准（每轮目录里的 snapshot 是它的副本）
    merged = merge_with_existing(delta.copy(), accumulator, accumulate=cfg.ACCUMULATE)
    net_new = len(merged) - before

    delta = order_columns(delta)
    merged = order_columns(merged)

    delta.to_csv(paths["delta"], index=False, encoding="utf-8-sig")
    print(f"[输出] 本轮采集（去重清洗后）→ {paths['delta']}")

    merged.to_csv(paths["cumulative"], index=False, encoding="utf-8-sig")
    merged.to_csv(accumulator, index=False, encoding="utf-8-sig")
    print(f"[输出] 累积快照 → {paths['cumulative']}")
    print(f"[输出] 累积总库 → {accumulator}")

    export_link_list(merged, paths["links"])

    print_quality_report(merged, stats,
                          title=f"{cfg.TOPIC_LABEL} · 官方源语料")

    print("\n[预览] 前5条标题与链接：")
    for _, row in merged.head().iterrows():
        print(f"  · {row['title']}")
        print(f"    {row['date'] or '(无日期)'} | [{row.get('source', '')}] "
              f"{row.get('url', '')}")

    print(f"\n[归档] 本轮采集 {len(delta)} 条，其中相对累积总库净新增 {net_new} 条"
          f" | 累积 {len(merged)} 条 → {run_dir}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · 官方政策源采集（用于交叉印证）")
    ap.add_argument("--output-dir",
                    help="数据根目录（覆盖 topic_config.DATA_ROOT）。"
                         "相对路径锚定到本主题目录，也可写绝对路径把"
                         "数据放到仓库外")
    ap.add_argument("--fresh", action="store_true",
                    help="忽略 official_progress.csv 台账，全量重采")
    ap.add_argument("--limit-pages", type=int, default=None,
                    help="每个源最多翻 N 页（默认 topic_config.MAX_PAGES_PER_SOURCE）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # 一切路径锚定到主题目录，不依赖当前工作目录
    topic_dir = _TOPIC_DIR
    out_root = pipeline_root(topic_dir, args.output_dir)
    accumulator = out_root / cfg.OFFICIAL_ACCUMULATOR

    run_dir = new_pipeline_run("collect-official", override=args.output_dir, topic_dir=topic_dir)
    paths = artifact_paths(run_dir)
    status = "failed"
    try:
        with run_logging(paths["log"]):
            print(f"数据根目录：{out_root}")
            run_once(run_dir, paths, accumulator,
                     progress_path=str(out_root / cfg.OFFICIAL_PROGRESS),
                     fresh=args.fresh, limit_pages=args.limit_pages)
        status = "ok"
    finally:
        finish_run_manifest(run_dir, status=status)
        # 此处 stdout 已恢复，只作提示不写入日志；崩溃时尤其需要知道存到哪了
        print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
