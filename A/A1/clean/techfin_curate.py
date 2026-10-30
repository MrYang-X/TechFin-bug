"""
科技金融指数研究 · ④ 语料清洗/提纯（三段流水线之上的"清洗环节"）

职责：读 ① 的两份累积总库（+ 可选读 ② 的正文缓存 body_cache/），做**纯本地**清洗：
相关性提纯、规范化、链接去重、质量分级、事件分组，产出：
  - curated_corpus.csv      文章级"干净语料"（一行一篇，含 relevance/quality/region 等富化列）
  - curated_uncertain.csv   官方"泛化标题"待正文确认队列（供 ② 补抓）
  - curated_dropped.csv     被剔除条目（含原因，便于复核）
  - curated_report.md       清洗报告（漏斗逐级计数、分布、抽检样本）

**不发任何网络请求**；**不 import 任何业务脚本**（只读 crawler_common / topic_config / 可选 extract_config）；
全量重算（幂等），新语料来了重跑即可。仿 verify_corpus.py 的「只读上游→本地变换→写产物」模式。

核心判断：① 里【检索语料】已被 is_relevant 的 token-AND + 地区约束筛过，④ 只做规范化/去重/分级；
【官方语料】在 ① 是零过滤全量保存（446 条里仅约 25 条与 A101 相关），故 ④ 承担相关性提纯重任。

用法：
  python techfin_curate.py                 # 生成清洗产物到最新运行目录 + 输出根
  python techfin_curate.py --sample 30     # 报告里抽检样本数
"""

import argparse
import difflib
import hashlib
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
import region_rules as rr   # noqa: E402
# noinspection PyUnresolvedReferences
from paths import finish_run_manifest, new_pipeline_run, pipeline_root  # noqa: E402
from crawler_common import (  # noqa: E402
    clean_text,
    hamming64,
    is_noise_source,
    looks_like_url_title,
    parse_date,
    run_logging,
    simhash64,
)

# ==================== 本脚本配置（不动 topic_config，便于将来照抄新主题）====================

CURATOR_VERSION = "curate-v1"   # 规则版本；改规则即 bump，输出带版本保证跨轮可比
RELEVANT_SCORE_MIN = 3          # 相关性通过阈值（= 单级标题/正文命中）
EVENT_SIM_THRESHOLD = 0.82      # 事件分组标题相似度阈值
TITLE_MIN_LEN = 5               # 与 ① 清洗一致

# 追踪参数白名单：只删这些，绝不删路径段与内容参数
TRACKING_PARAM_PREFIXES = ("utm_", "spm", "share_", "share_token")
TRACKING_PARAMS = {
    "scene", "from", "fr", "isappinstalled", "subscene", "clicktime", "timestamp",
    "__biz", "mid", "idx", "sn", "chksm", "mpshare", "srcid", "wxshare",
    "fbclid", "gclid", "ref", "source", "_t", "_",
}
KEEP_QUERY_PARAMS = {"id", "aid", "post", "articleid", "content", "p", "page", "type"}

# 输出列顺序
CURATED_COLS = [
    "curated_id", "article_id", "kind", "title", "title_clean", "title_norm",
    "summary", "source", "publisher_domain",
    "url", "url_canonical", "url_dedup_key",
    "date", "date_raw", "date_url", "date_final", "date_source",
    "level1_code", "level1_name", "level2_code", "level2_name",
    "level3_code", "level3_name", "unit", "indicators", "indicator_codes_all",
    "region_code", "region_name", "city_name", "region_level", "region_from",
    "region_confidence", "region_evidence",
    "rel_score", "rel_level", "rel_evidence", "n_number_statements", "is_relevant",
    "quality_grade", "grade_reason",
    "has_body", "body_chars", "body_method", "body_term_hits",
    "event_group_id", "n_sources_in_event",
    "near_dup_of", "near_dup_score", "is_near_dup",
    "crawl_time", "curate_time", "curator_version",
]
UNCERTAIN_COLS = ["article_id", "kind", "title", "source", "url", "date", "reason"]
DROPPED_COLS = ["article_id", "kind", "title", "source", "url", "drop_reason", "dup_of"]


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== 文本 / 链接规范化 ====================

