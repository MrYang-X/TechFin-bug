"""
科技金融指数研究 · ③ 结构化数值抽取（三段流水线的第三段）

职责：读取 ② 落下的正文缓存 body_cache/（**纯读，不再发网络请求**），从正文与附件文本里
抽出 A101「高新技术企业数量/密度」的**数据点**，落成"数据点级"CSV：
  - 证据级累积总库 techfin_a101_points.csv（一行 = 一个数据点，含原文证据片段）
  - 本轮聚合级 points_resolved.csv（一行 = 一个 地区×年份×指标 格，可直接映射 SQL 事实表）

抽取两路：
  - 规则层（零成本、高精度、中召回）：正则匹配"XX区新增高新技术企业 123 家"等明显模式。
  - LLM 层（Sonnet 5.5，仅在有歧义处触发）：区分"拟/计划/目标"与"实际/累计"，处理跨句表述。
  合并后按 point_id 去重；context_type 为 planned/target/decline 的数据点**照样入库**（标记而非丢弃），
  聚合层默认只取 actual 作为主值。

与②的解耦：改规则只动 extract_config.py、重跑本脚本即可，**不必重抓网页**。

用法：
  python techfin_extract_points.py --extractor rule     # 只跑规则层（零 LLM 成本）
  python techfin_extract_points.py --extractor hybrid   # 规则 + LLM 兜底
  python techfin_extract_points.py --limit 20           # 只处理前 20 篇（快速试验）
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
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
import topic_config as cfg       # noqa: E402
# noinspection PyUnresolvedReferences
import extract_config as ec      # noqa: E402
# noinspection PyUnresolvedReferences
import region_rules as rr        # noqa: E402
# noinspection PyUnresolvedReferences
from paths import (  # noqa: E402
    finish_run_manifest,
    new_pipeline_run,
    pipeline_root,
    resolve_cache_path,
)
from crawler_common import (     # noqa: E402
    run_logging,
)

LEVEL_RANK = {"province": 1, "city": 2, "county": 3}
LLM_TEXT_MAX = 6000     # 单次喂给 LLM 的相关片段上限（字符）

# 被丢弃数据点的审计列（写入 points_dropped.csv，不静默丢弃）
DROPPED_COLS = [
    "article_id", "level3_code", "level3_name", "region_code", "stat_year",
    "metric", "unit", "value_text", "extract_method", "drop_reason",
    "evidence_snippet",
]


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== 地区索引 ====================

def build_region_index():
    """兼容旧调用：地区索引统一由 config/region_rules.py 构建。"""
    return rr.build_region_index()


def _region_from_meta(meta):
    """正文里没提地区时，退回采集时地区（低置信，region_from='query'）。"""
    return rr.fallback_region(meta, "", source="query", require_evidence=False)


def map_region(text, meta, idx):
    """兼容旧调用：整段文本匹配（无数字锚点）。"""
    return rr.match_region(text, None, meta, idx)


# ==================== 句子与数值解析 ====================

_SENT_SPLIT = re.compile(r"[。！？；!?;\n]+")


def split_sentences(text):
    """按句读切句，返回 [(句子, 起始偏移)]（偏移用于 evidence_locator）。"""
    out, start = [], 0
    for m in _SENT_SPLIT.finditer(text or ""):
        s = text[start:m.start()]
        if s.strip():
            out.append((s.strip(), start))
        start = m.end()
    tail = (text or "")[start:]
    if tail.strip():
        out.append((tail.strip(), start))
    return out


def find_indicator(sent):
    """该句是否提及 A101 指标族，返回命中的词（无则空串）。"""
    for t in ec.A101_TERMS:
        if t in sent:
            return t
    return ""


def find_indicator_scope(sent):
    """
    返回 (命中词, 指标编码)。当前规则层只启用 A101；
    以后扩指标时在 extract_config.INDICATOR_SCOPE 里加词族即可。
    """
    for code, scope in ec.INDICATOR_SCOPE.items():
        for term in scope.get("terms", ()):
            if term in sent:
                return term, code
    return "", ""


def validate_points(points):
    """
    指标-口径-单位一致性校验。返回 (valid, dropped)。

    这是 A103 错标的第二道闸门：即使上游 meta 被回填成 A103，
    规则层产出的 count/家 点也会在这里被拦下并写入 points_dropped.csv。
    """
    valid, dropped = [], []
    for p in points:
        code = str(p.get("level3_code", "") or "")
        scope = ec.INDICATOR_SCOPE.get(code)
        reason = ""
        if not scope:
            reason = "INDICATOR_NOT_ENABLED"
        elif str(p.get("metric", "")) not in scope["metrics"]:
            reason = "METRIC_INDICATOR_MISMATCH"
        elif str(p.get("unit", "")) not in scope["units"]:
            reason = "UNIT_INDICATOR_MISMATCH"
        if reason:
            item = dict(p)
            item["drop_reason"] = reason
            dropped.append(item)
        else:
            valid.append(p)
    return valid, dropped


def _metric_of(unit):
    if unit and ("/" in unit or unit == "家每万人"):
        return "density"
    return "count"


def _num_value(num, mag):
    v = float(str(num).replace(",", ""))
    if mag in ("万余", "万"):
        v *= 10000
    return v


def classify_context(text):
    """判定语境类型。顺序：planned/target/decline 优先于 actual（避免"拟新增"被当成"新增"）。"""
    for v in ec.PLANNED_VERBS:
        if v in text:
            return "planned", v
    for v in ec.TARGET_VERBS:
        if v in text:
            return "target", v
    for v in ec.DECLINE_VERBS:
        if v in text:
            return "decline", v
    for v in ec.ACTUAL_VERBS:
        if v in text:
            return "actual", v
    return "unknown", ""


def _min_distance(sent, term, pos):
    """给定数字位置 pos，返回它与句中最近一次指标词出现的字符距离。"""
    d = 10 ** 9
    start = 0
    while True:
        i = sent.find(term, start)
        if i < 0:
            break
        d = min(d, abs(i - pos))
        start = i + 1
    return d


_CLAUSE_SEP = re.compile(r"[，,、；;：:]")

# 同一子句里出现这些"非目标主语"时，数字不是高新技术企业数量。
# 例："高新技术企业254家，科技型中小企业421家，研发机构57家" 只保留 254。
_NON_TARGET_SUBJECTS = (
    "科技型中小企业", "研发机构", "孵化器", "服务机构", "创新平台",
    "科技项目", "认定名单", "入库企业", "规上企业",
)
_LEAD_BREAKDOWN = ("分别有", "分别为", "各区", "四区")
_LEAD_AGGREGATE = ("合计", "总计")
_LEAD_SUBSET = ("其中", "规上", "工业、建筑业", "服务业", "纳入统计")


def _clause_at(text, pos):
    left, right = _clause_bounds(text, pos)
    return text[left:right].strip(), left, right


def _prev_clause(text, left):
    """返回当前子句之前的那一个子句文本。"""
    if left <= 0:
        return ""
    j = left - 2
    while j >= 0 and text[j] not in "，,、；;：:\n":
        j -= 1
    return text[j + 1:left - 1].strip()


def _bare_number_clause(clause):
    """子句是否只有"数字+单位"（列表项，如"147家"）。"""
    return bool(re.fullmatch(
        r"\s*[\d,]+(?:\.\d+)?\s*(?:万余|万|余|多)?\s*"
        r"(?:家|户|个|家/万人|家/万|户/万人|家每万人)\s*", clause or ""))


def _nearest_term_pos(text, term, pos):
    """返回 (最近指标词位置, 距离)。"""
    best, best_d = -1, 10 ** 9
    start = 0
    while True:
        i = text.find(term, start)
        if i < 0:
            break
        d = abs(i - pos)
        if d < best_d:
            best, best_d = i, d
        start = i + 1
    return best, best_d


def _clause_bounds(text, pos):
    """返回 pos 所在子句的 [left, right)。分隔符：，,、；;：:"""
    left = 0
    for m in _CLAUSE_SEP.finditer(text, 0, max(0, pos)):
        left = m.end()
    m = _CLAUSE_SEP.search(text, pos)
    right = m.start() if m else len(text)
    return left, right


def _aggregation_type(text, pos):
    """子句级口径：total / subset / breakdown / aggregate。"""
    clause, left, _ = _clause_at(text, pos)
    if any(w in clause for w in _LEAD_BREAKDOWN):
        return "breakdown"
    if any(w in clause for w in _LEAD_AGGREGATE):
        return "aggregate"
    if any(w in clause for w in _LEAD_SUBSET):
        return "subset"
    if _bare_number_clause(clause):
        prev = _prev_clause(text, left)
        if any(w in prev for w in _LEAD_BREAKDOWN):
            return "breakdown"
        if any(w in prev for w in _LEAD_AGGREGATE):
            return "aggregate"
        if any(w in prev for w in _LEAD_SUBSET):
            return "subset"
        # 列表项：句首引导子句含"分别有/各区"，且已出现指标词
        prefix = text[:left]
        if any(w in prefix for w in _LEAD_BREAKDOWN) and any(t in prefix for t in ec.A101_TERMS):
            return "breakdown"
    return "total"


# ==================== 规则层抽取 ====================

def _make_point(article, meta, region, value_num, value_text, unit, metric,
                stat_year, stat_period, ctype, cue, snippet, locator,
                method, confidence, llm=None, year_src="", level3_code="A101",
                aggregation_type="total", year_confidence="", year_evidence="",
                evidence_verified=""):
    code, name, level, city, rfrom = region[:5]
    rconf = region[5] if len(region) > 5 else ""
    revid = region[6] if len(region) > 6 else ""
    scope = ec.INDICATOR_SCOPE.get(level3_code, {})
    return {
        "article_id": article.get("article_id", ""),
        "source_url": meta.get("url", ""),
        "fetch_url": article.get("final_url", "") or article.get("fetch_url", ""),
        "page_type": article.get("page_type", ""),
        "title": meta.get("title", ""),
        "publisher": meta.get("source", ""),
        "publish_date": meta.get("date", ""),
        "authority": authority_of(meta),
        "quality_grade": str(meta.get("quality_grade", "") or ""),
        "level1_code": meta.get("level1_code", ""),
        "level1_name": meta.get("level1_name", ""),
        "level2_code": meta.get("level2_code", ""),
        "level2_name": meta.get("level2_name", ""),
        "level3_code": level3_code,
        "level3_name": scope.get("level3_name", meta.get("level3_name", "")),
        "metric": metric,
        "region_code": code, "region_name": name, "region_level": level,
        "city_name": city, "region_from": rfrom,
        "region_confidence": rconf, "region_evidence": revid,
        "value_num": value_num, "value_text": value_text, "unit": unit or "",
        "stat_year": stat_year, "stat_period": stat_period, "stat_year_src": year_src,
        "year_confidence": year_confidence, "year_evidence": year_evidence,
        "context_type": ctype, "context_cue": cue,
        "aggregation_type": aggregation_type,
        "is_breakdown": 1 if aggregation_type == "breakdown" else 0,
        "evidence_snippet": snippet[:300],
        "evidence_source": locator[0], "evidence_locator": locator[1],
        "evidence_verified": evidence_verified,
        "extract_method": method, "confidence": round(confidence, 2),
        "llm_model": (llm or {}).get("model", ""),
        "llm_prompt_version": (llm or {}).get("prompt_version", ""),
        "extract_time": now_str(),
    }


def authority_of(meta):
    url = str(meta.get("url", "") or "") + str(meta.get("source", "") or "")
    if "gov.cn" in url or str(meta.get("kind", "")) == "official":
        return "官方"
    return "媒体"


_YEAR_RE = re.compile(r"((?:19|20)\d{2})\s*年")
_YEAR_PERIOD_RE = re.compile(r"((?:19|20)\d{2})\s*年(?:度|底|末|初|上半|下半|全年)")
_REL_TOKENS = ("去年", "上年", "上年度", "今年", "当年", "本年度", "同期")


def _pub_year(meta):
    """文章发布日期的年份（无则 None）。"""
    m = re.match(r"(\d{4})", str(meta.get("date", "") or ""))
    return int(m.group(1)) if m else None


def _rel_year(token, meta):
    """相对表述 → 具体年份（按发布年换算）；无发布年返回 None。"""
    py = _pub_year(meta)
    if py is None:
        return None
    return py - 1 if token in ("去年", "上年", "上年度") else py


def infer_stat_year(sent, window, meta, doc_text=""):
    """
    推断数据点所属【数据年份】，返回 (year|None, src)。src 标明年份来源，供下游审计/加权：
      period   口径短语（"截至2024年底""2024年度""2024年末"）——比裸年更具体，先判
      text     句内显式年（"2024年新增…"）
      relative 相对表述（今年/去年…，按发布日期换算）
      window   邻句（±1 句）里的年
      doc      同段落/该句前 300 字内最近的年（不是全文第一个年）
      none     都没有
    文章发布年不再作为默认回退：发布年不等于统计年，误用会系统性错标。
    """
    m = _YEAR_PERIOD_RE.search(sent)
    if m:
        return int(m.group(1)), "period"
    m = _YEAR_RE.search(sent)
    if m:
        return int(m.group(1)), "text"
    for tok in _REL_TOKENS:
        if tok in sent:
            y = _rel_year(tok, meta)
            if y:
                return y, "relative"
    m = _YEAR_RE.search(window)
    if m:
        return int(m.group(1)), "window"
    if doc_text:
        years = list(_YEAR_RE.finditer(doc_text))
        if years:
            return int(years[-1].group(1)), "doc"
    return None, "none"


def _year_evidence(sent, window, doc_text, src, year):
    """返回年份的原文证据短语（用于审计与 loader 复核）。"""
    if not year:
        return ""
    if src in ("period", "text", "relative", "llm"):
        text = sent
    elif src == "window":
        text = window
    else:
        text = doc_text
    m = re.search(rf"(?:截至|到)?\s*{int(year)}\s*年(?:度|底|末|初|上半|下半|全年)?", text or "")
    return m.group(0).strip() if m else f"{int(year)}年"


def extract_sentence(sent, window, offset, label, article, meta, idx, doc_text=""):
    """对单个含指标词的句子跑规则匹配，返回数据点列表。"""
    term, indicator_code = find_indicator_scope(sent)
    if not indicator_code:
        return []
    points = []
    ctype, cue = classify_context(sent)
    year, ysrc = infer_stat_year(sent, window, meta, doc_text)
    yconf = ec.YEAR_SRC_CONF.get(ysrc, 0.2)
    yevid = _year_evidence(sent, window, doc_text, ysrc, year)
    period = f"{year}年" if year else ""
    src = ("body", f"char:{offset}") if label == "body" else (label, "")

    for m in ec.NUM_UNIT_RE.finditer(sent):
        unit = m.group("unit")
        num = m.group("num")
        mag = m.group("mag")
        # 指标与数字必须同子句；列表项（纯"数字+单位"）允许从引导子句延续
        clause, _, _ = _clause_at(sent, m.start())
        if any(t in clause for t in _NON_TARGET_SUBJECTS) and term not in clause:
            continue
        term_pos, d = _nearest_term_pos(sent, term, m.start())
        same_clause = _clause_bounds(sent, term_pos) == _clause_bounds(sent, m.start())
        if not ((same_clause and d <= 25) or (_bare_number_clause(clause) and d <= 60)):
            continue
        # 地区按“数字锚点”判定，同一句里不同数字可归属不同地区/口径。
        # 列表项（"3082家、147家、2315家"）的锚点窗口可能看不到句首地区，
        # 此时回退到整句匹配，让列表继承句首的市/区口径。
        region = rr.match_region(sent, m.start(), meta, idx)
        if region.source == "aggregate" and _bare_number_clause(_clause_at(sent, m.start())[0]):
            region = rr.match_region(sent, None, meta, idx)
        if not region.code or region.source in ("aggregate", "scope_national"):
            continue
        agg_type = _aggregation_type(sent, m.start())
        val = _num_value(num, mag)
        vtext = m.group(0).strip()
        conf = 0.9 if d <= 30 else 0.65
        if ctype == "unknown":
            conf -= 0.15
        if mag in ("余", "多"):
            conf -= 0.15
        points.append(_make_point(
            article, meta, region, val, vtext, unit, _metric_of(unit),
            year, period, ctype, cue, sent, src, "rule", max(conf, 0.2),
            year_src=ysrc, level3_code=indicator_code,
            aggregation_type=agg_type,
            year_confidence=yconf, year_evidence=yevid))

    return points


def collect_sources(article, cache_dir=None):
    """一篇文章的文本源列表：正文 + 各附件（只取有文本的）。"""
    sources = [("body", article.get("body_text", "") or "")]
    for att in article.get("attachments", []) or []:
        if not att.get("status") == "ok":
            continue
        txt = ""
        tp = (str(resolve_cache_path(cache_dir, att.get("text_path", ""), att.get("text_rel", "")))
              if cache_dir else att.get("text_path", ""))
        if tp and os.path.exists(tp):
            try:
                txt = open(tp, encoding="utf-8").read()
            except OSError:
                txt = ""
        if txt.strip():
            fname = att.get("filename", "attachment")
            sources.append((f"attachment:{fname}", txt))
    return sources


def rule_extract_article(article, idx, cache_dir=None):
    """规则层抽一篇文章。返回 (points, windows, indicator_sents 数)。"""
    meta = article.get("meta", {}) or {}
    sources = collect_sources(article, cache_dir)
    points, windows = [], []
    n_ind_sents = 0
    for label, text in sources:
        sents = split_sentences(text)
        for i, (s, off) in enumerate(sents):
            if not find_indicator(s):
                continue
            n_ind_sents += 1
            window = " ".join(x for x, _ in sents[max(0, i - 1):i + 2])
            windows.append(window)
            # 年份回退只看同一文本源中该句之前的 300 字，避免跨附件/全文误取
            doc_before = text[max(0, off - 300):off]
            points.extend(extract_sentence(s, window, off, label, article, meta, idx,
                                           doc_before))
    return points, windows, n_ind_sents


# ==================== LLM 兜底层（Sonnet 5.5）====================

def _llm_needed(points, windows, n_ind_sents):
    """是否值得为该文调用 LLM（控成本；模式见 extract_config.LLM_TRIGGER_MODE）。"""
    if n_ind_sents == 0:
        return False
    if ec.LLM_TRIGGER_MODE == "any_indicator":
        return True                                    # 提到指标就调（精度优先）
    if not points:
        return True                                    # 提到指标却没抽到数
    if any(p["confidence"] < 0.5 for p in points):
        return True                                    # 低置信
    if any(p["context_type"] == "unknown" for p in points):
        return True                                    # 语境不明（拟/实际分不清）
    return False


def _system_text():
    shots = []
    for ex in ec.FEW_SHOT:
        shots.append(f"输入：{ex['user']}\n输出：{json.dumps(ex['assistant'], ensure_ascii=False)}")
    return ec.SYSTEM_PROMPT + "\n\n示例：\n" + "\n".join(shots)


def _cache_key(article_id, rel_text):
    raw = f"{article_id}|{ec.PROMPT_VERSION}|{ec.LLM_MODEL}|{hashlib.md5(rel_text.encode()).hexdigest()}"
    return hashlib.md5(raw.encode()).hexdigest()


def _llm_error_info(exc):
    """返回 (错误类名, 是否致命, 是否可重试, HTTP 状态码)。"""
    name = type(exc).__name__
    status = int(getattr(exc, "status_code", 0) or 0)
    fatal = name in ("AuthenticationError", "PermissionDeniedError",
                     "NotFoundError", "BadRequestError")
    retryable = name in ("RateLimitError", "APIConnectionError", "APITimeoutError",
                         "InternalServerError") or (name == "APIStatusError" and status >= 500)
    return name, fatal, retryable, status


def _append_llm_log(calls_log, entry):
    try:
        with open(calls_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _usage_dict(resp):
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {}

    def val(name):
        return int(getattr(usage, name, 0) or 0)

    return {
        "input_tokens": val("input_tokens"),
        "output_tokens": val("output_tokens"),
        "cache_read_input_tokens": val("cache_read_input_tokens"),
        "cache_creation_input_tokens": val("cache_creation_input_tokens"),
    }


def _estimate_cost(usage):
    price = ec.LLM_PRICING.get(ec.LLM_MODEL, {})
    if not usage or not price:
        return 0.0
    return round(
        usage.get("input_tokens", 0) / 1e6 * price.get("input", 0)
        + usage.get("output_tokens", 0) / 1e6 * price.get("output", 0)
        + usage.get("cache_read_input_tokens", 0) / 1e6 * price.get("cache_read", 0)
        + usage.get("cache_creation_input_tokens", 0) / 1e6 * price.get("cache_write", 0),
        6,
    )


def call_llm(rel_text, meta, client, cache_dir, calls_log):
    """
    调用 Sonnet 5.5 抽取（结构化输出强约束 JSON）。
    注意：**不传 temperature**——Sonnet 5.5 对非默认 temperature 会返回 400；
    可靠性由 output_config.format 的 JSON schema 保证。结果按 key 缓存、并记审计日志。

    返回 (data, meta_info)；meta_info 含 cached/usage/latency_ms/cost_usd/request_id。
    可重试错误（429/5xx/连接/超时）指数退避 3 次；认证/参数错误立即失败。
    """
    key = _cache_key(str(meta.get("article_id", "")), rel_text)
    cache_path = os.path.join(cache_dir, f"{key}.json")
    if os.path.exists(cache_path):
        try:
            data = json.load(open(cache_path, encoding="utf-8"))
            return data, {"cached": True, "usage": {}, "latency_ms": 0,
                          "cost_usd": 0.0, "request_id": ""}
        except Exception:
            pass

    user_text = (f"采集时地区：{meta.get('region_name', '')}（{meta.get('region_code', '')}）\n\n"
                 f"正文相关片段：\n{rel_text}")
    resp = None
    last_exc = None
    start = time.perf_counter()
    for attempt in range(3):
        start = time.perf_counter()
        try:
            resp = client.messages.create(
                model=ec.LLM_MODEL,
                max_tokens=ec.LLM_MAX_TOKENS,
                system=[{"type": "text", "text": _system_text(),
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_text}],
                output_config={
                    "effort": ec.LLM_EFFORT,
                    "format": {"type": "json_schema", "schema": ec.LLM_SCHEMA},
                },
            )
            break
        except Exception as exc:
            last_exc = exc
            name, fatal, retryable, status = _llm_error_info(exc)
            if fatal or not retryable or attempt >= 2:
                _append_llm_log(calls_log, {
                    "key": key, "model": ec.LLM_MODEL,
                    "prompt_version": ec.PROMPT_VERSION, "ts": now_str(),
                    "ok": False, "cached": False, "error_type": name,
                    "status_code": status, "error": str(exc)[:500],
                    "attempt": attempt + 1,
                })
                raise
            time.sleep(2 ** attempt)
    if resp is None:
        raise last_exc or RuntimeError("LLM 调用失败且无异常信息")
    latency_ms = int((time.perf_counter() - start) * 1000)

    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text) if text else {"data_points": []}
    data["_model"] = ec.LLM_MODEL
    data["_prompt_version"] = ec.PROMPT_VERSION
    usage = _usage_dict(resp)
    data["_usage"] = usage
    cost = _estimate_cost(usage)

    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    request_id = getattr(resp, "_request_id", "")
    _append_llm_log(calls_log, {
        "key": key, "model": ec.LLM_MODEL, "prompt_version": ec.PROMPT_VERSION,
        "ts": now_str(), "ok": True, "cached": False, "usage": usage,
        "latency_ms": latency_ms, "request_id": request_id,
        "cost_usd": cost, "raw": data,
    })
    return data, {"cached": False, "usage": usage, "latency_ms": latency_ms,
                  "cost_usd": cost, "request_id": request_id}


def _locate_evidence(article, snippet):
    """在正文/附件文本中定位 LLM 给出的证据片段，返回 (locator, verified)。"""
    snippet = (snippet or "").strip()
    if not snippet:
        return ("body", ""), 0
    for label, text in collect_sources(article):
        pos = (text or "").find(snippet)
        if pos >= 0:
            return (label, f"char:{pos}"), 1
    return ("body", ""), 0


def llm_points(data, article, meta, idx):
    """把 LLM 返回的 data_points 换成统一的 point dict。"""
    out = []
    llm = {"model": data.get("_model", ec.LLM_MODEL),
           "prompt_version": data.get("_prompt_version", ec.PROMPT_VERSION)}
    for dp in data.get("data_points", []):
        snippet = str(dp.get("evidence_snippet", "") or "")
        locator, verified = _locate_evidence(article, snippet)
        code = str(dp.get("region_code", "") or "")
        name = str(dp.get("region_name", "") or "")
        if code:
            # 用 code 反查级别/市
            level, city = "county", ""
            for alias, v in idx.items():
                if v[0] == code:
                    level, city = v[2], v[3]
                    break
            if code.endswith("0000"):
                level = "province"
            elif code.endswith("00") and level != "county":
                level = "city"
            region = (code, name or code, level, city, "text")
        elif name:
            region = map_region(name, meta, idx)
        else:
            region = _region_from_meta(meta)
        out.append(_make_point(
            article, meta, region,
            dp.get("value"), str(dp.get("value_text", "") or ""), dp.get("unit", ""),
            dp.get("metric", "count"),
            dp.get("stat_year"), str(dp.get("stat_period", "") or ""),
            dp.get("context_type", "unknown"), str(dp.get("context_cue", "") or ""),
            snippet, locator, "llm", float(dp.get("confidence", 0.6) or 0.6), llm=llm,
            year_src=("llm" if dp.get("stat_year") else "none"),
            level3_code=str(dp.get("indicator", "") or "A101"),
            year_confidence=(ec.YEAR_SRC_CONF.get("llm", 0.75) if dp.get("stat_year") else 0.0),
            year_evidence=str(dp.get("stat_period", "") or
                              (f"{dp.get('stat_year')}年" if dp.get("stat_year") else "")),
            evidence_verified=verified))
    return out


# ==================== 合并与去重 ====================

def point_id_of(p):
    raw = "|".join(str(p.get(k, "")) for k in (
        "article_id", "level3_code", "region_code", "stat_year", "metric",
        "value_num", "unit", "context_type", "evidence_locator", "aggregation_type"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _point_key(p):
    """两条数据点是否"同一件事"的判据（跨规则层/LLM 层对齐用）。

    用规范化后的 value_num + unit，而不是原始 value_text——
    否则"突破1000家"和"1000家"会被当成两个点。
    """
    return (p.get("region_code", ""), str(p.get("value_num", "")),
            p.get("unit", ""), p.get("context_type", ""),
            p.get("aggregation_type", "total"))


def merge_points(rule_pts, llm_pts, llm_ran):
    """
    合并规则层与 LLM 层。返回 (points, n_rule_dropped)。

    - 未跑 LLM：直接用规则层结果（离线/纯规则模式）。
    - 跑了（无论 LLM 有无结果）：**以 LLM 为准**——规则层在真实政府文本上假阳性较多
      （"每家…1家""第 46 家"等），不能让它污染数据集。规则层与 LLM 命中同一数据点的
      标记 rule+llm 并取较高置信度；规则层多出的（疑似假阳性）丢弃，但计入 n_rule_dropped，
      在汇总里可见（不静默）。
    """
    if not llm_ran:
        pts, dropped = list(rule_pts), 0
    else:
        by_key = {_point_key(p): p for p in llm_pts}
        for rp in rule_pts:
            k = _point_key(rp)
            if k in by_key:
                by_key[k]["extract_method"] = "rule+llm"
                by_key[k]["confidence"] = max(by_key[k]["confidence"], rp["confidence"])
        pts = list(by_key.values())
        dropped = sum(1 for rp in rule_pts if _point_key(rp) not in by_key)

    out, ids = [], set()
    for p in pts:
        p["point_id"] = point_id_of(p)
        if p["point_id"] in ids:
            continue
        ids.add(p["point_id"])
        out.append(p)
    return out, dropped


# ==================== 聚合层 ====================

def _quality_of(chosen):
    """data_quality：优先用 ④ 清洗给出的 quality_grade；无则按 authority 回退（官方→2/媒体→3）。"""
    q = str(chosen.get("quality_grade", "") or "").strip()
    if q in ("1", "2", "3", "4"):
        return q
    return "2" if chosen.get("authority") == "官方" else "3"


def resolve_points(points):
    """把证据级数据点归一到 (region_code, level3_code, stat_year, metric) 一格一行。"""
    rows = []
    df = pd.DataFrame(points)
    if df.empty:
        return pd.DataFrame(columns=ec.RESOLVED_COLS)
    df["_val"] = pd.to_numeric(df["value_num"], errors="coerce")
    if "aggregation_type" in df.columns:
        df["_agg"] = df["aggregation_type"].replace("", "total").fillna("total")
    else:
        df["_agg"] = "total"
    for (rcode, l3, year, metric), g in df.groupby(
            ["region_code", "level3_code", "stat_year", "metric"], dropna=False):
        # 主值只取 total；breakdown/aggregate 保留在证据级，不参与 canonical
        total_all = g[g["_agg"] == "total"]
        total = total_all
        if len(total_all) and "year_confidence" in total_all.columns:
            hc = pd.to_numeric(total_all["year_confidence"], errors="coerce").fillna(0)
            if (hc >= 0.5).any():
                total = total_all[hc >= 0.5]
            else:
                # 只有低置信年份（doc/article/none）：不产主值，保留证据供复核
                total = total_all.iloc[0:0]
        actual = total[total["context_type"] == "actual"] if len(total) else total
        pool = actual if len(actual) else total
        # 择优：官方 > 媒体；再比置信度；再比发布时间新
        if len(pool) == 0:
            chosen = g.assign(
                _auth=g["authority"].map({"官方": 0, "媒体": 1}).fillna(2)
            ).sort_values(by=["_auth", "confidence", "publish_date"],
                          ascending=[True, False, False]).iloc[0]
        else:
            ranked = pool.assign(
                _auth=pool["authority"].map({"官方": 0, "媒体": 1}).fillna(2)
            ).sort_values(by=["_auth", "confidence", "publish_date"],
                          ascending=[True, False, False])
            chosen = ranked.iloc[0]
        vals = pd.to_numeric(pool["_val"], errors="coerce").dropna() if len(pool) else pd.Series(dtype=float)
        source_pool = actual if len(actual) else (total if len(total) else g)
        aids = [a for a in source_pool["article_id"].astype(str).unique() if a]
        n_uniq = vals.nunique()
        canonical = chosen["value_num"] if len(actual) else ""
        try:
            if canonical == "" or pd.isna(canonical):
                canonical = ""
        except (TypeError, ValueError):
            canonical = ""
        agg = str(chosen.get("aggregation_type", "total") or "total")
        reason = ("取实际口径(" + str(chosen["authority"]) + ")→"
                  if len(actual) else "无实际口径→") + str(chosen["context_type"])
        if not len(actual):
            reason += "；无 total 实际值，未取主值"
        elif agg == "breakdown":
            reason += "；仅拆分口径，未取主值"
        elif agg == "aggregate":
            reason += "；汇总口径"
        rows.append({
            "region_code": rcode,
            "region_name": chosen.get("region_name", "") or (g["region_name"].iloc[0] if len(g) else ""),
            "city_name": chosen.get("city_name", "") or (g["city_name"].iloc[0] if len(g) else ""),
            "region_from": chosen.get("region_from", ""),
            "region_confidence": chosen.get("region_confidence", ""),
            "region_evidence": chosen.get("region_evidence", ""),
            "level3_code": l3, "level3_name": chosen.get("level3_name", ""),
            "metric": metric, "stat_year": year,
            "stat_year_src": chosen.get("stat_year_src", ""),
            "year_confidence": chosen.get("year_confidence", ""),
            "year_evidence": chosen.get("year_evidence", ""),
            "aggregation_type": agg,
            "canonical_value": canonical,
            "n_sources": len(aids),
            "n_support": len(actual),
            "source_article_ids": ",".join(aids),
            "value_min": (vals.min() if len(vals) else ""),
            "value_max": (vals.max() if len(vals) else ""),
            "agreement": ("True" if n_uniq <= 1 else "False"),
            "chosen_reason": reason,
            "authority": chosen["authority"],
            "data_quality": _quality_of(chosen),
            "data_source": f"新闻语料抽取（{len(aids)} 篇）",
        })
    return pd.DataFrame(rows, columns=ec.RESOLVED_COLS)


# ==================== 主流程 ====================

def load_cached_articles(cache_dir):
    """遍历 body_cache/<桶>/<aid>.json（跳过 attach/），返回文章对象列表。"""
    out = []
    base = Path(cache_dir)
    if not base.exists():
        return out
    for bucket in base.iterdir():
        if not bucket.is_dir() or bucket.name == "attach":
            continue
        for jf in bucket.glob("*.json"):
            try:
                out.append(json.load(open(jf, encoding="utf-8")))
            except Exception:
                continue
    return out


def load_curated(out_root):
    """读 ④ 清洗清单 curated_corpus.csv → {article_id: {quality_grade, level3_code, region_*}}。
    文件不存在返回 {}（③ 行为完全不变——④ 是"提纯器"不是"必经关卡"）。"""
    path = Path(out_root) / cfg.CURATED_ACCUMULATOR
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return {}
    out = {}
    for _, r in df.iterrows():
        aid = str(r.get("article_id", "") or "")
        if not aid:
            continue
        out[aid] = {k: str(r.get(k, "") or "") for k in (
            "quality_grade", "level1_code", "level1_name", "level2_code", "level2_name",
            "level3_code", "level3_name", "region_code", "region_name",
            "region_from", "region_confidence", "region_evidence")}
    return out


def apply_curated_filter(articles, out_root):
    """有清洗清单时：只保留提纯通过的文章，并把 ④ 字段回填进 meta（补官方缺列）。"""
    curated = load_curated(out_root)
    if not curated:
        return articles
    kept = []
    for a in articles:
        c = curated.get(str(a.get("article_id", "") or ""))
        if not c:
            continue                      # 未通过 ④ 提纯 → 不抽
        meta = a.setdefault("meta", {})
        for k, v in c.items():
            if v:
                meta[k] = v
        kept.append(a)
    print(f"[清洗] 读取 {cfg.CURATED_ACCUMULATOR}：缓存 {len(articles)} 篇 → 提纯通过 {len(kept)} 篇")
    return kept


def build_llm_client():
    """惰性构造 Anthropic 客户端；不可用时返回 None（是否允许降级由调用方决定）。"""
    try:
        import anthropic
    except Exception:
        print("[LLM] 未安装 anthropic（pip install anthropic）；hybrid 模式将中止，"
              "加 --allow-rule-fallback 可纯规则继续。")
        return None
    try:
        return anthropic.Anthropic()
    except Exception as e:
        print(f"[LLM] 无法初始化 Anthropic 客户端（{e}）；hybrid 模式将中止，"
              "加 --allow-rule-fallback 可纯规则继续。")
        return None


def merge_accumulator(delta, path):
    """证据级累积总库：按 point_id 合并（新结果覆盖旧的）。"""
    if os.path.exists(path) and len(delta):
        try:
            old = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
            # 兼容旧累积库：缺少新列时补空串，避免升级后 KeyError
            for col in ec.POINT_COLS:
                if col not in old.columns:
                    old[col] = ""
                if col not in delta.columns:
                    delta[col] = ""
            merged = pd.concat([old, delta.astype(str)], ignore_index=True)
            merged = merged.drop_duplicates(subset=["point_id"], keep="last")
            return merged[ec.POINT_COLS]
        except Exception as e:
            print(f"[累积] 读取失败，按本次写入：{e}")
    return delta


def write_extraction_report(path, stats, extractor, llm_degraded, llm_fatal):
    """写 extraction_report.md：LLM 尝试/成功/失败、token、成本、是否降级。"""
    lines = [
        "# ③ 数值抽取报告",
        "",
        f"- 抽取模式：`{extractor}`",
        f"- 文章数：{stats['articles']}",
        f"- 规则层点数：{stats['rule_pts']}（含指标句 {stats['indicator_sents']}）",
        f"- 指标/口径/单位不合格丢弃：{stats['indicator_dropped']}",
        f"- LLM 尝试/成功/缓存/失败：{stats['llm_attempted']}/"
        f"{stats['llm_succeeded']}/{stats['llm_cached']}/{stats['llm_failed']}",
        f"- LLM 错误类型：{stats['llm_error_types']}",
        f"- token 用量：{stats['llm_usage']}",
        f"- 累计延迟：{stats['llm_latency_ms']} ms",
        f"- 估算成本：${stats['llm_cost_usd']:.4f}",
        f"- 是否降级为纯规则：{'是' if llm_degraded else '否'}",
        f"- 是否触发熔断：{'是' if llm_fatal else '否'}",
    ]
    try:
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[警告] 写抽取报告失败：{exc}")


def run_once(run_dir, paths, cache_dir, out_root, extractor="hybrid", limit=None,
             allow_rule_fallback=False, max_llm_failure_rate=ec.LLM_MAX_FAILURE_RATE):
    print("=" * 60)
    print(f"{cfg.TOPIC_LABEL} · ③ 数值数据点抽取（A101，{extractor}）")
    print("=" * 60)

    articles = apply_curated_filter(load_cached_articles(cache_dir), out_root)
    if limit:
        articles = articles[:limit]
    print(f"缓存文章：{len(articles)} 篇")
    if not articles:
        print("[中止] body_cache 为空，请先运行 techfin_fetch_body.py。")
        return 0

    idx = build_region_index()
    client = build_llm_client() if extractor == "hybrid" else None
    llm_unavailable = (extractor == "hybrid" and client is None)
    llm_cache_dir = str(out_root / cfg.LLM_CACHE_DIR)
    calls_log = str(out_root / cfg.LLM_CALLS_LOG)

    all_points = []
    all_dropped = []
    stats = {
        "articles": len(articles), "rule_pts": 0, "llm_calls": 0,
        "llm_pts": 0, "indicator_sents": 0, "rule_dropped": 0,
        "indicator_dropped": 0,
        "llm_attempted": 0, "llm_succeeded": 0, "llm_cached": 0, "llm_failed": 0,
        "llm_error_types": {}, "llm_usage": {}, "llm_cost_usd": 0.0,
        "llm_latency_ms": 0,
    }
    llm_fatal = False
    for n, art in enumerate(articles, 1):
        rule_pts, windows, n_ind = rule_extract_article(art, idx, cache_dir)
        stats["rule_pts"] += len(rule_pts)
        stats["indicator_sents"] += n_ind
        rule_pts, dropped_rule = validate_points(rule_pts)
        stats["indicator_dropped"] += len(dropped_rule)
        all_dropped.extend(dropped_rule)

        llm_pts, llm_ran = [], False
        if client is not None and windows and not llm_fatal and _llm_needed(rule_pts, windows, n_ind):
            rel = "\n---\n".join(dict.fromkeys(windows))[:LLM_TEXT_MAX]
            stats["llm_attempted"] += 1
            try:
                data, llm_meta = call_llm(rel, art.get("meta", {}), client,
                                          llm_cache_dir, calls_log)
                llm_ran = True
                if llm_meta.get("cached"):
                    stats["llm_cached"] += 1
                else:
                    stats["llm_calls"] += 1
                    stats["llm_succeeded"] += 1
                usage = llm_meta.get("usage") or {}
                for k, v in usage.items():
                    stats["llm_usage"][k] = stats["llm_usage"].get(k, 0) + int(v or 0)
                stats["llm_cost_usd"] += float(llm_meta.get("cost_usd", 0) or 0)
                stats["llm_latency_ms"] += int(llm_meta.get("latency_ms", 0) or 0)
                llm_pts = llm_points(data, art, art.get("meta", {}), idx)
                llm_pts, dropped_llm = validate_points(llm_pts)
                stats["indicator_dropped"] += len(dropped_llm)
                all_dropped.extend(dropped_llm)
                stats["llm_pts"] += len(llm_pts)
            except Exception as e:
                name = type(e).__name__
                stats["llm_failed"] += 1
                stats["llm_error_types"][name] = stats["llm_error_types"].get(name, 0) + 1
                print(f"    [LLM异常] {name}: {e}")
                if _llm_error_info(e)[1]:
                    print("[错误] LLM 认证/权限/参数错误，停止后续 LLM 调用。")
                    llm_fatal = True
                elif (stats["llm_attempted"] >= 5 and
                      stats["llm_failed"] / stats["llm_attempted"] > max_llm_failure_rate):
                    print(f"[错误] LLM 失败率 {stats['llm_failed']}/{stats['llm_attempted']} "
                          f"超过阈值 {max_llm_failure_rate:.0%}，停止后续 LLM 调用。")
                    llm_fatal = True

        pts, dropped = merge_points(rule_pts, llm_pts, llm_ran)
        stats["rule_dropped"] += dropped
        all_points.extend(pts)
        if n % 20 == 0 or n == len(articles):
            print(f"  ... 已处理 {n}/{len(articles)} 篇（LLM 调用 {stats['llm_calls']} 次）")

    llm_degraded = (llm_unavailable or
                    (client is not None and stats["llm_attempted"] > 0
                     and stats["llm_succeeded"] == 0 and stats["llm_cached"] == 0))
    if llm_degraded:
        if allow_rule_fallback:
            print("[警告] LLM 0 次成功，已按 --allow-rule-fallback 降级为规则层。")
        else:
            print("[错误] LLM 0 次成功；hybrid 模式不静默降级。"
                  "如确认要用纯规则，加 --allow-rule-fallback。")

    dropped_df = pd.DataFrame(all_dropped, columns=DROPPED_COLS)
    dropped_df.to_csv(paths["dropped"], index=False, encoding="utf-8-sig")
    write_extraction_report(paths["report"], stats, extractor, llm_degraded, llm_fatal)

    if not all_points:
        print(f"\n[结果] 未抽到任何数据点（指标/口径/单位不合格丢弃 {len(dropped_df)} 条，"
              f"见 {paths['dropped']}）。")
        return 3 if (llm_degraded and not allow_rule_fallback) else 0

    delta = pd.DataFrame(all_points)[ec.POINT_COLS]
    resolved = resolve_points(all_points)

    delta.to_csv(paths["delta"], index=False, encoding="utf-8-sig")
    resolved.to_csv(paths["resolved"], index=False, encoding="utf-8-sig")
    merged = merge_accumulator(delta, str(out_root / cfg.POINTS_ACCUMULATOR))
    merged.to_csv(str(out_root / cfg.POINTS_ACCUMULATOR), index=False, encoding="utf-8-sig")

    print("\n" + "-" * 60)
    print(f"[汇总] 规则层抽点 {stats['rule_pts']}（其中有 LLM 时被规则层误报丢弃 "
          f"{stats['rule_dropped']}），LLM 尝试 {stats['llm_attempted']} 次"
          f"（成功 {stats['llm_succeeded']} / 缓存 {stats['llm_cached']} / 失败 {stats['llm_failed']}，"
          f"抽出 {stats['llm_pts']} 点）；"
          f"指标/口径/单位不合格丢弃 {stats['indicator_dropped']} 条")
    print(f"  LLM token：{stats['llm_usage']}；估算成本 ${stats['llm_cost_usd']:.4f}")
    print(f"  证据级数据点（本轮）：{len(delta)}  → {paths['delta']}")
    print(f"  聚合级数据格（本轮）：{len(resolved)}  → {paths['resolved']}")
    print(f"  丢弃审计：{len(dropped_df)} 条  → {paths['dropped']}")
    print(f"  抽取报告：{paths['report']}")
    print(f"  证据级累积总库：{len(merged)}  → {out_root / cfg.POINTS_ACCUMULATOR}")
    if len(resolved):
        n_act = int((resolved["canonical_value"].astype(str).str.strip() != "").sum())
        print(f"  其中可定主值（有 actual 口径）：{n_act} 格")
    print("  [提示] context_type=planned/target 的数据点已保留但未参与主值；")
    print("         运行 verify_points.py 查看精度抽检报告与多源冲突组。")
    print("-" * 60)
    return 3 if (llm_degraded and not allow_rule_fallback) else 0


def artifact_paths(run_dir):
    p = cfg.POINTS_PREFIX
    return {
        "log": os.path.join(run_dir, p + "log"),
        "delta": os.path.join(run_dir, p + "delta.csv"),
        "resolved": os.path.join(run_dir, p + "resolved.csv"),
        "dropped": os.path.join(run_dir, p + "dropped.csv"),
        "report": os.path.join(run_dir, "extraction_report.md"),
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · ③ 数值数据点抽取（A101）")
    ap.add_argument("--output-dir", help="数据根目录（覆盖 topic_config.DATA_ROOT）")
    ap.add_argument("--extractor", choices=["rule", "hybrid"], default="hybrid",
                    help="rule=只跑规则层（零 LLM 成本）；hybrid=规则+LLM 兜底（默认）")
    ap.add_argument("--allow-rule-fallback", action="store_true",
                    help="hybrid 模式 LLM 全部失败时允许纯规则继续（默认退出码 3）")
    ap.add_argument("--max-llm-failure-rate", type=float, default=ec.LLM_MAX_FAILURE_RATE,
                    help=f"LLM 失败率超过该值即熔断（默认 {ec.LLM_MAX_FAILURE_RATE}）")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 篇缓存文章（试验用）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    topic_dir = _TOPIC_DIR
    out_root = pipeline_root(topic_dir, args.output_dir)
    cache_dir = str(out_root / cfg.BODY_CACHE_DIR)

    run_dir = new_pipeline_run("extract", override=args.output_dir, topic_dir=topic_dir)
    paths = artifact_paths(run_dir)
    status = "failed"
    rc = 1
    try:
        with run_logging(paths["log"]):
            print(f"数据根目录：{out_root}")
            rc = run_once(run_dir, paths, cache_dir, out_root,
                          extractor=args.extractor, limit=args.limit,
                          allow_rule_fallback=args.allow_rule_fallback,
                          max_llm_failure_rate=args.max_llm_failure_rate)
        status = "ok" if rc == 0 else "failed"
    finally:
        finish_run_manifest(run_dir, status=status)
        print(f"[归档] 本轮产物目录：{run_dir}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
