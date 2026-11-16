"""
C/C2 离线准确度回归（不联网、不连数据库）

运行：
  python C/C2/tests/test_accuracy_regression.py

覆盖：
  - C201 高新技术产业产值占比 vs 产值绝对量；
  - C202 科技服务业增加值（含科学研究和技术服务业代理）vs 营业收入；
  - 检索计划规模、无地区检索式、产物名与 LLM schema 枚举。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # C/C2
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


def test_c201_c202() -> None:
    print("[1] C201 产值占比 / C202 科技服务业增加值")
    p = one("2024年，荔湾区高新技术产业产值占规上工业产值比重达55.3%")
    check("C201 单点", p.get("level3_code") == "C201", p.get("level3_code"))
    check("C201 口径 share", p.get("metric") == "share", p.get("metric"))
    check("C201 单位 %", p.get("unit") == "%", p.get("unit"))
    check("C201 数值 55.3", float(p.get("value_num", 0)) == 55.3, p.get("value_num"))
    check("产值绝对量不产 C201",
          extract("荔湾区高新技术产业产值1200亿元") == [])
    check("高技术制造业代理不产 C201",
          extract("荔湾区高技术制造业增加值占规上工业增加值比重48.2%") == [])

    p = one("荔湾区科技服务业增加值350亿元")
    check("C202 金额", p.get("level3_code") == "C202" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("C202 数值 350 亿元",
          float(p.get("value_num", 0)) == 350 and p.get("unit") == "亿元",
          f"{p.get('value_num')}/{p.get('unit')}")
    p = one("荔湾区科学研究和技术服务业增加值420亿元")
    check("C202 接受科学研究和技术服务业代理",
          p.get("level3_code") == "C202" and float(p.get("value_num", 0)) == 420,
          f"{p.get('level3_code')}/{p.get('value_num')}")
    check("营业收入不产 C202",
          extract("荔湾区科技服务业营业收入500亿元") == [])


def test_metric_and_units() -> None:
    print("[2] share 口径与金额单位")
    c201_scope = ec.INDICATOR_SCOPE["C201"]
    c202_scope = ec.INDICATOR_SCOPE["C202"]
    check("_metric_of(%) + C201 -> share",
          ep._metric_of("%", c201_scope) == "share", ep._metric_of("%", c201_scope))
    check("_metric_of(亿元) + C202 -> amount",
          ep._metric_of("亿元", c202_scope) == "amount", ep._metric_of("亿元", c202_scope))

    p = one("荔湾区科技服务业增加值1200万元")
    check("万元保留原文单位",
          p.get("unit") == "万元" and float(p.get("value_num", 0)) == 1200,
          f"{p.get('value_num')}/{p.get('unit')}")
    p = one("荔湾区科技服务业增加值1.2万亿元")
    check("万亿元保留原文单位",
          p.get("unit") == "万亿元" and float(p.get("value_num", 0)) == 1.2,
          f"{p.get('value_num')}/{p.get('unit')}")


def test_plan_and_schema() -> None:
    print("[3] 检索计划与 LLM 契约")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×2×3=744", len(county) == 744, len(county))
    check("无地区 24 条", len(none) == 24, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"C201", "C202"},
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
    check("LLM metric enum 含 share", "share" in metric_enum, metric_enum)
    check("LLM indicator enum 为 C201/C202",
          indicator_enum == ["C201", "C202"], indicator_enum)
    check("产物名为 c2 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_c2_points.csv"
          and cfg.LLM_CACHE_DIR == "c2_llm_cache"
          and cfg.LLM_CALLS_LOG == "c2_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")


def main() -> int:
    test_c201_c202()
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
