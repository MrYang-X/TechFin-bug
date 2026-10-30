"""
主题配置：科技金融指数研究

**新增一个主题时，主要就是改这个文件。** 通用机械都在仓库根的 crawler_common.py，
本文件只放"这个主题特有"的东西：检索词、噪声源、采集范围、官方源清单、产物契约。

三个脚本都会 import 本文件，谁都不 import 谁（详见 README.md 的依赖图）。
"""

# ==================== 主题标识 ====================

TOPIC_LABEL = "科技金融指数研究"

# ==================== 官方源打标词 ====================
#
# 【搜索引擎的检索词不在这里！】它们由下方 INDEX_KEYWORDS 的三级指标 queries 逐条
# 展开（见 build_search_plan）：带地区时前拼地区名，不带地区时叠加 ANCHOR_TERMS。
#
# 本组常量只服务官方源爬虫 techfin_authoritative.py：给条目打 keyword 标签并统计。
# 因 SAVE_ALL_ITEMS=True（见下），它【不参与"是否入库"的筛选】，只是标签——但
# verify_corpus.py 的"仅官方源(命中检索词)"一列读的正是这个标签，故仍需维护。
#
# 【取值方式】不再是手写常量，而是由 INDEX_KEYWORDS 派生（见下方 QUERIES_PER_REGION
# 之后的 AUTH_KEYWORDS / _derive_auth_keywords）：每条三级指标取【实际启用的前
# QUERIES_PER_REGION 条检索式】——与采集口径完全一致，改检索词即自动跟随，杜绝
# "配置里两处各维护一份、互相漂移"。官方标题常用但指标里没有的写法（如"研发经费"
# "科技经费"）走 AUTH_EXTRA_TERMS 兜底。

# 相关性约束（核心词 / 限定词）：只有当检索词【整串】出现在 QUALIFIER_TERMS 里时，
# CORE_TERMS 才参与判断（见 crawler_common.is_relevant）。本主题的检索词都是独立
# 强词，无需"与核心词共现"的约束，故 QUALIFIER_TERMS 置空——该机制整体停用。
# 两个常量保留为空列表是因为两个爬虫的调用点仍传入它们（TechFin-bug.py、
# techfin_authoritative.py）；将来若引入易泛化的词，填 QUALIFIER_TERMS 即可重新启用。
CORE_TERMS = []
QUALIFIER_TERMS = []

# ==================== 地区口径：广东省县级区划 ====================
# 来源：民政部《2024 年中华人民共和国县以上行政区划代码》，与 db/10_schema.sql
# 的 dim_region 表同源同码。爬虫不连数据库（产物是 CSV），故区划清单以常量形式放这里。
#
# 结构：{地级市名: [(县级区划代码, 名称), ...]}。东莞市/中山市是不设区的地级市，
# 下辖无县级单位，故以市本级（441900/442000）作为最细采集单元——否则这两个制造业
# 大市会整个漏掉。共 21 市、124 个采集单元（122 个县级单位 + 东莞/中山 2 个市本级）。
GUANGDONG_REGIONS = {
    "广州市": [
        ("440103", "荔湾区"),
        ("440104", "越秀区"),
        ("440105", "海珠区"),
        ("440106", "天河区"),
        ("440111", "白云区"),
        ("440112", "黄埔区"),
        ("440113", "番禺区"),
        ("440114", "花都区"),
        ("440115", "南沙区"),
        ("440117", "从化区"),
        ("440118", "增城区"),
    ],
    "韶关市": [
        ("440203", "武江区"),
        ("440204", "浈江区"),
        ("440205", "曲江区"),
        ("440222", "始兴县"),
        ("440224", "仁化县"),
        ("440229", "翁源县"),
        ("440232", "乳源瑶族自治县"),
        ("440233", "新丰县"),
        ("440281", "乐昌市"),
        ("440282", "南雄市"),
    ],
    "深圳市": [
        ("440303", "罗湖区"),
        ("440304", "福田区"),
        ("440305", "南山区"),
        ("440306", "宝安区"),
        ("440307", "龙岗区"),
        ("440308", "盐田区"),
        ("440309", "龙华区"),
        ("440310", "坪山区"),
        ("440311", "光明区"),
    ],
    "珠海市": [
        ("440402", "香洲区"),
        ("440403", "斗门区"),
        ("440404", "金湾区"),
    ],
    "汕头市": [
        ("440507", "龙湖区"),
        ("440511", "金平区"),
        ("440512", "濠江区"),
        ("440513", "潮阳区"),
        ("440514", "潮南区"),
        ("440515", "澄海区"),
        ("440523", "南澳县"),
    ],
    "佛山市": [
        ("440604", "禅城区"),
        ("440605", "南海区"),
        ("440606", "顺德区"),
        ("440607", "三水区"),
        ("440608", "高明区"),
    ],
    "江门市": [
        ("440703", "蓬江区"),
        ("440704", "江海区"),
        ("440705", "新会区"),
        ("440781", "台山市"),
        ("440783", "开平市"),
        ("440784", "鹤山市"),
        ("440785", "恩平市"),
    ],
    "湛江市": [
        ("440802", "赤坎区"),
        ("440803", "霞山区"),
        ("440804", "坡头区"),
        ("440811", "麻章区"),
        ("440823", "遂溪县"),
        ("440825", "徐闻县"),
        ("440881", "廉江市"),
        ("440882", "雷州市"),
        ("440883", "吴川市"),
    ],
    "茂名市": [
        ("440902", "茂南区"),
        ("440904", "电白区"),
        ("440981", "高州市"),
        ("440982", "化州市"),
        ("440983", "信宜市"),
    ],
    "肇庆市": [
        ("441202", "端州区"),
        ("441203", "鼎湖区"),
        ("441204", "高要区"),
        ("441223", "广宁县"),
        ("441224", "怀集县"),
        ("441225", "封开县"),
        ("441226", "德庆县"),
        ("441284", "四会市"),
    ],
    "惠州市": [
        ("441302", "惠城区"),
        ("441303", "惠阳区"),
        ("441322", "博罗县"),
        ("441323", "惠东县"),
        ("441324", "龙门县"),
    ],
    "梅州市": [
        ("441402", "梅江区"),
        ("441403", "梅县区"),
        ("441422", "大埔县"),
        ("441423", "丰顺县"),
        ("441424", "五华县"),
        ("441426", "平远县"),
        ("441427", "蕉岭县"),
        ("441481", "兴宁市"),
    ],
    "汕尾市": [
        ("441502", "城区"),
        ("441521", "海丰县"),
        ("441523", "陆河县"),
        ("441581", "陆丰市"),
    ],
    "河源市": [
        ("441602", "源城区"),
        ("441621", "紫金县"),
        ("441622", "龙川县"),
        ("441623", "连平县"),
        ("441624", "和平县"),
        ("441625", "东源县"),
    ],
    "阳江市": [
        ("441702", "江城区"),
        ("441704", "阳东区"),
        ("441721", "阳西县"),
        ("441781", "阳春市"),
    ],
    "清远市": [
        ("441802", "清城区"),
        ("441803", "清新区"),
        ("441821", "佛冈县"),
        ("441823", "阳山县"),
        ("441825", "连山壮族瑶族自治县"),
        ("441826", "连南瑶族自治县"),
        ("441881", "英德市"),
        ("441882", "连州市"),
    ],
    "东莞市": [("441900", "东莞市")],
    "中山市": [("442000", "中山市")],
    "潮州市": [
        ("445102", "湘桥区"),
        ("445103", "潮安区"),
        ("445122", "饶平县"),
    ],
    "揭阳市": [
        ("445202", "榕城区"),
        ("445203", "揭东区"),
        ("445222", "揭西县"),
        ("445224", "惠来县"),
        ("445281", "普宁市"),
    ],
    "云浮市": [
        ("445302", "云城区"),
        ("445303", "云安区"),
        ("445321", "新兴县"),
        ("445322", "郁南县"),
        ("445381", "罗定市"),
    ],
}

