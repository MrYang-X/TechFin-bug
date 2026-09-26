"""
科技金融指数研究 - 新闻采集（多源：百度资讯 + 360新闻）与数据清洗

依赖：pip install requests beautifulsoup4 pandas
通用工具见仓库根 crawler_common.py；本主题配置见同目录 topic_config.py；
依赖关系与用法见同目录 README.md。

关于两个源的定位（实测结论，勿删）：
  - 百度资讯：唯一同时具备【历史深度 + 绝对日期】的源（实测语料跨 2018-2026），
    但按 IP 限流，连续 6~8 次快速请求后会返回"百度安全验证"页。
  - 360 新闻：连打 5/5 页无任何限流，链接即真实 URL；但日期只有相对值
    且饱和在"1天前"，深翻 41 页结果全部落在 1 天内。
    => 它只能补充近期增量与来源覆盖，【不能提供历史深度】。
"""

import argparse
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---- 引导：通用工具在仓库根，而本脚本在 A/A1/。Python 只把【脚本自身目录】
# ---- 放进 sys.path，父目录不会自动可见，故向上逐级找 crawler_common.py。
# ---- 写成"逐级向上找"而非写死层数，这样以后 A/A1、B/B2 或更深的目录都能用。
for _p in Path(__file__).resolve().parents:
    if (_p / "crawler_common.py").exists():
        sys.path.insert(0, str(_p))
        break
else:
    raise RuntimeError("找不到 crawler_common.py，请确认它位于仓库根目录")

# 本脚本所在目录同样要在 sys.path 上：正常 `python 脚本.py` 时 Python 会自动把
# 脚本目录放进 sys.path[0]，但用 importlib / IDE runner / 测试框架加载时【不会】，
# 那时 import topic_config 会失败。显式补一次，让脚本对启动方式不敏感。
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import topic_config as cfg  # noqa: E402  （须在 sys.path 引导之后导入）
from crawler_common import (  # noqa: E402
    blocking_reason,
    clean_dataframe,
    clean_text,
    csv_row_count,
    export_link_list,
    export_url_mapping,
    generate_article_id,
    get_random_headers,
    is_noise_source,
    is_relevant,
    looks_like_date,
    make_article,
    merge_with_existing,
    normalize_url,
    print_quality_report,
    random_delay,
    resolve_output_root,
    resolve_run_dir,
    run_logging,
)

# ==================== 配置区 ====================
# 主题相关配置全部来自 topic_config.py（关键词、噪声源、采集范围、产物契约）

# 被"安全验证"限流后的退避等待（秒），逐级加长
THROTTLE_BACKOFF = [60, 120]
MAX_RETRIES_PER_PAGE = len(THROTTLE_BACKOFF)
# 单次运行的退避总次数上限：限流往往是 IP 级的，同一轮里反复退避既无效
# 又耗时。用尽后直接放弃剩余页，改为"隔一段时间重跑累积"。
MAX_TOTAL_BACKOFFS = 2

# 本次运行的退避计数
_THROTTLE = {"used": 0}

# 是否解析跳转链接为真实URL（会增加请求量，谨慎开启）
# 注：实测百度资讯与 360 返回的通常已是真实URL，无需开启。
RESOLVE_REAL_URL = False
RESOLVE_DELAY = 0.8


# ==================== 请求（含限流退避） ====================

