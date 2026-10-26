-- ============================================================================
-- TechFin · 21 指标、口径与来源渠道种子
-- ============================================================================
-- 依赖：10_schema.sql、20_seed_region.sql
--
-- 数据来源：
--   - 35 条三级指标的名称/编码与 A/A1/config/topic_config.py 的 INDEX_KEYWORDS 对齐。
--   - 一级维度：A 科技金融资源基础 / B 科技金融活动强度 /
--               C 科技金融产出效能 / D 科技金融生态环境。
--   - 权重不在此脚本中：权重唯一权威是表外配置文件（如 config/weights/weights_v1.yaml）。
--
-- 口径设计：
--   每个三级指标至少一个口径，且恰好一个 is_primary=1。
--   A101 拆 count（家）/ density（家/万人）；
--   D102 拆 count（个）/ coverage（%）。
--   事实表通过 (level3_code, measure_code) 外键引用本表。
--
-- 幂等：使用 ON DUPLICATE KEY UPDATE，可重复执行。
-- ============================================================================

SET NAMES utf8mb4;
USE techfin;

-- ----------------------------------------------------------------------------
-- 1. 来源渠道
-- ----------------------------------------------------------------------------
INSERT INTO dim_source_channel
(channel_code, channel_name, default_quality, description) VALUES
('yearbook',    '统计年鉴',             1, '官方统计年鉴、统计公报'),
('bulletin',    '政府公报/部门公开数据', 2, '政府公报、部门公开数据、通知公告'),
('news_corpus', '新闻语料抽取',         3, '搜索引擎新闻语料 + 规则/LLM 抽取'),
('third_party', '第三方机构',           3, '第三方研究机构、数据库、榜单'),
('expert',      '专家打分/调研估算',     4, '专家评分、调研估算、推算值'),
('none',        '无来源/占位',           4, '缺失、不适用或尚未录入的占位')
ON DUPLICATE KEY UPDATE
    channel_name    = VALUES(channel_name),
    default_quality = VALUES(default_quality),
    description     = VALUES(description);

-- ----------------------------------------------------------------------------
-- 2. 三级指标
-- ----------------------------------------------------------------------------
INSERT INTO dim_index
(level3_code, level2_code, level2_name, level1_code, level1_name,
 level3_name, is_county_applicable, county_replacement, status, sort_order, note) VALUES
-- A 科技金融资源基础
('A101','A1','科技企业资源','A','科技金融资源基础','高新技术企业数量/密度',1,NULL,'active',1,NULL),
('A102','A1','科技企业资源','A','科技金融资源基础','科技型中小企业库入库数量',1,NULL,'active',2,NULL),
('A103','A1','科技企业资源','A','科技金融资源基础','R&D经费支出占GDP比重',1,NULL,'active',3,NULL),

('A201','A2','金融资源供给','A','科技金融资源基础','本外币存款余额',1,NULL,'active',1,NULL),
('A202','A2','金融资源供给','A','科技金融资源基础','私募股权/创业投资机构数量',0,'县域内注册的有限合伙企业/私募基金','active',2,NULL),
('A203','A2','金融资源供给','A','科技金融资源基础','私募股权/创业投资机构管理资本量',0,'县域内注册的有限合伙企业/私募基金','active',3,NULL),
('A204','A2','金融资源供给','A','科技金融资源基础','银行业金融机构网点密度',1,NULL,'active',4,NULL),

('A301','A3','政府支持力度','A','科技金融资源基础','财政科技拨款占公共预算支出比重',1,NULL,'active',1,NULL),
('A302','A3','政府支持力度','A','科技金融资源基础','科技金融风险补偿资金池规模',1,NULL,'active',2,NULL),

-- B 科技金融活动强度
('B101','B1','债权融资活跃度','B','科技金融活动强度','科技企业贷款余额',1,NULL,'active',1,'县域数据渠道可考虑县级农商行/农信社科技贷款'),
('B102','B1','债权融资活跃度','B','科技金融活动强度','科技企业贷款余额增速',1,NULL,'active',2,'县域数据渠道可考虑县级农商行/农信社科技贷款'),
('B103','B1','债权融资活跃度','B','科技金融活动强度','知识产权质押融资额',1,NULL,'active',3,NULL),
('B104','B1','债权融资活跃度','B','科技金融活动强度','信用贷款占比',1,NULL,'active',4,NULL),

