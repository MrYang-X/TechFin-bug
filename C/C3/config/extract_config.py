"""
数值抽取（techfin_extract_points.py）的**叶子配置**：规则正则 / 地区别名 / 指标定义 / LLM 契约（schema+prompt+模型）。

为什么单独一个文件、且不 import 任何本地模块（与 topic_config.py 同级）：
  抽取规则会高频迭代；把它与"抓取"（techfin_fetch_body.py）和"抽取主程序"（techfin_extract_points.py）分开，改规则只动这一个文件，
  重跑 techfin_extract_points.py 即生效，**不必重抓政府网站**（缓存见 body_cache/）。这正是抓取与抽取解耦的目的。

本次范围：C3「企业成长性」四个指标（C301 独角兽、C304 瞪羚、C302 营收增长率、C303 利润增长率）。
"""

import os
import re

# ==================== 指标词族（C3）====================
# 无空格短词族：规则层与清洗层共用。金额类保留原文单位，换算在 loader。
C301_TERMS = [
    "独角兽企业数量", "独角兽企业总数", "独角兽企业",
]
C304_TERMS = [
    "瞪羚企业数量", "瞪羚企业总数", "瞪羚企业",
]
C302_TERMS = [
    "高新技术企业营业收入", "科技企业营业收入",
    "高新技术企业营收", "科技企业营收",
    "规上高新技术企业营业收入", "高新技术企业营业收入增长",
    "科技企业营业收入增长", "高新技术企业营收增长", "科技企业营收增长",
]
C303_TERMS = [
    "高新技术企业利润总额", "科技企业利润总额",
    "高新技术企业净利润", "科技企业净利润",
    "高新技术企业利润", "科技企业利润",
    "规上高新技术企业利润", "高新技术企业利润增长",
    "科技企业利润增长", "高新技术企业净利润增长",
]

# 「独角兽、瞪羚企业共 X 家」是合并口径，不能分配给任一指标。
# 只匹配两个企业类型在短窗口内共现且后面带合并/分别引导词的写法；
# 「独角兽企业20家、瞪羚企业35家」这类分列表述不会命中（第二个类型后面没有"共/合计/分别"）。
COMBINED_UNICORN_GAZELLE_RE = re.compile(
    r"(?:独角兽|瞪羚).{0,8}(?:独角兽|瞪羚).{0,8}(?:共|合计|总计|分别|各)"
)

# ==================== 单位 → 口径映射（通用）====================
# 抽取层只负责识别"这个数字属于哪个口径"；金额的跨单位换算统一放在 loader，
# 避免 loader 优先解析 value_text 时发生双重换算。
UNIT_METRIC = {
    "家": "count", "户": "count", "个": "count", "所": "count",
    "件": "count", "起": "count",
    "家/万人": "density", "家/万": "density", "户/万人": "density",
    "家每万人": "density", "个/万人": "density", "个每万人": "density",
    "件/万人": "density", "件/万": "density", "件每万人": "density",
    "亿元": "amount", "万元": "amount", "元": "amount", "万亿元": "amount",
    "%": "share", "％": "share",
}
COUNT_UNITS = {"家", "户", "个", "所", "件", "起"}
DENSITY_UNITS = {"家/万人", "家/万", "户/万人", "家每万人",
                 "个/万人", "个每万人", "件/万人", "件/万", "件每万人"}
AMOUNT_UNITS = {"亿元", "万元", "元", "万亿元"}
SHARE_UNITS = {"%", "％"}
ALL_UNITS = set(UNIT_METRIC)

