-- ============================================================================
-- 县域科技金融指数研究 · 数据库【增量迁移脚本】
--   从「2026-10-04 的 tech_finance_county 结构」→「修订后结构」
-- ============================================================================
--
--  用途：目标库里【已有数据】，不能承受 DROP TABLE 的场景，用本脚本原地升级。
--        空库请直接用 tech_finance_index.sql，不要用本脚本。
--
--  本脚本【不含任何 DROP TABLE】，只做 ALTER / UPDATE / INSERT / DELETE（后者带守卫）。
--  但仍有不可逆操作，执行前必须备份：
--
--      mysqldump -u <user> -p --default-character-set=utf8mb4 --single-transaction \
--                --routines --triggers tech_finance_county \
--                > backup_tech_finance_county_$(date +%F).sql
--
--  ！！跑之前把第 2 步的"复合指标拆分"整段读完 —— 那是唯一涉及删除数据的地方 ！！
--
--  版本要求：MySQL 8.0+（用了 RENAME 之外的 8.0 语法；CHECK 需 8.0.16+ 才真正强制）。
--
-- ============================================================================
--  迁移总览
-- ----------------------------------------------------------------------------
--  第 1 步  dim_region : 补全 148 行区划数据、加 region_full_name/is_analysis_unit + 索引 + CHECK
--  第 2 步  dim_index  : 加 level3_code / level2_weight / level3_weight，30 → 35 条（含拆分）
--  第 3 步  fact 表    : 加 source_type / evidence_ref，唯一键三元组 → 四元组（双源并存）
--  第 4 步  新建 fact_index_score
--  第 5 步  自检
-- ============================================================================

SET NAMES utf8mb4;
USE tech_finance_county;

SET FOREIGN_KEY_CHECKS = 0;


-- ============================================================================
-- 第 0 步 · 迁移前快照
-- ============================================================================
SELECT '迁移前' AS 阶段,
       (SELECT COUNT(*) FROM dim_region) AS 区划,
       (SELECT COUNT(*) FROM dim_index)  AS 指标,
       (SELECT COUNT(*) FROM fact_tech_finance_data) AS 事实行;


-- ============================================================================
-- 第 1 步 · dim_region
-- ============================================================================
ALTER TABLE dim_region
    ADD COLUMN region_full_name VARCHAR(60) NOT NULL DEFAULT ''
        COMMENT '规范全称（如"汕尾市城区"）。仅用于报表展示消歧，不参与 join' AFTER region_name,
    ADD COLUMN is_analysis_unit TINYINT NOT NULL DEFAULT 0
        COMMENT '是否县域分析单元：1-是。东莞441900/中山442000 为 level=2 但本身即县域单元，须置1' AFTER city_code;

