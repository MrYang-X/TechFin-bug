"""
地区识别规则（③ 数值抽取与 ④ 语料清洗共用）

为什么要单独一个模块：
  - ③ 与 ④ 原先各有一份 `build_region_index` / `resolve_region`，逻辑漂移；
  - 旧逻辑在整句/全文里找"最深别名"，句中无地区时直接回退采集时的区县，
    导致"2023 年，全市高企 13171 家"被挂到荔湾区；
  - 本模块以**数字为锚点**取前 20 / 后 10 字窗口，先看显式别名，再看范围词，
    最后才允许低置信回退。

返回 `RegionHit`（7 元组）：
  code, name, level, city, source, confidence, evidence

source 取值：
  text            窗口内有明确地区别名
  scope_city      全市（映射到父级市码）
  scope_province  全省（440000）
  scope_national  全国（不归属任何市县）
  aggregate       各区/四区/合计/分别有（拆分或汇总口径，不归属具体县）
  query           无明确文本线索，回退采集时的地区（低置信）
  none            无法归属
"""

from __future__ import annotations

from collections import namedtuple

# noinspection PyUnresolvedReferences
import extract_config as ec
# noinspection PyUnresolvedReferences
import topic_config as cfg

RegionHit = namedtuple(
    "RegionHit", "code name level city source confidence evidence")

LEVEL_RANK = {"province": 1, "city": 2, "county": 3}

# 短别名后的阻断词：命中则视为非地区（如"粤语""中山大学"）
_BLOCK_AFTER = {
    "粤": ("语", "菜", "港澳", "东", "西", "北", "海", "剧", "曲"),
    "中山": ("大学", "路", "公园", "先生", "孙", "医", "纪念"),
}

_SCOPE_NATIONAL = ("全国",)
_SCOPE_AGGREGATE = ("各区", "四区", "合计", "总计", "分别有", "分别为", "其中")
_SCOPE_PROVINCE = ("全省", "全省域")
_SCOPE_CITY = ("全市", "全市域")

_CITY_NAMES: dict[str, str] | None = None


def city_names() -> dict[str, str]:
    """{地级市码: 市名}，由 GUANGDONG_REGIONS 派生。"""
    global _CITY_NAMES
    if _CITY_NAMES is None:
        names = {}
        for city, units in cfg.GUANGDONG_REGIONS.items():
            if units:
                names[units[0][0][:4] + "00"] = city
        _CITY_NAMES = names
    return _CITY_NAMES


def build_region_index() -> dict[str, tuple[str, str, str, str]]:
    """{别名: (区划码, 显示名, 级别, 所属市)}；③④ 共用。"""
    idx: dict[str, tuple[str, str, str, str]] = {}

    def add(alias: str, code: str, name: str, level: str, city: str) -> None:
        alias = (alias or "").strip()
        if not alias or alias in ec.AMBIGUOUS_REGION_NAMES:
            return
        idx.setdefault(alias, (code, name, level, city))

    add("广东省", "440000", "广东省", "province", "")
    for city, units in cfg.GUANGDONG_REGIONS.items():
        if not units:
            continue
        city_code = units[0][0][:4] + "00"
        add(city, city_code, city, "city", city)
        if len(city) >= 3:
            add(city[:-1], city_code, city, "city", city)
        for code, name in units:
            add(name, code, name, "county", city)
            if len(name) >= 3:
                add(name[:-1], code, name, "county", city)
    for code, qname in cfg.REGION_QUERY_NAME_OVERRIDES.items():
        add(qname, code, qname, "county", "")
    for alias, val in ec.REGION_ALIASES.items():
        # "粤" 单字歧义太大（粤语/粤菜/粤港澳），强制忽略；保留"广东"。
        if alias == "粤":
            continue
        idx[alias] = val
    return idx


def _blocked(alias: str, text: str, pos: int) -> bool:
    tail = text[pos + len(alias): pos + len(alias) + 2]
    return any(tail.startswith(word) for word in _BLOCK_AFTER.get(alias, ()))


def _find_explicit(window: str, idx: dict, anchor_in_window: int) -> RegionHit | None:
    """窗口内找显式别名；优先级：级别更深 > 别名更长 > 离锚点更近。"""
    hits = []
    for alias, val in idx.items():
        start = 0
        while True:
            pos = window.find(alias, start)
            if pos < 0:
                break
            if not _blocked(alias, window, pos):
                hits.append((
                    LEVEL_RANK.get(val[2], 0), len(alias),
                    abs(pos - anchor_in_window), pos, val, alias,
                ))
            start = pos + len(alias)
    if not hits:
        return None
    hits.sort(key=lambda h: (-h[0], -h[1], h[2], h[3]))
    _, _, _, _, val, alias = hits[0]
    return RegionHit(val[0], val[1], val[2], val[3], "text", 0.95, alias)


