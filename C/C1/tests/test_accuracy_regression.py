"""
C/C1 离线准确度回归（不联网、不连数据库）

运行：
  python C/C1/tests/test_accuracy_regression.py

覆盖：
  - C101 PCT 申请量 vs 发明专利授权/有效量；
  - C102 技术合同成交额 vs 技术交易额/合同数/增速；
  - C103 每万人发明专利拥有量（件 → 件/万人的密度归一化）；
  - 检索计划规模、无地区检索式、产物名与 LLM schema 枚举。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # C/C1
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


def test_c101_c102_c103() -> None:
    print("[1] C101 PCT / C102 技术合同 / C103 发明专利密度")
    p = one("2024年，荔湾区PCT国际专利申请量320件")
    check("C101 单点", p.get("level3_code") == "C101", p.get("level3_code"))
    check("C101 口径 count", p.get("metric") == "count", p.get("metric"))
    check("C101 单位 件", p.get("unit") == "件", p.get("unit"))
    check("C101 数值 320", float(p.get("value_num", 0)) == 320, p.get("value_num"))
    check("PCT 授权量不产 C101", extract("荔湾区PCT专利授权量120件") == [])

    p = one("荔湾区技术合同成交额120亿元")
    check("C102 金额", p.get("level3_code") == "C102" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("C102 数值 120 亿元",
          float(p.get("value_num", 0)) == 120 and p.get("unit") == "亿元",
          f"{p.get('value_num')}/{p.get('unit')}")
    check("技术合同增速不产 C102",
          extract("荔湾区技术合同成交额同比增长12.5%") == [])
    check("技术交易额不产 C102",
          extract("荔湾区技术交易额80亿元") == [])

    p = one("荔湾区每万人发明专利拥有量52.3件")
    check("C103 密度", p.get("level3_code") == "C103" and p.get("metric") == "density",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("C103 单位 件/万人", p.get("unit") == "件/万人", p.get("unit"))
    check("C103 数值 52.3", float(p.get("value_num", 0)) == 52.3, p.get("value_num"))
    check("有效发明专利绝对量不产 C103",
          extract("荔湾区有效发明专利拥有量5000件") == [])
    check("高价值发明专利不产 C103",
          extract("荔湾区每万人口高价值发明专利拥有量18.2件") == [])


def test_metric_and_units() -> None:
    print("[2] density 口径与金额单位")
    c103_scope = ec.INDICATOR_SCOPE["C103"]
    check("_metric_of(件/万人) -> density",
          ep._metric_of("件/万人", c103_scope) == "density",
          ep._metric_of("件/万人", c103_scope))

    p = one("荔湾区技术合同成交额1200万元")
    check("万元保留原文单位",
          p.get("unit") == "万元" and float(p.get("value_num", 0)) == 1200,
          f"{p.get('value_num')}/{p.get('unit')}")
    p = one("荔湾区技术合同成交额1.2万亿元")
    check("万亿元保留原文单位",
          p.get("unit") == "万亿元" and float(p.get("value_num", 0)) == 1.2,
          f"{p.get('value_num')}/{p.get('unit')}")


def test_plan_and_schema() -> None:
    print("[3] 检索计划与 LLM 契约")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×3×3=1116", len(county) == 1116, len(county))
    check("无地区 36 条", len(none) == 36, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"C101", "C102", "C103"},
          sorted({p["level3_code"] for p in none}))
    check("无地区锚点为空", all(p["anchor_terms"] == () for p in none))
    per = {}
    for p in county:
        per[p["level3_code"]] = per.get(p["level3_code"], 0) + 1
    check("县级每指标 124×3", all(v == 372 for v in per.values()), per)

    metric_enum = (ec.LLM_SCHEMA["properties"]["data_points"]["items"]
                   ["properties"]["metric"]["enum"])
    indicator_enum = (ec.LLM_SCHEMA["properties"]["data_points"]["items"]
                      ["properties"]["indicator"]["enum"])
    check("LLM metric enum 含 density", "density" in metric_enum, metric_enum)
    check("LLM indicator enum 为 C101-C103",
          indicator_enum == ["C101", "C102", "C103"], indicator_enum)
    check("产物名为 c1 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_c1_points.csv"
          and cfg.LLM_CACHE_DIR == "c1_llm_cache"
          and cfg.LLM_CALLS_LOG == "c1_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")


def main() -> int:
    test_c101_c102_c103()
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