-- 1.1 补全区划数据。原版只有 8 行示例（清远 6 + 成都 2），广东 124 个县域分析单元里
--     只有 6 个有码 —— 县域研究没得做。这里用 upsert 补到完整 148 行，
--     已存在的行只更新名称，不动用户可能手工调过的 region_type / start_year。
INSERT INTO dim_region (region_code, region_name, region_level, parent_code) VALUES
('440000','广东省',1,NULL),
('440100','广州市',2,'440000'),
('440103','荔湾区',3,'440100'),('440104','越秀区',3,'440100'),('440105','海珠区',3,'440100'),
('440106','天河区',3,'440100'),('440111','白云区',3,'440100'),('440112','黄埔区',3,'440100'),
('440113','番禺区',3,'440100'),('440114','花都区',3,'440100'),('440115','南沙区',3,'440100'),
('440117','从化区',3,'440100'),('440118','增城区',3,'440100'),
('440200','韶关市',2,'440000'),
('440203','武江区',3,'440200'),('440204','浈江区',3,'440200'),('440205','曲江区',3,'440200'),
('440222','始兴县',3,'440200'),('440224','仁化县',3,'440200'),('440229','翁源县',3,'440200'),
('440232','乳源瑶族自治县',3,'440200'),('440233','新丰县',3,'440200'),('440281','乐昌市',3,'440200'),
('440282','南雄市',3,'440200'),
('440300','深圳市',2,'440000'),
('440303','罗湖区',3,'440300'),('440304','福田区',3,'440300'),('440305','南山区',3,'440300'),
('440306','宝安区',3,'440300'),('440307','龙岗区',3,'440300'),('440308','盐田区',3,'440300'),
('440309','龙华区',3,'440300'),('440310','坪山区',3,'440300'),('440311','光明区',3,'440300'),
('440400','珠海市',2,'440000'),
('440402','香洲区',3,'440400'),('440403','斗门区',3,'440400'),('440404','金湾区',3,'440400'),
('440500','汕头市',2,'440000'),
('440507','龙湖区',3,'440500'),('440511','金平区',3,'440500'),('440512','濠江区',3,'440500'),
('440513','潮阳区',3,'440500'),('440514','潮南区',3,'440500'),('440515','澄海区',3,'440500'),
('440523','南澳县',3,'440500'),
('440600','佛山市',2,'440000'),
('440604','禅城区',3,'440600'),('440605','南海区',3,'440600'),('440606','顺德区',3,'440600'),
('440607','三水区',3,'440600'),('440608','高明区',3,'440600'),
('440700','江门市',2,'440000'),
('440703','蓬江区',3,'440700'),('440704','江海区',3,'440700'),('440705','新会区',3,'440700'),
('440781','台山市',3,'440700'),('440783','开平市',3,'440700'),('440784','鹤山市',3,'440700'),
('440785','恩平市',3,'440700'),
('440800','湛江市',2,'440000'),
('440802','赤坎区',3,'440800'),('440803','霞山区',3,'440800'),('440804','坡头区',3,'440800'),
('440811','麻章区',3,'440800'),('440823','遂溪县',3,'440800'),('440825','徐闻县',3,'440800'),
('440881','廉江市',3,'440800'),('440882','雷州市',3,'440800'),('440883','吴川市',3,'440800'),
('440900','茂名市',2,'440000'),
('440902','茂南区',3,'440900'),('440904','电白区',3,'440900'),('440981','高州市',3,'440900'),
('440982','化州市',3,'440900'),('440983','信宜市',3,'440900'),
('441200','肇庆市',2,'440000'),
('441202','端州区',3,'441200'),('441203','鼎湖区',3,'441200'),('441204','高要区',3,'441200'),
('441223','广宁县',3,'441200'),('441224','怀集县',3,'441200'),('441225','封开县',3,'441200'),
('441226','德庆县',3,'441200'),('441284','四会市',3,'441200'),
('441300','惠州市',2,'440000'),
('441302','惠城区',3,'441300'),('441303','惠阳区',3,'441300'),('441322','博罗县',3,'441300'),
('441323','惠东县',3,'441300'),('441324','龙门县',3,'441300'),
('441400','梅州市',2,'440000'),
('441402','梅江区',3,'441400'),('441403','梅县区',3,'441400'),('441422','大埔县',3,'441400'),
('441423','丰顺县',3,'441400'),('441424','五华县',3,'441400'),('441426','平远县',3,'441400'),
('441427','蕉岭县',3,'441400'),('441481','兴宁市',3,'441400'),
('441500','汕尾市',2,'440000'),
('441502','城区',3,'441500'),('441521','海丰县',3,'441500'),('441523','陆河县',3,'441500'),
('441581','陆丰市',3,'441500'),
('441600','河源市',2,'440000'),
('441602','源城区',3,'441600'),('441621','紫金县',3,'441600'),('441622','龙川县',3,'441600'),
('441623','连平县',3,'441600'),('441624','和平县',3,'441600'),('441625','东源县',3,'441600'),
('441700','阳江市',2,'440000'),
('441702','江城区',3,'441700'),('441704','阳东区',3,'441700'),('441721','阳西县',3,'441700'),
('441781','阳春市',3,'441700'),
('441800','清远市',2,'440000'),
('441802','清城区',3,'441800'),('441803','清新区',3,'441800'),('441821','佛冈县',3,'441800'),
('441823','阳山县',3,'441800'),('441825','连山壮族瑶族自治县',3,'441800'),
('441826','连南瑶族自治县',3,'441800'),('441881','英德市',3,'441800'),('441882','连州市',3,'441800'),
('441900','东莞市',2,'440000'),
('442000','中山市',2,'440000'),
('445100','潮州市',2,'440000'),
('445102','湘桥区',3,'445100'),('445103','潮安区',3,'445100'),('445122','饶平县',3,'445100'),
('445200','揭阳市',2,'440000'),
('445202','榕城区',3,'445200'),('445203','揭东区',3,'445200'),('445222','揭西县',3,'445200'),
('445224','惠来县',3,'445200'),('445281','普宁市',3,'445200'),
('445300','云浮市',2,'440000'),
('445302','云城区',3,'445300'),('445303','云安区',3,'445300'),('445321','新兴县',3,'445300'),
('445322','郁南县',3,'445300'),('445381','罗定市',3,'445300'),
('510000','四川省',1,NULL),
('510100','成都市',2,'510000'),
('510121','金堂县',3,'510100'),('510181','都江堰市',3,'510100')
ON DUPLICATE KEY UPDATE
    region_name  = VALUES(region_name),
    region_level = VALUES(region_level),
    parent_code  = VALUES(parent_code);