def fallback_region(meta: dict, text: str = "", source: str = "query",
                    idx: dict | None = None, require_evidence: bool = True) -> RegionHit:
    """
    无显式文本线索时回退采集地区。
    require_evidence=True 时，只有地区名出现在本句/标题（或标题含该地区别名）
    才允许回退，避免"文章别处提到某区"就全文挂到该区。
    """
    meta = meta or {}
    code = str(meta.get("region_code", "") or "").strip()
    name = str(meta.get("region_name", "") or "").strip()
    if not code:
        return RegionHit("", "", "unknown", "", "none", 0.0, "")
    if require_evidence:
        title = str(meta.get("title", "") or "")
        ok = bool(name and (name in (text or "") or name in title))
        if not ok and idx:
            ok = any(v[0] == code and alias in title for alias, v in idx.items())
        if not ok:
            return RegionHit("", "", "unknown", "", "none", 0.0, "")
    level = ("province" if code.endswith("0000")
             else ("city" if code.endswith("00") else "county"))
    confidence = {"query": 0.35, "meta": 0.5}.get(source, 0.3)
    return RegionHit(code, name, level, str(meta.get("city_name", "") or ""),
                     source, confidence, name)


def match_region(text: str, anchor_pos: int | None = None, meta: dict | None = None,
                 idx: dict | None = None) -> RegionHit:
    """
    文本 → RegionHit。anchor_pos 为数字在 text 中的位置；
    传 None 表示整段文本都是搜索范围（用于标题/摘要/前 500 字正文）。
    """
    text = text or ""
    meta = meta or {}
    idx = idx if idx is not None else build_region_index()
    if not text:
        return fallback_region(meta, "", idx=idx, require_evidence=False)

    if anchor_pos is None:
        window = text
        anchor_in_window = 0
    else:
        start = max(0, anchor_pos - 20)
        end = min(len(text), anchor_pos + 10)
        window = text[start:end]
        anchor_in_window = anchor_pos - start

    explicit = _find_explicit(window, idx, anchor_in_window)
    if explicit is not None:
        return explicit

    for word in _SCOPE_NATIONAL:
        if word in window:
            return RegionHit("", "全国", "country", "", "scope_national", 0.6, word)
    for word in _SCOPE_AGGREGATE:
        if word in window:
            return RegionHit("", "", "unknown", "", "aggregate", 0.5, word)
    for word in _SCOPE_PROVINCE:
        if word in window:
            return RegionHit("440000", "广东省", "province", "",
                             "scope_province", 0.9, word)
    for word in _SCOPE_CITY:
        if word in window:
            raw_code = str(meta.get("region_code", "") or "").strip()
            if not raw_code:
                return RegionHit("", "", "unknown", "", "aggregate", 0.5, word)
            if raw_code.endswith("0000") or raw_code.endswith("00"):
                city_code = raw_code
            else:
                city_code = raw_code[:4] + "00"
            city_name = city_names().get(city_code, "")
            if not city_name and raw_code.endswith("00"):
                city_name = str(meta.get("region_name", "") or "")
            return RegionHit(city_code, city_name, "city", city_name,
                             "scope_city", 0.9, word)

    return fallback_region(meta, text, idx=idx, require_evidence=True)


def resolve_article_region(title: str, summary: str, body: str, row: dict,
                           idx: dict | None = None) -> RegionHit:
    """
    文章级地区：标题 > 摘要 > 正文前 500 字。
    正文深层命中的区县不会覆盖标题/摘要里的市级口径。
    """
    idx = idx if idx is not None else build_region_index()
    meta = dict(row)
    for text, source in ((title, "title"), (summary, "summary"),
                         ((body or "")[:500], "body")):
        if not text:
            continue
        hit = match_region(text, None, meta, idx)
        if hit.code or hit.source in ("scope_national", "aggregate"):
            # 显式命中保留具体来源；范围词命中保留 scope_* 语义
            if hit.source == "text":
                return hit._replace(source=source)
            return hit
    return fallback_region(meta, title + summary + (body or "")[:500],
                           idx=idx, require_evidence=True)
