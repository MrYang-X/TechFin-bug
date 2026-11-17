"""
D/D1 离线准确度回归（不联网、不连数据库）

运行：
  python D/D1/tests/test_accuracy_regression.py

覆盖：
  - D102 服务中心数量（count）与覆盖率（coverage）的抽取；
  - "家"不产可入库点（loader 标准单位是"个"）；
  - D101 只采语料：有清洗词族、无抽取契约、不产数据点；
  - 检索计划规模、LLM enum、产物名；
  - loader 的单位兼容性（个/家）。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # D/D1
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


def test_d102() -> None:
    print("[1] D102 数量 vs 覆盖率")
    p = one("2024年，荔湾区建成科技金融服务中心12个")
    check("D102 数量", p.get("level3_code") == "D102" and p.get("metric") == "count",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("D102 数值 12 个", float(p.get("value_num", 0)) == 12 and p.get("unit") == "个",
          f"{p.get('value_num')}/{p.get('unit')}")

    p = one("2024年，荔湾区科技金融服务中心覆盖率达85%")
    check("D102 覆盖率", p.get("level3_code") == "D102" and p.get("metric") == "coverage",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("D102 覆盖率数值 85%", float(p.get("value_num", 0)) == 85 and p.get("unit") == "%",
          f"{p.get('value_num')}/{p.get('unit')}")

    check("家不产可入库点",
          extract("2024年，荔湾区科技金融服务中心120家") == [])
    check("增速不产覆盖率",
          extract("2024年，荔湾区科技金融服务中心数量同比增长12%") == [])
    check("满意度不产覆盖率",
          extract("2024年，荔湾区科技金融服务中心满意度95%") == [])


def test_corpus_only() -> None:
    print("[2] D101 只采语料")
    check("抽取契约只有 D102", set(ec.INDICATOR_SCOPE) == {"D102"},
          sorted(ec.INDICATOR_SCOPE))
    check("指标体系含 D101/D102",
          {i["level3_code"] for i in cfg.INDEX_KEYWORDS["D1"]["indicators"]}
          == {"D101", "D102"},
          sorted(i["level3_code"] for i in cfg.INDEX_KEYWORDS["D1"]["indicators"]))
    check("D101 不产数据点",
          extract("2024年，荔湾区科技金融政策完备度评分85分") == [])

    fams = curate.build_indicator_families()
    check("D101 有清洗词族", bool(fams.get("D101", {}).get("terms")),
          fams.get("D101", {}).get("terms"))
    check("D102 有清洗词族", bool(fams.get("D102", {}).get("terms")),
          fams.get("D102", {}).get("terms"))
    hits = curate.find_indicator_hits("荔湾区科技金融服务中心挂牌", fams)
    check("清洗层能判 D102 相关", any(c == "D102" for c, _ in hits), hits)
    hits = curate.find_indicator_hits("科技金融政策完备度", fams)
    check("清洗层能判 D101 相关", any(c == "D101" for c, _ in hits), hits)
    # 大量新闻只写“科技金融政策”，不写“完备度”；必须有这个无空格短词，
    # 否则 D101 语料会被清洗层整批丢掉（实测 curated 为 0）。
    hits = curate.find_indicator_hits("广东发布科技金融政策", fams)
    check("清洗层能判 D101 政策短词", any(c == "D101" for c, _ in hits), hits)


def test_metric_and_units() -> None:
    print("[3] coverage 口径与单位兼容")
    scope = ec.INDICATOR_SCOPE["D102"]
    check("_metric_of(%) -> coverage", ep._metric_of("%", scope) == "coverage",
          ep._metric_of("%", scope))
    check("_metric_of(％) -> coverage", ep._metric_of("％", scope) == "coverage",
          ep._metric_of("％", scope))
    check("_metric_of(个) -> count", ep._metric_of("个", scope) == "count",
          ep._metric_of("个", scope))
    check("loader 个/个 兼容", loader.unit_compatible("个", "个") == (True, ""),
          loader.unit_compatible("个", "个"))
    check("loader 家/个 拒绝", loader.unit_compatible("家", "个") == (False, "UNIT_MISMATCH"),
          loader.unit_compatible("家", "个"))


def test_plan_and_schema() -> None:
    print("[4] 检索计划与 LLM 契约")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×2×3=744", len(county) == 744, len(county))
    check("无地区 24 条", len(none) == 24, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"D101", "D102"},
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
    check("LLM indicator enum 只有 D102", indicator_enum == ["D102"], indicator_enum)
    check("LLM metric enum 含 coverage", "coverage" in metric_enum, metric_enum)
    check("产物名为 d1 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_d1_points.csv"
          and cfg.POINTS_PREFIX == "points_"
          and cfg.LLM_CACHE_DIR == "d1_llm_cache"
          and cfg.LLM_CALLS_LOG == "d1_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.POINTS_PREFIX}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")


def main() -> int:
    test_d102()
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