-- 1.2 派生列回填。只填空值，不覆盖已有（用户可能手工调过 region_type / start_year）
UPDATE dim_region SET region_type = CASE
    WHEN region_level = 1           THEN '省级'
    WHEN region_level = 2           THEN '地级市'
    WHEN region_name LIKE '%自治县' THEN '自治县'
    WHEN region_name LIKE '%区'     THEN '市辖区'
    WHEN region_name LIKE '%县'     THEN '普通县'
    WHEN region_name LIKE '%市'     THEN '县级市'
    ELSE NULL END
 WHERE region_type IS NULL;

UPDATE dim_region SET city_code = CASE
    WHEN region_level = 2 THEN region_code
    WHEN region_level = 3 THEN parent_code
    ELSE NULL END
 WHERE city_code IS NULL;

UPDATE dim_region SET region_full_name = region_name WHERE region_full_name = '';
UPDATE dim_region SET region_full_name = '汕尾市城区'       WHERE region_code = '441502';
UPDATE dim_region SET region_full_name = '东莞市（市本级）' WHERE region_code = '441900';
UPDATE dim_region SET region_full_name = '中山市（市本级）' WHERE region_code = '442000';

-- 1.3 县域分析单元 = 全部县/区 + 东莞/中山两个不设区地级市。
--     此后所有"取县域"的查询统一用 is_analysis_unit = 1，
--     绝不要再写 region_level = 3 —— 那会【静默】丢掉东莞和中山，124 变 122。
UPDATE dim_region SET is_analysis_unit = 1
 WHERE region_level = 3 OR region_code IN ('441900', '442000');

-- 1.4 索引与约束
ALTER TABLE dim_region
    ADD KEY idx_analysis_unit (is_analysis_unit, region_level, parent_code),
    ADD CONSTRAINT ck_region_level CHECK (region_level IN (1,2,3)),
    ADD CONSTRAINT ck_region_span  CHECK (end_year IS NULL OR start_year IS NULL
                                          OR end_year >= start_year);


