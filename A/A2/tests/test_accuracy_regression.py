"""
A/A2 离线准确度回归（不联网、不连数据库）

运行：
  python A/A2/tests/test_accuracy_regression.py

覆盖：
  - A201/A202/A203/A204 的规则抽取与口径/单位守卫；
  - 金额单位不在抽取层换算（由 loader 统一换算）；
  - A202 县域不适用门禁；
  - 检索计划（县域替代口径、密度优先、无地区锚点为空）；
  - 清洗词族与 LLM schema 枚举。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

TOPIC_DIR = Path(__file__).resolve().parents[1]      # A/A2
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
import techfin_curate as curate  # noqa: E402
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


def test_a201() -> None:
    print("[1] A201 本外币存款余额")
    p = one("2024年末，荔湾区金融机构本外币各项存款余额达1234.5亿元")
    check("A201 单点", bool(p), str(len(extract("2024年末，荔湾区金融机构本外币各项存款余额达1234.5亿元"))))
    check("指标 A201", p.get("level3_code") == "A201", p.get("level3_code"))
    check("口径 amount", p.get("metric") == "amount", p.get("metric"))
    check("单位亿元", p.get("unit") == "亿元", p.get("unit"))
    check("数值 1234.5", abs(float(p.get("value_num", 0)) - 1234.5) < 1e-9, str(p.get("value_num")))
    check("年份 2024", str(p.get("stat_year")) == "2024", str(p.get("stat_year")))

    p = one("荔湾区存款余额1200万元")
    check("万元保留原文单位", p.get("unit") == "万元" and float(p.get("value_num", 0)) == 1200,
          f"{p.get('value_num')}/{p.get('unit')}")
    p = one("荔湾区存款余额1.2万亿元")
    check("万亿元保留原文单位", p.get("unit") == "万亿元" and float(p.get("value_num", 0)) == 1.2,
          f"{p.get('value_num')}/{p.get('unit')}")
    check("增速不产 A201 点", extract("荔湾区存款余额增长8.5%") == [])
    pts = extract("荔湾区本外币各项存款余额5000亿元，贷款余额3000亿元")
    check("贷款余额不串到 A201",
          len(pts) == 1 and float(pts[0].get("value_num", 0)) == 5000,
          str([(p.get("level3_code"), p.get("value_num")) for p in pts]))


def test_a202() -> None:
    print("[2] A202 私募/创投机构数量")
    p = one("荔湾区私募基金管理人25家")
    check("A202 机构数量", p.get("level3_code") == "A202" and p.get("metric") == "count",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("数值 25 家", float(p.get("value_num", 0)) == 25 and p.get("unit") == "家",
          f"{p.get('value_num')}/{p.get('unit')}")
    check("新增流量不产 A202 点", extract("荔湾区新增私募基金管理人25家") == [])
    check("备案基金数量不产 A202 点", extract("荔湾区备案私募基金120家") == [])
    check("县域替代词可命中", one("荔湾区有限合伙企业80家").get("level3_code") == "A202")


def test_a203() -> None:
    print("[3] A203 管理资本量")
    p = one("荔湾区管理基金规模320亿元")
    check("A203 金额", p.get("level3_code") == "A203" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("数值 320 亿元", float(p.get("value_num", 0)) == 320 and p.get("unit") == "亿元",
          f"{p.get('value_num')}/{p.get('unit')}")
    check("投资金额不产 A203 点", extract("荔湾区股权投资金额50亿元") == [])
    check("县域替代词可命中", one("荔湾区基金实缴规模500亿元").get("level3_code") == "A203")


def test_a204() -> None:
    print("[4] A204 网点密度")
    p = one("荔湾区每万人拥有银行网点3.2个")
    check("A204 密度", p.get("level3_code") == "A204" and p.get("metric") == "density",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("单位个/万人", p.get("unit") == "个/万人", p.get("unit"))
    check("数值 3.2", abs(float(p.get("value_num", 0)) - 3.2) < 1e-9, str(p.get("value_num")))
    check("纯网点数量不产 A204 点", extract("荔湾区银行网点120个") == [])


def test_loader_units() -> None:
    print("[5] loader 金额换算与门禁")
    check("1.2亿元 → 12000万元",
          abs(loader.convert_amount_to_standard(1.2, "亿元", "万元") - 12000) < 1e-6)
    check("5000万元 → 0.5亿元",
          abs(loader.convert_amount_to_standard(5000, "万元", "亿元") - 0.5) < 1e-9)
    check("1.2万亿元 → 1.2e8万元",
          abs(loader.convert_amount_to_standard(1.2, "万亿元", "万元") - 1.2e8) < 1e-3)
    check("空单位金额被拒", loader.unit_compatible("", "亿元") == (False, "UNIT_MISSING"))
    check("个 → 个/万人 按密度接收",
          loader.unit_compatible("个", "个/万人") == (True, "UNIT_ASSUMED"))
    check("A302 是存量指标", "A302" in loader.STOCK_INDICATORS)


def test_loader_county_gate() -> None:
    print("[6] A202 县域不适用门禁")

    def dims():
        m = {"level3_code": "A202", "measure_code": "count",
             "standard_unit": "家", "value_kind": "count", "is_primary": 1}
        return loader.Dims(
            regions={"440103": {"region_code": "440103", "region_name": "荔湾区",
                                "region_level": 3, "is_active": 1,
                                "province_code": "440000"}},
            measures={("A202", "count"): m}, primary={"A202": m},
            channels={"news_corpus": {"default_quality": 3}},
            indexes={"A202": {"status": "active", "is_county_applicable": 0,
                              "county_replacement": "县域内注册的有限合伙企业/私募基金"}},
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
            "region_code": "440103", "level3_code": "A202", "metric": "count",
            "stat_year": "2023", "canonical_value": "25", "chosen_reason": "→ actual",
            "source_article_ids": "a1", "n_sources": "1", "agreement": "False",
            "value_min": "", "value_max": "", "stat_year_src": "text",
        })
        ctx = {"row": row, "row_no": 1, "input_file": "x", "run_dir": "",
               "args": args, "dims": dims(), "points": {}, "curated": {},
               "load_stats": loader.LoadStats()}
        _, issues = loader.evaluate_row(row, ctx)
        return [i.code for i in issues]

    check("默认拒绝县级 A202", codes(False)[:1] == ["COUNTY_NOT_APPLICABLE"], str(codes(False)))
    check("显式放行县级 A202", "COUNTY_NOT_APPLICABLE" not in codes(True), str(codes(True)))


def test_plan() -> None:
    print("[7] 检索计划")
    regions = cfg.guangdong_regions(cities=["广州市"])
    plans = cfg.build_search_plan(["A2"], regions)
    check("广州 132 条", len(plans) == 132, str(len(plans)))
    a202 = [p for p in plans if p["level3_code"] == "A202"]
    check("A202 县域替代口径", all(p["county_replacement"] for p in a202)
          and "有限合伙企业" in a202[0]["query"], a202[0]["query"])
    a204 = [p for p in plans if p["level3_code"] == "A204"]
    check("A204 密度优先", "每万人" in a204[0]["query"], a204[0]["query"])
    no_region = cfg.build_search_plan(["A2"], [])
    check("无地区 39 条", len(no_region) == 39, str(len(no_region)))
    check("无地区锚点为空", all(not p["anchor_terms"] for p in no_region))


def test_schema_and_clean() -> None:
    print("[8] LLM schema 与清洗词族")
    props = ec.LLM_SCHEMA["properties"]["data_points"]["items"]["properties"]
    check("indicator 枚举完整",
          set(props["indicator"]["enum"]) == {"A201", "A202", "A203", "A204"},
          str(props["indicator"]["enum"]))
    check("metric 含 amount/count/density",
          {"amount", "count", "density"} <= set(props["metric"]["enum"]),
          str(props["metric"]["enum"]))
    fams = curate.build_indicator_families()
    for code in ("A201", "A202", "A203", "A204"):
        check(f"{code} 词族非空", bool(fams[code]["terms"]), str(fams[code]["terms"]))
        check(f"{code} 单位非空", bool(fams[code]["units"]), str(fams[code]["units"]))
    check("A201 数值型报道计数",
          curate.count_number_statements("存款余额1234.5亿元", fams) == 1)


def main() -> int:
    test_a201()
    test_a202()
    test_a203()
    test_a204()
    test_loader_units()
    test_loader_county_gate()
    test_plan()
    test_schema_and_clean()
    print("\n" + "=" * 50)
    if FAILS:
        print(f"[失败] {len(FAILS)} 项：{FAILS}")
        return 1
    print("[通过] A2 全部回归通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