# 检索式里用哪个名字。默认取区划名称，个别泛化到无法检索的单独覆盖。
# 例：汕尾市城区（441502）的规范名就叫"城区"，单独检索会命中一堆无关结果。
REGION_QUERY_NAME_OVERRIDES = {
    "441502": "汕尾城区",
}

# ==================== 指标体系（与 db/10_schema.sql 的 dim_index 同构） ====================
# 字段名逐一对齐 dim_index 的列名（level1_code/level1_name/level1_weight、
# level2_code/level2_name、level3_code/level3_name、unit），
# 将来 CSV → SQL join 不用再改名。level2_code 取自下面的字典键（"A1"/"B2"…）。
#
# 权重（level1_weight/level2_weight/level3_weight）【不在这里维护】——爬虫不消费权重，
# 放两份只会互相漂移。权重的唯一权威在 db/10_schema.sql 的 dim_index 表。
#
# 指标的县域适用性（is_county_applicable）与替代口径（county_replacement）同样
# 【只活在 DB 的 dim_index 里】。它们是研究设计元数据，对每条语料行都是噪声，
# 不进 CSV、不进 SEARCH_COLS。
#
# 指标名是【统计口径名】而不是新闻常用短语——"高新技术企业数量/密度"带斜杠，
# 中文新闻里说"研发"而不是"R&D"。所以每条指标展开成若干检索式，按优先级排列。
# 带地区前缀采集时只取前 QUERIES_PER_REGION 条（地区 × 指标会成倍放大请求量）。
#
# 【2024 版复合指标已拆分】原 30 条里有 5 条把两个度量塞在一行（单位形如"亿元/%"），
# 单个 DECIMAL 列装不下、也无法统一赋权，已拆成单一单位（30 → 35 条）：
#   A202+A203 / B101+B102 / B201+B202 / B203+B204 / C302+C303
INDEX_KEYWORDS = {
    "A1": {
        "level1_code": "A",
        "level1_name": "科技金融资源基础",
        "level1_weight": 25.00,
        "level2_name": "科技企业资源",
        "indicators": [
            {
                "level3_code": "A101",
                "level3_name": "高新技术企业数量/密度",
                "unit": "家或家/万人",
                "queries": [
                    "高新技术企业",
                    "国家高新技术企业",
                    "高企",
                    "高新技术企业认定",
                    "高新技术企业名单",
                    "高新技术企业公示",
                    "有效高新技术企业",
                    "当年认定高新技术企业",
                    "高新技术企业数量",
                    "高企数量",
                    "高新技术企业密度",
                    "高企密度",
                    "每万人高新技术企业数",
                    "每万家企业高新技术企业数",
                    "高新技术企业占规上工业企业比重",
                    # ---- 数值型检索式（2026-10 新增，供正文数值抽取用）----
                    # 以上前 3 条（QUERIES_PER_REGION）用于【带地区】采集；这里追加的词
                    # 排在后面，故【只在 --no-region 无地区采集时启用】，专门召回
                    # "新增高企 X 家""总数突破 X 家""存量 X 家"这类含具体数字的报道，
                    # 供 techfin_extract_points.py 抽数据点。带地区口径保持不变（不重跑采集）。
                    "新增高新技术企业",
                    "高新技术企业 净增",
                    "高新技术企业 总数",
                    "高新技术企业 存量",
                    "高新技术企业 保有量",
                    "高新技术企业 累计",
                    "高新技术企业 数量 突破",
                    "高新技术企业 家",
                    "高企 新增",
                    "高企 突破",
                ],
            },
            {
                "level3_code": "A102",
                "level3_name": "科技型中小企业库入库数量",
                "unit": "家",
                "queries": [
                    "科技型中小企业",
                    "国家科技型中小企业",
                    "科技型中小企业评价",
                    "科技型中小企业信息库",
                    "入库登记编号",
                    "入库企业",
                    "入库数量",
                    "科技型中小企业入库",
                    "科技型中小企业名单",
                    "科技型中小企业公示",
                    "科技型中小企业评价系统",
                    "科技部火炬中心 科技型中小企业",
                ],
            },
            {
                "level3_code": "A103",
                "level3_name": "R&D经费支出占GDP比重",
                "unit": "%",
                "queries": [
                    # 前 3 条是带地区采集实际启用的（见 QUERIES_PER_REGION）。刻意混入
                    # "研发"写法（中文新闻/统计公报常用），避免只打 R&D/研究与试验发展
                    # 口径而整块漏掉"研发经费""研发投入强度"这类表述。
                    "R&D经费",
                    "研发投入强度",
                    "研究与试验发展经费",
                    "全社会R&D经费",
                    "R&D经费支出",
                    "R&D经费投入强度",
                    "研发经费投入强度",
                    "研究与试验发展经费支出占GDP比重",
                    "全社会研发投入占GDP比重",
                    "研发经费支出占地区生产总值比重",
                    "规上工业企业研发费用",
                    "规上工业企业R&D经费",
                    "研发费用加计扣除",
                ],
            },
        ],
    },
    "A2": {
        "level1_code": "A",
        "level1_name": "科技金融资源基础",
        "level1_weight": 25.00,
        "level2_name": "金融资源供给",
        "indicators": [
            {
                "level3_code": "A201",
                "level3_name": "本外币存款余额",
                "unit": "亿元",
                "queries": ["本外币存款余额", "金融机构 存款余额", "存款余额 统计公报"],
            },
            {
                "level3_code": "A202",
                "level3_name": "私募股权/创业投资机构数量",
                "unit": "家",
                "queries": ["私募股权 创业投资 机构", "创业投资机构 数量", "私募基金管理人 数量"],
            },
            {
                "level3_code": "A203",
                "level3_name": "私募股权/创业投资机构管理资本量",
                "unit": "亿元",
                "queries": ["创业投资 管理资本", "私募股权 管理规模", "创投机构 管理资金"],
            },
            {
                "level3_code": "A204",
                "level3_name": "银行业金融机构网点密度",
                "unit": "个/万人",
                "queries": ["银行业金融机构 网点", "银行网点 数量", "金融机构 营业网点"],
            },
        ],
    },
    "A3": {
        "level1_code": "A",
        "level1_name": "科技金融资源基础",
        "level1_weight": 25.00,
        "level2_name": "政府支持力度",
        "indicators": [
            {
                "level3_code": "A301",
                "level3_name": "财政科技拨款占公共预算支出比重",
                "unit": "%",
                "queries": ["财政科技拨款", "科技支出 占比", "财政科技投入 比重"],
            },
            {
                "level3_code": "A302",
                "level3_name": "科技金融风险补偿资金池规模",
                "unit": "万元",
                "queries": ["科技金融 风险补偿 资金池", "科技信贷 风险补偿", "风险补偿基金 规模"],
            },
        ],
    },

    "B1": {
        "level1_code": "B",
        "level1_name": "科技金融活动强度",
        "level1_weight": 35.00,
        "level2_name": "债权融资活跃度",
        "indicators": [
            {
                "level3_code": "B101",
                "level3_name": "科技企业贷款余额",
                "unit": "亿元",
                "queries": ["科技企业贷款余额", "科技贷款 余额", "高新技术企业 贷款 余额"],
            },
            {
                "level3_code": "B102",
                "level3_name": "科技企业贷款余额增速",
                "unit": "%",
                "queries": ["科技贷款 增速", "科技企业贷款 同比增长", "科技贷款 增长"],
            },
            {
                "level3_code": "B103",
                "level3_name": "知识产权质押融资额",
                "unit": "亿元",
                "queries": ["知识产权质押融资", "专利质押 融资金额", "商标质押 融资"],
            },
            {
                "level3_code": "B104",
                "level3_name": "信用贷款占比",
                "unit": "%",
                "queries": ["信用贷款 占比", "科技企业 信用贷款", "信用贷款 比例"],
            },
        ],
    },
    "B2": {
        "level1_code": "B",
        "level1_name": "科技金融活动强度",
        "level1_weight": 35.00,
        "level2_name": "股权融资活跃度",
        "indicators": [
            {
                "level3_code": "B201",
                "level3_name": "VC/PE投资案例数",
                "unit": "起",
                "queries": ["创业投资 案例数", "股权投资 案例", "VC PE 投资 案例"],
            },
            {
                "level3_code": "B202",
                "level3_name": "VC/PE投资金额",
                "unit": "亿元",
                "queries": ["股权投资 金额", "创业投资 投资金额", "VC PE 投资额"],
            },
            {
                "level3_code": "B203",
                "level3_name": "科创板/创业板等上市科技企业数量",
                "unit": "家",
                "queries": ["科创板 上市 企业 数量", "创业板 上市 科技企业", "科技企业 上市 数量"],
            },
            {
                "level3_code": "B204",
                "level3_name": "科创板/创业板等上市科技企业募资额",
                "unit": "亿元",
                "queries": ["科创板 募资额", "创业板 首发募资", "上市 募资 规模"],
            },
        ],
    },
    "B3": {
        "level1_code": "B",
        "level1_name": "科技金融活动强度",
        "level1_weight": 35.00,
        "level2_name": "政策性融资效果",
        "indicators": [
            {
                "level3_code": "B301",
                "level3_name": "科技保险保费收入",
                "unit": "亿元",
                "queries": ["科技保险 保费收入", "科技保险 保费", "科技保险 试点"],
            },
            {
                "level3_code": "B302",
                "level3_name": "创业担保贷款发放额",
                "unit": "亿元",
                "queries": ["创业担保贷款 发放", "创业担保贷款 金额", "创业担保贷款"],
            },
        ],
    },
    "B4": {
        "level1_code": "B",
        "level1_name": "科技金融活动强度",
        "level1_weight": 35.00,
        "level2_name": "金融科技应用",
        "indicators": [
            {
                "level3_code": "B401",
                "level3_name": "移动支付交易额",
                "unit": "亿元",
                "queries": ["移动支付 交易额", "移动支付 交易规模", "移动支付 金额"],
            },
            {
                "level3_code": "B402",
                "level3_name": "数字普惠金融指数",
                "unit": "指数值",
                "queries": ["数字普惠金融指数", "普惠金融 指数", "数字普惠金融"],
            },
            {
                "level3_code": "B403",
                "level3_name": "大数据/AI在风控中的应用情况",
                "unit": "定性/评分",
                "queries": ["大数据 风控 应用", "人工智能 风控", "金融科技 风险管理"],
            },
        ],
    },

    "C1": {
        "level1_code": "C",
        "level1_name": "科技金融产出效能",
        "level1_weight": 30.00,
        "level2_name": "科技创新产出",
        "indicators": [
            {
                "level3_code": "C101",
                "level3_name": "PCT国际专利申请量",
                "unit": "件",
                "queries": ["PCT 专利申请量", "PCT 国际专利", "国际专利申请"],
            },
            {
                "level3_code": "C102",
                "level3_name": "技术合同成交额",
                "unit": "亿元",
                "queries": ["技术合同成交额", "技术合同 成交金额", "技术交易 成交额"],
            },
            {
                "level3_code": "C103",
                "level3_name": "每万人发明专利拥有量",
                "unit": "件/万人",
                "queries": ["每万人发明专利拥有量", "发明专利 拥有量", "万人发明专利"],
            },
        ],
    },
    "C2": {
        "level1_code": "C",
        "level1_name": "科技金融产出效能",
        "level1_weight": 30.00,
        "level2_name": "经济发展贡献",
        "indicators": [
            {
                "level3_code": "C201",
                "level3_name": "高新技术产业产值占规上工业产值比重",
                "unit": "%",
                "queries": ["高新技术产业产值 占比", "高新技术产业 产值 比重", "高技术制造业 产值 占比"],
            },
            {
                "level3_code": "C202",
                "level3_name": "科技服务业增加值",
                "unit": "亿元",
                "queries": ["科技服务业 增加值", "科技服务 增加值", "科技服务业 收入"],
            },
        ],
    },
    "C3": {
        "level1_code": "C",
        "level1_name": "科技金融产出效能",
        "level1_weight": 30.00,
        "level2_name": "企业成长性",
        "indicators": [
            {
                "level3_code": "C301",
                "level3_name": '"独角兽"、"瞪羚"企业数量',
                "unit": "家",
                "queries": ["独角兽企业 数量", "瞪羚企业 数量", "独角兽 瞪羚 企业"],
            },
            {
                "level3_code": "C302",
                "level3_name": "科技企业营收增长率",
                "unit": "%",
                "queries": ["科技企业 营收增长率", "高新技术企业 营收 增长", "科技企业 营业收入 增速"],
            },
            {
                "level3_code": "C303",
                "level3_name": "科技企业利润增长率",
                "unit": "%",
                "queries": ["科技企业 利润增长率", "高新技术企业 利润 增长", "科技企业 净利润 增速"],
            },
        ],
    },

    "D1": {
        "level1_code": "D",
        "level1_name": "科技金融生态环境",
        "level1_weight": 10.00,
        "level2_name": "政策与服务环境",
        "indicators": [
            {
                "level3_code": "D101",
                "level3_name": "科技金融政策完备度（专家打分）",
                "unit": "分",
                "queries": ["科技金融 政策", "科技金融 政策体系", "科技金融 实施方案"],
            },
            {
                "level3_code": "D102",
                "level3_name": "科技金融服务中心/网点覆盖情况",
                "unit": "个或%",
                "queries": ["科技金融服务中心", "科技金融服务 网点", "科技金融 服务站"],
            },
        ],
    },
    "D2": {
        "level1_code": "D",
        "level1_name": "科技金融生态环境",
        "level1_weight": 10.00,
        "level2_name": "人力与创新环境",
        "indicators": [
            {
                "level3_code": "D201",
                "level3_name": "R&D人员全时当量",
                "unit": "人年",
                "queries": ["R&D人员全时当量", "研发人员 全时当量", "R&D人员"],
            },
            {
                "level3_code": "D202",
                "level3_name": "普通高等学校数量",
                "unit": "所",
                "queries": ["普通高等学校 数量", "高等学校 数量", "高校 数量"],
            },
            {
                "level3_code": "D203",
                "level3_name": "数字经济发展指数",
                "unit": "指数值",
                "queries": ["数字经济发展指数", "数字经济 指数", "数字经济 发展水平"],
            },
        ],
    },
}