-- ============================================================================
-- 第 2 步 · dim_index
-- ============================================================================
ALTER TABLE dim_index
    ADD COLUMN level2_weight DECIMAL(5,2) NULL
        COMMENT '二级指标在所属一级维度内权重(%)，同一 level1_code 下合计=100' AFTER level2_name,
    ADD COLUMN level3_code   VARCHAR(20) NULL
        COMMENT '三级指标稳定业务编码（如 A101）。重命名不失效，是 join 与 upsert 的依据' AFTER level2_weight,
    ADD COLUMN level3_weight DECIMAL(5,2) NULL
        COMMENT '三级指标在所属二级指标内权重(%)，同一 level2_code 下合计=100' AFTER level3_name;

-- 2.1 未拆分的 25 条：按中文名回填稳定编码（名字是原唯一键的一部分，可唯一匹配）
UPDATE dim_index SET level3_code = CASE level3_name
    WHEN '高新技术企业数量/密度'              THEN 'A101'
    WHEN '科技型中小企业库入库数量'           THEN 'A102'
    WHEN 'R&D经费支出占GDP比重'               THEN 'A103'
    WHEN '本外币存款余额'                     THEN 'A201'
    WHEN '银行业金融机构网点密度'             THEN 'A204'
    WHEN '财政科技拨款占公共预算支出比重'     THEN 'A301'
    WHEN '科技金融风险补偿资金池规模'         THEN 'A302'
    WHEN '知识产权质押融资额'                 THEN 'B103'
    WHEN '信用贷款占比'                       THEN 'B104'
    WHEN '科技保险保费收入'                   THEN 'B301'
    WHEN '创业担保贷款发放额'                 THEN 'B302'
    WHEN '移动支付交易额'                     THEN 'B401'
    WHEN '数字普惠金融指数'                   THEN 'B402'
    WHEN '大数据/AI在风控中的应用情况'        THEN 'B403'
    WHEN 'PCT国际专利申请量'                  THEN 'C101'
    WHEN '技术合同成交额'                     THEN 'C102'
    WHEN '每万人发明专利拥有量'               THEN 'C103'
    WHEN '高新技术产业产值占规上工业产值比重' THEN 'C201'
    WHEN '科技服务业增加值'                   THEN 'C202'
    WHEN '"独角兽"、"瞪羚"企业数量'           THEN 'C301'
    WHEN '科技金融政策完备度（专家打分）'     THEN 'D101'
    WHEN '科技金融服务中心/网点覆盖情况'      THEN 'D102'
    WHEN 'R&D人员全时当量'                    THEN 'D201'
    WHEN '普通高等学校数量'                   THEN 'D202'
    WHEN '数字经济发展指数'                   THEN 'D203'
    ELSE NULL END
 WHERE level3_code IS NULL;

-- 2.2 权重回填（★ 不可省）。原版只有一级权重，二三级权重全新，必须补齐 ——
--     否则会得到一张权重全为 0 的指标表，合成出来的指数全是 0，且【不报任何错】。
--     level1_weight 原版已有，无需回填。
UPDATE dim_index SET level2_weight = CASE level2_code
    WHEN 'A1' THEN 40.00 WHEN 'A2' THEN 35.00 WHEN 'A3' THEN 25.00
    WHEN 'B1' THEN 30.00 WHEN 'B2' THEN 35.00 WHEN 'B3' THEN 20.00 WHEN 'B4' THEN 15.00
    WHEN 'C1' THEN 40.00 WHEN 'C2' THEN 35.00 WHEN 'C3' THEN 25.00
    WHEN 'D1' THEN 60.00 WHEN 'D2' THEN 40.00
    ELSE 0.00 END
 WHERE level2_weight IS NULL;