# 指标-口径-单位契约：抽取层只允许产出这里启用的指标。
# 这是 A103 系统性错标的根因修复之一：规则层只匹配已启用指标的词族，就不会继承文章级的错误编码。
#   metrics        ：允许的 measure_code
#   units          ：允许的源单位（金额指标允许元/万元/亿元/万亿元，落库前由 loader 换算）
#   target_unit    ：落库标准单位（供 loader 校验/换算）
#   terms          ：规则层与清洗层共用的无空格词族
#   non_target_subjects：同子句出现即拒绝的非目标主语
#   prefix_reject  ：数字前局部窗口出现即拒绝的词（如"增长/同比"）
#   prefix_allow   ：占比指标专用，出现即允许（如"占比/比重"）
#   bind           ：数字与指标词的绑定距离（同子句/列表项/前子句）
INDICATOR_SCOPE = {
    "C301": {
        "level3_name": "独角兽企业数量",
        "metrics": {"count"},
        "units": {"家"},
        "target_unit": "家",
        "terms": C301_TERMS,
        "non_target_subjects":
            ("潜在独角兽", "瞪羚", "估值", "营收", "利润", "增长率", "增速"),
        "prefix_reject":
            ("潜在独角兽", "增长", "增速", "同比", "环比", "新增", "减少", "下降"),
        "prefix_allow": (),
        "reject_pattern": COMBINED_UNICORN_GAZELLE_RE,
        "bind": {"same_clause": 25, "bare": 60},

        "stock_default_actual": True,
    },
    "C304": {
        "level3_name": "瞪羚企业数量",
        "metrics": {"count"},
        "units": {"家"},
        "target_unit": "家",
        "terms": C304_TERMS,
        "non_target_subjects":
            ("独角兽", "估值", "营收", "利润", "增长率", "增速"),
        "prefix_reject":
            ("增长", "增速", "同比", "环比", "新增", "减少", "下降"),
        "prefix_allow": (),
        "reject_pattern": COMBINED_UNICORN_GAZELLE_RE,
        "bind": {"same_clause": 25, "bare": 60},

        "stock_default_actual": True,
    },
    "C302": {
        "level3_name": "科技企业营收增长率",
        "metrics": {"growth"},
        "units": SHARE_UNITS,
        "target_unit": "%",
        "terms": C302_TERMS,
        "non_target_subjects":
            ("利润", "净利润", "营业利润", "利润总额", "占比", "比重"),
        "prefix_reject":
            ("占比", "比重"),
        "prefix_allow": (),
        "bind": {"same_clause": 25, "bare": 60},
    },
    "C303": {
        "level3_name": "科技企业利润增长率",
        "metrics": {"growth"},
        "units": SHARE_UNITS,
        "target_unit": "%",
        "terms": C303_TERMS,
        "non_target_subjects":
            ("营业收入", "营收", "销售收入", "主营业务收入", "占比", "比重"),
        "prefix_reject":
            ("占比", "比重"),
        "prefix_allow": (),
        "bind": {"same_clause": 25, "bare": 60},
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

# 数量类单位（"家/户/个"）与密度类单位（"家/万人"）——保留旧常量名，供旧调用兼容。
COUNT_UNIT = r"(?:家|户|个)"
DENSITY_UNIT = r"(?:家/万人|家/万|户/万人|家每万人)"
# 阿拉伯数字：允许千分位与小数
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
# 数字 + 可选量级词 + 单位。量级词"万余/万"表示 ×10000，"余/多"表示近似。
# 负向后视 (?<![每第]) 排除"每家""第 X 家"这类非计数的假阳性。
# pre 把"近/超/逾/约/超过/突破"并入同一次匹配，避免与 APPROX_RE 重复产点。
# 单位按长度倒序，避免"亿元"被"元"抢先。
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(ALL_UNITS, key=len, reverse=True))
NUM_UNIT_RE = re.compile(
    rf"(?<![每第])(?P<pre>近|超|逾|约|超过|突破|首次突破)?\s*"
    rf"(?P<num>{_NUM})\s*(?P<mag>万余|万(?![元亿])|余|多)?\s*(?P<unit>{_UNIT_ALT})"
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
# 可用环境变量覆盖，便于接入第三方 Anthropic 兼容端点（改配置不改代码）：
#   TECHFIN_LLM_MODEL         模型名（默认 claude-sonnet-5-5）
#   TECHFIN_LLM_BASE_URL      Anthropic 兼容端点（留空则用 SDK 的 ANTHROPIC_BASE_URL）
#   TECHFIN_LLM_STRUCTURED    1=用 output_config（默认）；0=省略，改由提示词约束 JSON
#   TECHFIN_LLM_PROMPT_CACHE  1=发送 cache_control（默认）；0=system 用纯字符串
LLM_MODEL = os.environ.get("TECHFIN_LLM_MODEL") or "claude-sonnet-5-5"
LLM_BASE_URL = os.environ.get("TECHFIN_LLM_BASE_URL", "")
LLM_USE_STRUCTURED_OUTPUT = os.environ.get("TECHFIN_LLM_STRUCTURED", "1") != "0"
LLM_USE_PROMPT_CACHE = os.environ.get("TECHFIN_LLM_PROMPT_CACHE", "1") != "0"
LLM_EFFORT = "medium"
LLM_MAX_TOKENS = 8000
PROMPT_VERSION = "c3-v1"        # 改 prompt/规则即 bump

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
                    "indicator": {"type": "string", "enum": ["C301", "C304", "C302", "C303"]},
                    "metric": {"type": "string", "enum": ["count", "growth", "unspecified"],
                               "description": "count=独角兽/瞪羚企业数量（家）；growth=营收/利润增长率（%）"},
                    "value": {"type": ["number", "null"],
                              "description": "原文数值，保留原文单位：如'20家'填20、unit=家"},
                    "value_text": {"type": "string", "description": "原文数字串，如 '20家'"},
                    "unit": {"type": "string",
                             "enum": ["家", "%", "％", ""]},
                    "stat_year": {"type": ["integer", "null"], "description": "数据所属年份：正文有'截至XXXX年底/XXXX年度/XXXX年末'就用该年；'今年/去年/当年'按文章发布日期换算；都没有填 null"},
                    "stat_period": {"type": "string", "description": "口径时点，如 '年末' '截至2024年6月' '2024年'"},
                    "context_type": {"type": "string",
                                     "enum": ["actual", "planned", "target", "decline", "unknown"],
                                     "description": "actual=已完成/实际/累计；planned=拟/计划/预计；target=目标/力争；decline=减少/注销；unknown=无法判断"},
                    "context_cue": {"type": "string", "description": "触发该判定的原文短语，如 '同比增长'"},
                    "evidence_snippet": {"type": "string", "description": "支持该数据点的原文片段（可含适度上下文）"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}

SYSTEM_PROMPT = """你是严谨的数据抽取助手，任务是从中国政府/新闻文本中抽取 C3「企业成长性」四个指标的数据点：

- C301 独角兽企业数量（metric=count，单位必须是 家）
- C304 瞪羚企业数量（metric=count，单位必须是 家）
- C302 科技企业营收增长率（metric=growth，单位必须是 %）
- C303 科技企业利润增长率（metric=growth，单位必须是 %）

要求：
1. 只抽取文中【明确出现】的数字，不得推算、不得臆造、不得把不同句子拼成一个数。
2. C301 与 C304 严格分开：
   - "独角兽企业20家" → C301 count；
   - "瞪羚企业35家" → C304 count；
   - "独角兽、瞪羚企业共50家" 是合并口径，不要分配给 C301 或 C304。
3. C301/C304 只抽企业数量；"估值10亿美元""营业收入5亿元""新增独角兽企业3家"不产数量点
   （新增/减少是流量，不是存量数量）。
4. C302 只抽营业收入/营收的增长：
   - "高新技术企业营业收入同比增长8.5%" → C302 growth；
   - "利润总额同比增长12.3%" 不产 C302 点。
5. C303 只抽利润/净利润/利润总额的增长：
   - "高新技术企业利润总额同比增长12.3%" → C303 growth；
   - "营业收入同比增长8.5%" 不产 C303 点。
6. 金额（亿元/万元）不是增长率；只有带 % 的数字才产 C302/C303。
7. 必须区分口径（context_type）：
   - actual：已完成/实际/累计/年末值
   - planned：拟/计划/预计/有望
   - target：目标/力争/规划
   - decline：减少/下降/注销/取消
   - unknown：无法判断
8. region_code 尽量给出六位行政区划码（广东省县级如 440103=广州荔湾）；拿不准就填空串，并在 region_name 写文中地区名。
9. stat_year 是【数据所指的年份】，不是文章发布日期；"今年/去年/当年"按文章发布日期换算；都没有才填 null。
10. evidence_snippet 必须是原文片段，供人工核对。
11. 若某句只是在谈政策、名单、申报流程而没有具体数量，就不要产出数据点。

只输出符合给定 JSON schema 的结果，不要输出解释。"""
# few-shot：覆盖独角兽、瞪羚、营收增长、利润增长四类
FEW_SHOT = [
    {
        "user": "2024年，荔湾区独角兽企业20家，瞪羚企业35家。",
        "assistant": {"data_points": [
            {"region_name": "荔湾区", "region_code": "440103", "indicator": "C301",
             "metric": "count", "value": 20, "value_text": "20家", "unit": "家",
             "stat_year": 2024, "stat_period": "2024年", "context_type": "actual",
             "context_cue": "2024年", "evidence_snippet": "荔湾区独角兽企业20家", "confidence": 0.95},
            {"region_name": "荔湾区", "region_code": "440103", "indicator": "C304",
             "metric": "count", "value": 35, "value_text": "35家", "unit": "家",
             "stat_year": 2024, "stat_period": "2024年", "context_type": "actual",
             "context_cue": "2024年", "evidence_snippet": "瞪羚企业35家", "confidence": 0.95},
        ]},
    },
    {
        "user": "荔湾区高新技术企业营业收入同比增长8.5%，利润总额同比增长12.3%。",
        "assistant": {"data_points": [
            {"region_name": "荔湾区", "region_code": "440103", "indicator": "C302",
             "metric": "growth", "value": 8.5, "value_text": "8.5%", "unit": "%",
             "stat_year": None, "stat_period": "", "context_type": "unknown",
             "context_cue": "同比增长", "evidence_snippet": "高新技术企业营业收入同比增长8.5%", "confidence": 0.9},
            {"region_name": "荔湾区", "region_code": "440103", "indicator": "C303",
             "metric": "growth", "value": 12.3, "value_text": "12.3%", "unit": "%",
             "stat_year": None, "stat_period": "", "context_type": "unknown",
             "context_cue": "同比增长", "evidence_snippet": "利润总额同比增长12.3%", "confidence": 0.9},
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
