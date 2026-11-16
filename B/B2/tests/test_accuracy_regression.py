"""
B/B2 离线准确度回归（不联网、不连数据库）

运行：
  python B/B2/tests/test_accuracy_regression.py

覆盖：
  - B201 案例数 vs B202 金额；B203 上市企业数 vs B204 募资额；
  - 一句多点的数字归属；
  - 县域替代口径（天使轮/A轮、新三板/区域股权交易中心）；
  - 县域不适用门禁（B202 is_county_applicable=0）；
  - 检索计划规模与产物名。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

TOPIC_DIR = Path(__file__).resolve().parents[1]      # B/B2
REPO_ROOT = Path(__file__).resolve().parents[3]      # 仓库根
sys.path[:0] = [
    str(REPO_ROOT),
    str(TOPIC_DIR / "config"),
    str(TOPIC_DIR / "extract"),
    str(TOPIC_DIR / "clean"),
    str(TOPIC_DIR / "load"),
]

import pandas as pd  # noqa: E402

# noinspection PyUnresolvedReferences
import topic_config as cfg  # noqa: E402
# noinspection PyUnresolvedReferences
import extract_config as ec  # noqa: E402
# noinspection PyUnresolvedReferences
import region_rules as rr  # noqa: E402
# noinspection PyUnresolvedReferences
import techfin_extract_points as ep  # noqa: E402
# noinspection PyUnresolvedReferences
import techfin_load_to_db as loader  # noqa: E402

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


def test_b201_b202() -> None:
    print("[1] B201 案例数 vs B202 金额")
    p = one("荔湾区股权投资案例120起")
    check("B201 案例数", p.get("level3_code") == "B201" and p.get("metric") == "count",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("B201 单位 起", p.get("unit") == "起" and float(p.get("value_num", 0)) == 120,
          f"{p.get('value_num')}/{p.get('unit')}")

    p = one("荔湾区股权投资金额300亿元")
    check("B202 金额", p.get("level3_code") == "B202" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("B202 数值 300 亿元", float(p.get("value_num", 0)) == 300 and p.get("unit") == "亿元",
          f"{p.get('value_num')}/{p.get('unit')}")

    pts = extract("荔湾区创业投资案例数50起，投资金额120亿元")
    got = sorted((x.get("level3_code"), x.get("metric")) for x in pts)
    check("一句两点 B201+B202", got == [("B201", "count"), ("B202", "amount")], str(got))

    check("天使轮融资替代口径命中 B201",
          one("荔湾区天使轮融资案例3起").get("level3_code") == "B201")


def test_b203_b204() -> None:
    print("[2] B203 上市企业数 vs B204 募资额")
    pts = extract("荔湾区科创板上市企业5家，募资20亿元")
    got = sorted((x.get("level3_code"), x.get("value_num")) for x in pts)
    check("一句两点 B203+B204", got == [("B203", 5.0), ("B204", 20.0)], str(got))
    p = one("荔湾区新增上市企业3家")
    check("B203 计数", p.get("level3_code") == "B203" and p.get("unit") == "家",
          f"{p.get('level3_code')}/{p.get('unit')}")
    p = one("荔湾区科创板首发募资15亿元")
    check("B204 金额", p.get("level3_code") == "B204" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("新三板挂牌替代口径命中 B203",
          one("荔湾区新三板挂牌企业8家").get("level3_code") == "B203")


def test_metric_and_units() -> None:
    print("[3] 口径映射")
    check("起 -> count", ep._metric_of("起", ec.INDICATOR_SCOPE["B201"]) == "count")
    check("亿元 -> amount", ep._metric_of("亿元", ec.INDICATOR_SCOPE["B202"]) == "amount")
    check('没有把"笔"塞进 UNIT_METRIC', "笔" not in ec.UNIT_METRIC)


def test_plan_and_schema() -> None:
    print("[4] 检索计划与 LLM 契约")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×4×3=1488", len(county) == 1488, len(county))
    check("无地区 49 条", len(none) == 49, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"B201", "B202", "B203", "B204"},
          sorted({p["level3_code"] for p in none}))
    check("无地区锚点为空", all(p["anchor_terms"] == () for p in none))

    b201_county = [p["query"] for p in county if p["level3_code"] == "B201"][:3]
    check("B201 县级走天使轮/A轮替代口径",
          any("天使轮" in q for q in b201_county) and any("A轮" in q for q in b201_county),
          str(b201_county))
    b203_county = [p["query"] for p in county if p["level3_code"] == "B203"][:3]
    check("B203 县级走新三板/区域股权替代口径",
          any("新三板" in q or "区域股权" in q for q in b203_county),
          str(b203_county))

    metric_enum = (ec.LLM_SCHEMA["properties"]["data_points"]["items"]
                   ["properties"]["metric"]["enum"])
    check("LLM metric enum", metric_enum == ["count", "amount", "unspecified"], metric_enum)
    check("产物名为 b2 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_b2_points.csv"
          and cfg.LLM_CACHE_DIR == "b2_llm_cache"
          and cfg.LLM_CALLS_LOG == "b2_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")


def test_county_gate() -> None:
    print("[5] B202 县域不适用门禁")

    def dims():
        m = {"level3_code": "B202", "measure_code": "amount",
             "standard_unit": "亿元", "value_kind": "numeric", "is_primary": 1}
        return loader.Dims(
            regions={"440103": {"region_code": "440103", "region_name": "荔湾区",
                                "region_level": 3, "is_active": 1,
                                "province_code": "440000"}},
            measures={("B202", "amount"): m}, primary={"B202": m},
            channels={"news_corpus": {"default_quality": 3}},
            indexes={"B202": {"status": "active", "is_county_applicable": 0,
                              "county_replacement": "县域内企业获得的天使轮/A轮融资"}},
            other_county_names={}, region_names={"440103": {"荔湾区", "荔湾"}},
        )

    def codes(allow: bool) -> list[str]:
        args = SimpleNamespace(
            only_level3=set(), province_code=None, block_level3=set(),
            allow_inactive_index=False, include_non_actual=False,
            include_missing=True, allow_region_query=False,
            allow_breakdown=False, allow_partial_batch=True,
            allow_approx=True, allow_low_confidence_year=True,
            allow_unverified_evidence=False,
            allow_county_not_applicable=allow,
        )
        row = pd.Series({
            "region_code": "440103", "level3_code": "B202", "metric": "amount",
            "stat_year": "2023", "canonical_value": "50", "chosen_reason": "→ actual",
            "source_article_ids": "a1", "n_sources": "1", "agreement": "False",
            "value_min": "", "value_max": "", "stat_year_src": "text",
        })
        ctx = {"row": row, "row_no": 1, "input_file": "x", "run_dir": "",
               "args": args, "dims": dims(), "points": {}, "curated": {},
               "load_stats": loader.LoadStats()}
        _, issues = loader.evaluate_row(row, ctx)
        return [i.code for i in issues]

    check("默认拒绝县级 B202", codes(False)[:1] == ["COUNTY_NOT_APPLICABLE"], str(codes(False)))
    check("显式放行县级 B202", "COUNTY_NOT_APPLICABLE" not in codes(True), str(codes(True)))


def main() -> int:
    test_b201_b202()
    test_b203_b204()
    test_metric_and_units()
    test_plan_and_schema()
    test_county_gate()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