UPDATE dim_index SET level3_weight = CASE level3_name
    WHEN '高新技术企业数量/密度'              THEN 40.00
    WHEN '科技型中小企业库入库数量'           THEN 30.00
    WHEN 'R&D经费支出占GDP比重'               THEN 30.00
    WHEN '本外币存款余额'                     THEN 35.00
    WHEN '银行业金融机构网点密度'             THEN 25.00
    WHEN '财政科技拨款占公共预算支出比重'     THEN 60.00
    WHEN '科技金融风险补偿资金池规模'         THEN 40.00
    WHEN '知识产权质押融资额'                 THEN 25.00
    WHEN '信用贷款占比'                       THEN 20.00
    WHEN '科技保险保费收入'                   THEN 50.00
    WHEN '创业担保贷款发放额'                 THEN 50.00
    WHEN '移动支付交易额'                     THEN 40.00
    WHEN '数字普惠金融指数'                   THEN 40.00
    WHEN '大数据/AI在风控中的应用情况'        THEN 20.00
    WHEN 'PCT国际专利申请量'                  THEN 35.00
    WHEN '技术合同成交额'                     THEN 35.00
    WHEN '每万人发明专利拥有量'               THEN 30.00
    WHEN '高新技术产业产值占规上工业产值比重' THEN 60.00
    WHEN '科技服务业增加值'                   THEN 40.00
    WHEN '"独角兽"、"瞪羚"企业数量'           THEN 40.00
    WHEN '科技金融政策完备度（专家打分）'     THEN 55.00
    WHEN '科技金融服务中心/网点覆盖情况'      THEN 45.00
    WHEN 'R&D人员全时当量'                    THEN 40.00
    WHEN '普通高等学校数量'                   THEN 30.00
    WHEN '数字经济发展指数'                   THEN 30.00
    WHEN '私募股权/创业投资机构数量与管理资本量' THEN 40.00
    WHEN '科技企业贷款余额及增速'                THEN 55.00
    WHEN 'VC/PE投资案例数与金额'                 THEN 50.00
    WHEN '科创板/创业板等上市科技企业数量与募资额' THEN 50.00
    WHEN '科技企业营收和利润增长率'              THEN 60.00
    ELSE 0.00 END
 WHERE level3_weight IS NULL;

-- ----------------------------------------------------------------------------
-- 2.3 复合指标拆分   ★★★ 唯一涉及删除数据的地方，务必读完 ★★★
-- ----------------------------------------------------------------------------
--
--  原 5 条指标把两个度量塞在一行（单位形如"亿元/%"），单个 DECIMAL 列装不下，
--  也无法统一赋权。要拆成单一单位的独立指标（30 → 35 条）。
--
--  为什么不能自动迁移：旧事实表一行只有一个 index_value —— "科技企业贷款余额及增速"
--  那一列存的是金额还是增速？脚本无从判断（可能两种情况混着存），
--  所以【无法自动把存量值拆到两条新指标上】。
--
--  本脚本的处理：
--    ① 先给这 5 条打上 X 结尾的临时编码（保证 level3_code 可置 NOT NULL）；
--    ② 只删除【没有任何事实行引用】的复合指标 —— 那些行没数据，删掉零损失；
--    ③ 新增 10 条拆分后的干净指标；
--    ④ 仍有数据的复合指标会【保留下来】，并连同其事实行列在文末第 5 步的清单里，
--       由你人工把值重新录入到新指标上（确认无误后再手工删除旧行）。

-- 2.3.1 给 5 条复合指标打临时退役编码
UPDATE dim_index SET level3_code = 'A202X'
 WHERE level3_name = '私募股权/创业投资机构数量与管理资本量' AND level3_code IS NULL;
UPDATE dim_index SET level3_code = 'B101X'
 WHERE level3_name = '科技企业贷款余额及增速' AND level3_code IS NULL;
UPDATE dim_index SET level3_code = 'B201X'
 WHERE level3_name = 'VC/PE投资案例数与金额' AND level3_code IS NULL;
UPDATE dim_index SET level3_code = 'B203X'
 WHERE level3_name = '科创板/创业板等上市科技企业数量与募资额' AND level3_code IS NULL;
UPDATE dim_index SET level3_code = 'C302X'
 WHERE level3_name = '科技企业营收和利润增长率' AND level3_code IS NULL;