# 锚点词（OR 集）：指标判别词偏泛，命中后还须与下列【任一】锚点共现才入库。
#
# 【实测结论·勿删】锚点只对【不带地区】的检索式启用，带地区的一律不加——
# 地区词本身已经是强约束（实测百度对"荔湾区 高新技术企业"返回 10 条里 9 条含
# "荔湾区"、8 条同时含两个词），再叠一层主题锚点属于重复约束，代价是把真数据杀掉：
# 同一轮里被锚点丢弃的正是"荔湾区现有高新技术企业 302 家""科技型中小企业申报"
# 这类 A1 指标的核心语料。故带地区检索式只要求"地区 + 指标词"共现。
# 不带地区时锚点仍然有用（防止"高新技术企业"泛化到全国无关报道）。
# 收紧/放宽只改这里；置空列表即全局关闭该约束。
ANCHOR_TERMS = ["科技金融", "金融科技", "科技创新", "科技企业",
                "科技成果转化", "高新区", "高新技术产业", "科创"]

# 带地区前缀采集时，每条指标取前 N 条检索式（见 INDEX_KEYWORDS 上方说明）。
# A1 的检索词表已扩到 40 条（A101=15 / A102=12 / A103=13），地区采集取前 3 条 =
# 124 个县级单元 × 3 指标 × 3 = 1116 条检索式。百度限流下不可能单轮跑完，
# 靠 --offset/--limit/--cities 分批 + 累积总库补齐；其余 37 条留给 --no-region。
QUERIES_PER_REGION = 3