('B201','B2','股权融资活跃度','B','科技金融活动强度','VC/PE投资案例数',0,'县域内企业获得的天使轮/A轮融资','active',1,NULL),
('B202','B2','股权融资活跃度','B','科技金融活动强度','VC/PE投资金额',0,'县域内企业获得的天使轮/A轮融资','active',2,NULL),
('B203','B2','股权融资活跃度','B','科技金融活动强度','科创板/创业板等上市科技企业数量',0,'新三板/区域股权交易中心挂牌企业数量','active',3,NULL),
('B204','B2','股权融资活跃度','B','科技金融活动强度','科创板/创业板等上市科技企业募资额',0,'新三板/区域股权交易中心挂牌企业数量','active',4,NULL),

('B301','B3','政策性融资效果','B','科技金融活动强度','科技保险保费收入',0,'县级科技保险保费收入（如有）','active',1,NULL),
('B302','B3','政策性融资效果','B','科技金融活动强度','创业担保贷款发放额',1,NULL,'active',2,NULL),

('B401','B4','金融科技应用','B','科技金融活动强度','移动支付交易额',1,NULL,'active',1,NULL),
('B402','B4','金融科技应用','B','科技金融活动强度','数字普惠金融指数',1,NULL,'active',2,'可考虑北大数字普惠金融指数（县域层面）'),
('B403','B4','金融科技应用','B','科技金融活动强度','大数据/AI在风控中的应用情况',0,'县级金融科技应用情况（定性）','active',3,NULL),

-- C 科技金融产出效能
('C101','C1','科技创新产出','C','科技金融产出效能','PCT国际专利申请量',0,'县级PCT申请量（如有）','active',1,NULL),
('C102','C1','科技创新产出','C','科技金融产出效能','技术合同成交额',1,NULL,'active',2,NULL),
('C103','C1','科技创新产出','C','科技金融产出效能','每万人发明专利拥有量',1,NULL,'active',3,NULL),

('C201','C2','经济发展贡献','C','科技金融产出效能','高新技术产业产值占规上工业产值比重',1,NULL,'active',1,NULL),
('C202','C2','经济发展贡献','C','科技金融产出效能','科技服务业增加值',1,NULL,'active',2,NULL),

('C301','C3','企业成长性','C','科技金融产出效能','"独角兽"、"瞪羚"企业数量',1,NULL,'active',1,NULL),
('C302','C3','企业成长性','C','科技金融产出效能','科技企业营收增长率',1,NULL,'active',2,NULL),
('C303','C3','企业成长性','C','科技金融产出效能','科技企业利润增长率',1,NULL,'active',3,NULL),

-- D 科技金融生态环境
('D101','D1','政策与服务环境','D','科技金融生态环境','科技金融政策完备度（专家打分）',1,NULL,'active',1,'可考虑县级科技金融政策文本评分'),
('D102','D1','政策与服务环境','D','科技金融生态环境','科技金融服务中心/网点覆盖情况',1,NULL,'active',2,'按“个”或“%”两个口径分别记录'),

('D201','D2','人力与创新环境','D','科技金融生态环境','R&D人员全时当量',1,NULL,'active',1,NULL),
('D202','D2','人力与创新环境','D','科技金融生态环境','普通高等学校数量',1,NULL,'active',2,NULL),
('D203','D2','人力与创新环境','D','科技金融生态环境','数字经济发展指数',1,NULL,'active',3,NULL)
ON DUPLICATE KEY UPDATE
    level2_code          = VALUES(level2_code),
    level2_name          = VALUES(level2_name),
    level1_code          = VALUES(level1_code),
    level1_name          = VALUES(level1_name),
    level3_name          = VALUES(level3_name),
    is_county_applicable = VALUES(is_county_applicable),
    county_replacement   = VALUES(county_replacement),
    status               = VALUES(status),
    sort_order           = VALUES(sort_order),
    note                 = VALUES(note);

