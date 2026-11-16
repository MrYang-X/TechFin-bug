"""
B/B1 离线准确度回归（不联网、不连数据库）

运行：
  python B/B1/tests/test_accuracy_regression.py

覆盖：
  - B101 贷款余额 vs B102 增速的互斥（含 growth 口径）；
  - B103 质押融资额 vs 项目数；B104 信用贷款占比 vs 余额；
  - 金额单位不在抽取层换算；
  - 检索计划规模、县域前 3 条、无地区锚点为空；
  - 产物名与 LLM schema 枚举。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # B/B1
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


def test_b101_b102() -> None:
    print("[1] B101 贷款余额 vs B102 增速")
    p = one("2024年末，荔湾区科技型企业贷款余额1200亿元")
    check("B101 单点", p.get("level3_code") == "B101", p.get("level3_code"))
    check("B101 口径 amount", p.get("metric") == "amount", p.get("metric"))
    check("B101 单位亿元", p.get("unit") == "亿元", p.get("unit"))
    check("B101 数值 1200", float(p.get("value_num", 0)) == 1200, p.get("value_num"))

    pts = extract("荔湾区科技贷款余额同比增长20.5%")
    check("增速只产 B102", len(pts) == 1 and pts[0].get("level3_code") == "B102",
          str([(x.get("level3_code"), x.get("value_num")) for x in pts]))
    check("B102 口径 growth", pts and pts[0].get("metric") == "growth",
          pts[0].get("metric") if pts else "")
    check("B102 单位 %", pts and pts[0].get("unit") == "%", pts[0].get("unit") if pts else "")
    check("B102 数值 20.5", pts and float(pts[0].get("value_num", 0)) == 20.5,
          pts[0].get("value_num") if pts else "")

    p = one("荔湾区高新技术企业贷款增长18%")
    check("增长写法命中 B102", p.get("level3_code") == "B102" and p.get("metric") == "growth",
          f"{p.get('level3_code')}/{p.get('metric')}")

    # 多子句写法：规则层只能拿到 B101，B102 依赖 LLM 兜底（已知召回缺口）
    pts = extract("荔湾区科技型企业贷款余额1200亿元，同比增长20.5%")
    check("多子句规则层只产 B101",
          len(pts) == 1 and pts[0].get("level3_code") == "B101",
          str([(x.get("level3_code"), x.get("value_num")) for x in pts]))


def test_b103_b104() -> None:
    print("[2] B103 质押融资额 / B104 信用贷款占比")
    p = one("荔湾区知识产权质押融资金额50亿元")
    check("B103 金额", p.get("level3_code") == "B103" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("B103 数值 50 亿元", float(p.get("value_num", 0)) == 50 and p.get("unit") == "亿元",
          f"{p.get('value_num')}/{p.get('unit')}")
    check("质押项目数不产 B103", extract("荔湾区知识产权质押融资项目120个") == [])

    p = one("荔湾区科技型企业信用贷款占比35.2%")
    check("B104 占比", p.get("level3_code") == "B104" and p.get("metric") == "share",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("B104 单位 %", p.get("unit") == "%" and float(p.get("value_num", 0)) == 35.2,
          f"{p.get('value_num')}/{p.get('unit')}")
    check("信用贷款余额不产 B104", extract("荔湾区信用贷款余额300亿元") == [])


def test_metric_and_units() -> None:
    print("[3] growth 口径与金额单位")
    b102_scope = ec.INDICATOR_SCOPE["B102"]
    b104_scope = ec.INDICATOR_SCOPE["B104"]
    check("_metric_of(%) + B102 -> growth",
          ep._metric_of("%", b102_scope) == "growth", ep._metric_of("%", b102_scope))
    check("_metric_of(%) + B104 -> share",
          ep._metric_of("%", b104_scope) == "share", ep._metric_of("%", b104_scope))

    p = one("荔湾区科技型企业贷款余额1200万元")
    check("万元保留原文单位", p.get("unit") == "万元" and float(p.get("value_num", 0)) == 1200,
          f"{p.get('value_num')}/{p.get('unit')}")
    p = one("荔湾区科技型企业贷款余额1.2万亿元")
    check("万亿元保留原文单位", p.get("unit") == "万亿元" and float(p.get("value_num", 0)) == 1.2,
          f"{p.get('value_num')}/{p.get('unit')}")


def test_plan_and_schema() -> None:
    print("[4] 检索计划与 LLM 契约")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×4×3=1488", len(county) == 1488, len(county))
    check("无地区 42 条", len(none) == 42, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"B101", "B102", "B103", "B104"},
          sorted({p["level3_code"] for p in none}))
    check("无地区锚点为空", all(p["anchor_terms"] == () for p in none))
    per = {}
    for p in county:
        per[p["level3_code"]] = per.get(p["level3_code"], 0) + 1
    check("县级每指标 124×3", all(v == 372 for v in per.values()), per)

    metric_enum = (ec.LLM_SCHEMA["properties"]["data_points"]["items"]
                   ["properties"]["metric"]["enum"])
    check("LLM metric enum 含 growth", "growth" in metric_enum, metric_enum)
    check("产物名为 b1 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_b1_points.csv"
          and cfg.LLM_CACHE_DIR == "b1_llm_cache"
          and cfg.LLM_CALLS_LOG == "b1_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")


def main() -> int:
    test_b101_b102()
    test_b103_b104()
    test_metric_and_units()
    test_plan_and_schema()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
