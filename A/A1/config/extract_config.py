"""
数值抽取（techfin_extract_points.py）的**叶子配置**：规则正则 / 地区别名 / 指标定义 / LLM 契约（schema+prompt+模型）。

为什么单独一个文件、且不 import 任何本地模块（与 topic_config.py 同级）：
  抽取规则会高频迭代；把它与"抓取"（techfin_fetch_body.py）和"抽取主程序"（techfin_extract_points.py）分开，改规则只动这一个文件，
  重跑 techfin_extract_points.py 即生效，**不必重抓政府网站**（缓存见 body_cache/）。这正是抓取与抽取解耦的目的。

本次范围：只做 A101「高新技术企业数量/密度」。
"""

import re

# ==================== 指标词族（A101）====================
# 判定"这句话在谈高企数量"，取自 topic_config 的 A101 queries 并补数值报道写法。
A101_TERMS = [
    "国家高新技术企业", "国家级高新技术企业", "高新技术企业", "有效期内高新技术企业",
    "有效高新技术企业", "高新技术企业数量", "高企数量", "高企",
]

# 指标-口径-单位契约：抽取层只允许产出这里启用的指标。
# 这是 A103 系统性错标的根因修复之一：规则层只匹配 A101 词族，就不能继承文章级的 A103 编码。
#   metrics：允许的 measure_code
#   units  ：允许的观测单位（空单位不在此列，会被判为不合法并进入 points_dropped.csv）
INDICATOR_SCOPE = {
    "A101": {
        "level3_name": "高新技术企业数量/密度",
        "metrics": {"count", "density"},
        "units": {"家", "户", "个", "家/万人", "家/万", "户/万人", "家每万人"},
        "terms": A101_TERMS,
    },
    # A103 目前不由规则层产出；保留契约供 LLM/未来扩展使用，单位必须是百分比。
    "A103": {
        "level3_name": "R&D经费支出占GDP比重",
        "metrics": {"share"},
        "units": {"%", "％"},
        "terms": [],
    },
}

# 年份来源 → 置信度。低置信年份不进入聚合主值，只保留在证据级供复核。
YEAR_SRC_CONF = {
    "period": 0.95,
    "text": 0.90,
    "relative": 0.80,
    "llm": 0.75,
    "window": 0.65,
    "doc": 0.35,
    "article": 0.20,
    "none": 0.0,
}

# 数量类单位（"家/户/个"）与密度类单位（"家/万人"）——数值语义靠这里区分 metric。
COUNT_UNIT = r"(?:家|户|个)"
DENSITY_UNIT = r"(?:家/万人|家/万|户/万人|家每万人)"
# 阿拉伯数字：允许千分位与小数
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
# 数字 + 可选量级词 + 单位。量级词"万余/万"表示 ×10000，"余/多"表示近似。
# 负向后视 (?<![每第]) 排除"每家""第 X 家"这类非计数的假阳性。
# pre 把"近/超/逾/约/超过/突破"并入同一次匹配，避免与 APPROX_RE 重复产点。
NUM_UNIT_RE = re.compile(
    rf"(?<![每第])(?P<pre>近|超|逾|约|超过|突破|首次突破)?\s*"
    rf"(?P<num>{_NUM})\s*(?P<mag>万余|万|余|多)?\s*(?P<unit>{COUNT_UNIT}|{DENSITY_UNIT})"
)
# 单位直接跟在数字后、无空格的情况（如"302家"）已被上式覆盖；此式另抓"近/超/逾/约 N 家"。
APPROX_RE = re.compile(r"(?P<pre>近|超|逾|约|超过|突破)\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>家|户|个)")
# 中文数词兜底（"十万家""百余家"等粗表述）——命中即 value_num 置空、保留原文。
CN_APPROX_RE = re.compile(r"(?P<text>[几十百千万余]{1,3})\s*(?P<unit>家|户|个)")

# ==================== 语境动词：判定 context_type ====================
# actual：已完成 / 实际 / 累计口径（研究主值默认取它）
ACTUAL_VERBS = ["新增", "增加了", "增加", "增长", "升至", "达到", "达", "突破",
                "累计", "现有", "总量达", "保有量", "净增", "认定为", "翻番", "扩容至"]
# planned：拟 / 计划 / 预计（不代表最终实行）
PLANNED_VERBS = ["拟", "计划", "预计", "将新增", "有望", "预期达", "预期达到", "争取达到"]
# target：目标 / 规划 / 力争（政策目标值）。注意不要用"到20xx年""达到"这类——它们多是 actual。
TARGET_VERBS = ["目标", "力争", "规划", "考核目标", "预期目标", "指标值"]
# decline：减少 / 注销 / 迁出
DECLINE_VERBS = ["减少", "下降", "注销", "取消", "退出", "迁出", "净减", "核减", "撤销"]
# 相对口径：需结合上下文才能定年份
RELATIVE_VERBS = ["同比", "环比", "较去年", "较上年", "同期"]