-- 2.3.2 只删"没人引用"的复合指标（有数据的留着，文末会列出来）
DELETE di FROM dim_index di
 WHERE di.level3_code IN ('A202X','B101X','B201X','B203X','C302X')
   AND NOT EXISTS (SELECT 1 FROM fact_tech_finance_data f WHERE f.index_id = di.id);

-- 2.3.3 新增 10 条拆分后的独立指标。
--       is_county_applicable / county_replacement 【机械继承】原行的标注，不改研究判断。
INSERT INTO dim_index
(level1_code, level1_name, level1_weight, level2_code, level2_name, level2_weight,
 level3_code, level3_name, level3_weight, unit, is_county_applicable, county_replacement) VALUES
('A','科技金融资源基础',25.00,'A2','金融资源供给',35.00,'A202','私募股权/创业投资机构数量',20.00,'家',0,'县域内注册的有限合伙企业/私募基金'),
('A','科技金融资源基础',25.00,'A2','金融资源供给',35.00,'A203','私募股权/创业投资机构管理资本量',20.00,'亿元',0,'县域内注册的有限合伙企业/私募基金'),
('B','科技金融活动强度',35.00,'B1','债权融资活跃度',30.00,'B101','科技企业贷款余额',30.00,'亿元',1,'县级农商行/农信社科技贷款数据'),
('B','科技金融活动强度',35.00,'B1','债权融资活跃度',30.00,'B102','科技企业贷款余额增速',25.00,'%',1,'县级农商行/农信社科技贷款数据'),
('B','科技金融活动强度',35.00,'B2','股权融资活跃度',35.00,'B201','VC/PE投资案例数',25.00,'起',0,'县域内企业获得的天使轮/A轮融资'),
('B','科技金融活动强度',35.00,'B2','股权融资活跃度',35.00,'B202','VC/PE投资金额',25.00,'亿元',0,'县域内企业获得的天使轮/A轮融资'),
('B','科技金融活动强度',35.00,'B2','股权融资活跃度',35.00,'B203','科创板/创业板等上市科技企业数量',25.00,'家',0,'新三板/区域股权交易中心挂牌企业数量'),
('B','科技金融活动强度',35.00,'B2','股权融资活跃度',35.00,'B204','科创板/创业板等上市科技企业募资额',25.00,'亿元',0,'新三板/区域股权交易中心挂牌企业数量'),
('C','科技金融产出效能',30.00,'C3','企业成长性',25.00,'C302','科技企业营收增长率',30.00,'%',1,NULL),
('C','科技金融产出效能',30.00,'C3','企业成长性',25.00,'C303','科技企业利润增长率',30.00,'%',1,NULL);

-- 2.4 补齐列约束与唯一键
ALTER TABLE dim_index
    MODIFY COLUMN level2_weight DECIMAL(5,2) NOT NULL
        COMMENT '二级指标在所属一级维度内权重(%)，同一 level1_code 下合计=100',
    MODIFY COLUMN level3_code   VARCHAR(20)  NOT NULL
        COMMENT '三级指标稳定业务编码（如 A101）。重命名不失效，是 join 与 upsert 的依据',
    MODIFY COLUMN level3_weight DECIMAL(5,2) NOT NULL
        COMMENT '三级指标在所属二级指标内权重(%)，同一 level2_code 下合计=100',
    MODIFY COLUMN is_county_applicable TINYINT NOT NULL DEFAULT 1
        COMMENT '是否适用于县域研究：1-适用，0-需替换或不适用',
    MODIFY COLUMN county_replacement VARCHAR(100) DEFAULT NULL
        COMMENT '县域替代指标建议（若不适用）';

