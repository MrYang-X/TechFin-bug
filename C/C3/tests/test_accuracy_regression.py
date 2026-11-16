"""
C/C3 离线准确度回归（不联网、不连数据库）

运行：
  python C/C3/tests/test_accuracy_regression.py

覆盖：
  - C301 独角兽 vs C304 瞪羚；合并口径「独角兽、瞪羚企业共 X 家」不产点；
  - C302 营收增长率 vs C303 利润增长率；
  - 金额/估值/新增不产数量点；
  - 检索计划规模、无地区检索式、产物名、LLM schema 与 C304 种子。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # C/C3
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


def test_c301_c304() -> None:
    print("[1] C301 独角兽 / C304 瞪羚")
    p = one("2024年，荔湾区独角兽企业20家")
    check("C301 单点", p.get("level3_code") == "C301", p.get("level3_code"))
    check("C301 口径 count", p.get("metric") == "count", p.get("metric"))
    check("C301 单位 家", p.get("unit") == "家", p.get("unit"))
    check("C301 数值 20", float(p.get("value_num", 0)) == 20, p.get("value_num"))

    p = one("荔湾区瞪羚企业35家")
    check("C304 单点", p.get("level3_code") == "C304", p.get("level3_code"))
    check("C304 口径 count", p.get("metric") == "count", p.get("metric"))
    check("C304 数值 35", float(p.get("value_num", 0)) == 35, p.get("value_num"))

    check("合并口径不产 C301/C304",
          extract("荔湾区独角兽、瞪羚企业共50家") == [])
    check("分列表述分别归属",
          [(x.get("level3_code"), float(x.get("value_num", 0)))
           for x in extract("荔湾区独角兽企业20家、瞪羚企业35家")]
          == [("C301", 20.0), ("C304", 35.0)])
    check("估值不产数量点",
          extract("荔湾区独角兽企业估值10亿美元") == [])
    check("新增不产存量数量点",
          extract("荔湾区新增独角兽企业5家") == [])
    check("潜在独角兽不产 C301",
          extract("荔湾区潜在独角兽企业8家") == [])


def test_c302_c303() -> None:
    print("[2] C302 营收增长率 / C303 利润增长率")
    p = one("荔湾区高新技术企业营业收入同比增长8.5%")
    check("C302 growth", p.get("level3_code") == "C302" and p.get("metric") == "growth",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("C302 单位 %", p.get("unit") == "%" and float(p.get("value_num", 0)) == 8.5,
          f"{p.get('value_num')}/{p.get('unit')}")

    p = one("荔湾区高新技术企业利润总额同比增长12.3%")
    check("C303 growth", p.get("level3_code") == "C303" and p.get("metric") == "growth",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("C303 单位 %", p.get("unit") == "%" and float(p.get("value_num", 0)) == 12.3,
          f"{p.get('value_num')}/{p.get('unit')}")

    check("营收金额不产 C302",
          extract("荔湾区高新技术企业营业收入1200亿元") == [])
    check("利润金额不产 C303",
          extract("荔湾区高新技术企业利润总额120亿元") == [])


def test_plan_and_schema() -> None:
    print("[3] 检索计划、LLM 契约与 C304 种子")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×4×3=1488", len(county) == 1488, len(county))
    check("无地区 48 条", len(none) == 48, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"C301", "C304", "C302", "C303"},
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
    check("LLM metric enum 含 count/growth",
          "count" in metric_enum and "growth" in metric_enum, metric_enum)
    check("LLM indicator enum 为 C301/C304/C302/C303",
          indicator_enum == ["C301", "C304", "C302", "C303"], indicator_enum)
    check("产物名为 c3 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_c3_points.csv"
          and cfg.LLM_CACHE_DIR == "c3_llm_cache"
          and cfg.LLM_CALLS_LOG == "c3_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")

    seed = (REPO_ROOT / "db" / "21_seed_index.sql").read_text(encoding="utf-8")
    loader_src = (TOPIC_DIR / "load" / "techfin_load_to_db.py").read_text(encoding="utf-8")
    check("DB 种子含 C304", "'C304'" in seed)
    check("loader STOCK_INDICATORS 含 C304", '"C304"' in loader_src)


def main() -> int:
    test_c301_c304()
    test_c302_c303()
    test_plan_and_schema()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
