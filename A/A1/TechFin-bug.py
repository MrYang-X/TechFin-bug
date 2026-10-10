"""
科技金融指数研究 - 新闻采集（多源：百度资讯 + Bing网页搜索 + 360新闻）与数据清洗

依赖：pip install requests beautifulsoup4 pandas
通用工具见仓库根 crawler_common.py；本主题配置见同目录 topic_config.py；
依赖关系与用法见同目录 README.md。

关于各源的定位（实测结论，勿删）：
  - 百度资讯：唯一做【严格 AND】的源（"荔湾区 高新技术企业"10 条里 8 条两词兼有），
    且有绝对日期（语料跨 2018-2026）。按 IP 限流（IP 级、粘滞，同 IP 下 www/news/m
    各站返回同一张验证页）。关键实测：间隔 ≥20s 可持续采集（连打 13+ 次全成功），
    1s 连打几次即被封——放慢节奏是唯一有效手段。
  - Bing 网页搜索 cn.bing.com/search：不限流（连打 12 次全 200），但只做松散匹配：
    县级检索式 10 条里地区词命中 8、指标词命中 0，经 is_relevant 全被过滤（0%）。
    仅无地区检索式约 1% 可用。=> 不能替代百度做县级，仅作补充。
  - 360 新闻：不限流、有日期，但同样松散（县级近乎 0，无地区约 4%），仅作补充。

结论：县级指标采集本质只能靠百度，靠"放慢节奏 + 多轮累积 + 断点续跑"补齐。
按源隔离是核心：某源被限流只跳过该源，其余源继续（见 topic_config.BACKOFF_BY_SOURCE）。
"""

import argparse
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

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
    append_progress,
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
    load_progress,
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

# 源规范键 → 显示名（日志用）。fetch_with_backoff 用规范键隔离退避状态。
SOURCE_LABEL = {"baidu": "百度", "bing": "Bing", "360": "360"}

# 按源隔离的退避状态：source -> {"used": 已退避次数, "tripped": 本轮是否已跳闸}。
# 某源跳闸只停用该源，其它源继续（多源改造的核心）。
_SOURCE_THROTTLE = {}

# 是否解析跳转链接为真实URL（会增加请求量，谨慎开启）
# 注：实测百度资讯与 360 返回的通常已是真实URL，无需开启。
RESOLVE_REAL_URL = False
RESOLVE_DELAY = 0.8


def _throttle_state(source):
    """取（必要时初始化）某源的退避状态。"""
    return _SOURCE_THROTTLE.setdefault(source, {"used": 0, "tripped": False})


# ==================== 请求（含按源隔离的限流退避） ====================

def fetch_with_backoff(url, session, referer, source):
    """
    发起请求并统一处理限流退避。退避状态与额度【按源隔离】。

    source 用规范键（"baidu"/"bing"/"360"），退避参数取自
    topic_config.BACKOFF_BY_SOURCE。返回 requests.Response；
    被拦截 / 网络异常 / 该源跳闸时返回 None。
    None 表示"这次没拿到，且原因不是没结果"——调用方据此区分处理，
    避免把限流伪装成"无结果"（这正是原版的静默失败之处）。
    """
    params = cfg.BACKOFF_BY_SOURCE.get(source, cfg.BACKOFF_DEFAULT)
    waits, max_total = params["backoff"], params["max_total"]
    state = _throttle_state(source)
    label = SOURCE_LABEL.get(source, source)

    for attempt in range(len(waits) + 1):
        try:
            resp = session.get(url, headers=get_random_headers(referer=referer),
                               timeout=15)
        except requests.RequestException as e:
            print(f"  [异常] {label} 请求失败：{e}")
            return None

        resp.encoding = "utf-8"
        if resp.status_code != 200:
            print(f"  [警告] {label} 状态码 {resp.status_code}")
            return None

        reason = blocking_reason(resp)
        if not reason:
            return resp

        print(f"  [警告] {label} {reason}（第 {attempt + 1}/{len(waits) + 1} 次）")

        if state["used"] >= max_total:
            state["tripped"] = True
            print(f"  [跳闸] {label} 本轮退避额度已用尽，停用该源（其它源继续）")
            return None

        if attempt < len(waits):
            wait = waits[min(attempt, len(waits) - 1)]
            state["used"] += 1
            print(f"  [退避] {label} 等待 {wait}s 后重试"
                  f"（{label} 本次已退避 {state['used']}/{max_total} 次）...")
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

    resp = fetch_with_backoff(url, session, "https://news.baidu.com/", "baidu")
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


