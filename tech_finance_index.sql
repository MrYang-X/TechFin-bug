-- ============================================================================
-- 县域科技金融指数研究 · 数据库【首建脚本】
-- ============================================================================
--
--  在 2026-10-04 那版（库名 tech_finance_county，113 行）基础上补齐。保留原版的
--  全部字段与设计取向（region_type / is_county_applicable / county_replacement /
--  data_quality / 区划沿革），只做以下增量：
--
--  ① dim_region 补全数据：原版只有 8 行示例（清远 6 + 成都 2），广东 124 个县域
--     分析单元里只有 6 个有码 —— 县域研究没得做。现补到完整 148 行。
--  ② dim_region 补 is_analysis_unit：东莞 441900 / 中山 442000 是不设区地级市
--     （region_level=2），但【本身即县域分析单元】。任何 WHERE region_level = 3
--     都会把它们悄悄丢掉 —— 124 变 122，不报错。判据从此统一为 is_analysis_unit = 1。
--  ③ dim_region 补 region_full_name：441502 常用名"城区"，报表会与别市城区混淆。
--  ④ dim_index 补 level3_code：稳定 join 键。原版唯一键是 (level2_code, level3_name)，
--     中文口径名一改（改口径、加限定词）就被当成新指标，历史数据断链。
--  ⑤ dim_index 补 level2_weight / level3_weight：原版只有一级权重，加权合成算不出来。
--  ⑥ dim_index 复合指标拆分：原版 5 条指标把两个度量塞在一行（单位形如"亿元/%"），
--     单个 DECIMAL 列根本装不下、也无法统一赋权。拆成单一单位（30 → 35 条）。
--  ⑦ dim_index 保留原版 is_county_applicable / county_replacement，随拆分【机械传播】
--     （拆出的两行继承同一个适用性标记与替代建议，不改研究判断）。
--  ⑧ fact 表补 source_type 并改唯一键：原键 (region_code, year, index_id) 规定一地区
--     一年一指标只能一行，容不下"官方统计值 + 新闻估算值并存"。改为四元组。
--  ⑨ fact 表补 evidence_ref（轻量指回 CSV 的 article_id）与 idx_year_index。
--  ⑩ 新增 fact_index_score：合成指数结果与原始观测【分层】存放。
--
--  ⚠ 本脚本会 DROP TABLE 后重建。对空库/全新环境执行。若库里已有数据，
--    请改用 tech_finance_index_migration.sql（零 DROP TABLE）。
--
--  执行前自查：
--      SELECT COUNT(*) FROM information_schema.tables
--       WHERE table_schema = 'tech_finance_county';
--  返回非 0 且事实表有行 → 停手，走迁移脚本。
-- ============================================================================


-- ==========================================
-- 0. 会话与库
-- ==========================================
-- 客户端连接字符集若非 utf8mb4，下面所有中文都会乱码写入
SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS tech_finance_county
DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE tech_finance_county;

-- DROP 前先关外键检查，避免 DROP 顺序被外键依赖卡住
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS fact_index_score;
DROP TABLE IF EXISTS fact_tech_finance_data;
DROP TABLE IF EXISTS dim_index;
DROP TABLE IF EXISTS dim_region;
SET FOREIGN_KEY_CHECKS = 1;