def to_halfwidth(text):
    """全角 ASCII（FF01–FF5E）与全角空格 → 半角；只动标点/数字/字母，不动中文。"""
    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def normalize_text(text):
    """clean_text（去 HTML/URL/零宽/压空白）+ 全角转半角。"""
    return to_halfwidth(clean_text(text))


def canonical_url(url):
    """
    规范链接：小写 scheme/netloc、去默认端口、删 fragment、只删白名单追踪参数。
    **绝不删路径段或内容参数**；任何异常都原样返回（保底不误合）。
    """
    if not url or not isinstance(url, str):
        return url or ""
    try:
        p = urlsplit(url.strip())
    except Exception:
        return url
    scheme = (p.scheme or "http").lower()
    netloc = p.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    kept = []
    if p.query:
        for pair in p.query.split("&"):
            if not pair:
                continue
            key = pair.split("=", 1)[0].lower()
            if key in KEEP_QUERY_PARAMS:
                kept.append(pair)
            elif key in TRACKING_PARAMS:
                continue
            elif any(key.startswith(pre) for pre in TRACKING_PARAM_PREFIXES):
                continue
            else:
                kept.append(pair)
    return urlunsplit((scheme, netloc, p.path, "&".join(kept), ""))


def url_dedup_key(url):
    return hashlib.md5(canonical_url(url).encode("utf-8")).hexdigest()[:16]


# ==================== 地区索引（不 import ③，自带一份）====================

def build_region_index():
    """兼容旧调用：地区索引统一由 config/region_rules.py 构建。"""
    return rr.build_region_index()


def resolve_region(title, summary, body, row, idx):
    """兼容旧调用：标题 > 摘要 > 正文前 500 字，统一由 region_rules 实现。"""
    return rr.resolve_article_region(title, summary, body, row, idx)


# ==================== 指标词族与命中 ====================

# "高企"作为形容词的前缀（成本/价格高企…），命中即不算指标词
_ADJ_LEFT = ("成本", "价格", "房价", "物价", "通胀", "利率", "估值", "售价",
             "电价", "油价", "费", "价", "涨")


def _is_adjectival_gaoqi(text, pos):
    left = text[max(0, pos - 2):pos]
    if any(left.endswith(w) for w in _ADJ_LEFT):
        return True
    if text[pos + 2:pos + 5].startswith(("的", "不下", "难下")):
        return True
    return False


def build_indicator_families():
    """{level3_code: {terms, level3_name, unit, level1/2_code/name}}，由 INDEX_KEYWORDS 派生。"""
    fams = {}
    for l2, node in cfg.INDEX_KEYWORDS.items():
        for ind in node["indicators"]:
            terms = []
            for q in ind["queries"]:
                if " " not in q and len(q) <= 12 and q not in terms:
                    terms.append(q)
            fams[ind["level3_code"]] = {
                "terms": terms, "level3_name": ind["level3_name"], "unit": ind["unit"],
                "level1_code": node["level1_code"], "level1_name": node["level1_name"],
                "level2_code": l2, "level2_name": node["level2_name"],
            }
    try:
        # noinspection PyUnresolvedReferences
        import extract_config as ec
        if "A101" in fams:
            for t in ec.A101_TERMS:
                if t not in fams["A101"]["terms"]:
                    fams["A101"]["terms"].append(t)
    except Exception:
        pass
    return fams


def _find_term(text, term):
    start = 0
    while True:
        i = text.find(term, start)
        if i < 0:
            return -1
        if term == "高企" and _is_adjectival_gaoqi(text, i):
            start = i + 1
            continue
        return i


def find_indicator_hits(text, fams):
    """返回 [(level3_code, term), ...]，每个指标取首个有效命中。"""
    hits = []
    if not text:
        return hits
    for code, fam in fams.items():
        for term in fam["terms"]:
            if _find_term(text, term) >= 0:
                hits.append((code, term))
                break
    return hits


_NUM_UNIT = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:万余|万|余|多)?\s*(?:家|户|个|家/万人|家/万|家每万人)")


def count_number_statements(text, fams, window=60):
    """统计"指标词邻近 window 字内有 数字+家/户/个"的语句数（判是否"数值型报道"）。"""
    if not text:
        return 0
    positions = []
    for code, fam in fams.items():
        for term in fam["terms"]:
            s = 0
            while True:
                i = text.find(term, s)
                if i < 0:
                    break
                if term != "高企" or not _is_adjectival_gaoqi(text, i):
                    positions.append(i)
                s = i + 1
    if not positions:
        return 0
    return sum(1 for m in _NUM_UNIT.finditer(text)
               if any(abs(m.start() - p) <= window for p in positions))