# ==================== 源3：Bing 网页搜索 ====================

# Bing 摘要里内嵌的日期（绝对日期形如 2026-09-22 / 2026年9月22日）
_BING_DATE_RE = re.compile(
    r"(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日")


def extract_bing_item(item):
    """
    从 Bing 结果块 li.b_algo 抽取字段。返回 (title, url, summary, source, date_raw)。

    - 标题/直链：h2 > a[href]（真实直链，非跳转）
    - 摘要：.b_caption（日期内嵌其中）
    - 来源：cite 的展示域名（形如 "www.gz.gov.cn › ..."，取 › 之前）
    """
    h2 = item.select_one("h2")
    a = h2.select_one("a[href]") if h2 else None
    url = normalize_url(a.get("href", "")) if a else ""
    title = clean_text(h2.get_text(" ")) if h2 else ""

    cap = item.select_one(".b_caption")
    cap_text = clean_text(cap.get_text(" ")) if cap else ""
    m = _BING_DATE_RE.search(cap_text)
    date_raw = m.group(0) if m else ""
    # 摘要去掉开头的"时间 · 日期 —"前缀，保留正文
    summary = re.sub(r"^.*?—\s*", "", cap_text) if "—" in cap_text[:40] else cap_text

    cite = item.select_one("cite")
    if cite:
        source = clean_text(cite.get_text(" ")).split("›")[0].strip()
    else:
        source = urlparse(url).netloc
    return title, url, summary, source, date_raw


def fetch_bing_web_page(keyword, page=0, session=None):
    """
    Bing 网页搜索（cn.bing.com/search）。返回结果列表；被拦截返回 None。

    注意：Bing【新闻】垂直（news/search、?format=RSS）已失效，只有网页搜索可用；
    网页结果日期稀疏（约 1~2/10 有条目日期）。分页参数 first 从 1 开始
    （第 n 页 first = page*10 + 1）。
    """
    if session is None:
        session = requests.Session()

    params = {"q": keyword, "first": page * 10 + 1}
    url = "https://cn.bing.com/search?" + "&".join(
        f"{k}={quote(str(v))}" for k, v in params.items())

    resp = fetch_with_backoff(url, session, "https://cn.bing.com/", "bing")
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    results = soup.select("li.b_algo")
    if not results:
        print(f"  [警告] Bing 页面正常但未匹配到结果节点（可能改版），"
              f"正文 {len(resp.text)} 字符")
        return []

    articles = []
    for item in results:
        title, link, summary, source, date_raw = extract_bing_item(item)
        if title and link:
            articles.append(make_article(title, link, summary, source, date_raw,
                                          keyword, "bing"))
    return articles


# ==================== 采集主循环 ====================

SOURCES = {
    "baidu": fetch_baidu_news_page,
    "bing": fetch_bing_web_page,
    "360": fetch_360_news_page,
}


