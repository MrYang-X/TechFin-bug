"""
B/B4 离线准确度回归（不联网、不连数据库）

运行：
  python B/B4/tests/test_accuracy_regression.py

覆盖：
  - B401 移动支付交易额 vs 交易笔数；
  - B402/B403 只采语料：有清洗词族、无抽取契约、不产数据点；
  - 检索计划规模与产物名。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # B/B4
REPO_ROOT = Path(__file__).resolve().parents[3]      # 仓库根
sys.path[:0] = [
    str(REPO_ROOT),
    str(TOPIC_DIR / "config"),
    str(TOPIC_DIR / "extract"),
    str(TOPIC_DIR / "clean"),
]

# noinspection PyUnresolvedReferences
import topic_config as cfg  # noqa: E402
# noinspection PyUnresolvedReferences
import extract_config as ec  # noqa: E402
# noinspection PyUnresolvedReferences
import region_rules as rr  # noqa: E402
# noinspection PyUnresolvedReferences
import techfin_extract_points as ep  # noqa: E402
# noinspection PyUnresolvedReferences
import techfin_curate as curate  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILS.append(name)


META = {
    "region_code": "440103", "region_name": "荔湾区",
    "title": "荔湾区统计", "date": "2025-06-01",
}


def extract(sentence: str) -> list[dict]:
    idx = rr.build_region_index()
    return ep.extract_sentence(sentence, sentence, 0, "body",
                               {"article_id": "test"}, META, idx, "")


def one(sentence: str) -> dict:
    pts = extract(sentence)
    return pts[0] if len(pts) == 1 else {}


def test_b401() -> None:
    print("[1] B401 移动支付交易额")
    p = one("2024年，荔湾区移动支付交易额5000亿元")
    check("B401 金额", p.get("level3_code") == "B401" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("B401 数值 5000 亿元", float(p.get("value_num", 0)) == 5000 and p.get("unit") == "亿元",
          f"{p.get('value_num')}/{p.get('unit')}")
    check("交易笔数不产 B401", extract("荔湾区移动支付交易笔数3亿笔") == [])
    check("万亿元保留原文单位",
          one("荔湾区移动支付交易额1.2万亿元").get("unit") == "万亿元")


def test_corpus_only() -> None:
    print("[2] B402/B403 只采语料")
    check("抽取契约只有 B401", set(ec.INDICATOR_SCOPE) == {"B401"},
          sorted(ec.INDICATOR_SCOPE))
    check("指标体系含 B401-B403",
          {i["level3_code"] for i in cfg.INDEX_KEYWORDS["B4"]["indicators"]}
          == {"B401", "B402", "B403"},
          sorted(i["level3_code"] for i in cfg.INDEX_KEYWORDS["B4"]["indicators"]))
    check("B402 指数不产数据点", extract("荔湾区数字普惠金融指数为120.5") == [])
    check("B403 风控描述不产数据点",
          extract("荔湾区该行运用智能风控模型提升审批效率") == [])

    fams = curate.build_indicator_families()
    check("B402 有清洗词族", bool(fams.get("B402", {}).get("terms")),
          fams.get("B402", {}).get("terms"))
    b403_terms = fams.get("B403", {}).get("terms") or []
    check("B403 有无空格词族", any(" " not in t for t in b403_terms), b403_terms)
    check("清洗层能判 B403 相关",
          bool(curate.find_indicator_hits("该行用大数据风控模型提升审批效率", fams)),
          curate.find_indicator_hits("该行用大数据风控模型提升审批效率", fams))


def test_plan_and_schema() -> None:
    print("[3] 检索计划与 LLM 契约")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×3×3=1116", len(county) == 1116, len(county))
    check("无地区 32 条", len(none) == 32, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"B401", "B402", "B403"},
          sorted({p["level3_code"] for p in none}))
    check("无地区锚点为空", all(p["anchor_terms"] == () for p in none))

    indicator_enum = (ec.LLM_SCHEMA["properties"]["data_points"]["items"]
                      ["properties"]["indicator"]["enum"])
    check("LLM indicator enum 只有 B401", indicator_enum == ["B401"], indicator_enum)
    check("产物名为 b4 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_b4_points.csv"
          and cfg.LLM_CACHE_DIR == "b4_llm_cache"
          and cfg.LLM_CALLS_LOG == "b4_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")


def main() -> int:
    test_b401()
    test_corpus_only()
    test_plan_and_schema()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