-- ==========================================
-- 1. 行政区划维度表 (dim_region)
-- ==========================================
CREATE TABLE dim_region (
    id               INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    region_code      VARCHAR(12) NOT NULL COMMENT '行政区划代码（如441882）',
    region_name      VARCHAR(50) NOT NULL COMMENT '行政区划名称（如连州市）。须与 topic_config.GUANGDONG_REGIONS 及 CSV 的 region_name 完全一致',
    region_full_name VARCHAR(60) NOT NULL DEFAULT '' COMMENT '规范全称（如"汕尾市城区"）。仅用于报表展示消歧，不参与 join',
    region_level     TINYINT NOT NULL COMMENT '层级：1-省，2-市，3-县/区',
    region_type      VARCHAR(20) DEFAULT NULL COMMENT '县域类型：普通县、县级市、市辖区、自治县、地级市、省级',
    parent_code      VARCHAR(12) DEFAULT NULL COMMENT '父级行政区划代码（地级市代码）',
    city_code        VARCHAR(12) DEFAULT NULL COMMENT '所属地级市代码（方便跨县分组比对）',
    is_analysis_unit TINYINT NOT NULL DEFAULT 0 COMMENT '是否县域分析单元：1-是。东莞441900/中山442000 为 level=2 但本身即县域单元，须置1',
    is_active        TINYINT DEFAULT 1 COMMENT '是否现行有效：1-有效，0-已撤销/合并',
    start_year       INT DEFAULT NULL COMMENT '该区划代码生效年份（区划沿革）',
    end_year         INT DEFAULT NULL COMMENT '该区划代码失效年份，NULL=现行',
    UNIQUE KEY uk_region_code (region_code),
    KEY idx_parent_code (parent_code),
    KEY idx_city_code (city_code),
    KEY idx_analysis_unit (is_analysis_unit, region_level, parent_code),
    CONSTRAINT ck_region_level CHECK (region_level IN (1,2,3)),
    CONSTRAINT ck_region_span  CHECK (end_year IS NULL OR start_year IS NULL
                                      OR end_year >= start_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='行政区划维度表（支持县域历史变更）';

-- 插入广东省完整区划：21 个地级市 + 122 个县级单位（共 144 行），
-- 末尾附四川省 4 行作示例。来源：民政部《2024 年中华人民共和国县以上行政区划代码》，
-- 与 A/A1/topic_config.py 的 GUANGDONG_REGIONS 同源同码——改一处务必同步另一处。
-- 注：东莞市(441900)、中山市(442000) 为不设区的地级市，下辖无县级单位，
--     故只到市级，但它们的 is_analysis_unit = 1（即本研究的县域采集单元）。
-- 依赖说明：parent_code 未加自引用外键（区划表常被裁剪，加了容易在导入部分数据时失败），
--           层级完整性由索引 + 应用层保证。
INSERT INTO dim_region (region_code, region_name, region_level, parent_code) VALUES
('440000', '广东省', 1, NULL),
('440100', '广州市', 2, '440000'),
('440103', '荔湾区', 3, '440100'),
('440104', '越秀区', 3, '440100'),
('440105', '海珠区', 3, '440100'),
('440106', '天河区', 3, '440100'),
('440111', '白云区', 3, '440100'),
('440112', '黄埔区', 3, '440100'),
('440113', '番禺区', 3, '440100'),
('440114', '花都区', 3, '440100'),
('440115', '南沙区', 3, '440100'),
('440117', '从化区', 3, '440100'),
('440118', '增城区', 3, '440100'),
('440200', '韶关市', 2, '440000'),
('440203', '武江区', 3, '440200'),
('440204', '浈江区', 3, '440200'),
('440205', '曲江区', 3, '440200'),
('440222', '始兴县', 3, '440200'),
('440224', '仁化县', 3, '440200'),
('440229', '翁源县', 3, '440200'),
('440232', '乳源瑶族自治县', 3, '440200'),
('440233', '新丰县', 3, '440200'),
('440281', '乐昌市', 3, '440200'),
('440282', '南雄市', 3, '440200'),
('440300', '深圳市', 2, '440000'),
('440303', '罗湖区', 3, '440300'),
('440304', '福田区', 3, '440300'),
('440305', '南山区', 3, '440300'),
('440306', '宝安区', 3, '440300'),
('440307', '龙岗区', 3, '440300'),
('440308', '盐田区', 3, '440300'),
('440309', '龙华区', 3, '440300'),
('440310', '坪山区', 3, '440300'),
('440311', '光明区', 3, '440300'),
('440400', '珠海市', 2, '440000'),
('440402', '香洲区', 3, '440400'),
('440403', '斗门区', 3, '440400'),
('440404', '金湾区', 3, '440400'),
('440500', '汕头市', 2, '440000'),
('440507', '龙湖区', 3, '440500'),
('440511', '金平区', 3, '440500'),
('440512', '濠江区', 3, '440500'),
('440513', '潮阳区', 3, '440500'),
('440514', '潮南区', 3, '440500'),
('440515', '澄海区', 3, '440500'),
('440523', '南澳县', 3, '440500'),
('440600', '佛山市', 2, '440000'),
('440604', '禅城区', 3, '440600'),
('440605', '南海区', 3, '440600'),
('440606', '顺德区', 3, '440600'),
('440607', '三水区', 3, '440600'),
('440608', '高明区', 3, '440600'),
('440700', '江门市', 2, '440000'),
('440703', '蓬江区', 3, '440700'),
('440704', '江海区', 3, '440700'),
('440705', '新会区', 3, '440700'),
('440781', '台山市', 3, '440700'),
('440783', '开平市', 3, '440700'),
('440784', '鹤山市', 3, '440700'),
('440785', '恩平市', 3, '440700'),
('440800', '湛江市', 2, '440000'),
('440802', '赤坎区', 3, '440800'),
('440803', '霞山区', 3, '440800'),
('440804', '坡头区', 3, '440800'),
('440811', '麻章区', 3, '440800'),
('440823', '遂溪县', 3, '440800'),
('440825', '徐闻县', 3, '440800'),
('440881', '廉江市', 3, '440800'),
('440882', '雷州市', 3, '440800'),
('440883', '吴川市', 3, '440800'),
('440900', '茂名市', 2, '440000'),
('440902', '茂南区', 3, '440900'),
('440904', '电白区', 3, '440900'),
('440981', '高州市', 3, '440900'),
('440982', '化州市', 3, '440900'),
('440983', '信宜市', 3, '440900'),
('441200', '肇庆市', 2, '440000'),
('441202', '端州区', 3, '441200'),
('441203', '鼎湖区', 3, '441200'),
('441204', '高要区', 3, '441200'),
('441223', '广宁县', 3, '441200'),
('441224', '怀集县', 3, '441200'),
('441225', '封开县', 3, '441200'),
('441226', '德庆县', 3, '441200'),
('441284', '四会市', 3, '441200'),
('441300', '惠州市', 2, '440000'),
('441302', '惠城区', 3, '441300'),
('441303', '惠阳区', 3, '441300'),
('441322', '博罗县', 3, '441300'),
('441323', '惠东县', 3, '441300'),
('441324', '龙门县', 3, '441300'),
('441400', '梅州市', 2, '440000'),
('441402', '梅江区', 3, '441400'),
('441403', '梅县区', 3, '441400'),
('441422', '大埔县', 3, '441400'),
('441423', '丰顺县', 3, '441400'),
('441424', '五华县', 3, '441400'),
('441426', '平远县', 3, '441400'),
('441427', '蕉岭县', 3, '441400'),
('441481', '兴宁市', 3, '441400'),
('441500', '汕尾市', 2, '440000'),
('441502', '城区', 3, '441500'),
('441521', '海丰县', 3, '441500'),
('441523', '陆河县', 3, '441500'),
('441581', '陆丰市', 3, '441500'),
('441600', '河源市', 2, '440000'),
('441602', '源城区', 3, '441600'),
('441621', '紫金县', 3, '441600'),
('441622', '龙川县', 3, '441600'),
('441623', '连平县', 3, '441600'),
('441624', '和平县', 3, '441600'),
('441625', '东源县', 3, '441600'),
('441700', '阳江市', 2, '440000'),
('441702', '江城区', 3, '441700'),
('441704', '阳东区', 3, '441700'),
('441721', '阳西县', 3, '441700'),
('441781', '阳春市', 3, '441700'),
('441800', '清远市', 2, '440000'),
('441802', '清城区', 3, '441800'),
('441803', '清新区', 3, '441800'),
('441821', '佛冈县', 3, '441800'),
('441823', '阳山县', 3, '441800'),
('441825', '连山壮族瑶族自治县', 3, '441800'),
('441826', '连南瑶族自治县', 3, '441800'),
('441881', '英德市', 3, '441800'),
('441882', '连州市', 3, '441800'),
('441900', '东莞市', 2, '440000'),
('442000', '中山市', 2, '440000'),
('445100', '潮州市', 2, '440000'),
('445102', '湘桥区', 3, '445100'),
('445103', '潮安区', 3, '445100'),
('445122', '饶平县', 3, '445100'),
('445200', '揭阳市', 2, '440000'),
('445202', '榕城区', 3, '445200'),
('445203', '揭东区', 3, '445200'),
('445222', '揭西县', 3, '445200'),
('445224', '惠来县', 3, '445200'),
('445281', '普宁市', 3, '445200'),
('445300', '云浮市', 2, '440000'),
('445302', '云城区', 3, '445300'),
('445303', '云安区', 3, '445300'),
('445321', '新兴县', 3, '445300'),
('445322', '郁南县', 3, '445300'),
('445381', '罗定市', 3, '445300'),
('510000', '四川省', 1, NULL),
('510100', '成都市', 2, '510000'),
('510121', '金堂县', 3, '510100'),
('510181', '都江堰市', 3, '510100');

-- ---- 派生列回填（避免 148 行 INSERT 塞 8 个字段，规则集中在此，便于审计）----
-- 每条都带 WHERE 守卫：本脚本先 DROP 再重建，字段值为空/默认值，命中行数不变；
-- 既消除 IDE「不带 where 的 Update」告警，也让本段可安全重复执行。

-- 1) region_type 按名称后缀分类。顺序要紧：自治县必须在"县"之前判断
UPDATE dim_region SET region_type = CASE
    WHEN region_level = 1              THEN '省级'
    WHEN region_level = 2              THEN '地级市'
    WHEN region_name LIKE '%自治县'    THEN '自治县'
    WHEN region_name LIKE '%区'        THEN '市辖区'
    WHEN region_name LIKE '%县'        THEN '普通县'
    WHEN region_name LIKE '%市'        THEN '县级市'
    END
 WHERE region_type IS NULL;

-- 2) city_code：市本级填自身，县区填所属地级市，省填 NULL
UPDATE dim_region SET city_code = CASE
    WHEN region_level = 2 THEN region_code
    WHEN region_level = 3 THEN parent_code
    END
 WHERE city_code IS NULL;