def crawl_all_keywords(plans, max_pages=3, resolve_url=False,
                       enabled_sources=None, progress_path=None, fresh=False):
    """
    多源采集。返回 (文章列表, 统计信息)。

    参数 plans 是 topic_config.build_search_plan() 产出的检索计划列表——每个元素
    是一个 dict（query / level1_code / ... / level3_code / level3_name / unit /
    region_code / region_name / anchor_terms / region_aliases），而不是裸字符串。
    这样采到的每一条都能归属到具体的指标和地区上，而不是只知道"被某个词搜到"。

    源按检索式粒度选择（cfg.SOURCES_BY_REGION_LEVEL）：带地区=county，反之 none。
    按源隔离：某源被限流只跳过该源该检索式（额度用尽则跳闸停用该源），其余源继续——
    这是多源改造的核心目的。

    progress_path 非空且 fresh=False 时，跳过台账里已成功的 (源, 检索式) 并边采边
    落盘，让重跑只补缺口（省下最稀缺的百度额度）。
    """
    enabled = enabled_sources or cfg.ENABLED_SOURCES
    session = requests.Session()
    _SOURCE_THROTTLE.clear()
    done = set()
    if progress_path and not fresh:
        done = load_progress(progress_path)

    if "baidu" in enabled:
        warm_up_session(session)

    by_id = {}          # article_id -> 条目，便于跨源合并 engines
    stats = {
        "per_keyword": {},
        "per_source": {name: 0 for name in enabled},
        # 覆盖率口径：分母是"该源被路由到的检索式数"（非总检索式数），
        # 否则只走百度的县级检索式会被误报成"bing/360 有缺口"。
        "plans_by_source": {name: 0 for name in enabled},
        "ok_by_source": {name: 0 for name in enabled},
        "skipped_by_source": {name: 0 for name in enabled},
        "engines_merged": 0,
        "noise_dropped": 0,
        "irrelevant_dropped": 0,
        "failed_pages": 0,
        "failed_by_source": {},
        "resumed_skipped": 0,
    }

    total = len(plans)
    idle_noted = False
    for idx, plan in enumerate(plans, 1):
        kw = plan["query"]
        stats["per_keyword"][kw] = 0

        # 按检索式粒度选源：带地区 = county，不带 = none
        level = "county" if plan.get("region_code") else "none"
        allowed = cfg.SOURCES_BY_REGION_LEVEL.get(level, tuple(enabled))
        plan_sources = [s for s in enabled if s in allowed]
        for s in plan_sources:
            stats["plans_by_source"][s] += 1
        # 断点续跑键：确定性、跨轮稳定
        plan_key = (f"{plan.get('region_code', '')}"
                    f"|{plan.get('level3_code', '')}|{kw}")

        # 该检索式的所有路由源都已跳闸 → 不做请求、也不空等，直接看下一条。
        # 否则某源跳闸后，剩余上千条检索式会以 3~5s 的间隔空跑一遍，纯浪费（实测踩过）。
        active = [s for s in plan_sources if not _throttle_state(s)["tripped"]]
        if not active:
            if plan_sources and not idle_noted:
                print(f"\n[跳过] 从第 {idx} 条起，其全部源均已跳闸，"
                      f"后续检索式不再请求、也不再空等，本轮到此基本结束。")
                idle_noted = True
            continue

        where = plan["region_name"] or "（无地区）"
        print(f"\n[{idx}/{total}] {where} · {plan['level3_name']}")
        print(f"  检索式：{kw}")

        for source_name in active:
            label = SOURCE_LABEL.get(source_name, source_name)
            if progress_path and (source_name, plan_key) in done:
                stats["resumed_skipped"] += 1
                stats["skipped_by_source"][source_name] += 1
                print(f"  -- 源：{label} [续跑] 该检索式已采过，跳过")
                continue

            fetcher = SOURCES[source_name]
            # 每个源翻几页由配置决定：百度吃限流额度，指标检索式只取首屏
            per_source_pages = cfg.MAX_PAGES_BY_SOURCE.get(source_name, max_pages)
            print(f"  -- 源：{label}（{per_source_pages} 页）")
            source_failed = False
            # 松散匹配源需补偿过滤：结果须命中地区别名之一（无地区时自动失效）
            required = (plan.get("region_aliases", ())
                        if source_name in cfg.LOOSE_SOURCES else ())

            for page in range(per_source_pages):
                print(f"    第 {page + 1} 页 ...", end=" ")
                articles = fetcher(kw, page=page, session=session)

                if articles is None:
                    # 该源被限流/失败：只跳过这个源，其它源继续
                    source_failed = True
                    stats["failed_pages"] += 1
                    stats["failed_by_source"][source_name] = \
                        stats["failed_by_source"].get(source_name, 0) + 1
                    print(f"[中断] {label} 该检索式被限流，跳过；"
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
                                             cfg.QUALIFIER_TERMS,
                                             plan.get("anchor_terms", ()),
                                             required_terms=required):
                        stats["irrelevant_dropped"] += 1
                        continue

                    art_id = generate_article_id(art["title"], art["url"])
                    art["article_id"] = art_id
                    # 指标归属：一篇文章可能同时印证多条指标，用 indicators 累积
                    tag = f"{plan['level2_code']}:{plan['level3_name']}"

                    if art_id in by_id:
                        # 同一篇被两源/多个检索式命中 —— 记录引擎与指标叠加，
                        # 这本身就是一个免费的双源印证信号
                        existing = by_id[art_id]
                        if art["engine"] not in existing["engines"].split("+"):
                            existing["engines"] += "+" + art["engine"]
                            stats["engines_merged"] += 1
                        cur = existing.get("indicators", "")
                        if tag not in cur.split("+"):
                            existing["indicators"] = (cur + "+" + tag).strip("+")
                        continue

                    art["engines"] = art["engine"]
                    # 指标与地区标识（单值列取"首个命中"的指标，便于简单聚合；
                    # 全量归属在 indicators 里）
                    art["level1_code"] = plan["level1_code"]
                    art["level1_name"] = plan["level1_name"]
                    art["level2_code"] = plan["level2_code"]
                    art["level2_name"] = plan["level2_name"]
                    art["level3_code"] = plan["level3_code"]
                    art["level3_name"] = plan["level3_name"]
                    art["unit"] = plan["unit"]
                    art["region_code"] = plan["region_code"]
                    art["region_name"] = plan["region_name"]
                    art["indicators"] = tag

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
                # 每页间隔按源给定：只有会被限流的源才需要长间隔
                dmin, dmax = cfg.DELAY_BY_SOURCE.get(source_name,
                                                     cfg.DELAY_DEFAULT)
                random_delay(dmin, dmax)

            if not source_failed:
                stats["ok_by_source"][source_name] += 1
                # 该源该检索式成功采完 → 边采边记入台账（崩溃/中断也不丢）
                if progress_path:
                    append_progress(progress_path, [(
                        source_name, plan_key, "ok",
                        time.strftime("%Y-%m-%d %H:%M:%S"))])

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