# ==================== 相关性打分 ====================

def score_relevance(row, body_text, fams, idx):
    """多级加权相关性；返回判据字典。标题+3 / 摘要+2 / 正文+3 / 锚点+1 / 地区+1。"""
    title = str(row.get("title", "") or "")
    summary = str(row.get("summary", "") or "")
    body_text = body_text or ""
    score, codes, evidence = 0, [], []
    levels = []
    code_levels: dict[str, set[str]] = {}

    def hit(text, pts, level):
        nonlocal score
        h = find_indicator_hits(text, fams)
        if h:
            score += pts
            levels.append(level)
            for c, t in h:
                if c not in codes:
                    codes.append(c)
                code_levels.setdefault(c, set()).add(level)
                if len(evidence) < 3:
                    evidence.append(t)
        return bool(h)

    t_hit = hit(title, 3, "title")
    s_hit = hit(summary, 2, "summary")
    b_hit = hit(body_text, 3, "body") if body_text else False
    joined = title + summary + body_text
    if any(a in joined for a in cfg.ANCHOR_TERMS):
        score += 1
    region = resolve_region(title, summary, body_text, row, idx)
    if region.code and region.source not in ("aggregate", "query", "none"):
        score += 1

    rel_level = "title" if t_hit else ("body" if b_hit else ("summary" if s_hit else "none"))
    return {
        "rel_score": score,
        "rel_level": rel_level,
        "level3_codes": codes,
        "level3_code_levels": {c: sorted(v) for c, v in code_levels.items()},
        "rel_evidence": "、".join(evidence),
        "region": region,
        "n_number_statements": count_number_statements(body_text or joined, fams),
        "body_term_hits": len(find_indicator_hits(body_text, fams)) if body_text else 0,
    }


# ==================== 来源分类与质量分级 ====================

_STAT_DOC_RE = re.compile(r"统计年鉴|年鉴|统计公报|经济运行|统计月报|国民经济和社会发展")


def classify_source(row, url):
    src = str(row.get("source", "") or "") + str(row.get("title", "") or "")
    u = url or ""
    if _STAT_DOC_RE.search(src):
        return "stat_doc"
    if "gov.cn" in u or "gov.cn" in str(row.get("source", "") or "") or row.get("kind") == "official":
        return "gov_doc"
    return "media" if u else "unknown"


def grade_quality(src_class, rel, has_body):
    """→ fact_tech_finance_data.data_quality 的 1-4。返回 (grade, reason)。"""
    relevant = rel["rel_score"] >= RELEVANT_SCORE_MIN
    has_num = rel["n_number_statements"] > 0
    if src_class == "stat_doc":
        return 1, "官方统计文档(年鉴/公报)"
    if src_class == "gov_doc":
        if relevant and (has_body or has_num):
            return 2, "政府公报/部门数据"
        return 4, "相关但证据不足(待补正文/数值)"
    if src_class == "media":
        if relevant and (has_body or has_num):
            return 3, "媒体调研估算(新闻)"
        return 4, "相关但证据不足(待补正文/数值)"
    return 4, "来源未知"


# ==================== 标题归一化（事件分组用）====================

_TITLE_SUFFIX_RE = re.compile(r"[\|｜\-–—_—]+\s*([^|｜\-–—_—\d]{1,10})$")
_NON_WORD_RE = re.compile(r"[\W_]+")


def title_variants(title):
    base = clean_text(title)
    full = _NON_WORD_RE.sub("", base).lower()
    core = _NON_WORD_RE.sub("", _TITLE_SUFFIX_RE.sub("", base)).lower()
    out = {full}
    if core and core != full:
        out.add(core)
    return out


def title_norm(title):
    return _NON_WORD_RE.sub("", clean_text(title)).lower()


def _sim(set_a, set_b):
    best = 0.0
    for a in set_a:
        for b in set_b:
            if a and b:
                best = max(best, difflib.SequenceMatcher(None, a, b).ratio())
    return best


