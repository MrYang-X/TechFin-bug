"""
数值抽取（techfin_extract_points.py）的**叶子配置**：规则正则 / 地区别名 / 指标定义 / LLM 契约（schema+prompt+模型）。

为什么单独一个文件、且不 import 任何本地模块（与 topic_config.py 同级）：
  抽取规则会高频迭代；把它与"抓取"（techfin_fetch_body.py）和"抽取主程序"（techfin_extract_points.py）分开，改规则只动这一个文件，
  重跑 techfin_extract_points.py 即生效，**不必重抓政府网站**（缓存见 body_cache/）。这正是抓取与抽取解耦的目的。

本次范围：只做 A101「高新技术企业数量/密度」。
"""

import os
import re

# ==================== 指标词族（A3）====================
# 无空格短词族：规则层与清洗层共用。金额类保留原文单位，换算在 loader。
A301_TERMS = [
    "财政科技支出", "科学技术支出", "财政科技拨款", "财政科学技术支出",
    "财政科技投入", "科技支出", "科技投入", "财政科技投入强度",
]
A302_TERMS = [
    "科技信贷风险补偿", "科技金融风险补偿资金池", "风险补偿基金",
    "科技企业信贷风险补偿", "科技型中小企业信贷风险补偿",
    "信贷风险补偿资金池", "风险补偿资金池", "科技贷款风险补偿",
    "风险补偿资金",
]