-- 3) 全称默认等于常用名，再覆盖有歧义的少数几个
UPDATE dim_region SET region_full_name = region_name WHERE region_full_name = '';
UPDATE dim_region SET region_full_name = '汕尾市城区'       WHERE region_code = '441502';
UPDATE dim_region SET region_full_name = '东莞市（市本级）' WHERE region_code = '441900';
UPDATE dim_region SET region_full_name = '中山市（市本级）' WHERE region_code = '442000';

-- 4) 县域分析单元 = 全部县/区 + 东莞/中山两个不设区地级市。
--    此后所有"取县域"的查询统一用 is_analysis_unit = 1，
--    绝不要再写 region_level = 3 —— 那会【静默】丢掉东莞和中山。
UPDATE dim_region SET is_analysis_unit = 1
 WHERE region_level = 3 OR region_code IN ('441900', '442000');

-- 5) 区划沿革生效年份。已知的几个（清远系沿用原版给定值）；
--    其余留 NULL 待补 —— 编造年份比留空更危险。
--    影响多年面板的主要变更：2014 年从化/增城撤市设区（440117/440118）、
--    2012 年清新撤县设区（441803）、2014 年电白撤县设区（440904）等，
--    补 start_year 时优先补这些。
UPDATE dim_region SET start_year = 1988 WHERE region_code IN ('441800','441802','441821','441825');
UPDATE dim_region SET start_year = 2012 WHERE region_code = '441803';
UPDATE dim_region SET start_year = 1994 WHERE region_code = '441882';
UPDATE dim_region SET start_year = 1976 WHERE region_code = '510121';
UPDATE dim_region SET start_year = 1988 WHERE region_code = '510181';

