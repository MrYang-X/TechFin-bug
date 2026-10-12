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
from datetime import datetime
from pathlib import Path

import pandas as pd

# ---- 引导：通用工具在仓库根，本脚本在 A/A1/ ----
for _p in Path(__file__).resolve().parents:
    if (_p / "crawler_common.py").exists():
        sys.path.insert(0, str(_p))
        break
else:
    raise RuntimeError("找不到 crawler_common.py，请确认它位于仓库根目录")

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import topic_config as cfg       # noqa: E402
import extract_config as ec      # noqa: E402
from crawler_common import (     # noqa: E402
    resolve_output_root,
    resolve_run_dir,
    run_logging,
)

LEVEL_RANK = {"province": 1, "city": 2, "county": 3}
LLM_TEXT_MAX = 6000     # 单次喂给 LLM 的相关片段上限（字符）


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== 地区索引 ====================

def build_region_index():
    """
    预构建 {别名: (区划码, 显示名, 级别, 所属市)}。来源：
      - GUANGDONG_REGIONS 展开的省/市/县三级，并补"去掉区/县/市后缀"的简称；
      - extract_config.REGION_ALIASES 手写补充。
    匹配时取【最深（最具体）命中】，故先按名称长度排好优先级即可。
    """
    idx = {}

    def add(alias, code, name, level, city):
        alias = (alias or "").strip()
        if not alias or alias in ec.AMBIGUOUS_REGION_NAMES:
            return
        idx.setdefault(alias, (code, name, level, city))

    add("广东省", "440000", "广东省", "province", "")
    for city, units in cfg.GUANGDONG_REGIONS.items():
        city_code = units[0][0][:4] + "00"
        add(city, city_code, city, "city", city)
        if len(city) >= 3:
            add(city[:-1], city_code, city, "city", city)
        for code, name in units:
            add(name, code, name, "county", city)
            if len(name) >= 3:
                add(name[:-1], code, name, "county", city)
    for alias, val in ec.REGION_ALIASES.items():
        idx[alias] = val
    return idx


def _region_from_meta(meta):
    """正文里没提地区时，退回采集时地区（较低置信，region_from='query'）。"""
    code = str(meta.get("region_code", "") or "").strip()
    name = str(meta.get("region_name", "") or "").strip()
    if not code:
        return "", "", "unknown", "", "query"
    if code.endswith("0000"):
        level = "province"
    elif code.endswith("00"):
        level = "city"
    else:
        level = "county"
    return code, name, level, "", "query"


def map_region(text, meta, idx):
    """文本 → (区划码, 名称, 级别, 市, 来源)。取最深命中；无则回退 meta。"""
    if text:
        hits = []
        for alias, val in idx.items():
            pos = text.find(alias)
            if pos >= 0:
                hits.append((LEVEL_RANK.get(val[2], 0), len(alias), pos, val))
        if hits:
            hits.sort(key=lambda h: (-h[0], -h[1], h[2]))
            v = hits[0][3]
            return v[0], v[1], v[2], v[3], "text"
    return _region_from_meta(meta)


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


# ==================== 规则层抽取 ====================

def _make_point(article, meta, region, value_num, value_text, unit, metric,
                stat_year, stat_period, ctype, cue, snippet, locator,
                method, confidence, llm=None):
    code, name, level, city, rfrom = region
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
        "level3_code": meta.get("level3_code", "") or "A101",
        "level3_name": meta.get("level3_name", ""),
        "metric": metric,
        "region_code": code, "region_name": name, "region_level": level,
        "city_name": city, "region_from": rfrom,
        "value_num": value_num, "value_text": value_text, "unit": unit or "",
        "stat_year": stat_year, "stat_period": stat_period,
        "context_type": ctype, "context_cue": cue,
        "evidence_snippet": snippet[:300],
        "evidence_source": locator[0], "evidence_locator": locator[1],
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


def _guess_year(sent, window):
    m = _YEAR_RE.search(sent) or _YEAR_RE.search(window)
    return int(m.group(1)) if m else None