# 数据集语义类型枚举（写入 context_type 列）
CONTEXT_TYPES = ("actual", "planned", "target", "decline", "unknown")

# ==================== 地区别名（人工补充；其余自动从 GUANGDONG_REGIONS 派生）====================
# 键是文本里可能出现的写法，值是 (区划码, 显示名, 级别, 所属地级市)。
REGION_ALIASES = {
    "广东": ("440000", "广东省", "province", ""),
    "粤": ("440000", "广东省", "province", ""),
    "汕尾城区": ("441502", "汕尾城区", "county", "汕尾市"),
    "汕尾市城区": ("441502", "汕尾城区", "county", "汕尾市"),
}
# 这些名字太泛（多处同名），不自动派生为别名，避免误配。
AMBIGUOUS_REGION_NAMES = {"城区"}

# ==================== LLM 契约 ====================
# 模型：Claude Sonnet 5.5（用户选定，质量/成本均衡）。
# 注意：Sonnet 5.5 **不接受非默认 temperature**（传 temperature=0 会 400），
# 故本脚本【不设 temperature】，靠结构化输出（output_config.format）保证 JSON 可靠；
# thinking 省略即默认 adaptive。effort 用 low/medium 控成本（抽取属高频、类分类任务）。
LLM_MODEL = "claude-sonnet-5-5"
LLM_EFFORT = "medium"
LLM_MAX_TOKENS = 8000
PROMPT_VERSION = "a101-v2"      # 改 prompt/规则即 bump（v2：补强 stat_year 口径指引）

# 每 1M tokens 的美元单价（Anthropic 第一方 API，2026-10）。
# 仅用于运行报告里的成本估算；实际账单以控制台为准。
LLM_PRICING = {
    "claude-sonnet-5-5": {
        "input": 2.00,
        "output": 10.00,
        "cache_read": 0.20,
        "cache_write": 2.50,
    },
}
# LLM 失败率超过该比例即中止本轮 hybrid 抽取（防止"每篇都失败还跑完全程"）。
LLM_MAX_FAILURE_RATE = 0.2

# LLM 触发模式：
#   "any_indicator" —— 只要某篇提到 A101 指标词就调 LLM（精度优先；规则层仅作交叉印证，
#                      有 LLM 结果时以 LLM 为准）。规则层在真实政府文本上假阳性较多，
#                      故默认用这个。
#   "ambiguous_only" —— 只在"提到指标却没抽到数""语境不明""低置信"等歧义处才调 LLM（省钱）。
LLM_TRIGGER_MODE = "any_indicator"