# ---- 官方源打标词（给 techfin_authoritative.py 用）：由 INDEX_KEYWORDS 派生 ----
# 官方标题多为政策/公报口径，is_relevant 要求【逐 token 字面命中】，故只取各指标
# 实际启用的前几条检索式（短而常用）；长口径名几乎不命中，不取自全部 queries。
# 改上面的 queries 或 QUERIES_PER_REGION，这里自动跟随，不再手写第二份。
AUTH_EXTRA_TERMS = ["研发经费", "科技经费"]


def _derive_auth_keywords():
    """
    各三级指标 queries[:QUERIES_PER_REGION] 中【短且无空格】的词（保序去重）+ AUTH_EXTRA_TERMS。

    只取短词，是因为 is_relevant 要求标题【逐 token 字面命中】：带空格的多词短语
    （如"私募股权 创业投资 机构"）在官方标题里几乎不可能整串出现，纳进来只会让
    打标与 per_keyword 统计全是空转。
    """
    terms = []
    for node in INDEX_KEYWORDS.values():
        for ind in node["indicators"]:
            for q in ind["queries"][:QUERIES_PER_REGION]:
                if " " not in q and len(q) <= 8 and q not in terms:
                    terms.append(q)
    for t in AUTH_EXTRA_TERMS:
        if t not in terms:
            terms.append(t)
    return terms


