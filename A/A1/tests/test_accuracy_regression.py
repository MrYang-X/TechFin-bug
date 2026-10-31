"""
离线准确度回归（不联网、不连数据库）

运行：
  python A/A1/tests/test_accuracy_regression.py

覆盖本次改造的核心断言：
  - A103 系统性错标：规则层只产 A101，指标-口径-单位不一致点被丢弃；
  - 地区归属：全市 → 父级市码；query/无证据不回退；短别名阻断；
  - 子句级数字绑定：254/421/57 只保留 254；拆分口径标 breakdown；
  - 数值去重：突破1000家只产 1 点；
  - 相关性词距、URL 标题回退、loader 专家渠道与截断计数。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

TOPIC_DIR = Path(__file__).resolve().parents[1]      # A/A1
REPO_ROOT = Path(__file__).resolve().parents[3]      # 仓库根
sys.path[:0] = [
    str(REPO_ROOT),
    str(TOPIC_DIR / "config"),
    str(TOPIC_DIR / "extract"),
    str(TOPIC_DIR / "clean"),
    str(TOPIC_DIR / "load"),
]

import pandas as pd  # noqa: E402

import crawler_common as cc  # noqa: E402
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


def extract(sentence: str, meta: dict, doc_before: str = "") -> list[dict]:
    idx = rr.build_region_index()
    return ep.extract_sentence(sentence, sentence, 0, "body",
                               {"article_id": "test"}, meta, idx, doc_before)


def test_a103_forced_to_a101() -> None:
    print("[1] A103 错标根治")
    meta = {
        "level3_code": "A103", "level3_name": "R&D经费支出占GDP比重",
        "region_code": "440103", "region_name": "荔湾区",
        "title": "广州市2023年统计公报", "date": "2024-01-01",
    }
    pts = extract("2023年，全市有效期内高新技术企业13171家", meta)
    check("只产 1 个点", len(pts) == 1, str(len(pts)))
    if pts:
        p = pts[0]
        check("指标码强制 A101", p["level3_code"] == "A101", p["level3_code"])
        check("全市映射到广州市", p["region_code"] == "440100", p["region_code"])
        check("地区来源 scope_city", p["region_from"] == "scope_city", p["region_from"])
        check("主值口径 total", p["aggregation_type"] == "total", p["aggregation_type"])
        check("年份 2023", str(p["stat_year"]) == "2023", str(p["stat_year"]))

    bad = dict(pts[0], level3_code="A103", metric="count", unit="家")
    valid, dropped = ep.validate_points([bad])
    check("A103+count 被丢弃", not valid and dropped
          and dropped[0]["drop_reason"] == "METRIC_INDICATOR_MISMATCH")


def test_clause_binding() -> None:
    print("[2] 子句级数字绑定与拆分口径")
    meta = {
        "region_code": "440103", "region_name": "荔湾区",
        "title": "荔湾区高新技术企业统计", "date": "2024-01-01",
    }
    pts = extract("2022年，荔湾区高新技术企业254家，科技型中小企业421家，市级研发机构57家", meta)
    check("421/57 被跨子句过滤", len(pts) == 1, str([p["value_text"] for p in pts]))
    if pts:
        check("保留 254 家", pts[0]["value_text"] == "254家", pts[0]["value_text"])

    pts = extract("2023年，广州市各区分别有高新技术企业3082家、147家、2315家", meta)
    cols = [(p["value_text"], p["aggregation_type"]) for p in pts]
    check("列表 3 个点", len(pts) == 3, str(cols))
    check("全部标 breakdown",
          all(p["aggregation_type"] == "breakdown" for p in pts), str(cols))

    pts = extract("2023年，花都区高新技术企业首次突破1000家", meta)
    check("突破1000家只 1 点", len(pts) == 1, str([p["value_text"] for p in pts]))


def test_region_aliases() -> None:
    print("[3] 地区别名与低置信回退")
    idx = rr.build_region_index()
    meta = {"region_code": "440103", "region_name": "荔湾区", "title": "无地区标题"}
    hit = rr.match_region("孙中山先生与中山大学", None, meta, idx)
    check("中山大学不命中", not hit.code, str(hit))
    hit = rr.match_region("粤语和粤菜", None, meta, idx)
    check("粤语/粤菜不命中", not hit.code, str(hit))
    meta2 = {"region_code": "440103", "region_name": "荔湾区", "title": "荔湾区统计"}
    hit = rr.match_region("2023年，全市高企13171家", 8, meta2, idx)
    check("全市 → 广州市", hit.code == "440100", str(hit))


def test_relevance_and_title() -> None:
    print("[4] 相关性与 URL 标题")
    text = "荔湾区发布通知。" + "无关内容" * 40 + "高新技术企业名单"
    check("词距过大不命中",
          not cc.is_relevant(text, "荔湾区 高新技术企业", (), (), max_token_gap=20))
    check("不做词距约束时命中",
          cc.is_relevant(text, "荔湾区 高新技术企业", (), (), max_token_gap=0))

    df = pd.DataFrame([{
        "article_id": "a", "title": "https://example.com/news/1",
        "summary": "荔湾区高新技术企业数量达302家", "source": "测试",
        "date_raw": "2024-01-01", "url": "https://example.com/news/1",
        "keyword": "高新技术企业", "engine": "baidu",
    }])
    out = cc.clean_dataframe(df)
    check("URL 标题回退摘要", len(out) == 1 and out.iloc[0]["title"].startswith("荔湾区"),
          str(out[["title"]].to_dict()))


def test_loader_helpers() -> None:
    print("[5] loader 辅助函数")
    check("专家 → expert",
          loader.map_channel({}, {"authority": "专家"}, {"authority": ""}) == "expert")
    stats = loader.LoadStats()
    ctx = {"load_stats": stats}
    clipped = loader._clip("x" * 300, 255, "value_text", ctx)
    check("截断长度 255", len(clipped) == 255, str(len(clipped)))
    check("截断计数", stats.truncated_fields.get("value_text") == 1,
          str(stats.truncated_fields))
    rec = loader.build_missing_record(
        {"region_code": "440103", "stat_year": "2023", "level3_code": "A101",
         "n_sources": "1", "agreement": "True", "stat_year_src": "text"},
        {"measure_code": "count"}, "actual", ctx=ctx,
    )
    check("missing 记录年份正确", rec["year"] == 2023, str(rec["year"]))
    rec_float = loader.build_missing_record(
        {"region_code": "440103", "stat_year": "2025.0", "level3_code": "A101",
         "n_sources": "1", "agreement": "True", "stat_year_src": "text"},
        {"measure_code": "count"}, "actual", ctx=ctx,
    )
    check("浮点年份落库为整数", rec_float["year"] == 2025, str(rec_float["year"]))
    point = {"level3_code": "A101", "region_code": "440103", "stat_year": "2025.0",
             "metric": "count", "value_num": "100", "value_text": "100家",
             "unit": "家", "context_type": "actual", "article_id": "a1",
             "authority": "官方", "confidence": "0.9", "publish_date": "2025-06-01"}
    matched, match_err = loader.match_evidence(
        {"region_code": "440103", "level3_code": "A101", "stat_year": "2025",
         "metric": "count", "canonical_value": "100", "source_article_ids": "a1"},
        {"a1": [point]}, "actual")
    check("年份新旧格式可匹配", matched is not None, match_err)


def test_loader_gates() -> None:
    print("[6] loader 门禁与过滤顺序")

    def dims(status: str = "active"):
        m = {"level3_code": "A101", "measure_code": "count",
             "standard_unit": "家", "value_kind": "count", "is_primary": 1}
        return loader.Dims(
            regions={"440103": {"region_code": "440103", "region_name": "荔湾区",
                                "region_level": 3, "is_active": 1,
                                "province_code": "440000"}},
            measures={("A101", "count"): m}, primary={"A101": m},
            channels={"news_corpus": {"default_quality": 3}},
            indexes={"A101": {"status": status}},
            other_county_names={}, region_names={"440103": {"荔湾区", "荔湾"}},
        )

    def args(**kw):
        base = dict(only_level3=set(), province_code=None, block_level3=set(),
                    allow_inactive_index=False, include_non_actual=False,
                    include_missing=True, allow_region_query=False,
                    allow_breakdown=False, allow_partial_batch=True,
                    allow_approx=True, allow_low_confidence_year=True,
                    allow_unverified_evidence=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def row(**kw):
        base = {"region_code": "440103", "level3_code": "A101", "metric": "count",
                "stat_year": "2023", "canonical_value": "", "chosen_reason": "→ actual",
                "source_article_ids": "a1", "n_sources": "1", "agreement": "False",
                "value_min": "", "value_max": "", "stat_year_src": "text"}
        base.update(kw)
        return pd.Series(base)

    def codes(r, d, a):
        ctx = {"row": r, "row_no": 1, "input_file": "x", "run_dir": "",
               "args": a, "dims": d, "points": {}, "curated": {},
               "load_stats": loader.LoadStats()}
        _, issues = loader.evaluate_row(r, ctx)
        return [i.code for i in issues]

    check("空年份不崩溃", codes(row(stat_year=""), dims(), args()) == ["YEAR_INVALID"])
    check("浮点年份不崩溃", codes(row(stat_year="2025.0"), dims(), args())
          == ["EMPTY_VALUE_MISSING"])
    check("retired 指标被拒", codes(row(canonical_value="100"), dims("retired"), args()) == ["INDEX_INACTIVE"])
    check("missing 不绕过 non-actual",
          codes(row(chosen_reason="→ planned"), dims(), args()) == ["NON_ACTUAL_CONTEXT"])
    check("显式 non-actual 才写 missing",
          codes(row(chosen_reason="→ planned"), dims(), args(include_non_actual=True))
          == ["EMPTY_VALUE_MISSING"])


def test_stat_year_normalization() -> None:
    print("[7] stat_year 落盘格式归一化")
    check("None → 空串", ep._normalize_year(None) == "", str(ep._normalize_year(None)))
    check("float → int", ep._normalize_year(2025.0) == 2025, str(ep._normalize_year(2025.0)))
    check("字符串浮点 → int", ep._normalize_year("2025.0") == 2025,
          str(ep._normalize_year("2025.0")))

    base = {
        "region_code": "440103", "region_name": "荔湾区", "city_name": "广州市",
        "region_from": "text", "region_confidence": "0.9", "region_evidence": "荔湾",
        "level3_code": "A101", "level3_name": "高新技术企业数量/密度",
        "metric": "count", "stat_year_src": "text", "year_confidence": "0.9",
        "year_evidence": "2025年", "aggregation_type": "total",
        "value_num": "100", "context_type": "actual", "authority": "官方",
        "confidence": "0.9", "publish_date": "2025-06-01", "article_id": "a1",
    }
    points = [dict(base, stat_year=2025), dict(base, stat_year=None, article_id="a2")]
    out = ep.resolve_points(points)
    years = out["stat_year"].tolist()
    check("resolved 年份无浮点格式",
          all(v == "" or isinstance(v, int) for v in years), str(years))


def main() -> int:
    test_a103_forced_to_a101()
    test_clause_binding()
    test_region_aliases()
    test_relevance_and_title()
    test_loader_helpers()
    test_loader_gates()
    test_stat_year_normalization()
    print("\n" + "=" * 50)
    if FAILS:
        print(f"[失败] {len(FAILS)} 项：{FAILS}")
        return 1
    print("[通过] 全部准确度回归通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