def group_events(rows):
    """按 (region_code, level3_code) 分桶，桶内按标题相似度聚类；只标注不合并。"""
    buckets = defaultdict(list)
    for r in rows:
        buckets[(r.get("region_code", ""), r.get("level3_code", ""))].append(r)
    gid = 0
    for key, group in buckets.items():
        clusters = []   # [{"members":[...], "variants":set}]
        for r in group:
            tv = title_variants(r.get("title", ""))
            for c in clusters:
                if _sim(tv, c["variants"]) >= EVENT_SIM_THRESHOLD:
                    c["members"].append(r)
                    c["variants"] |= tv
                    break
            else:
                clusters.append({"members": [r], "variants": set(tv)})
        for c in clusters:
            if len(c["members"]) > 1:
                gid += 1
                for m in c["members"]:
                    m["event_group_id"] = f"E{gid:05d}"
                    m["n_sources_in_event"] = len(c["members"])


# ==================== 输入加载 ====================

def load_inputs(out_root):
    frames = []
    for kind in ("search", "official"):
        path = out_root / cfg.ACCUMULATOR_FILES[kind]
        if not path.exists():
            print(f"[跳过] 累积总库不存在：{path}")
            continue
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
        df["kind"] = kind
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df.fillna("")


def load_body_index(cache_dir):
    """{article_id: {body_text, status, body_method, attachments}}；不进 attach/。"""
    import json
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
                out[obj.get("article_id", "")] = obj
            except Exception:
                continue
    return out


# ==================== 主流水线 ====================

def _enrich(row, body_obj, fams, idx):
    """给一篇文章算出全部富化列。"""
    url = str(row.get("url", "") or "").strip()
    title = str(row.get("title", "") or "")
    title_clean = normalize_text(title)
    summary_clean = normalize_text(str(row.get("summary", "") or ""))
    body_text = (body_obj.get("body_text", "") or "") if body_obj else ""
    has_body = bool(body_text)

    rel = score_relevance(row, body_text, fams, idx)
    codes = rel["level3_codes"]
    src_class = classify_source(row, url)
    grade, reason = grade_quality(src_class, rel, has_body)
    region = rel["region"]
    date_final, date_source = _resolve_date(row)

    out = dict(row)
    out.update({
        "kind": row.get("kind", ""),
        "title_clean": title_clean,
        "title_norm": title_norm(title),
        "summary": summary_clean or str(row.get("summary", "") or ""),
        "publisher_domain": urlsplit(url).netloc.lower() if url else "",
        "url_canonical": canonical_url(url),
        "url_dedup_key": url_dedup_key(url),
        "date_final": date_final, "date_source": date_source,
        "region_code": region[0], "region_name": region[1],
        "region_level": region[2], "city_name": region[3], "region_from": region[4],
        "region_confidence": region[5], "region_evidence": region[6],
        "rel_score": rel["rel_score"], "rel_level": rel["rel_level"],
        "rel_evidence": rel["rel_evidence"],
        "n_number_statements": rel["n_number_statements"],
        "is_relevant": rel["rel_score"] >= RELEVANT_SCORE_MIN,
        "quality_grade": grade, "grade_reason": reason,
        "has_body": has_body,
        "body_chars": len(body_text),
        "body_method": (body_obj.get("body_method", "") if body_obj else ""),
        "body_term_hits": rel["body_term_hits"],
        "event_group_id": "", "n_sources_in_event": 1,
        "near_dup_of": "", "near_dup_score": "", "is_near_dup": 0,
        "curate_time": now_str(), "curator_version": CURATOR_VERSION,
    })
    # 近重复内部指纹（不写入 CSV）：只取前 2000 字，避免长正文拖慢清洗
    out["_simhash"] = simhash64((body_text or (title + " " + summary_clean))[:2000])
    # 指标列：官方语料原本没有，用词族命中回填；检索语料保留原值。
    # 多词族命中时不再盲取 codes[0]，而是按"标题命中 > 摘要命中 > 正文命中"选主指标，
    # 并把全部命中保留在 indicator_codes_all 供复核。
    out["indicators"] = row.get("indicators", "") or ""
    out["indicator_codes_all"] = ";".join(codes)
    if codes and not str(out.get("level3_code", "")).strip():
        def _rank(code):
            hit_levels = set(rel["level3_code_levels"].get(code, []))
            priority = 3 if "title" in hit_levels else (
                2 if "summary" in hit_levels else (1 if "body" in hit_levels else 0))
            return priority, -codes.index(code)
        primary = max(codes, key=_rank)
        fam = fams[primary]
        out.update({"level1_code": fam["level1_code"], "level1_name": fam["level1_name"],
                    "level2_code": fam["level2_code"], "level2_name": fam["level2_name"],
                    "level3_code": primary, "level3_name": fam["level3_name"],
                    "unit": fam["unit"]})
    if codes and not out["indicators"]:
        out["indicators"] = "+".join(f"{fams[c]['level2_code']}:{fams[c]['level3_name']}"
                                     for c in codes)
    out["curated_id"] = hashlib.md5(
        (out["url_dedup_key"] + out["title_norm"]).encode("utf-8")).hexdigest()[:16]
    return out