ALTER TABLE dim_index
    ADD UNIQUE KEY uk_level3_code (level3_code),
    ADD KEY idx_level1 (level1_code),
    ADD KEY idx_level2 (level2_code),
    ADD KEY idx_county_applicable (is_county_applicable),
    ADD CONSTRAINT ck_l1w CHECK (level1_weight BETWEEN 0 AND 100),
    ADD CONSTRAINT ck_l2w CHECK (level2_weight BETWEEN 0 AND 100),
    ADD CONSTRAINT ck_l3w CHECK (level3_weight BETWEEN 0 AND 100);

-- 表注释同步（原版注释未提县域适用性；不改不影响功能，只是与首建脚本产物对齐）
ALTER TABLE dim_index
    COMMENT='科技金融指标维度表（含县域适用性与替代口径）';


-- ============================================================================
-- 第 3 步 · fact_tech_finance_data
-- ============================================================================
-- 3.1 加双来源与溯源列
ALTER TABLE fact_tech_finance_data
    ADD COLUMN source_type ENUM('official','news','third_party','expert','none') NOT NULL DEFAULT 'none'
        COMMENT '来源类型：official-年鉴/公报填报 news-新闻语料抽取 third_party-第三方机构 expert-专家打分 none-无来源(缺失/不适用占位)' AFTER index_id,
    ADD COLUMN evidence_ref VARCHAR(500) DEFAULT NULL
        COMMENT '轻量溯源：CSV 的 article_id，多个用逗号分隔。官方值可空' AFTER data_source;

-- 3.2 存量行的来源归类。原版用 data_quality 一列表达来源+质量，可按下表反推：
--       1-官方统计年鉴 / 2-政府公报·部门数据 → official
--       3-调研估算                          → expert
--       4-缺失待补                          → none
UPDATE fact_tech_finance_data SET source_type = CASE data_quality
    WHEN 1 THEN 'official'
    WHEN 2 THEN 'official'
    WHEN 3 THEN 'expert'
    WHEN 4 THEN 'none'
    ELSE 'none' END
 WHERE source_type = 'none';

-- 3.3 换唯一键：旧三元组 (region_code, year, index_id) 规定一地区一年一指标只能一行，
--     容不下"官方值 + 新闻估算值并存"。先加四元新键（它以 region_code 前导，
--     同时满足 fk_fact_region 的索引需求），再删旧键。
ALTER TABLE fact_tech_finance_data
    ADD UNIQUE KEY uk_fact_cell (region_code, year, index_id, source_type);

ALTER TABLE fact_tech_finance_data
    DROP INDEX uk_region_year_index;

-- 3.4 索引：新增年份前导索引（合成指数的跨区截面查询热点），
--     并把 idx_index_id 从 (index_id) 扩成 (index_id, year) 以覆盖单指标时间序列。
--     改索引前先摘掉 fk_fact_index —— 否则 DROP 会因"外键缺少支撑索引"被拒。
--     顺带把两条外键的策略显式化（原版没写 ON UPDATE/DELETE，业务键变更时会被默认
--     RESTRICT 挡住；region 那条原样留着会与首建脚本产物不一致）。
ALTER TABLE fact_tech_finance_data
    DROP FOREIGN KEY fk_fact_index,
    DROP FOREIGN KEY fk_fact_region;

ALTER TABLE fact_tech_finance_data
    DROP INDEX idx_index_id,
    ADD KEY idx_year_index (year, index_id, region_code),
    ADD KEY idx_index_id (index_id, year);

ALTER TABLE fact_tech_finance_data
    ADD CONSTRAINT fk_fact_region FOREIGN KEY (region_code) REFERENCES dim_region (region_code)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    ADD CONSTRAINT fk_fact_index FOREIGN KEY (index_id) REFERENCES dim_index (id)
        ON UPDATE CASCADE ON DELETE RESTRICT;