# ==================== 单位 → 口径映射（通用）====================
# 抽取层只负责识别"这个数字属于哪个口径"；金额的跨单位换算统一放在 loader，
# 避免 loader 优先解析 value_text 时发生双重换算。
UNIT_METRIC = {
    "家": "count", "户": "count", "个": "count", "所": "count",
    "件": "count", "起": "count",
    "家/万人": "density", "家/万": "density", "户/万人": "density",
    "家每万人": "density", "个/万人": "density", "个每万人": "density",
    "亿元": "amount", "万元": "amount", "元": "amount", "万亿元": "amount",
    "%": "share", "％": "share",
}
COUNT_UNITS = {"家", "户", "个", "所", "件", "起"}
DENSITY_UNITS = {"家/万人", "家/万", "户/万人", "家每万人",
                 "个/万人", "个每万人"}
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
    "A301": {
        "level3_name": "财政科技拨款占公共预算支出比重",
        "metrics": {"share"},
        "units": SHARE_UNITS,
        "target_unit": "%",
        "terms": A301_TERMS,
        "non_target_subjects": (
            "科技项目", "科技企业", "研发投入", "财政总收入", "一般公共预算收入",
        ),
        "prefix_reject": ("增长", "增速", "同比", "环比", "提高", "增加",
                          "下降", "降低", "减少"),
        "prefix_allow": ("占比", "比重", "占一般公共预算", "占财政支出"),
        "bind": {"same_clause": 25, "bare": 60, "prev_clause": 60,
                 "prev_clause_terms": ("占", "比重", "占比")},
        "stock_default_actual": True,
    },
    "A302": {
        "level3_name": "科技金融风险补偿资金池规模",
        "metrics": {"amount"},
        "units": AMOUNT_UNITS,
        "target_unit": "万元",
        "terms": A302_TERMS,
        "non_target_subjects": (
            "贷款余额", "贷款投放", "担保金额", "补贴资金", "补偿金额",
        ),
        "prefix_reject": ("增长", "增速", "同比", "环比", "新增", "增加",
                          "减少", "下降", "较上年", "较去年"),
        "prefix_allow": (),
        "bind": {"same_clause": 25, "bare": 60},
        "stock_default_actual": True,
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
PROMPT_VERSION = "a3-v1"        # 改 prompt/规则即 bump

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
                    "indicator": {"type": "string", "enum": ["A301", "A302"]},
                    "metric": {"type": "string", "enum": ["share", "amount", "unspecified"],
                               "description": "share=占比（%）；amount=资金池规模（金额）"},
                    "value": {"type": ["number", "null"],
                              "description": "原文数值，保留原文单位：如'1.2亿元'填1.2、unit=亿元"},
                    "value_text": {"type": "string", "description": "原文数字串，如 '6.2%' '1.2亿元'"},
                    "unit": {"type": "string",
                             "enum": ["%", "％", "亿元", "万元", "元", "万亿元", ""]},
                    "stat_year": {"type": ["integer", "null"], "description": "数据所属年份：正文有'截至XXXX年底/XXXX年度/XXXX年末'就用该年；'今年/去年/当年'按文章发布日期换算；都没有填 null"},
                    "stat_period": {"type": "string", "description": "口径时点，如 '年末' '截至2024年6月' '2024年'"},
                    "context_type": {"type": "string",
                                     "enum": ["actual", "planned", "target", "decline", "unknown"],
                                     "description": "actual=已完成/实际/累计；planned=拟/计划/预计；target=目标/力争；decline=减少/注销；unknown=无法判断"},
                    "context_cue": {"type": "string", "description": "触发该判定的原文短语，如 '占比为'"},
                    "evidence_snippet": {"type": "string", "description": "支持该数据点的原文片段（可含适度上下文）"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}

SYSTEM_PROMPT = """你是严谨的数据抽取助手，任务是从中国政府/新闻文本中抽取 A3「政府支持力度」两个指标的数据点：

- A301 财政科技拨款占公共预算支出比重（metric=share，必须输出 0—100 的百分数，单位 %）
- A302 科技金融风险补偿资金池规模（metric=amount，单位常见万元/亿元）

要求：
1. 只抽取文中【明确出现】的数字，不得推算、不得臆造、不得把不同句子拼成一个数。
2. A301 只输出【占比】本身，不要输出财政科技支出的绝对额：
   - "财政科技支出50亿元，占一般公共预算支出4.2%" → 只产 A301 share=4.2、unit=%；不要产 50 亿元。
   - "财政科技支出增长20%" → 不产 A301 点（这是增速，不是占比）。
   - "财政科技支出占比较上年提高0.5个百分点" → 不产 A301 点（这是变化量，不是占比水平）。
3. A302 只抽"风险补偿资金池/风险补偿基金"的规模，不要抽贷款发放额、贷款余额、担保金额、补贴/补偿金额：
   - "风险补偿资金池规模达1.2亿元" → A302 amount=1.2、unit=亿元、value_text="1.2亿元"。
   - "发放科技贷款5亿元" → 不产 A302 点。
   - 金额保留原文单位，不要自行换算（换算由入库脚本统一完成）。
4. 必须区分口径（context_type）：
   - actual：已完成/实际/累计/年末水平
   - planned：拟/计划/预计/有望
   - target：目标/力争/规划
   - decline：减少/注销/取消
   - unknown：无法判断
5. region_code 尽量给出六位行政区划码（广东省县级如 440103=广州荔湾）；拿不准就填空串，并在 region_name 写文中地区名。
6. stat_year 是【数据所指的年份】，不是文章发布日期：正文有"截至XXXX年底""XXXX年度""XXXX年末"就用那个年；
   "今年/去年/当年"这类相对表述，按文章的发布日期换算成具体年；都没有才填 null。
7. evidence_snippet 必须是原文片段，供人工核对。
8. 若某句只是在谈政策、名单、申报流程而没有具体数量，就不要产出数据点。

只输出符合给定 JSON schema 的结果，不要输出解释。"""
# few-shot：含一个"拟新增"示例，提醒模型区分实际与计划
# few-shot：覆盖占比（不取绝对额/增速）与资金池规模（不取贷款发放额）
FEW_SHOT = [
    {
        "user": "2024年，南山区财政科技支出50亿元，占一般公共预算支出4.2%。",
        "assistant": {"data_points": [
            {"region_name": "南山区", "region_code": "440305", "indicator": "A301",
             "metric": "share", "value": 4.2, "value_text": "4.2%", "unit": "%",
             "stat_year": 2024, "stat_period": "2024年", "context_type": "actual",
             "context_cue": "占一般公共预算支出", "evidence_snippet": "占一般公共预算支出4.2%", "confidence": 0.9},
        ]},
    },
    {
        "user": "2024年，南山区科技信贷风险补偿资金池规模达1.2亿元，累计发放科技贷款5亿元。",
        "assistant": {"data_points": [
            {"region_name": "南山区", "region_code": "440305", "indicator": "A302",
             "metric": "amount", "value": 1.2, "value_text": "1.2亿元", "unit": "亿元",
             "stat_year": 2024, "stat_period": "2024年", "context_type": "actual",
             "context_cue": "规模达", "evidence_snippet": "科技信贷风险补偿资金池规模达1.2亿元", "confidence": 0.9},
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