def fetch_with_backoff(url, session, referer, tag):
    """
    发起请求并统一处理限流退避。

    返回 requests.Response；被拦截/网络异常/放弃时返回 None。
    None 表示"这次没拿到，且原因不是没结果"——调用方据此区分处理，
    避免把限流伪装成"无结果"（这正是原版的静默失败之处）。
    """
    for attempt in range(MAX_RETRIES_PER_PAGE + 1):
        try:
            resp = session.get(url, headers=get_random_headers(referer=referer),
                               timeout=15)
        except requests.RequestException as e:
            print(f"  [异常] {tag} 请求失败：{e}")
            return None

        resp.encoding = "utf-8"
        if resp.status_code != 200:
            print(f"  [警告] {tag} 状态码 {resp.status_code}")
            return None

        reason = blocking_reason(resp)
        if not reason:
            return resp

        print(f"  [警告] {tag} {reason}（第 {attempt + 1}/{MAX_RETRIES_PER_PAGE + 1} 次）")

        if _THROTTLE["used"] >= MAX_TOTAL_BACKOFFS:
            print(f"  [放弃] {tag} 本次运行退避额度已用尽，放弃该页"
                  "（限流通常是 IP 级的，反复重试无效）")
            return None

        if attempt < MAX_RETRIES_PER_PAGE:
            wait = THROTTLE_BACKOFF[min(attempt, len(THROTTLE_BACKOFF) - 1)]
            _THROTTLE["used"] += 1
            print(f"  [退避] 等待 {wait}s 后重试"
                  f"（本次已退避 {_THROTTLE['used']}/{MAX_TOTAL_BACKOFFS} 次）...")
            time.sleep(wait)

    return None


def warm_up_session(session):
    """
    访问百度主站首页取得 BAIDUID 等 cookie，降低被限流概率。

    必须打 www.baidu.com：实测 news.baidu.com 不发放任何 cookie（预热等于没做），
    而主站会下发 BAIDUID / BIDUPSID / PSTM / BDSVRTM / BD_HOME 整套。
    """
    try:
        resp = session.get("https://www.baidu.com/",
                           headers=get_random_headers(referer="https://www.baidu.com/"),
                           timeout=15)
        got = [c for c in ("BAIDUID", "BIDUPSID", "PSTM") if c in session.cookies]
        print(f"[预热] 状态 {resp.status_code}，已取得 cookie：{got or '无'}")
    except requests.RequestException as e:
        print(f"[预热] 失败（不影响继续）：{e}")
    random_delay(cfg.MIN_DELAY, cfg.MAX_DELAY)


def resolve_real_url(baidu_url, session=None):
    """
    解析跳转链接的真实目标URL（仅在启用 RESOLVE_REAL_URL 时使用）。
    优先用 HEAD 请求（更省流量），失败则退回 GET。
    """
    if not baidu_url or ("baidu.com/link" not in baidu_url
                         and "/link?" not in baidu_url):
        return ""
    if session is None:
        session = requests.Session()

    try:
        resp = session.head(
            baidu_url,
            headers=get_random_headers(referer="https://www.baidu.com/"),
            allow_redirects=True,
            timeout=10,
        )
        real_url = resp.url

        # 有些情况 HEAD 不返回跳转，需要 GET
        if "/link?" in real_url:
            resp = session.get(
                baidu_url,
                headers=get_random_headers(referer="https://www.baidu.com/"),
                allow_redirects=True,
                timeout=10,
            )
            real_url = resp.url

        # 某些跳转是 JS 形式，尝试从正文提取
        if "/link?" in real_url:
            m = re.search(r'URL=[\'"]?(https?://[^\'"<>]+)', resp.text)
            if m:
                real_url = m.group(1)

        return real_url if real_url.startswith("http") else ""
    except requests.RequestException:
        return ""


# ==================== 源1：百度资讯 ====================

def extract_source_date(item):
    """
    从百度结果块中分别抽取来源与日期。

    注意日期与来源在不同元素里：日期在 c-color-gray2，来源在 c-color-gray /
    news-source_*。用 `.c-color-gray` 是匹配不到 `c-color-gray2` 的（CSS 类名是
    独立的 token），这正是原版日期全空的原因。
    类名带 webpack 哈希后缀（如 news-source_Xj4Dv），故用前缀包含匹配更耐改版。
    """
    date_raw = ""
    for sel in ("[class*='c-color-gray2']", ".c-color-gray", ".c-author"):
        tag = item.select_one(sel)
        if tag:
            candidate = clean_text(tag.get_text())
            if candidate and looks_like_date(candidate):
                date_raw = candidate
                break

    source = ""
    for sel in (".c-color-gray", "[class*='news-source']", ".c-author"):
        tag = item.select_one(sel)
        if tag:
            text = clean_text(tag.get_text())
            # 来源文本里常混着时间（如"百度百科 15:12"），去掉
            text = re.sub(r"\d{1,2}:\d{2}", "", text).strip()
            # 该元素若装的就是日期本身，去掉日期后应为空，换下一个选择器
            text = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日", "", text).strip()
            if text and text != date_raw:
                source = text
                break

    return source, date_raw


