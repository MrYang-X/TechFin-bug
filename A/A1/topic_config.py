"""
主题配置：科技金融指数研究

**新增一个主题时，主要就是改这个文件。** 通用机械都在仓库根的 crawler_common.py，
本文件只放"这个主题特有"的东西：检索词、噪声源、采集范围、官方源清单、产物契约。

三个脚本都会 import 本文件，谁都不 import 谁（详见 README.md 的依赖图）。
"""

# ==================== 主题标识 ====================

TOPIC_LABEL = "科技金融指数研究"

# ==================== 检索词 ====================

# 搜索引擎用的检索词（逐个检索，各自翻页）
TECH_FIN_KEYWORDS = [
    "金融科技", "科技金融", "发展指数"
]

# 核心检索词：命中即可入库
CORE_TERMS = ["金融科技", "科技金融"]
# 限定词：必须与核心词在同一篇标题/摘要中共现，否则视为噪声
# （实测"发展指数"单独检索 10 条中 0 条与科技金融相关：教育强国指数、
#   人类发展指数、商贸零售上市公司发展指数……故加 AND 约束）
QUALIFIER_TERMS = ["发展指数"]

# 官方源用的筛选词。官方源是全量抓取后再筛，默认沿用上面那组。
AUTH_KEYWORDS = TECH_FIN_KEYWORDS

# ==================== 噪声源黑名单 ====================

# 非新闻源：百科词条、行情页、厂商专题页等，不属于新闻语料
NOISE_DOMAINS = [
    "baike.baidu.com",
    "xueqiu.com",
    "ibm.com",
    "10jqka.com.cn",
    "eastmoney.com",
]
NOISE_SOURCES = ["百度百科", "雪球", "东方财富"]
# 非新闻文件类型（如教材PDF）
NOISE_URL_SUFFIXES = [".pdf"]

# ==================== 采集范围与节奏 ====================

# 搜索引擎：启用哪些源。某源被限流时只跳过该源，其它源继续。
ENABLED_SOURCES = ["baidu", "360"]
MAX_PAGES_PER_KEYWORD = 3
MIN_DELAY = 5.0
MAX_DELAY = 12.0

# 官方源：每个源翻多少页（页数直接决定回溯的年头）
MAX_PAGES_PER_SOURCE = 10
OFFICIAL_DELAY = (2.0, 4.0)
# 官方源标题多为政策/会议口径，几乎不含字面检索词，故默认全量保存、
# 匹配交给 verify_corpus.py；详见 techfin_authoritative.py 的说明。
SAVE_ALL_ITEMS = True

# 增量累积：把新结果合并进累积总库而不是覆盖。
# 搜索引擎按 IP 限流，单轮往往采不全，需要隔一段时间重跑补齐；
# 若不累积，一次"收获较少"的重跑会毁掉之前采到的数据。
ACCUMULATE = True

# 官方政策源清单。分页规律各不相同，first_page_no 为"第二页"的编号。
SOURCES = [
    {
        "name": "pbc",
        "label": "中国人民银行",
        "list_url": "http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html",
        "page_url": "http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/11040-{n}.html",
        "first_page_no": 2,
    },
    {
        "name": "most",
        "label": "科技部",
        "list_url": "https://www.most.gov.cn/kjbgz/index.html",
        "page_url": "https://www.most.gov.cn/kjbgz/index_{n}.html",
        "first_page_no": 1,
    },
    {
        "name": "ndrc",
        "label": "国家发改委",
        "list_url": "https://www.ndrc.gov.cn/xwdt/xwfb/",
        "page_url": "https://www.ndrc.gov.cn/xwdt/xwfb/index_{n}.html",
        "first_page_no": 1,
    },
]

# ==================== 产物契约 ====================

# 输出根目录（相对本主题目录；也可写绝对路径把数据放到仓库外）。
# 可用命令行 --output-dir 临时覆盖。
OUTPUT_ROOT = "techfin_common_output"

# 累积总库（位于输出根内），跨轮读写；每轮目录里的 *_cumulative.csv 是它的快照副本
SEARCH_ACCUMULATOR = "tech_fin_news.csv"
OFFICIAL_ACCUMULATOR = "techfin_official.csv"

# 每轮产物的文件名前缀：两个采集脚本共用同一个输出根与当日序号，
# 靠前缀区分是谁产出的（目录里可能同时出现 search_* 与 official_*）
SEARCH_PREFIX = "search_"
OFFICIAL_PREFIX = "official_"

# 运行目录里的累积快照文件名（verify_corpus.py 用它定位语料）
CUMULATIVE_NAMES = {
    "search": SEARCH_PREFIX + "cumulative.csv",
    "official": OFFICIAL_PREFIX + "cumulative.csv",
}
# 累积总库缺名映射（无运行目录时的回退）
ACCUMULATOR_FILES = {
    "search": SEARCH_ACCUMULATOR,
    "official": OFFICIAL_ACCUMULATOR,
}

# 印证报告文件名
REPORT_NAME = "corpus_verification.md"

# 列顺序：链接与日期放显眼位置，方便手动查看
SEARCH_COLS = [
    "article_id", "title", "source", "date", "date_raw", "date_url",
    "keyword", "engine", "engines", "url", "real_url",
    "summary", "title_is_url", "crawl_time",
]
OFFICIAL_COLS = [
    "article_id", "title", "source", "date", "date_raw", "keyword",
    "url", "crawl_time",
]