def print_coverage(stats, total_plans):
    """
    打印本轮检索式的源覆盖率。

    指标化采集的请求量是"地区 × 指标"，远超百度 20s 间隔下能跑完的额度，单轮必然采不全。
    关键不是消灭失败，而是让缺口【始终可见】——否则"看着跑完了其实没采全"是最危险的。
    分母是"该源被路由到的检索式数"（按粒度选源），而非总检索式数。
    """
    print("\n" + "-" * 60)
    print(f"[覆盖率] 本轮共 {total_plans} 条检索式")
    routed = stats.get("plans_by_source") or {}
    for name, routed_n in routed.items():
        if not routed_n:
            continue        # 该源本轮未被路由到任何检索式（按粒度选源），不报缺口
        done = (stats.get("ok_by_source", {}).get(name, 0)
                + stats.get("skipped_by_source", {}).get(name, 0))
        mark = "" if done >= routed_n else "   ← 有缺口"
        print(f"  {name}：{done}/{routed_n} 条已路由{mark}")
    if stats.get("resumed_skipped"):
        print(f"  （其中 {stats['resumed_skipped']} 次因续跑台账已采而跳过）")
    if stats.get("failed_pages"):
        print(f"  采集失败页数：{stats['failed_pages']}"
              f"（{stats.get('failed_by_source')}）")
        print("  [提示] 百度按 IP 限流。若频繁触发，请调大 topic_config.MIN_DELAY"
              "（实测间隔 ≥20s 可持续采集）；已采成功的检索式会记入续跑台账，"
              "重跑只补缺口（--fresh 可忽略台账全量重采）。")
    print("-" * 60)