# 抽取的 JSON schema（结构化输出强约束；additionalProperties=false + required 齐全）
LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["data_points"],
    "properties": {
        "data_points": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "region_name", "region_code", "indicator", "metric",
                    "value", "value_text", "unit", "stat_year", "stat_period",
                    "context_type", "context_cue", "evidence_snippet", "confidence",
                ],
                "properties": {
                    "region_name": {"type": "string", "description": "文中地区名，如 荔湾区；无法确定填空串"},
                    "region_code": {"type": "string", "description": "六位行政区划码；无法确定填空串"},
                    "indicator": {"type": "string", "enum": ["A101"]},
                    "metric": {"type": "string", "enum": ["count", "density", "share", "unspecified"],
                               "description": "count=家；density=家/万人；share=占比"},
                    "value": {"type": ["number", "null"], "description": "归一化数值，'万'需×10000；无数值填 null"},
                    "value_text": {"type": "string", "description": "原文数字串，如 '1.2万家'"},
                    "unit": {"type": "string", "enum": ["家", "家/万人", "%", "户", "个", ""]},
                    "stat_year": {"type": ["integer", "null"], "description": "数据所属年份：正文有'截至XXXX年底/XXXX年度/XXXX年末'就用该年；'今年/去年/当年'按文章发布日期换算；都没有填 null"},
                    "stat_period": {"type": "string", "description": "口径时点，如 '年末' '截至2024年6月' '2024年'"},
                    "context_type": {"type": "string",
                                     "enum": ["actual", "planned", "target", "decline", "unknown"],
                                     "description": "actual=已完成/实际/累计；planned=拟/计划/预计；target=目标/力争；decline=减少/注销；unknown=无法判断"},
                    "context_cue": {"type": "string", "description": "触发该判定的原文短语，如 '拟新增'"},
                    "evidence_snippet": {"type": "string", "description": "支持该数据点的原文片段（可含适度上下文）"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}

SYSTEM_PROMPT = """你是严谨的数据抽取助手，任务是从中国政府/新闻文本中抽取【高新技术企业数量/密度】(指标码 A101) 的数据点。

要求：
1. 只抽取文中【明确出现】的数字，不得推算、不得臆造、不得把不同句子拼成一个数。
2. 必须区分口径（context_type）：
   - actual：已完成/实际/累计/现有的数（如"新增X家""现有X家""累计X家""达到X家"）
   - planned：拟/计划/预计/有望（如"拟新增X家""预计达X家"）——这些【不代表最终实行】
   - target：目标/力争/规划（如"目标X家""力争突破X家"）
   - decline：减少/注销/取消/迁出/净减
   - unknown：无法判断
3. region_code 尽量给出六位行政区划码（广东省县级如 440103=广州荔湾）；拿不准就填空串，并在 region_name 写文中地区名。
4. stat_year 是【数据所指的年份】，不是文章发布日期：正文有"截至XXXX年底""XXXX年度""XXXX年末"就用那个年；
   "今年/去年/当年"这类相对表述，按文章的发布日期换算成具体年；都没有才填 null。
5. evidence_snippet 必须是原文片段，供人工核对。
6. 若某句只是在谈政策、名单、申报流程而没有具体数量，就不要产出数据点。

只输出符合给定 JSON schema 的结果，不要输出解释。"""

# few-shot：含一个"拟新增"示例，提醒模型区分实际与计划
FEW_SHOT = [    {
        "user": "2024年，荔湾区新增高新技术企业85家，累计有效高新技术企业达512家。",
        "assistant": {"data_points": [
            {"region_name": "荔湾区", "region_code": "440103", "indicator": "A101",
             "metric": "count", "value": 85, "value_text": "85家", "unit": "家",
             "stat_year": 2024, "stat_period": "2024年", "context_type": "actual",
             "context_cue": "新增", "evidence_snippet": "2024年，荔湾区新增高新技术企业85家", "confidence": 0.95},
            {"region_name": "荔湾区", "region_code": "440103", "indicator": "A101",
             "metric": "count", "value": 512, "value_text": "512家", "unit": "家",
             "stat_year": 2024, "stat_period": "2024年", "context_type": "actual",
             "context_cue": "累计有效高新技术企业达", "evidence_snippet": "累计有效高新技术企业达512家", "confidence": 0.9},
        ]},
    },
    {
        "user": "到2026年，该市拟新增高新技术企业300家，力争总量突破2000家。",
        "assistant": {"data_points": [
            {"region_name": "", "region_code": "", "indicator": "A101",
             "metric": "count", "value": 300, "value_text": "300家", "unit": "家",
             "stat_year": 2026, "stat_period": "到2026年", "context_type": "planned",
             "context_cue": "拟新增", "evidence_snippet": "到2026年，该市拟新增高新技术企业300家", "confidence": 0.9},
            {"region_name": "", "region_code": "", "indicator": "A101",
             "metric": "count", "value": 2000, "value_text": "2000家", "unit": "家",
             "stat_year": 2026, "stat_period": "到2026年", "context_type": "target",
             "context_cue": "力争总量突破", "evidence_snippet": "力争总量突破2000家", "confidence": 0.85},
        ]},
    },
]

# ==================== 产物列定义（techfin_extract_points.py 与 report/verify_points.py 共用）====================
# 证据级：一行 = 一个数据点（一篇文章可产生多行）。
POINT_COLS = [
    "point_id", "article_id", "source_url", "fetch_url", "page_type",
    "title", "publisher", "publish_date", "authority", "quality_grade",
    "level1_code", "level1_name", "level2_code", "level2_name",
    "level3_code", "level3_name",
    "metric", "region_code", "region_name", "region_level", "city_name", "region_from",
    "region_confidence", "region_evidence",
    "value_num", "value_text", "unit", "stat_year", "stat_period", "stat_year_src",
    "year_confidence", "year_evidence",
    "context_type", "context_cue",
    "aggregation_type", "is_breakdown",
    "evidence_snippet", "evidence_source", "evidence_locator", "evidence_verified",
    "extract_method", "confidence", "llm_model", "llm_prompt_version", "extract_time",
]

# 聚合级：一行 = 一个 (region_code, level3_code, stat_year, metric) 格；loader 负责映射到 SQL
# 的 fact_index_value（region_code/year/level3_code/measure_code(由 metric 查 dim_index_measure)/
# value_num 或 value_text/value_unit/source_channel/source_ref/data_quality/evidence_ref）。
RESOLVED_COLS = [
    "region_code", "region_name", "city_name",
    "region_from", "region_confidence", "region_evidence",
    "level3_code", "level3_name", "metric", "stat_year", "stat_year_src",
    "year_confidence", "year_evidence", "aggregation_type",
    "canonical_value", "n_sources", "n_support", "source_article_ids",
    "value_min", "value_max", "agreement", "chosen_reason", "authority",
    "data_quality", "data_source",
]