def extract_sentence(sent, window, offset, label, article, meta, idx):
    """对单个含指标词的句子跑规则匹配，返回数据点列表。"""
    term = find_indicator(sent)
    points = []
    ctype, cue = classify_context(sent)
    region = map_region(sent, meta, idx)
    year = _guess_year(sent, window)
    period = f"{year}年" if year else ""
    src = ("body", f"char:{offset}") if label == "body" else (label, "")

    for m in ec.NUM_UNIT_RE.finditer(sent):
        unit = m.group("unit")
        num = m.group("num")
        mag = m.group("mag")
        # 距离门槛：数字要挨着指标词，否则多半不是高企数量
        d = _min_distance(sent, term, m.start())
        if d > 60:
            continue
        val = _num_value(num, mag)
        vtext = m.group(0).strip()
        conf = 0.9 if d <= 30 else 0.65
        if ctype == "unknown":
            conf -= 0.15
        if mag in ("余", "多"):
            conf -= 0.15
        points.append(_make_point(
            article, meta, region, val, vtext, unit, _metric_of(unit),
            year, period, ctype, cue, sent, src, "rule", max(conf, 0.2)))

    # "近/超/逾/约 N 家"：数值保留、标记近似
    for m in ec.APPROX_RE.finditer(sent):
        d = _min_distance(sent, term, m.start())
        if d > 60:
            continue
        points.append(_make_point(
            article, meta, region, _num_value(m.group("num"), None),
            m.group(0).strip(), m.group("unit"), _metric_of(m.group("unit")),
            year, period, ctype, cue, sent, src, "rule", 0.5))

    return points


def collect_sources(article):
    """一篇文章的文本源列表：正文 + 各附件（只取有文本的）。"""
    sources = [("body", article.get("body_text", "") or "")]
    for att in article.get("attachments", []) or []:
        if not att.get("status") == "ok":
            continue
        txt = ""
        tp = att.get("text_path", "")
        if tp and os.path.exists(tp):
            try:
                txt = open(tp, encoding="utf-8").read()
            except OSError:
                txt = ""
        if txt.strip():
            fname = att.get("filename", "attachment")
            sources.append((f"attachment:{fname}", txt))
    return sources


def rule_extract_article(article, idx):
    """规则层抽一篇文章。返回 (points, windows, indicator_sents 数)。"""
    meta = article.get("meta", {}) or {}
    points, windows = [], []
    n_ind_sents = 0
    for label, text in collect_sources(article):
        sents = split_sentences(text)
        for i, (s, off) in enumerate(sents):
            if not find_indicator(s):
                continue
            n_ind_sents += 1
            window = " ".join(x for x, _ in sents[max(0, i - 1):i + 2])
            windows.append(window)
            points.extend(extract_sentence(s, window, off, label, article, meta, idx))
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


def call_llm(rel_text, meta, client, cache_dir, calls_log):
    """
    调用 Sonnet 5.5 抽取（结构化输出强约束 JSON）。
    注意：**不传 temperature**——Sonnet 5.5 对非默认 temperature 会返回 400；
    可靠性由 output_config.format 的 JSON schema 保证。结果按 key 缓存、并记审计日志。
    """
    key = _cache_key(str(meta.get("article_id", "")), rel_text)
    cache_path = os.path.join(cache_dir, f"{key}.json")
    if os.path.exists(cache_path):
        try:
            return json.load(open(cache_path, encoding="utf-8"))
        except Exception:
            pass

    user_text = (f"采集时地区：{meta.get('region_name', '')}（{meta.get('region_code', '')}）\n\n"
                 f"正文相关片段：\n{rel_text}")
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
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text) if text else {"data_points": []}
    data["_model"] = ec.LLM_MODEL
    data["_prompt_version"] = ec.PROMPT_VERSION

    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    try:
        with open(calls_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "key": key, "model": ec.LLM_MODEL, "prompt_version": ec.PROMPT_VERSION,
                "ts": now_str(), "raw": data}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return data


def llm_points(data, article, meta, idx):
    """把 LLM 返回的 data_points 换成统一的 point dict。"""
    out = []
    llm = {"model": data.get("_model", ec.LLM_MODEL),
           "prompt_version": data.get("_prompt_version", ec.PROMPT_VERSION)}
    for dp in data.get("data_points", []):
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
            str(dp.get("evidence_snippet", "") or ""),
            ("body", ""), "llm", float(dp.get("confidence", 0.6) or 0.6), llm=llm))
    return out