def fetch_baidu_news_page(keyword, page=0, session=None):
    """百度资讯搜索。返回结果列表；被拦截时返回 None（区别于"真的没结果"的 []）。"""
    if session is None:
        session = requests.Session()

    params = {"tn": "news", "word": keyword, "pn": page * 10, "oq": keyword}
    url = "https://www.baidu.com/s?" + "&".join(
        f"{k}={quote(str(v))}" for k, v in params.items())

    resp = fetch_with_backoff(url, session, "https://news.baidu.com/", "百度")
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    results = soup.select(".result, .result-op")
    if not results:
        print(f"  [警告] 百度页面正常但未匹配到结果节点（可能改版），"
              f"正文 {len(resp.text)} 字符")
        return []

    articles = []
    for item in results:
        title_tag = item.select_one("h3 a")
        if not title_tag:
            continue
        title = clean_text(title_tag.get_text())
        link = normalize_url(title_tag.get("href", ""))
        summary_tag = item.select_one(".c-summary, .c-span-last, .content-right_8Zs40")
        summary = clean_text(summary_tag.get_text()) if summary_tag else ""
        source, date_raw = extract_source_date(item)

        if title and link:
            articles.append(make_article(title, link, summary, source, date_raw,
                                          keyword, "baidu"))
    return articles


# ==================== 源2：360 新闻 ====================

def extract_360_item(item):
    """从 360 结果块抽取字段。返回 (title, url, summary, source, date_raw)"""
    link = item.select_one("a[href]")
    url = normalize_url(link.get("href", "")) if link else ""
    if not url:  # <li> 上的 data-url 是备用来源
        url = normalize_url(item.get("data-url", ""))

    # 标题必须取 title 属性：可见文本被 <em> 高亮切断，
    # get_text() 会得到"聚焦 金融科技 主业发展"这种被插了空格的结果。
    title = ""
    if link and link.get("title"):
        title = clean_text(link["title"])
    else:
        h3 = item.select_one("h3")
        title = clean_text(h3.get_text()) if h3 else ""

    summary_tag = item.select_one("p.summary")
    summary = clean_text(summary_tag.get_text()) if summary_tag else ""

    src_tag = item.select_one("cite.sitename")
    source = clean_text(src_tag.get_text()) if src_tag else ""

    date_tag = item.select_one("span.time")
    date_raw = clean_text(date_tag.get_text()) if date_tag else ""

    return title, url, summary, source, date_raw


def fetch_360_news_page(keyword, page=0, session=None):
    """
    360 新闻搜索。pn 从 1 开始（与百度的 0-based offset 不同）。

    实测无任何限流（连打 5 页全部 200）；但日期只有相对值，
    见文件头说明。
    """
    if session is None:
        session = requests.Session()

    url = f"https://news.so.com/ns?q={quote(keyword)}&pn={page + 1}"
    resp = fetch_with_backoff(url, session, "https://news.so.com/", "360")
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    results = soup.select("li.res-list")
    if not results:
        print(f"  [警告] 360 页面正常但未匹配到结果节点（可能改版），"
              f"正文 {len(resp.text)} 字符")
        return []

    articles = []
    for item in results:
        title, link, summary, source, date_raw = extract_360_item(item)
        if title and link:
            articles.append(make_article(title, link, summary, source, date_raw,
                                          keyword, "360"))
    return articles


# ==================== 采集主循环 ====================

SOURCES = {
    "baidu": fetch_baidu_news_page,
    "360": fetch_360_news_page,
}