AUTH_KEYWORDS = _derive_auth_keywords()

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
# 非新闻文件类型：只挡可执行包/压缩包，**不再把 .pdf 一刀切当噪声**。
# 政策文件、统计公报、名单公示大量是 PDF，techfin_fetch_body.py 已支持 PDF 正文/附件解析；
# 旧规则会把最有价值的官方 PDF 直接丢掉。
NOISE_URL_SUFFIXES = [".exe", ".zip", ".rar", ".7z"]

# 多词检索式的 token 共现窗口（字符）。0 = 不做词距约束（旧行为）。
# 县级检索式常出现"标题有地区、摘要远处有指标"的松散匹配，80 字可挡住大部分。
RELEVANCE_MAX_TOKEN_GAP = 80

# ==================== 采集范围与节奏 ====================

# 搜索引擎：启用哪些源。某源被限流时只跳过该源，其它源继续（见 BACKOFF_BY_SOURCE）。
#
# 【实测结论·2026-10】本轮检索式是"县级地区 + 指标词"，逐源实测如下：
#   - 百度资讯：唯一做严格 AND 的源（"荔湾区 高新技术企业"10 条里 8 条两词兼有），
#     且有绝对日期；但按 IP 限流（IP 级、粘滞，同 IP 下 www/news/m 各站返回同一张
#     验证页）。关键实测：请求间隔拉到 ≥20s 可持续采集（连打 13+ 次全部成功），
#     而 1s 连打几次即被封——【放慢节奏是唯一有效的手段】。
#   - Bing 网页搜索 cn.bing.com/search：不限流（连打 12 次全 200），但只做松散匹配：
#     县级检索式 10 条里地区词命中 8 条、指标词命中 0 条，全被 is_relevant 过滤
#     （0% 通过）；无地区检索式约 1% 可用。=>【不能替代百度做县级】。
#   - 360 新闻：不限流、有日期，但同样松散，县级近乎 0；无地区约 4% 可用。
#   - 搜狗：第 2 次请求即返 200+验证页，限流更甚于百度，不接入。
#   - Google 网络不可达；Bing 新闻垂直（news/search、?format=RSS）已失效。
#
# 结论：县级指标采集【本质只能靠百度】；真正的解法是"放慢百度节奏 + 多轮累积 +
# 断点续跑"，换源收益极小。Bing/360 仅作无限流的补充源，在无地区检索式上略有增益。
ENABLED_SOURCES = ["baidu", "bing", "360"]