-- ----------------------------------------------------------------------------
-- 3. 指标口径/单位
-- ----------------------------------------------------------------------------
INSERT INTO dim_index_measure
(level3_code, measure_code, measure_name, standard_unit, value_kind, is_primary, sort_order) VALUES
('A101','count',      '企业数量',                 '家',       'numeric', 1, 1),
('A101','density',    '企业密度',                 '家/万人',  'numeric', 0, 2),
('A102','count',      '入库企业数量',             '家',       'numeric', 1, 1),
('A103','share',      'R&D经费占GDP比重',         '%',        'numeric', 1, 1),

('A201','amount',     '存款余额',                 '亿元',     'numeric', 1, 1),
('A202','count',      '机构数量',                 '家',       'numeric', 1, 1),
('A203','amount',     '管理资本量',               '亿元',     'numeric', 1, 1),
('A204','density',    '网点密度',                 '个/万人',  'numeric', 1, 1),

('A301','share',      '财政科技拨款占比',         '%',        'numeric', 1, 1),
('A302','amount',     '资金池规模',               '万元',     'numeric', 1, 1),

('B101','amount',     '贷款余额',                 '亿元',     'numeric', 1, 1),
('B102','growth',     '贷款余额增速',             '%',        'numeric', 1, 1),
('B103','amount',     '质押融资额',               '亿元',     'numeric', 1, 1),
('B104','share',      '信用贷款占比',             '%',        'numeric', 1, 1),

('B201','count',      '投资案例数',               '起',       'numeric', 1, 1),
('B202','amount',     '投资金额',                 '亿元',     'numeric', 1, 1),
('B203','count',      '上市企业数量',             '家',       'numeric', 1, 1),
('B204','amount',     '募资额',                   '亿元',     'numeric', 1, 1),

('B301','amount',     '保费收入',                 '亿元',     'numeric', 1, 1),
('B302','amount',     '贷款发放额',               '亿元',     'numeric', 1, 1),

('B401','amount',     '交易额',                   '亿元',     'numeric', 1, 1),
('B402','index',      '指数值',                   '指数值',   'numeric', 1, 1),
('B403','score',      '应用情况评分',             '分',       'score',   1, 1),

('C101','count',      '申请量',                   '件',       'numeric', 1, 1),
('C102','amount',     '成交额',                   '亿元',     'numeric', 1, 1),
('C103','density',    '每万人发明专利拥有量',     '件/万人',  'numeric', 1, 1),

('C201','share',      '产值占比',                 '%',        'numeric', 1, 1),
('C202','amount',     '增加值',                   '亿元',     'numeric', 1, 1),

('C301','count',      '企业数量',                 '家',       'numeric', 1, 1),
('C302','growth',     '营收增长率',               '%',        'numeric', 1, 1),
('C303','growth',     '利润增长率',               '%',        'numeric', 1, 1),

('D101','score',      '政策完备度评分',           '分',       'score',   1, 1),
('D102','count',      '服务中心/网点数量',        '个',       'numeric', 1, 1),
('D102','coverage',   '服务中心/网点覆盖率',      '%',        'numeric', 0, 2),

('D201','amount',     '全时当量',                 '人年',     'numeric', 1, 1),
('D202','count',      '高校数量',                 '所',       'numeric', 1, 1),
('D203','index',      '指数值',                   '指数值',   'numeric', 1, 1)
ON DUPLICATE KEY UPDATE
    measure_name  = VALUES(measure_name),
    standard_unit = VALUES(standard_unit),
    value_kind    = VALUES(value_kind),
    is_primary    = VALUES(is_primary),
    sort_order    = VALUES(sort_order);

-- 自检（应输出 35 / 37 / 0 / 0）：
-- SELECT COUNT(*) AS 指标数 FROM dim_index;
-- SELECT COUNT(*) AS 口径数 FROM dim_index_measure;
-- SELECT COUNT(*) AS 无主口径指标数
--   FROM dim_index i
--   LEFT JOIN dim_index_measure m
--     ON m.level3_code = i.level3_code AND m.is_primary = 1
--  WHERE m.level3_code IS NULL;
-- SELECT COUNT(*) AS 多主口径指标数
--   FROM (SELECT level3_code FROM dim_index_measure
--          WHERE is_primary = 1 GROUP BY level3_code HAVING COUNT(*) > 1) t;