def crawl_all_keywords(keywords, max_pages=3, resolve_url=False,
                       enabled_sources=None):
    """
    多源采集。返回 (文章列表, 统计信息)。

    按源隔离：某源被限流只跳过该源该关键词，其余源继续——这是多源改造的核心目的。
    """
    enabled = enabled_sources or cfg.ENABLED_SOURCES
    session = requests.Session()
    _THROTTLE["used"] = 0

    if "baidu" in enabled:
        warm_up_session(session)

    by_id = {}          # article_id -> 条目，便于跨源合并 engines
    stats = {
        "per_keyword": {},
        "per_source": {name: 0 for name in enabled},
        "engines_merged": 0,
        "noise_dropped": 0,
        "irrelevant_dropped": 0,
        "failed_pages": 0,
        "failed_by_source": {},
    }

    total = len(keywords)
    for idx, kw in enumerate(keywords, 1):
        print(f"\n[{idx}/{total}] 关键词：{kw}")
        stats["per_keyword"][kw] = 0

        for source_name in enabled:
            fetcher = SOURCES[source_name]
            print(f"  -- 源：{source_name}")

            for page in range(max_pages):
                print(f"    第 {page + 1} 页 ...", end=" ")
                articles = fetcher(kw, page=page, session=session)

                if articles is None:
                    # 该源被限流/失败：只跳过这个源，其它源继续
                    stats["failed_pages"] += 1
                    stats["failed_by_source"][source_name] = \
                        stats["failed_by_source"].get(source_name, 0) + 1
                    print(f"[中断] {source_name} 该关键词被限流，跳过；"
                          f"其它源继续，已采数据保留")
                    break

                if not articles:
                    print("无结果，停止翻页")
                    break

                new_count = 0
                for art in articles:
                    combined = f"{art['title']} {art['summary']}"

                    if is_noise_source(art["url"], art["source"], cfg.NOISE_DOMAINS,
                                   cfg.NOISE_SOURCES, cfg.NOISE_URL_SUFFIXES):
                        stats["noise_dropped"] += 1
                        continue
                    if not is_relevant(combined, kw, cfg.CORE_TERMS,
                                             cfg.QUALIFIER_TERMS):
                        stats["irrelevant_dropped"] += 1
                        continue

                    art_id = generate_article_id(art["title"], art["url"])
                    art["article_id"] = art_id

                    if art_id in by_id:
                        # 同一篇被两源同时命中 —— 记录引擎叠加，
                        # 这本身就是一个免费的双源印证信号
                        existing = by_id[art_id]
                        if art["engine"] not in existing["engines"].split("+"):
                            existing["engines"] += "+" + art["engine"]
                            stats["engines_merged"] += 1
                        continue

                    art["engines"] = art["engine"]

                    # 可选：解析真实URL
                    if resolve_url:
                        art["real_url"] = resolve_real_url(art["url"], session)
                        time.sleep(random.uniform(RESOLVE_DELAY * 0.7,
                                                  RESOLVE_DELAY * 1.3))

                    by_id[art_id] = art
                    new_count += 1
                    stats["per_keyword"][kw] += 1
                    stats["per_source"][source_name] += 1

                print(f"获取 {len(articles)} 条，新增 {new_count} 条")
                random_delay(cfg.MIN_DELAY, cfg.MAX_DELAY)

        if idx < total:
            extra = random.uniform(2.0, 5.0)
            print(f"  [暂停 {extra:.1f}s 后继续]")
            time.sleep(extra)

    return list(by_id.values()), stats


# ==================== 主流程 ====================

def backfill_legacy_engine(df):
    """
    迁移旧数据：多源改造之前采集的历史行没有 engine 列（当时只有百度一个源），
    补记为 baidu，避免按源统计时把历史数据算成"未知"。
    """
    if "engine" not in df.columns:
        return df
    blank = df["engine"].fillna("").astype(str).str.strip().isin(["", "nan"])
    if blank.any():
        df.loc[blank, "engine"] = "baidu"
        if "engines" in df.columns:
            e_blank = (df["engines"].fillna("").astype(str).str.strip()
                       .isin(["", "nan"]))
            df.loc[e_blank, "engines"] = "baidu"
        print(f"[迁移] 历史 {int(blank.sum())} 条无 engine 记录，补记为 baidu"
              f"（多源改造前仅有百度一个源）")
    return df


def order_columns(df):
    """按主题配置的列顺序整理（与旧数据合并后可能多出列）"""
    return df[[c for c in cfg.SEARCH_COLS if c in df.columns]]