# 按检索式粒度选源：键为级别，值为该级别允许的源（仍受 ENABLED_SOURCES / --sources 约束）。
# 级别由计划是否带地区判定：带地区=county（县级单元），不带=none。
# 县级实测只有百度能过相关性过滤，Bing/360 跑它纯属浪费请求，故只留百度；
# 无地区检索式三源并用。
SOURCES_BY_REGION_LEVEL = {
    "county": ("baidu",),
    "none": ("baidu", "bing", "360"),
}

# 按源隔离的退避策略：某源被限流只影响该源，其它源继续采（这是多源改造的核心）。
# backoff = 逐次等待秒数；max_total = 本轮该源最多退避几次，用尽即"跳闸"停用该源。
# 百度实测（20s 间隔）：约每 13 次撞一次限流，且【等待会恢复】——实测退避到 ~8 分钟
# 后下一条即成功。故给一条能覆盖该恢复时间的阶梯 [60,180,300]，但用 max_total 限总量
# （否则一次硬封会在一轮里干等几十分钟）。
BACKOFF_BY_SOURCE = {
    "baidu": {"backoff": [60, 180, 300], "max_total": 2},
    "bing": {"backoff": [10, 30], "max_total": 3},
    "360": {"backoff": [5, 10], "max_total": 2},
}
BACKOFF_DEFAULT = {"backoff": [30], "max_total": 2}

# "松散匹配源"：它们返回的结果可能不含地区词字面量，须用 region_aliases 补偿过滤。
# （注：带地区检索式里 is_relevant 的 token-AND 已隐含地区词约束，此项主要是别名
#  兜底与策略显式化；无地区检索式时 region_aliases 为空，自动失效。）
LOOSE_SOURCES = ("360",)

# 每个源翻多少页。指标检索式取首屏即够，额度要花在"多检索式"而不是"多翻页"上。
MAX_PAGES_PER_KEYWORD = 3          # 默认值（未在 MAX_PAGES_BY_SOURCE 中指定的源用它）
MAX_PAGES_BY_SOURCE = {
    "baidu": 1,
    "bing": 1,
    "360": 1,
}
# 每页之间的礼貌间隔（秒）。实测百度间隔 ≥20s 可持续采集（<20s 很快触发验证页），
# 故百度地板抬高到 20s；Bing/360 几乎不限流，用短间隔即可。
# MIN_DELAY/MAX_DELAY 是百度的默认值（warm_up_session 亦用它）。
# 注意：1116 条 × 约 27s ≈ 8 小时/轮；要更快请用 --limit/--offset 分批多轮累积，
# 或下调 QUERIES_PER_REGION 减少检索式总量。
MIN_DELAY = 20.0
MAX_DELAY = 35.0
DELAY_BY_SOURCE = {
    "baidu": (MIN_DELAY, MAX_DELAY),
    "bing": (1.0, 3.0),
    "360": (1.0, 3.0),
}
DELAY_DEFAULT = (3.0, 6.0)

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
#
# 【2026-10 换栏目·勿删】原三个人行/科技部/发改委源指向的都是"新闻/发布会"栏目
# （人行新闻发布、科技部工作动态、发改委新闻发布），标题全是会谈/发布会口径——
# 600 条里【没有一条】含 A1 指标词，keyword 列全空。故换成真正发布指标内容的栏目：
#   - 广东省科技厅·通知公告：高企认定/取消资格公告、科技型中小企业评价/入库名单公示等，
#     每页 3~7 条命中指标词（实测），且是省级口径、与本主题的广东县级范围同侧。
#   - 广东省科技厅·政策文件：科技政策口径补充。
#   - 科技部·通知通告：国家级补充（命中较少）。
# 分页实测：gdstc 为 index.html + index_2.html…（无 index_1.html）；most 为 index.html + index_1.html…。
# 未采用的站点：科技部科技统计/国家统计局/广东统计局统计年鉴均 404 或 SPA（0 条可解析）。
SOURCES = [
    {
        "name": "gdstc_tzgg",
        "label": "广东省科学技术厅·通知公告",
        "list_url": "https://gdstc.gd.gov.cn/zwgk_n/tzgg/index.html",
        "page_url": "https://gdstc.gd.gov.cn/zwgk_n/tzgg/index_{n}.html",
        "first_page_no": 2,
    },
    {
        "name": "gdstc_zcfg",
        "label": "广东省科学技术厅·政策文件",
        "list_url": "https://gdstc.gd.gov.cn/zwgk_n/zcfg/gfwj/index.html",
        "page_url": "https://gdstc.gd.gov.cn/zwgk_n/zcfg/gfwj/index_{n}.html",
        "first_page_no": 2,
    },
    {
        "name": "most_tztg",
        "label": "科技部·通知通告",
        "list_url": "https://www.most.gov.cn/tztg/index.html",
        "page_url": "https://www.most.gov.cn/tztg/index_{n}.html",
        "first_page_no": 1,
    },
]

# ==================== 产物契约 ====================