def _resolve_date(row):
    d = str(row.get("date", "") or "").strip()
    if d:
        return d, "article"
    du = str(row.get("date_url", "") or "").strip()
    if du:
        return du, "url"
    d2 = parse_date(str(row.get("date_raw", "") or ""))
    if d2:
        return d2, "article_raw"
    return "", "none"


def dedup_by_url(rows):
    """按 url_dedup_key 折叠；组内按 (grade↑, has_body↓, crawl_time↓) 择优。返回 (kept, dups)。"""
    groups = defaultdict(list)
    for r in rows:
        groups[r["url_dedup_key"]].append(r)
    kept, dups = [], []
    for grp in groups.values():
        if len(grp) == 1:
            kept.append(grp[0])
            continue
        # Python 排序稳定：先按更次要的键排，再按主键排
        grp.sort(key=lambda r: str(r.get("crawl_time", "")), reverse=True)  # 时间新→旧
        grp.sort(key=lambda r: (int(r.get("quality_grade", 4)),
                                0 if r.get("has_body") else 1))             # grade/正文优先
        win = grp[0]
        kept.append(win)
        for r in grp[1:]:
            r["drop_reason"] = "同链接(去追踪参数)重复"
            r["dup_of"] = win["curated_id"]
            dups.append(r)
    return kept, dups


_UNCERTAIN_RE = re.compile(r"名单|公示|认定|公布")


def _drop_entry(row, reason, dup_of=""):
    """统一成 DROPPED_COLS 结构，避免 DataFrame 出现 NaN。"""
    return {"article_id": str(row.get("article_id", "") or ""),
            "kind": row.get("kind", ""),
            "title": str(row.get("title", "") or ""),
            "source": row.get("source", ""),
            "url": str(row.get("url", "") or ""),
            "drop_reason": reason, "dup_of": dup_of}


def annotate_near_dups(rows, threshold=3):
    """
    近重复标注：同 (region_code, level3_code) 桶内 SimHash 汉明距离 ≤ threshold 的
    后来者标 `is_near_dup=1` / `near_dup_of` / `near_dup_score`。
    **只标注、不删除**——多源转载是研究特性，是否折叠由下游决定。
    """
    groups = defaultdict(list)
    for r in rows:
        groups[(r.get("region_code", ""), r.get("level3_code", ""))].append(r)
    for group in groups.values():
        if len(group) < 2:
            continue
        for i, anchor in enumerate(group):
            if anchor.get("is_near_dup"):
                continue
            ha = anchor.get("_simhash", 0)
            if not ha:
                continue
            for other in group[i + 1:]:
                hb = other.get("_simhash", 0)
                if not hb:
                    continue
                dist = hamming64(ha, hb)
                if dist <= threshold:
                    other["is_near_dup"] = 1
                    other["near_dup_of"] = anchor.get("curated_id", "")
                    other["near_dup_score"] = round(1 - dist / 64, 3)


