"""
A/A3 离线准确度回归（不联网、不连数据库）

运行：
  python A/A3/tests/test_accuracy_regression.py

覆盖：
  - A301 占比（只取占比，不取绝对额/增速，支持前子句绑定）；
  - A302 资金池规模（不取贷款发放额/贷款余额，金额保留原文单位）；
  - loader 金额换算与 A302 存量门禁；
  - 检索计划（县级 6 条/单元、无地区 20 条、锚点为空）；
  - 清洗词族与 LLM schema 枚举。
"""

from __future__ import annotations

import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parents[1]      # A/A3
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


def test_a301() -> None:
    print("[1] A301 财政科技支出占比")
    p = one("2024年，荔湾区财政科技支出50亿元，占一般公共预算支出6.2%")
    check("A301 单点", bool(p), str(extract("2024年，荔湾区财政科技支出50亿元，占一般公共预算支出6.2%")))
    check("指标 A301", p.get("level3_code") == "A301", p.get("level3_code"))
    check("口径 share", p.get("metric") == "share", p.get("metric"))
    check("单位 %", p.get("unit") == "%", p.get("unit"))
    check("数值 6.2", abs(float(p.get("value_num", 0)) - 6.2) < 1e-9, str(p.get("value_num")))
    check("不把绝对额当占比", all(float(x.get("value_num", 0)) != 50 for x in
                                  extract("2024年，荔湾区财政科技支出50亿元，占一般公共预算支出6.2%")))
    check("纯支出额不产点", extract("荔湾区财政科技支出15.8亿元") == [])
    check("增速不产点", extract("荔湾区财政科技支出增长20%") == [])


def test_a302() -> None:
    print("[2] A302 风险补偿资金池规模")
    p = one("荔湾区科技信贷风险补偿资金池规模5亿元")
    check("A302 金额", p.get("level3_code") == "A302" and p.get("metric") == "amount",
          f"{p.get('level3_code')}/{p.get('metric')}")
    check("保留原文单位亿元", p.get("unit") == "亿元", p.get("unit"))
    check("数值 5", float(p.get("value_num", 0)) == 5, str(p.get("value_num")))
    check("贷款发放额不产 A302 点", extract("荔湾区发放科技贷款5亿元") == [])
    check("贷款余额不产 A302 点", extract("荔湾区科技企业贷款余额5亿元") == [])
    check("补偿金额不串成资金池规模",
          extract("荔湾区科技信贷风险补偿资金池累计补偿金额5000万元") == [])
    check("万元保留原文单位", one("荔湾区风险补偿资金池规模5000万元").get("unit") == "万元")


def test_loader_units() -> None:
    print("[3] loader 金额换算与门禁")
    check("1.2亿元 → 12000万元",
          abs(loader.convert_amount_to_standard(1.2, "亿元", "万元") - 12000) < 1e-6)
    check("5000万元 → 0.5亿元",
          abs(loader.convert_amount_to_standard(5000, "万元", "亿元") - 0.5) < 1e-9)
    check("1.2万亿元 → 1.2e8万元",
          abs(loader.convert_amount_to_standard(1.2, "万亿元", "万元") - 1.2e8) < 1e-3)
    check("空单位金额被拒", loader.unit_compatible("", "万元") == (False, "UNIT_MISSING"))
    check("A302 是存量指标", "A302" in loader.STOCK_INDICATORS)
    check("A301 不是存量指标", "A301" not in loader.STOCK_INDICATORS)
    check("百分比 0-100 兼容", loader.unit_compatible("%", "%")[0])


def test_plan() -> None:
    print("[4] 检索计划")
    regions = cfg.guangdong_regions(cities=["广州市"])
    plans = cfg.build_search_plan(["A3"], regions)
    check("广州 66 条", len(plans) == 66, str(len(plans)))
    a301 = [p for p in plans if p["level3_code"] == "A301"]
    a302 = [p for p in plans if p["level3_code"] == "A302"]
    check("A301 首条财政科技支出", "财政科技支出" in a301[0]["query"], a301[0]["query"])
    check("A302 首条科技信贷风险补偿", "科技信贷风险补偿" in a302[0]["query"], a302[0]["query"])
    no_region = cfg.build_search_plan(["A3"], [])
    check("无地区 20 条", len(no_region) == 20, str(len(no_region)))
    check("无地区锚点为空", all(not p["anchor_terms"] for p in no_region))


def test_schema_and_clean() -> None:
    print("[5] LLM schema 与清洗词族")
    props = ec.LLM_SCHEMA["properties"]["data_points"]["items"]["properties"]
    check("indicator 枚举完整",
          set(props["indicator"]["enum"]) == {"A301", "A302"},
          str(props["indicator"]["enum"]))
    check("metric 含 share/amount",
          {"share", "amount"} <= set(props["metric"]["enum"]),
          str(props["metric"]["enum"]))
    fams = curate.build_indicator_families()
    for code in ("A301", "A302"):
        check(f"{code} 词族非空", bool(fams[code]["terms"]), str(fams[code]["terms"]))
        check(f"{code} 单位非空", bool(fams[code]["units"]), str(fams[code]["units"]))
    check("A302 数值型报道计数",
          curate.count_number_statements("风险补偿资金池规模5亿元", fams) == 1)


def main() -> int:
    test_a301()
    test_a302()
    test_loader_units()
    test_plan()
    test_schema_and_clean()
    print("\n" + "=" * 50)
    if FAILS:
        print(f"[失败] {len(FAILS)} 项：{FAILS}")
        return 1
    print("[通过] A3 全部回归通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