# 流水线数据根（相对本主题目录；也可写绝对路径把数据放到仓库外）。
# 可用命令行 --output-dir 临时覆盖。
# 目录分离说明：
#   - data/           流水线数据根：采集累积库、正文缓存、各阶段运行目录、评估；
#   - reports/db_load/ 入库报告根（techfin_load_to_db.py）：load_log/load_report.md/load_errors.csv/load_manifest.json；
#   - techfin_common_output/ 旧产物根：只读保留，新流水线不再写入。
DATA_ROOT = "data"
# 向后兼容别名：旧脚本/旧命令仍可用 cfg.OUTPUT_ROOT。
OUTPUT_ROOT = DATA_ROOT
# CSV → MySQL 入库报告根（techfin_load_to_db.py；相对本主题目录，独立于流水线运行目录序号）。
LOAD_REPORT_ROOT = "reports/db_load"
# 旧产物根：仅用于启动提示与 --input-dir 兼容，不在代码中写入。
LEGACY_OUTPUT_ROOT = "techfin_common_output"
# 运行目录自描述清单文件名（由 config/paths.py 写入）。
RUN_MANIFEST = "_run.json"

# 累积总库（位于输出根内），跨轮读写；每轮目录里的 *_cumulative.csv 是它的快照副本
SEARCH_ACCUMULATOR = "tech_fin_news.csv"
OFFICIAL_ACCUMULATOR = "techfin_official.csv"

# 断点续跑台账（位于输出根内，跨轮读写）：**只是"进度标记"，不是数据文件**——
# 一行 = (source, plan_key, status, n_items, ts)，记录某个 (源, 检索式) 已成功采完。
# 重跑时跳过，避免把稀缺的百度额度重打在已完成的检索式上；--fresh 可忽略它全量重采。
# 数据与出处链接在【运行目录】里：<日期-次数>/search_delta.csv 与 search_links.txt。
# 注：数据是"边采边增量落盘"的（不是跑完才写），且台账只在数据落盘后才记。
SEARCH_PROGRESS = "tech_fin_search_progress.csv"
# 官方源分页进度台账（列：source, page_url, status, n_items, ts）。
# 与搜索台账分开：官方源按"源+页 URL"记录，重跑跳过已成功页。
OFFICIAL_PROGRESS = "official_progress.csv"

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


# ==================== 正文抓取 / 数值抽取 产物契约（techfin_fetch_body.py / techfin_extract_points.py）====================
# techfin_fetch_body.py 与 techfin_extract_points.py 的常量。
# 这些脚本与采集脚本一样只通过文件耦合、互不 import。

# ---- techfin_fetch_body.py 正文缓存（放输出根内、不放运行目录：跨轮复用，是"抽取规则迭代时不重抓网页"的核心）----
BODY_CACHE_DIR = "body_cache"          # 缓存根：正文 JSON/TXT + 附件二进制
BODY_PROGRESS = "body_progress.csv"    # techfin_fetch_body.py 断点续跑台账（source="body", plan_key=<article_id>）
BODY_PREFIX = "body_"                  # techfin_fetch_body.py 每轮产物前缀（body_log / body_index.csv ...）

# ---- techfin_extract_points.py 数值数据点产物 ----
POINTS_ACCUMULATOR = "techfin_a101_points.csv"  # 证据级数据点累积总库（跨轮读写）
POINTS_PREFIX = "points_"                       # techfin_extract_points.py 每轮产物前缀（points_log / points_delta.csv ...）
LLM_CACHE_DIR = "a101_llm_cache"                # LLM 结果缓存（key=article_id+prompt_version+model+md5(正文路径)）
LLM_CALLS_LOG = "a101_llm_calls.jsonl"          # LLM 调用审计（逐次追加）

# ---- techfin_fetch_body.py 二级抓取的节奏与范围 ----
HOST_MIN_INTERVAL = 3.0        # 同一主机两次请求的最小间隔（秒），按 host 礼貌限速
ATTACHMENT_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".wps", ".et")
MAX_ATTACHMENTS_PER_PAGE = 10  # 单页最多下载的附件数（防个别页面挂几十个附件）
ATTACH_MIN_BYTES = 1024        # 小于此字节数的附件视为无效（多为错误页/占位）
ROBOTS_ENFORCE = True          # 是否遵守 robots.txt
BODY_MAX_BYTES = 20 * 1024 * 1024  # 单文件下载上限（防超大附件拖垮）


# ---- techfin_curate.py 语料清洗/提纯产物契约 ----
CURATED_ACCUMULATOR = "curated_corpus.csv"    # 文章级干净语料（跨轮覆盖式重构，幂等）
CURATED_UNCERTAIN = "curated_uncertain.csv"   # 官方"泛化标题"待正文确认队列（供 techfin_fetch_body.py --from-curated 补抓）
CURATED_DROPPED = "curated_dropped.csv"       # 被剔除条目（含原因，便于复核）
CURATED_PREFIX = "curated_"                   # techfin_curate.py 每轮产物前缀（curated_log / curated_report.md ...）


# ---- techfin_ocr.py OCR 补全（扫描件附件）契约与参数 ----
# techfin_ocr.py 读 body_cache、给扫描件 PDF 补 OCR 并写回缓存。纯本地、不发网络请求。
OCR_ENABLED = True
OCR_LANG = "ch"                # PaddleOCR 语言/模型族
OCR_VERSION = "PP-OCRv5"       # 模型版本
# 用 mobile 轻量模型：server 版检测/识别在 CPU 上极慢（实测单页数十秒~分钟），
# mobile 版实测单页 ~4s、识别质量足够，故默认 mobile。
OCR_DET_MODEL = "PP-OCRv5_mobile_det"
OCR_REC_MODEL = "PP-OCRv5_mobile_rec"
OCR_DEVICE = "cpu"             # 本机无 CUDA；有 GPU 可改 "gpu"
OCR_DPI = 200                  # PDF 渲染分辨率（越大越准越慢）
OCR_MAX_PAGES = 50             # 单个附件最多 OCR 的页数（防超大件拖垮）
OCR_PREFIX = "ocr_"            # techfin_ocr.py 每轮产物前缀（ocr_log / ocr_index.csv）