def artifact_paths(run_dir):
    """本轮产物的落地路径。前缀来自主题配置——两个采集脚本共用同一个输出根与
    当日序号，靠前缀区分是谁产出的。"""
    p = cfg.SEARCH_PREFIX
    return {
        "log": os.path.join(run_dir, p + "log"),
        "delta": os.path.join(run_dir, p + "delta.csv"),
        "cumulative": os.path.join(run_dir, p + "cumulative.csv"),
        "links": os.path.join(run_dir, p + "links.txt"),
        "url_map": os.path.join(run_dir, p + "url_mapping.csv"),
    }


def run_once(run_dir, paths, accumulator):
    print("=" * 60)
    print(f"{cfg.TOPIC_LABEL} · 新闻采集（多源：百度资讯 + 360新闻）")
    print("=" * 60)
    print(f"启用源：{', '.join(cfg.ENABLED_SOURCES)}")
    print(f"产物目录：{run_dir}")

    raw, stats = crawl_all_keywords(
        cfg.TECH_FIN_KEYWORDS,
        max_pages=cfg.MAX_PAGES_PER_KEYWORD,
        resolve_url=RESOLVE_REAL_URL,
    )

    if not raw:
        print("\n[结果] 未采集到任何文章。")
        if stats["failed_pages"]:
            print(f"       原因：有 {stats['failed_pages']} 页采集失败"
                  f"（{stats['failed_by_source']}），非真的没有数据，请稍后重跑。")
        elif stats["irrelevant_dropped"] or stats["noise_dropped"]:
            print(f"       原因：抓到结果但全部被过滤"
                  f"（相关性 {stats['irrelevant_dropped']} 条 / "
                  f"噪声源 {stats['noise_dropped']} 条），"
                  f"可放宽 is_relevant 或调整 NOISE_DOMAINS。")
        return

    # 本轮增量（合并历史之前），与累积快照分开保存
    before = csv_row_count(accumulator)
    delta = clean_dataframe(pd.DataFrame(raw))

    # 累积总库仍在顶层：它是跨轮累积基准。若改成"读上一轮目录"，一旦某轮
    # 在写出前崩掉就会断链；顶层总库不受失败影响。
    merged = merge_with_existing(delta.copy(), accumulator, accumulate=cfg.ACCUMULATE)
    merged = backfill_legacy_engine(merged)
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
    export_url_mapping(merged, paths["url_map"])

    print_quality_report(merged, stats)

    print("\n[预览] 前5条标题与链接：")
    for _, row in merged.head().iterrows():
        # engines 是"命中过该条的源"列表，多源时形如 baidu+360；
        # 单源时与 engine 相同，不要重复显示
        role = str(row.get("engines", "") or "").strip() \
            or str(row.get("engine", "") or "").strip()
        print(f"  · {row['title']}")
        print(f"    {row['date'] or '(无日期)'} | [{role or '?'}] {row.get('url', '')}")

    print(f"\n[归档] 本轮采集 {len(delta)} 条，其中相对累积总库净新增 {net_new} 条"
          f" | 累积 {len(merged)} 条 → {run_dir}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · 搜索引擎语料采集（百度 + 360）")
    ap.add_argument("--output-dir",
                    help="输出根目录（覆盖 topic_config.OUTPUT_ROOT）。"
                         "相对路径锚定到本脚本所在目录，也可写绝对路径把"
                         "数据放到仓库外")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # 一切路径锚定到脚本自身位置，不依赖当前工作目录——
    # 这样从任何地方执行，产物都稳定落在本主题的输出根下。
    topic_dir = Path(__file__).resolve().parent
    out_root = resolve_output_root(topic_dir, cfg.OUTPUT_ROOT, args.output_dir)
    accumulator = out_root / cfg.SEARCH_ACCUMULATOR

    run_dir = resolve_run_dir(out_root)
    paths = artifact_paths(run_dir)
    try:
        with run_logging(paths["log"]):
            print(f"输出根目录：{out_root}")
            run_once(run_dir, paths, accumulator)
    finally:
        # 此处 stdout 已恢复，只作提示不写入日志；崩溃时尤其需要知道存到哪了
        print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