def run_once(run_dir, paths, accumulator, plans, enabled_sources=None,
             progress_path=None, fresh=False):
    print("=" * 60)
    print(f"{cfg.TOPIC_LABEL} · 指标化新闻采集")
    print("=" * 60)
    print(f"启用源：{', '.join(enabled_sources or cfg.ENABLED_SOURCES)}")
    print(f"检索式：{len(plans)} 条")
    print(f"产物目录：{run_dir}")
    if progress_path and not fresh:
        print(f"续跑台账：{progress_path}（已成功的检索式将跳过；--fresh 可忽略）")

    raw, stats = crawl_all_keywords(
        plans,
        max_pages=cfg.MAX_PAGES_PER_KEYWORD,
        resolve_url=RESOLVE_REAL_URL,
        enabled_sources=enabled_sources,
        progress_path=progress_path,
        fresh=fresh,
    )

    print_coverage(stats, len(plans))

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
        description=f"{cfg.TOPIC_LABEL} · 指标化搜索引擎语料采集")
    ap.add_argument("--output-dir",
                    help="输出根目录（覆盖 topic_config.OUTPUT_ROOT）。"
                         "相对路径锚定到本脚本所在目录，也可写绝对路径把"
                         "数据放到仓库外")
    ap.add_argument("--level2", nargs="*", default=["A1"],
                    help="只采指定二级指标，如 --level2 A1 A2（默认 A1）")
    ap.add_argument("--cities", nargs="*", default=None,
                    help="只采广东省内的指定地级市，如 --cities 广州市 深圳市"
                         "（默认全省 124 个县级单元）")
    ap.add_argument("--no-region", action="store_true",
                    help="不带地区前缀采集（每个指标用全部展开检索式）")
    ap.add_argument("--sources", nargs="*", default=None,
                    help="只启用指定源，如 --sources bing（默认 baidu+bing+360 全开）")
    ap.add_argument("--offset", type=int, default=0,
                    help="检索计划切片起点，用于分批绕开百度限流")
    ap.add_argument("--limit", type=int, default=None,
                    help="本轮最多采前 N 条检索式")
    ap.add_argument("--fresh", action="store_true",
                    help="忽略续跑台账、全量重采（默认跳过台账里已成功的检索式）")
    ap.add_argument("--list-plan", action="store_true",
                    help="只打印检索计划，不采集、不建目录（零请求，先核对）")
    return ap.parse_args(argv)


def build_plans(args):
    """按命令行参数生成检索计划（地区为外层、指标为内层）。"""
    regions = [] if args.no_region else cfg.guangdong_regions(cities=args.cities)
    return cfg.build_search_plan(
        level2_codes=args.level2,
        regions=regions,
        offset=args.offset,
        limit=args.limit,
    )


def main(argv=None):
    args = parse_args(argv)
    plans = build_plans(args)

    if args.list_plan:
        # 零请求核对：先看清要打哪些检索式、各走哪些源，再决定怎么分批
        print(f"检索计划：{len(plans)} 条")
        for i, p in enumerate(plans, 1):
            level = "county" if p.get("region_code") else "none"
            srcs = "+".join(cfg.SOURCES_BY_REGION_LEVEL.get(level, ()))
            print(f"{i:>4}. [{p['region_name'] or '无地区'}·{level}→{srcs}]"
                  f" {p['level3_name']}  →  {p['query']}")
        return

    if not plans:
        print("[中止] 检索计划为空，请检查 --level2 / --cities 参数。")
        return

    # 一切路径锚定到脚本自身位置，不依赖当前工作目录——
    # 这样从任何地方执行，产物都稳定落在本主题的输出根下。
    topic_dir = Path(__file__).resolve().parent
    out_root = resolve_output_root(topic_dir, cfg.OUTPUT_ROOT, args.output_dir)
    accumulator = out_root / cfg.SEARCH_ACCUMULATOR
    progress_path = out_root / cfg.SEARCH_PROGRESS

    run_dir = resolve_run_dir(out_root)
    paths = artifact_paths(run_dir)
    try:
        with run_logging(paths["log"]):
            print(f"输出根目录：{out_root}")
            run_once(run_dir, paths, accumulator, plans,
                     enabled_sources=args.sources,
                     progress_path=progress_path, fresh=args.fresh)
    finally:
        # 此处 stdout 已恢复，只作提示不写入日志；崩溃时尤其需要知道存到哪了
        print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