-- 自检：广东省部分必须返回 124。
-- 注意务必带上 LIKE '44%' —— 库里还挂着四川省的 4 行示例，不加省份过滤会更多。
-- SELECT COUNT(*) AS 广东县域分析单元 FROM dim_region
--  WHERE is_analysis_unit = 1 AND region_code LIKE '44%';
-- SELECT region_type, COUNT(*) FROM dim_region GROUP BY region_type;   -- 不应有 NULL


-- ==========================================
-- 2. 科技金融指标维度表 (dim_index)
-- ==========================================
DROP TABLE IF EXISTS dim_index;
CREATE TABLE dim_index (
    id                   INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    level1_code          VARCHAR(10)  NOT NULL COMMENT '一级维度代码（A/B/C/D）',
    level1_name          VARCHAR(50)  NOT NULL COMMENT '一级维度名称',
    level1_weight        DECIMAL(5,2) NOT NULL COMMENT '一级维度权重(%)，全表一级合计=100',
    level2_code          VARCHAR(10)  NOT NULL COMMENT '二级指标代码（A1/A2…）',
    level2_name          VARCHAR(50)  NOT NULL COMMENT '二级指标名称',
    level2_weight        DECIMAL(5,2) NOT NULL COMMENT '二级指标在所属一级维度内权重(%)，同一 level1_code 下合计=100',
    level3_code          VARCHAR(20)  NOT NULL COMMENT '三级指标稳定业务编码（如 A101）。重命名不失效，是 join 与 upsert 的依据',
    level3_name          VARCHAR(100) NOT NULL COMMENT '三级指标统计口径名。须与 topic_config.INDEX_KEYWORDS 及 CSV 的 level3_name 完全一致',
    level3_weight        DECIMAL(5,2) NOT NULL COMMENT '三级指标在所属二级指标内权重(%)，同一 level2_code 下合计=100',
    unit                 VARCHAR(20)  DEFAULT NULL COMMENT '指标单位。"或"结构（如 家或家/万人）表示二选一口径，入库时须归一到唯一口径',
    is_county_applicable TINYINT      NOT NULL DEFAULT 1 COMMENT '是否适用于县域研究：1-适用，0-需替换或不适用',
    county_replacement   VARCHAR(100) DEFAULT NULL COMMENT '县域替代指标建议（若不适用）',
    UNIQUE KEY uk_level3_code (level3_code),
    UNIQUE KEY uk_level3 (level2_code, level3_name),
    KEY idx_level1 (level1_code),
    KEY idx_level2 (level2_code),
    KEY idx_county_applicable (is_county_applicable),
    CONSTRAINT ck_l1w CHECK (level1_weight BETWEEN 0 AND 100),
    CONSTRAINT ck_l2w CHECK (level2_weight BETWEEN 0 AND 100),
    CONSTRAINT ck_l3w CHECK (level3_weight BETWEEN 0 AND 100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='科技金融指标维度表（含县域适用性与替代口径）';

-- 指标体系：A 资源基础 25% / B 活动强度 35% / C 产出效能 30% / D 生态环境 10%
--
-- 【复合指标拆分】原 30 条里有 5 条把两个度量塞在同一行（单位形如"亿元/%"），
-- 单个 DECIMAL 列根本装不下，也无法统一赋权。已拆成单一单位的独立指标（30 → 35 条）：
--     私募股权/创业投资机构数量与管理资本量 [家/亿元]
--         → A202 机构数量 [家] + A203 管理资本量 [亿元]
--     科技企业贷款余额及增速 [亿元/%]
--         → B101 贷款余额 [亿元] + B102 余额增速 [%]
--     VC/PE投资案例数与金额 [起/亿元]
--         → B201 案例数 [起] + B202 投资金额 [亿元]
--     科创板/创业板等上市科技企业数量与募资额 [家/亿元]
--         → B203 企业数量 [家] + B204 募资额 [亿元]
--     科技企业营收和利润增长率 [%]（隐性双值）
--         → C302 营收增长率 [%] + C303 利润增长率 [%]
--
-- 【is_county_applicable / county_replacement 的来源】完整沿用原版 30 条的标注，
-- 拆出的两行【机械继承】原行的同一个标记与替代建议，未改任何研究判断。
-- 注意：原版存在"标 1（适用）同时给了替代建议"的写法（如 B101/B102/B402/D101 等），
-- 语义上是矛盾的——标了适用就不需要替代口径。此处原样保留，建议回头复核一遍。
INSERT INTO dim_index
(level1_code, level1_name, level1_weight, level2_code, level2_name, level2_weight,
 level3_code, level3_name, level3_weight, unit, is_county_applicable, county_replacement) VALUES

-- A. 科技金融资源基础 (25%)
('A','科技金融资源基础',25.00,'A1','科技企业资源',40.00,'A101','高新技术企业数量/密度',40.00,'家或家/万人',1,NULL),
('A','科技金融资源基础',25.00,'A1','科技企业资源',40.00,'A102','科技型中小企业库入库数量',30.00,'家',1,NULL),
('A','科技金融资源基础',25.00,'A1','科技企业资源',40.00,'A103','R&D经费支出占GDP比重',30.00,'%',1,NULL),
('A','科技金融资源基础',25.00,'A2','金融资源供给',35.00,'A201','本外币存款余额',35.00,'亿元',1,NULL),
('A','科技金融资源基础',25.00,'A2','金融资源供给',35.00,'A202','私募股权/创业投资机构数量',20.00,'家',0,'县域内注册的有限合伙企业/私募基金'),
('A','科技金融资源基础',25.00,'A2','金融资源供给',35.00,'A203','私募股权/创业投资机构管理资本量',20.00,'亿元',0,'县域内注册的有限合伙企业/私募基金'),
('A','科技金融资源基础',25.00,'A2','金融资源供给',35.00,'A204','银行业金融机构网点密度',25.00,'个/万人',1,NULL),
('A','科技金融资源基础',25.00,'A3','政府支持力度',25.00,'A301','财政科技拨款占公共预算支出比重',60.00,'%',1,NULL),
('A','科技金融资源基础',25.00,'A3','政府支持力度',25.00,'A302','科技金融风险补偿资金池规模',40.00,'万元',1,NULL),

-- B. 科技金融活动强度 (35%)
('B','科技金融活动强度',35.00,'B1','债权融资活跃度',30.00,'B101','科技企业贷款余额',30.00,'亿元',1,'县级农商行/农信社科技贷款数据'),
('B','科技金融活动强度',35.00,'B1','债权融资活跃度',30.00,'B102','科技企业贷款余额增速',25.00,'%',1,'县级农商行/农信社科技贷款数据'),
('B','科技金融活动强度',35.00,'B1','债权融资活跃度',30.00,'B103','知识产权质押融资额',25.00,'亿元',1,NULL),
('B','科技金融活动强度',35.00,'B1','债权融资活跃度',30.00,'B104','信用贷款占比',20.00,'%',1,NULL),
('B','科技金融活动强度',35.00,'B2','股权融资活跃度',35.00,'B201','VC/PE投资案例数',25.00,'起',0,'县域内企业获得的天使轮/A轮融资'),
('B','科技金融活动强度',35.00,'B2','股权融资活跃度',35.00,'B202','VC/PE投资金额',25.00,'亿元',0,'县域内企业获得的天使轮/A轮融资'),
('B','科技金融活动强度',35.00,'B2','股权融资活跃度',35.00,'B203','科创板/创业板等上市科技企业数量',25.00,'家',0,'新三板/区域股权交易中心挂牌企业数量'),
('B','科技金融活动强度',35.00,'B2','股权融资活跃度',35.00,'B204','科创板/创业板等上市科技企业募资额',25.00,'亿元',0,'新三板/区域股权交易中心挂牌企业数量'),
('B','科技金融活动强度',35.00,'B3','政策性融资效果',20.00,'B301','科技保险保费收入',50.00,'亿元',0,'县域科技保险试点覆盖企业数'),
('B','科技金融活动强度',35.00,'B3','政策性融资效果',20.00,'B302','创业担保贷款发放额',50.00,'亿元',1,NULL),
('B','科技金融活动强度',35.00,'B4','金融科技应用',15.00,'B401','移动支付交易额',40.00,'亿元',1,NULL),
('B','科技金融活动强度',35.00,'B4','金融科技应用',15.00,'B402','数字普惠金融指数',40.00,'指数值',1,'北大数字普惠金融指数（县域层面）'),
('B','科技金融活动强度',35.00,'B4','金融科技应用',15.00,'B403','大数据/AI在风控中的应用情况',20.00,'定性/评分',0,'县域金融机构数字化风控调研评分'),

-- C. 科技金融产出效能 (30%)
('C','科技金融产出效能',30.00,'C1','科技创新产出',40.00,'C101','PCT国际专利申请量',35.00,'件',0,'县级国内发明专利申请量'),
('C','科技金融产出效能',30.00,'C1','科技创新产出',40.00,'C102','技术合同成交额',35.00,'亿元',1,NULL),
('C','科技金融产出效能',30.00,'C1','科技创新产出',40.00,'C103','每万人发明专利拥有量',30.00,'件/万人',1,NULL),
('C','科技金融产出效能',30.00,'C2','经济发展贡献',35.00,'C201','高新技术产业产值占规上工业产值比重',60.00,'%',1,NULL),
('C','科技金融产出效能',30.00,'C2','经济发展贡献',35.00,'C202','科技服务业增加值',40.00,'亿元',1,NULL),
('C','科技金融产出效能',30.00,'C3','企业成长性',25.00,'C301','"独角兽"、"瞪羚"企业数量',40.00,'家',1,'县级瞪羚企业/专精特新企业数量'),
('C','科技金融产出效能',30.00,'C3','企业成长性',25.00,'C302','科技企业营收增长率',30.00,'%',1,NULL),
('C','科技金融产出效能',30.00,'C3','企业成长性',25.00,'C303','科技企业利润增长率',30.00,'%',1,NULL),

-- D. 科技金融生态环境 (10%)
('D','科技金融生态环境',10.00,'D1','政策与服务环境',60.00,'D101','科技金融政策完备度（专家打分）',55.00,'分',1,'县级科技金融政策文本评分'),
('D','科技金融生态环境',10.00,'D1','政策与服务环境',60.00,'D102','科技金融服务中心/网点覆盖情况',45.00,'个或%',1,'县级科技支行/金融服务站数量'),
('D','科技金融生态环境',10.00,'D2','人力与创新环境',40.00,'D201','R&D人员全时当量',40.00,'人年',1,'县级规上工业企业R&D人员折合全时当量'),
('D','科技金融生态环境',10.00,'D2','人力与创新环境',40.00,'D202','普通高等学校数量',30.00,'所',1,'县级中职/高职院校数量或产学研合作基地数'),
('D','科技金融生态环境',10.00,'D2','人力与创新环境',40.00,'D203','数字经济发展指数',30.00,'指数值',1,'县级数字经济核心产业增加值占比');

-- 自检：应返回 35 行；各组合计应为 100
-- SELECT COUNT(*) FROM dim_index;
-- SELECT level2_code, SUM(level3_weight) FROM dim_index GROUP BY level2_code;
-- SELECT COUNT(*) FROM dim_index WHERE is_county_applicable = 0;   -- 应为 6


-- ==========================================
-- 3. 指标数据事实表 (fact_tech_finance_data)
-- ==========================================
DROP TABLE IF EXISTS fact_tech_finance_data;
CREATE TABLE fact_tech_finance_data (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    region_code   VARCHAR(12) NOT NULL COMMENT '行政区划代码（县域）',
    year          INT NOT NULL COMMENT '数据年份',
    index_id      INT NOT NULL COMMENT '关联dim_index表的主键',
    source_type   ENUM('official','news','third_party','expert','none') NOT NULL
                  COMMENT '来源类型：official-年鉴/公报填报 news-新闻语料抽取 third_party-第三方机构 expert-专家打分 none-无来源(缺失/不适用占位)',
    index_value   DECIMAL(20,4) DEFAULT NULL COMMENT '指标数值',
    data_quality  TINYINT DEFAULT 1 COMMENT '数据质量：1-官方统计年鉴，2-政府公报/部门数据，3-调研估算，4-缺失待补',
    data_source   VARCHAR(200) DEFAULT NULL COMMENT '数据来源（如：2023年连州市统计年鉴）',
    evidence_ref  VARCHAR(500) DEFAULT NULL COMMENT '轻量溯源：CSV 的 article_id，多个用逗号分隔。官方值可空',
    remark        VARCHAR(255) DEFAULT NULL COMMENT '备注（如：2020年行政区划调整，数据口径变化）',
    create_time   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '录入时间',
    update_time   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    -- 唯一键含 source_type：同一地区×年×指标下，官方统计值与新闻估算值各占一行、可比较、
    -- 可按 data_quality 择优。旧的三元键会直接把"双源并存"挡死。
    UNIQUE KEY uk_fact_cell (region_code, year, index_id, source_type),
    -- 合成指数的热点查询是"某年 × 某指标 → 全部区县"的跨区截面；
    -- 上面的唯一键以 region_code 前导，WHERE year = ? 走不到索引，故单建年份前导索引
    KEY idx_year_index (year, index_id, region_code),
    KEY idx_index_id (index_id, year),
    CONSTRAINT fk_fact_region FOREIGN KEY (region_code) REFERENCES dim_region (region_code)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_fact_index FOREIGN KEY (index_id) REFERENCES dim_index (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT ck_fact_year  CHECK (year BETWEEN 1990 AND 2100),
    CONSTRAINT ck_data_quality CHECK (data_quality BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='科技金融指标事实数据表（县域，支持官方值/新闻估算值双源并存）';


-- ==========================================
-- 4. 指数合成结果表 (fact_index_score)
-- ==========================================
-- 为什么与 fact_tech_finance_data 分层，而不是复用一张表：
--   粒度不同 —— fact 是"三级指标 × 来源"的原始观测（键含 source_type）；
--   score 是"总指数/一级维度/二级指标"的派生聚合（键含 node_code / calc_version）。
--   生命周期也不同 —— fact 录入即存在、按来源 upsert；score 是权重或方法一变就整体重算。
--   若塞进同表，就必须给派生值也带上 source_type，语义直接崩坏。
DROP TABLE IF EXISTS fact_index_score;
CREATE TABLE fact_index_score (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    region_code       VARCHAR(12) NOT NULL COMMENT '行政区划代码',
    year              INT NOT NULL COMMENT '年份',
    node_level        TINYINT NOT NULL COMMENT '1-总指数 2-一级维度 3-二级指标',
    node_code         VARCHAR(20) NOT NULL COMMENT '节点编码：TOTAL / A / A1 …',
    node_name         VARCHAR(50) NOT NULL COMMENT '节点名称',
    score             DECIMAL(12,6) NOT NULL COMMENT '归一化加权后的合成得分',
    rank_in_year      INT DEFAULT NULL COMMENT '同年同层级内排名',
    n_indicators_used INT DEFAULT NULL COMMENT '实际参与合成的三级指标数',
    coverage_ratio    DECIMAL(5,4) DEFAULT NULL COMMENT '该单元该节点的指标覆盖率(0~1)。县域面板大量缺失下，可比性必须能被解释',
    source_policy     ENUM('official_only','official_first','blend') NOT NULL DEFAULT 'official_first'
                      COMMENT '双源取舍策略。官方值优先，官方缺失才回退新闻估算值',
    norm_method       VARCHAR(20) DEFAULT NULL COMMENT '本次合成所用归一化法',
    weight_scheme     VARCHAR(50) DEFAULT NULL COMMENT '本次合成所用权重方案',
    calc_version      VARCHAR(20) NOT NULL DEFAULT 'v1' COMMENT '合成算法版本，保证跨年可比与可复现',
    computed_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '计算时间',
    UNIQUE KEY uk_score (region_code, year, node_code, source_policy, calc_version),
    KEY idx_score_year_node (year, node_code, score),
    CONSTRAINT fk_score_region FOREIGN KEY (region_code) REFERENCES dim_region (region_code)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT ck_node_level CHECK (node_level IN (1,2,3)),
    CONSTRAINT ck_coverage   CHECK (coverage_ratio IS NULL OR (coverage_ratio >= 0 AND coverage_ratio <= 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='指数合成结果表（与原始事实表分层，避免粒度混淆）';


-- ==========================================
-- 5. 幂等写入模板（示例，未执行——按需取用）
-- ==========================================
-- 【要点】
--   1) 用 level3_code 取 index_id，不要写死自增 id 也不用中文名——两者都会漂移。
--   2) 全部走 ON DUPLICATE KEY UPDATE，命中 uk_fact_cell / uk_region_code / uk_level3_code。
--      重复载入是研究库常态（常年增量更新），裸 INSERT 会抛 1062 直接中断。
--   3) 不要用 REPLACE INTO —— 它是 delete+insert，会重置自增 id 与 create_time。
--   4) 下面是 VALUES() 函数式（全版本可用；8.0.20+ 已标记 deprecated）。
--      若目标库 ≥ 8.0.19，可改写为别名式： ... VALUES (...) AS new
--      ON DUPLICATE KEY UPDATE index_value = new.index_value, ...
--
-- -- 5.1 官方值（人工从统计年鉴/公报填报）：连州市 2023 年高新技术企业数量
-- INSERT INTO fact_tech_finance_data
--   (region_code, year, index_id, source_type, index_value, data_quality, data_source)
-- SELECT '441882', 2023, di.id, 'official', 68.0000, 1, '2023年连州市统计年鉴'
--   FROM dim_index di WHERE di.level3_code = 'A101'
-- ON DUPLICATE KEY UPDATE
--   index_value = VALUES(index_value), data_quality = VALUES(data_quality),
--   data_source = VALUES(data_source);
--
-- -- 5.2 新闻估算值：同一格（连州市 2023 年 A101）的新闻抽取结果，
-- --     与上面那行【并存】—— 唯一键含 source_type，两者不冲突
-- INSERT INTO fact_tech_finance_data
--   (region_code, year, index_id, source_type, index_value, data_quality,
--    data_source, evidence_ref)
-- SELECT '441882', 2023, di.id, 'news', 65.0000, 3,
--        '新闻语料抽取（3 篇）', 'a1b2c3d4e5f60718,9f8e7d6c5b4a3210,0123456789abcdef'
--   FROM dim_index di WHERE di.level3_code = 'A101'
-- ON DUPLICATE KEY UPDATE
--   index_value = VALUES(index_value), data_quality = VALUES(data_quality),
--   evidence_ref = VALUES(evidence_ref);
--
-- -- 5.3 显式记录"该指标在县级不存在"（而非留空）—— 合成时权重须重新归一
-- INSERT INTO fact_tech_finance_data
--   (region_code, year, index_id, source_type, index_value, data_quality, remark)
-- SELECT '441882', 2023, di.id, 'none', NULL, 4, '县级无此项统计，见 dim_index.county_replacement'
--   FROM dim_index di WHERE di.level3_code = 'B203'
-- ON DUPLICATE KEY UPDATE data_quality = VALUES(data_quality), remark = VALUES(remark);
--
-- -- 5.4 维表 upsert（补数据/改名称不报 1062）
-- INSERT INTO dim_region (region_code, region_name, region_full_name, region_level,
--                         parent_code, city_code, is_analysis_unit)
-- VALUES ('441502', '城区', '汕尾市城区', 3, '441500', '441500', 1)
-- ON DUPLICATE KEY UPDATE
--   region_name = VALUES(region_name), region_full_name = VALUES(region_full_name);
--
-- -- 5.5 合成结果 upsert（重算指数时覆盖，不新增行）
-- INSERT INTO fact_index_score
--   (region_code, year, node_level, node_code, node_name, score,
--    n_indicators_used, coverage_ratio, source_policy, norm_method, weight_scheme, calc_version)
-- VALUES ('441882', 2023, 1, 'TOTAL', '科技金融指数', 61.240000,
--         35, 0.7714, 'official_first', 'minmax', '专家赋值（待校准）', 'v1')
-- ON DUPLICATE KEY UPDATE
--   score = VALUES(score), n_indicators_used = VALUES(n_indicators_used),
--   coverage_ratio = VALUES(coverage_ratio);