def curate_rows(df, body_index, fams, idx):
    rows, dropped = [], []
    funnel = Counter({"input": len(df)})
    for _, r in df.iterrows():
        row = r.to_dict()
        url = str(row.get("url", "") or "").strip()
        # ① 噪声源
        if is_noise_source(url, str(row.get("source", "") or ""),
                           cfg.NOISE_DOMAINS, cfg.NOISE_SOURCES, cfg.NOISE_URL_SUFFIXES):
            funnel["noise"] += 1
            dropped.append(_drop_entry(row, "noise"))
            continue
        # ② 空/废标题
        tc = normalize_text(str(row.get("title", "") or ""))
        if len(tc) < TITLE_MIN_LEN or (looks_like_url_title(tc) and not row.get("summary")):
            funnel["empty"] += 1
            dropped.append(_drop_entry(row, "empty"))
            continue
        rows.append(_enrich(row, body_index.get(str(row.get("article_id", "") or "")), fams, idx))

    # ③ 链接级去重
    kept, dups = dedup_by_url(rows)
    funnel["dup"] += len(dups)
    dropped.extend(dups)

    # ④ 相关性提纯
    final, uncertain = [], []
    for r in kept:
        if r["rel_score"] >= RELEVANT_SCORE_MIN:
            final.append(r)
        elif (r["kind"] == "official" and _UNCERTAIN_RE.search(str(r.get("title", "") or ""))
              and not r["has_body"]):
            r["reason"] = "官方泛化标题(名单/公示/认定)，待补正文确认"
            uncertain.append(r)
            funnel["uncertain"] += 1
        else:
            dropped.append(_drop_entry(r, "irrelevant"))
            funnel["irrelevant"] += 1

    # ⑦ 事件分组（只标注）+ 近重复标注（只标注、不删除）
    annotate_near_dups(final)
    group_events(final)
    return final, dropped, uncertain, funnel


# ==================== 报告 ====================

def build_report(funnel, final, dropped, uncertain, body_index, sample_n=20):
    L = ["# 语料清洗/提纯报告\n", f"生成时间：{now_str()} ｜ 规则版本 {CURATOR_VERSION}\n"]
    L.append("## 1. 漏斗（逐级）\n")
    L.append(f"- 输入：{funnel['input']} 条")
    L.append(f"  - 噪声源剔除：{funnel['noise']}")
    L.append(f"  - 空/废标题剔除：{funnel['empty']}")
    L.append(f"  - 同链接(去追踪参数)去重：{funnel['dup']}")
    L.append(f"  - 相关性不足剔除：{funnel['irrelevant']}")
    L.append(f"  - 官方泛化标题→待确认队列：{funnel['uncertain']}")
    L.append(f"- **保留（干净语料）：{len(final)} 条**\n")

    kinds = Counter(r["kind"] for r in final)
    L.append(f"- 保留的 kind 分布：{dict(kinds)}")
    L.append(f"- 保留的 quality_grade 分布：{dict(Counter(r['quality_grade'] for r in final))}")
    L.append(f"- 保留的 rel_level 分布：{dict(Counter(r['rel_level'] for r in final))}")
    L.append(f"- 近重复标注（SimHash≤3，只标注不删除）："
             f"{sum(1 for r in final if r.get('is_near_dup'))}")
    regions = Counter(r["region_code"] for r in final if r["region_code"])
    L.append(f"- 覆盖地区数：{len(regions)}（县级单元）\n")

    # 回归哨兵：检索语料本应全相关；只有"因不相关被剔除"才算异常
    L.append("## 2. 回归哨兵\n")
    search_final = [r for r in final if r["kind"] == "search"]
    s_bad = [d for d in dropped if d.get("kind") == "search" and d.get("drop_reason") == "irrelevant"]
    s_other = [d for d in dropped if d.get("kind") == "search" and d.get("drop_reason") != "irrelevant"]
    L.append(f"- 检索语料：保留 {len(search_final)}，因【不相关】被剔除 {len(s_bad)}"
             f"{'（⚠ 异常！检索语料本应全相关，请检查规则）' if s_bad else '（正常）'}")
    for d in s_bad[:20]:
        L.append(f"  - {str(d.get('title'))[:50]}")
    if s_other:
        L.append(f"- 检索语料中因噪声/空标题/同链接去重被剔除 {len(s_other)} 条（预期内）")
    L.append("")

    L.append("## 3. 正文覆盖\n")
    n_body = sum(1 for r in final if r["has_body"])
    L.append(f"- 保留语料中已有正文：{n_body}/{len(final)}"
             f"（正文只作相关性加分，不因缺正文而删）\n")

    L.append(f"## 4. 抽检样本（保留 {min(sample_n, len(final))} 条）\n")
    for i, r in enumerate(final[:sample_n], 1):
        L.append(f"{i}. [{r['quality_grade']}·{r['rel_level']}·{r['rel_score']}] "
                 f"{r['title_clean'][:46]}")
        L.append(f"   - {r['region_name'] or '(无地区)'} | {r['source']} | {r['url'][:70]}")
    L.append("")

    L.append(f"## 5. 被剔除抽检（{min(sample_n, len(dropped))} 条）\n")
    for i, d in enumerate(dropped[:sample_n], 1):
        L.append(f"{i}. [{d.get('drop_reason')}] {str(d.get('title'))[:60]}")
    L.append("")

    if uncertain:
        L.append(f"## 6. 待确认队列（官方泛化标题，供②补抓）{len(uncertain)} 条\n")
        for i, u in enumerate(uncertain[:sample_n], 1):
            L.append(f"{i}. {str(u.get('title'))[:60]} | {u.get('source')}")
        L.append("")
    return "\n".join(L)