# 印证报告文件名
REPORT_NAME = "corpus_verification.md"

# 列顺序：链接与日期放显眼位置，方便手动查看
# 注：level* / region* / indicators 是"A1 指标化采集"新增的列。旧语料（题目级关键词采的
# 那批）没有这些列，合并时会被 fillna("") 清成空串——旧行指标列为空是【正确语义】，
# 它们本就不对应任何一条指标。下游做指标分析时用 level3_name != "" 过滤即可。
SEARCH_COLS = [
    "article_id", "title", "source", "date", "date_raw", "date_url",
    "keyword",
    "level1_code", "level1_name", "level2_code", "level2_name",
    "level3_code", "level3_name", "unit",
    "region_code", "region_name",
    "indicators",
    "engine", "engines", "url", "real_url",
    "summary", "title_is_url", "crawl_time",
]
OFFICIAL_COLS = [
    "article_id", "title", "source", "date", "date_raw", "keyword",
    "url", "crawl_time",
]


# ==================== 检索计划生成 ====================
# 下面是纯数据变换（不 import 本地模块、不读数据库、不发请求），把上面的常量展平成
# 爬虫直接消费的检索计划。放在配置里而不是爬虫里，是为了让"A1 有哪些指标、每个指标
# 用什么词、覆盖哪些地区"这件事始终只用改这一个文件。

def guangdong_regions(cities=None):
    """
    展平 GUANGDONG_REGIONS 为 [(region_code, region_name, query_name, city_name), ...]。

    query_name 是实际拼进检索式的名字（默认取区划名，个别泛化名见
    REGION_QUERY_NAME_OVERRIDES）。cities 非空时只取指定地级市，便于按市分区推进。
    东莞/中山是不设区的地级市，GUANGDONG_REGIONS 里以市本级作为唯一单元。
    """
    out = []
    for city, units in GUANGDONG_REGIONS.items():
        if cities and city not in cities:
            continue
        for code, name in units:
            out.append((code, name, REGION_QUERY_NAME_OVERRIDES.get(code, name), city))
    return out


def _make_plan(node, l2, ind, query, region_code, region_name, city_name,
               anchor_terms):
    return {
        "query": query,
        "level1_code": node["level1_code"],
        "level1_name": node["level1_name"],
        "level2_code": l2,
        "level2_name": node["level2_name"],
        "level3_code": ind["level3_code"],
        "level3_name": ind["level3_name"],
        "unit": ind["unit"],
        "region_code": region_code,
        "region_name": region_name,
        "city_name": city_name,
        # 带地区时为 ()（地区词已是强约束，见 ANCHOR_TERMS 上方实测说明）；
        # 不带地区时用主题锚点，防止"高新技术企业"泛化到全国无关报道。
        "anchor_terms": anchor_terms,
        # 地区别名（规范名 / 市名），供松散源（LOOSE_SOURCES）做"必需地区词"补偿过滤；
        # 无地区检索式时为空元组，required_terms 自动失效。
        "region_aliases": tuple(filter(None, {region_name, city_name})),
    }


def build_search_plan(level2_codes=None, regions=None, queries_per_region=None,
                      offset=0, limit=None):
    """
    把 INDEX_KEYWORDS 展平成爬虫直接消费的检索计划（每个检索式一条 dict）。

    参数：
      level2_codes        只展开指定二级指标（如 ["A1"]）；None 表示全部。
      regions             guangdong_regions() 的返回值（[(code, name, query_name, city)]）。
                          非空时，检索式前拼地区名（如"南山区 高新技术企业"），且每条指标
                          只取前 queries_per_region 条检索式。传空/None 则不带地区。
      queries_per_region  带地区时每条指标取几条检索式，默认 QUERIES_PER_REGION。
      offset / limit      对最终计划切片。地区为外层、指标为内层，所以一片 = 若干个完整
                          地区，用于把总请求量切成多次运行，绕开百度按 IP 限流。

    返回的 list 直接交给 TechFin-bug.crawl_all_keywords()。
    """
    regions = list(regions or [])
    if queries_per_region is None:
        queries_per_region = QUERIES_PER_REGION

    # 指标维度的展开（地区为内层时复用这份）
    base = []
    for l2, node in INDEX_KEYWORDS.items():
        if level2_codes and l2 not in level2_codes:
            continue
        for ind in node["indicators"]:
            queries = ind["queries"]
            if regions:
                queries = queries[:queries_per_region]
            for q in queries:
                base.append((l2, node, ind, q))

    plans = []
    if regions:
        for code, name, qname, city in regions:
            for l2, node, ind, q in base:
                plans.append(_make_plan(node, l2, ind, f"{qname} {q}", code, name,
                                        city, ()))
    else:
        for l2, node, ind, q in base:
            plans.append(_make_plan(node, l2, ind, q, "", "", "",
                                    tuple(ANCHOR_TERMS)))

    return plans[offset: None if limit is None else offset + limit]