-- 3.5 时间列 TIMESTAMP → DATETIME（避开 2038 上限与 MySQL 5.6.5 版本依赖），
--     并去掉 source_type 的 DEFAULT 'none' —— 来源类型必须由写入方显式声明，
--     静默默认成"无来源"会让溯源信息悄悄丢失。
--     ★ TIMESTAMP 内部按 UTC 存储、显示时转会话时区；转 DATETIME 会把"当前会话
--       时区下的显示值"固化下来。执行前务必先把 time_zone 设到业务时区，
--       否则存量时间会整体偏移。
SET time_zone = '+08:00';

ALTER TABLE fact_tech_finance_data
    MODIFY COLUMN source_type ENUM('official','news','third_party','expert','none') NOT NULL
        COMMENT '来源类型：official-年鉴/公报填报 news-新闻语料抽取 third_party-第三方机构 expert-专家打分 none-无来源(缺失/不适用占位)',
    MODIFY COLUMN data_quality TINYINT DEFAULT 1
        COMMENT '数据质量：1-官方统计年鉴，2-政府公报/部门数据，3-调研估算，4-缺失待补',
    MODIFY COLUMN create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '录入时间',
    MODIFY COLUMN update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间';

ALTER TABLE fact_tech_finance_data
    ADD CONSTRAINT ck_fact_year   CHECK (year BETWEEN 1990 AND 2100),
    ADD CONSTRAINT ck_data_quality CHECK (data_quality BETWEEN 1 AND 4);

ALTER TABLE fact_tech_finance_data
    COMMENT='科技金融指标事实数据表（县域，支持官方值/新闻估算值双源并存）';


-- ============================================================================
-- 第 4 步 · 新建合成结果表
-- ============================================================================
CREATE TABLE IF NOT EXISTS fact_index_score (
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


-- ============================================================================
-- 第 5 步 · 收尾与自检
-- ============================================================================
SET FOREIGN_KEY_CHECKS = 1;

SHOW TABLES;

-- 5.1 区划：广东县域分析单元必须为 124（务必带 LIKE '44%'，库里还有四川省示例行）
SELECT COUNT(*) AS 广东县域分析单元 FROM dim_region
 WHERE is_analysis_unit = 1 AND region_code LIKE '44%';
SELECT COUNT(*) AS region_type为NULL的行 FROM dim_region WHERE region_type IS NULL;

-- 5.2 指标：应为 35 条；若第 2.3 步保留了尚有数据的旧复合指标，会更多
SELECT COUNT(*) AS 现行指标数 FROM dim_index;
SELECT COUNT(*) AS 未回填编码的行 FROM dim_index WHERE level3_code IS NULL OR level3_code = '';
SELECT COUNT(*) AS 县域不适用指标数 FROM dim_index WHERE is_county_applicable = 0;

-- 5.3 权重合计核对（排除 X 结尾的退役临时行）
SELECT level2_code, SUM(level3_weight) AS 三级权重合计
  FROM dim_index WHERE level3_code NOT LIKE '%X'
 GROUP BY level2_code ORDER BY level2_code;

-- 5.4 存量事实行有无丢失（应与第 0 步的"事实行"数一致）
SELECT COUNT(*) AS 迁移后事实行 FROM fact_tech_finance_data;

-- 5.5 ★ 需要人工处理的清单：仍挂在旧复合指标上的事实行。
--      这些值无法自动拆分，请人工重新录入到第 2.3.3 步新增的对应新指标上，
--      确认无误后再手工删除旧行。
SELECT di.level3_code AS 退役编码, di.level3_name AS 旧复合指标,
       f.region_code, f.year, f.source_type, f.index_value, f.data_source
  FROM fact_tech_finance_data f
  JOIN dim_index di ON di.id = f.index_id
 WHERE di.level3_code LIKE '%X'
 ORDER BY di.level3_code, f.region_code, f.year;

-- 5.6 迁移后仍存在的 X 结尾行（应只有 5.5 里列出的那几个；其余已被 2.3.2 删除）
SELECT level3_code, level3_name FROM dim_index WHERE level3_code LIKE '%X';