# ==================== 产物与主流程 ====================

def artifact_paths(run_dir):
    p = cfg.CURATED_PREFIX
    return {
        "log": os.path.join(run_dir, p + "log"),
        "report": os.path.join(run_dir, p + "report.md"),
        "snapshot": os.path.join(run_dir, p + "corpus.csv"),
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · ④ 语料清洗/提纯")
    ap.add_argument("--output-dir", help="数据根目录（覆盖 topic_config.DATA_ROOT）")
    ap.add_argument("--sample", type=int, default=20, help="报告抽检样本数（默认 20）")
    return ap.parse_args(argv)


def run_once(run_dir, paths, out_root, sample_n):
    print("=" * 60)
    print(f"{cfg.TOPIC_LABEL} · ④ 语料清洗/提纯")
    print("=" * 60)

    df = load_inputs(out_root)
    if df.empty:
        print("[中止] 没有可清洗的语料（请先运行 ① 采集脚本）。")
        return
    body_index = load_body_index(str(out_root / cfg.BODY_CACHE_DIR))
    fams = build_indicator_families()
    idx = build_region_index()
    print(f"输入：{len(df)} 条 | 正文缓存：{len(body_index)} 篇 | 指标族：{len(fams)}")

    final, dropped, uncertain, funnel = curate_rows(df, body_index, fams, idx)

    fdf = pd.DataFrame(final, columns=CURATED_COLS)
    fdf.to_csv(paths["snapshot"], index=False, encoding="utf-8-sig")
    fdf.to_csv(out_root / cfg.CURATED_ACCUMULATOR, index=False, encoding="utf-8-sig")
    pd.DataFrame(uncertain, columns=UNCERTAIN_COLS).to_csv(
        out_root / cfg.CURATED_UNCERTAIN, index=False, encoding="utf-8-sig")
    pd.DataFrame(dropped, columns=DROPPED_COLS).to_csv(
        out_root / cfg.CURATED_DROPPED, index=False, encoding="utf-8-sig")

    report = build_report(funnel, final, dropped, uncertain, body_index, sample_n)
    with open(paths["report"], "w", encoding="utf-8") as f:
        f.write(report)

    print("\n" + "-" * 60)
    print(f"[漏斗] 输入 {funnel['input']} → 噪声 {funnel['noise']}，空标题 {funnel['empty']}，"
          f"重复 {funnel['dup']}，不相关 {funnel['irrelevant']}，待确认 {funnel['uncertain']}")
    print(f"[保留] 干净语料 {len(final)} 条 → {out_root / cfg.CURATED_ACCUMULATOR}")
    print(f"[近重复] 标注 {sum(1 for r in final if r.get('is_near_dup'))} 条"
          f"（只标注，不删除多源转载）")
    print(f"[队列] 待确认 {len(uncertain)} 条 → {out_root / cfg.CURATED_UNCERTAIN}")
    print(f"[报告] {paths['report']}")
    print("-" * 60)


def main(argv=None):
    args = parse_args(argv)
    topic_dir = _TOPIC_DIR
    out_root = pipeline_root(topic_dir, args.output_dir)
    run_dir = new_pipeline_run("clean", override=args.output_dir, topic_dir=topic_dir)
    paths = artifact_paths(run_dir)
    status = "failed"
    try:
        with run_logging(paths["log"]):
            print(f"数据根目录：{out_root}")
            run_once(run_dir, paths, out_root, args.sample)
        status = "ok"
    finally:
        finish_run_manifest(run_dir, status=status)
        print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
