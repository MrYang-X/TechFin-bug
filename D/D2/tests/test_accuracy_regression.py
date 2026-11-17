"""
D/D2 离线准确度回归（不联网、不连数据库）

运行：
  python D/D2/tests/test_accuracy_regression.py

覆盖：
  - D201 R&D人员全时当量（万人年 → 人年）；
  - D202 普通高等学校数量（所）；
  - D203 只采语料：有清洗词族、无抽取契约、不产数据点；
  - 人员数/在校生/招生不产点；
  - 检索计划规模、LLM enum、产物名；
  - loader 的 人年 解析与单位兼容性。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # D/D2
REPO_ROOT = Path(__file__).resolve().parents[3]      # 仓库根
sys.path[:0] = [
    str(REPO_ROOT),
    str(TOPIC_DIR / "config"),
    str(TOPIC_DIR / "extract"),
    str(TOPIC_DIR / "clean"),
    str(TOPIC_DIR / "load"),
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


def test_d201() -> None:
    print("[1] D201 R&D人员全时当量")
    p = one("2024年，荔湾区R&D人员全时当量12.5万人年")
    check("D201 口径 amount", p.get("level3_code") == "D201" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("D201 单位 人年", p.get("unit") == "人年", p.get("unit"))
    check("D201 万人年换算 125000", float(p.get("value_num", 0)) == 125000,
          p.get("value_num"))
    check("R&D人员数不产 D201",
          extract("2024年，荔湾区R&D人员10万人") == [])


def test_d202() -> None:
    print("[2] D202 普通高等学校数量")
    p = one("2024年，荔湾区普通高等学校数量16所")
    check("D202 口径 count", p.get("level3_code") == "D202" and p.get("metric") == "count",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("D202 数值 16 所", float(p.get("value_num", 0)) == 16 and p.get("unit") == "所",
          f"{p.get('value_num')}/{p.get('unit')}")
    check("在校生不产 D202",
          extract("2024年，荔湾区高校在校生10万人") == [])


def test_corpus_only() -> None:
    print("[3] D203 只采语料")
    check("抽取契约只有 D201/D202", set(ec.INDICATOR_SCOPE) == {"D201", "D202"},
          sorted(ec.INDICATOR_SCOPE))
    check("指标体系含 D201/D202/D203",
          {i["level3_code"] for i in cfg.INDEX_KEYWORDS["D2"]["indicators"]}
          == {"D201", "D202", "D203"},
          sorted(i["level3_code"] for i in cfg.INDEX_KEYWORDS["D2"]["indicators"]))
    check("D203 不产数据点",
          extract("2024年，荔湾区数字经济发展指数85.2") == [])

    fams = curate.build_indicator_families()
    for code in ("D201", "D202", "D203"):
        check(f"{code} 有清洗词族", bool(fams.get(code, {}).get("terms")),
              fams.get(code, {}).get("terms"))
    hits = curate.find_indicator_hits("R&D人员全时当量", fams)
    check("清洗层能判 D201 相关", any(c == "D201" for c, _ in hits), hits)


def test_metric_and_units() -> None:
    print("[4] 人年口径与 loader 解析")
    scope = ec.INDICATOR_SCOPE["D201"]
    check("_metric_of(人年) -> amount", ep._metric_of("人年", scope) == "amount",
          ep._metric_of("人年", scope))

    parsed = loader.parse_value_text("12.5万人年")
    check("loader 解析 12.5万人年", parsed is not None, parsed)
    if parsed:
        check("loader 数值 125000", float(parsed.num) == 125000, parsed.num)
        check("loader 单位 人年", parsed.unit == "人年", parsed.unit)
    check("loader 人年/人年 兼容", loader.unit_compatible("人年", "人年") == (True, ""),
          loader.unit_compatible("人年", "人年"))
    check("loader 人/人年 拒绝", loader.unit_compatible("人", "人年") == (False, "UNIT_MISMATCH"),
          loader.unit_compatible("人", "人年"))


def test_plan_and_schema() -> None:
    print("[5] 检索计划与 LLM 契约")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×3×3=1116", len(county) == 1116, len(county))
    check("无地区 36 条", len(none) == 36, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"D201", "D202", "D203"},
          sorted({p["level3_code"] for p in none}))
    check("无地区锚点为空", all(p["anchor_terms"] == () for p in none))
    per = {}
    for p in county:
        per[p["level3_code"]] = per.get(p["level3_code"], 0) + 1
    check("县级每指标 124×3", all(v == 372 for v in per.values()), per)

    indicator_enum = (ec.LLM_SCHEMA["properties"]["data_points"]["items"]
                      ["properties"]["indicator"]["enum"])
    metric_enum = (ec.LLM_SCHEMA["properties"]["data_points"]["items"]
                   ["properties"]["metric"]["enum"])
    check("LLM indicator enum 只有 D201/D202", indicator_enum == ["D201", "D202"], indicator_enum)
    check("LLM metric enum 含 amount/count",
          {"amount", "count"}.issubset(set(metric_enum)), metric_enum)
    check("产物名为 d2 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_d2_points.csv"
          and cfg.POINTS_PREFIX == "points_"
          and cfg.LLM_CACHE_DIR == "d2_llm_cache"
          and cfg.LLM_CALLS_LOG == "d2_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.POINTS_PREFIX}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")


def main() -> int:
    test_d201()
    test_d202()
    test_corpus_only()
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