# ==================== 合并与去重 ====================

def point_id_of(p):
    raw = "|".join(str(p.get(k, "")) for k in (
        "article_id", "level3_code", "region_code", "stat_year",
        "value_text", "evidence_snippet", "context_type"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _point_key(p):
    """两条数据点是否"同一件事"的判据（跨规则层/LLM 层对齐用）。"""
    return (p["region_code"], p["value_text"], p["context_type"])


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
    for (rcode, l3, year, metric), g in df.groupby(
            ["region_code", "level3_code", "stat_year", "metric"], dropna=False):
        actual = g[g["context_type"] == "actual"]
        pool = actual if len(actual) else g
        # 择优：官方 > 媒体；再比置信度；再比发布时间新
        pool = pool.assign(_auth=pool["authority"].map({"官方": 0, "媒体": 1}).fillna(2))
        pool = pool.sort_values(by=["_auth", "confidence", "publish_date"],
                                ascending=[True, False, False])
        chosen = pool.iloc[0]
        vals = pd.to_numeric(g["_val"], errors="coerce").dropna()
        aids = [a for a in g["article_id"].astype(str).unique() if a]
        n_uniq = vals.nunique()
        rows.append({
            "region_code": rcode, "region_name": g["region_name"].iloc[0],
            "city_name": g["city_name"].iloc[0],
            "level3_code": l3, "level3_name": g["level3_name"].iloc[0],
            "metric": metric, "stat_year": year,
            "canonical_value": chosen["value_num"] if len(actual) else "",
            "n_sources": len(aids),
            "n_support": len(actual),
            "source_article_ids": ",".join(aids),
            "value_min": (vals.min() if len(vals) else ""),
            "value_max": (vals.max() if len(vals) else ""),
            "agreement": ("True" if n_uniq <= 1 else "False"),
            "chosen_reason": ("取实际口径(" + str(chosen["authority"]) + ")→"
                              if len(actual) else "无实际口径，仅 planned/target/decline→")
                             + str(chosen["context_type"]),
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
            "level3_code", "level3_name", "region_code", "region_name")}
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
    """惰性构造 Anthropic 客户端；不可用时返回 None（降级为纯规则）。"""
    try:
        import anthropic
    except Exception:
        print("[LLM] 未安装 anthropic（pip install anthropic），本轮回退纯规则。")
        return None
    try:
        return anthropic.Anthropic()
    except Exception as e:
        print(f"[LLM] 无法初始化 Anthropic 客户端（{e}），本轮回退纯规则。")
        return None


def merge_accumulator(delta, path):
    """证据级累积总库：按 point_id 合并（新结果覆盖旧的）。"""
    if os.path.exists(path) and len(delta):
        try:
            old = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
            merged = pd.concat([old, delta.astype(str)], ignore_index=True)
            merged = merged.drop_duplicates(subset=["point_id"], keep="last")
            return merged[ec.POINT_COLS]
        except Exception as e:
            print(f"[累积] 读取失败，按本次写入：{e}")
    return delta


def run_once(run_dir, paths, cache_dir, out_root, extractor="hybrid", limit=None):
    print("=" * 60)
    print(f"{cfg.TOPIC_LABEL} · ③ 数值数据点抽取（A101，{extractor}）")
    print("=" * 60)

    articles = apply_curated_filter(load_cached_articles(cache_dir), out_root)
    if limit:
        articles = articles[:limit]
    print(f"缓存文章：{len(articles)} 篇")
    if not articles:
        print("[中止] body_cache 为空，请先运行 techfin_fetch_body.py。")
        return

    idx = build_region_index()
    client = build_llm_client() if extractor == "hybrid" else None
    llm_cache_dir = str(out_root / cfg.LLM_CACHE_DIR)
    calls_log = str(out_root / cfg.LLM_CALLS_LOG)

    all_points = []
    stats = {"articles": len(articles), "rule_pts": 0, "llm_calls": 0,
             "llm_pts": 0, "indicator_sents": 0, "rule_dropped": 0}
    for n, art in enumerate(articles, 1):
        rule_pts, windows, n_ind = rule_extract_article(art, idx)
        stats["rule_pts"] += len(rule_pts)
        stats["indicator_sents"] += n_ind

        llm_pts, llm_ran = [], False
        if client is not None and windows and _llm_needed(rule_pts, windows, n_ind):
            rel = "\n---\n".join(dict.fromkeys(windows))[:LLM_TEXT_MAX]
            try:
                data = call_llm(rel, art.get("meta", {}), client,
                                llm_cache_dir, calls_log)
                llm_ran = True
                stats["llm_calls"] += 1
                llm_pts = llm_points(data, art, art.get("meta", {}), idx)
                stats["llm_pts"] += len(llm_pts)
            except Exception as e:
                print(f"    [LLM异常] {type(e).__name__}: {e}")

        pts, dropped = merge_points(rule_pts, llm_pts, llm_ran)
        stats["rule_dropped"] += dropped
        all_points.extend(pts)
        if n % 20 == 0 or n == len(articles):
            print(f"  ... 已处理 {n}/{len(articles)} 篇（LLM 调用 {stats['llm_calls']} 次）")

    if not all_points:
        print("\n[结果] 未抽到任何数据点。")
        return

    delta = pd.DataFrame(all_points)[ec.POINT_COLS]
    resolved = resolve_points(all_points)

    delta.to_csv(paths["delta"], index=False, encoding="utf-8-sig")
    resolved.to_csv(paths["resolved"], index=False, encoding="utf-8-sig")
    merged = merge_accumulator(delta, str(out_root / cfg.POINTS_ACCUMULATOR))
    merged.to_csv(str(out_root / cfg.POINTS_ACCUMULATOR), index=False, encoding="utf-8-sig")

    print("\n" + "-" * 60)
    print(f"[汇总] 规则层抽点 {stats['rule_pts']}（其中有 LLM 时被规则层误报丢弃 "
          f"{stats['rule_dropped']}），LLM 调用 {stats['llm_calls']} 次"
          f"（抽出 {stats['llm_pts']} 点）")
    print(f"  证据级数据点（本轮）：{len(delta)}  → {paths['delta']}")
    print(f"  聚合级数据格（本轮）：{len(resolved)}  → {paths['resolved']}")
    print(f"  证据级累积总库：{len(merged)}  → {out_root / cfg.POINTS_ACCUMULATOR}")
    if len(resolved):
        n_act = int((resolved["canonical_value"].astype(str).str.strip() != "").sum())
        print(f"  其中可定主值（有 actual 口径）：{n_act} 格")
    print("  [提示] context_type=planned/target 的数据点已保留但未参与主值；")
    print("         运行 verify_points.py 查看精度抽检报告与多源冲突组。")
    print("-" * 60)


def artifact_paths(run_dir):
    p = cfg.POINTS_PREFIX
    return {
        "log": os.path.join(run_dir, p + "log"),
        "delta": os.path.join(run_dir, p + "delta.csv"),
        "resolved": os.path.join(run_dir, p + "resolved.csv"),
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · ③ 数值数据点抽取（A101）")
    ap.add_argument("--output-dir", help="输出根目录（覆盖 topic_config.OUTPUT_ROOT）")
    ap.add_argument("--extractor", choices=["rule", "hybrid"], default="hybrid",
                    help="rule=只跑规则层（零 LLM 成本）；hybrid=规则+LLM 兜底（默认）")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 篇缓存文章（试验用）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    topic_dir = Path(__file__).resolve().parent
    out_root = resolve_output_root(topic_dir, cfg.OUTPUT_ROOT, args.output_dir)
    cache_dir = str(out_root / cfg.BODY_CACHE_DIR)

    run_dir = resolve_run_dir(out_root)
    paths = artifact_paths(run_dir)
    try:
        with run_logging(paths["log"]):
            print(f"输出根目录：{out_root}")
            run_once(run_dir, paths, cache_dir, out_root,
                     extractor=args.extractor, limit=args.limit)
    finally:
        print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
