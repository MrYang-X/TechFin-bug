"""
B/B3 离线准确度回归（不联网、不连数据库）

运行：
  python B/B3/tests/test_accuracy_regression.py

覆盖：
  - B301 保费收入 vs 赔付/保额；
  - B302 发放额 vs 余额/贴息；
  - 检索计划规模与产物名。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # B/B3
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


def test_b301() -> None:
    print("[1] B301 科技保险保费收入")
    p = one("2024年，荔湾区科技保险保费收入8亿元")
    check("B301 金额", p.get("level3_code") == "B301" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("B301 数值 8 亿元", float(p.get("value_num", 0)) == 8 and p.get("unit") == "亿元",
          f"{p.get('value_num')}/{p.get('unit')}")
    check("赔付支出不产 B301", extract("荔湾区科技保险赔付支出2亿元") == [])
    check("保险金额不产 B301", extract("荔湾区科技保险保险金额50亿元") == [])


def test_b302() -> None:
    print("[2] B302 创业担保贷款发放额")
    p = one("2024年，荔湾区创业担保贷款发放额12亿元")
    check("B302 金额", p.get("level3_code") == "B302" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("B302 数值 12 亿元", float(p.get("value_num", 0)) == 12 and p.get("unit") == "亿元",
          f"{p.get('value_num')}/{p.get('unit')}")
    check("累计发放可命中 B302", one("荔湾区创业担保贷款累计发放10亿元").get("level3_code") == "B302")
    check("投放可命中 B302", one("荔湾区创业担保贷款投放8亿元").get("level3_code") == "B302")
    check("余额不产 B302", extract("荔湾区创业担保贷款余额120亿元") == [])
    check("贴息资金不产 B302", extract("荔湾区创业担保贷款贴息资金2亿元") == [])


def test_plan_and_schema() -> None:
    print("[3] 检索计划与 LLM 契约")
    regions = cfg.guangdong_regions()
    county = cfg.build_search_plan(regions=regions)
    none = cfg.build_search_plan()
    check("县级 124×2×3=744", len(county) == 744, len(county))
    check("无地区 21 条", len(none) == 21, len(none))
    check("指标编码齐全",
          {p["level3_code"] for p in none} == {"B301", "B302"},
          sorted({p["level3_code"] for p in none}))
    check("无地区锚点为空", all(p["anchor_terms"] == () for p in none))

    indicator_enum = (ec.LLM_SCHEMA["properties"]["data_points"]["items"]
                      ["properties"]["indicator"]["enum"])
    check("LLM indicator enum", indicator_enum == ["B301", "B302"], indicator_enum)
    check("产物名为 b3 专属",
          cfg.POINTS_ACCUMULATOR == "techfin_b3_points.csv"
          and cfg.LLM_CACHE_DIR == "b3_llm_cache"
          and cfg.LLM_CALLS_LOG == "b3_llm_calls.jsonl",
          f"{cfg.POINTS_ACCUMULATOR}/{cfg.LLM_CACHE_DIR}/{cfg.LLM_CALLS_LOG}")


def main() -> int:
    test_b301()
    test_b302()
    test_plan_and_schema()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
