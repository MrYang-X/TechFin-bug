-- ============================================================================
-- TechFin · 10 表结构
-- ============================================================================
-- 依赖：00_database.sql（已创建并使用 techfin 库）
--
-- 设计原则：
--   1. 原始观测永远存长表 fact_index_value；宽表只做视图/暂存，不做系统记录。
--   2. 权重、归一化、合成方法全部在表外（Python + 配置文件），本脚本不建权重列。
--   3. 每个值保留轻量溯源：来源渠道、发布方权威性、质量、证据 article_id、
--      来源数量、一致性、最小/最大值。
--   4. 指标口径与单位单独放 dim_index_measure，解决“家或家/万人”“个或%”混在一格。
--
-- 如需完全重建（会删除全部数据），请手动取消下面注释后逐条执行：
--   SET FOREIGN_KEY_CHECKS = 0;
--   DROP TABLE IF EXISTS fact_index_score, fact_index_run;
--   DROP TABLE IF EXISTS fact_index_value;
--   DROP TABLE IF EXISTS stg_wide_guangdong, import_batch;
--   DROP TABLE IF EXISTS dim_index_measure, dim_index, dim_source_channel, dim_region;
--   SET FOREIGN_KEY_CHECKS = 1;
-- ============================================================================

SET NAMES utf8mb4;
USE techfin;
SET time_zone = '+08:00';

-- ----------------------------------------------------------------------------
-- 1. 行政区划维度
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_region (
    region_code       VARCHAR(12) NOT NULL
                      COMMENT '行政区划代码（6 位，全国唯一）',
    region_name       VARCHAR(50) NOT NULL
                      COMMENT '区划名称，如 天河区',
    region_full_name  VARCHAR(60) NOT NULL DEFAULT ''
                      COMMENT '规范全称，仅用于展示消歧，如 汕尾市城区',
    region_level      TINYINT NOT NULL
                      COMMENT '1-省级 2-地级市 3-区县',
    region_type       VARCHAR(20) DEFAULT NULL
                      COMMENT '省级/地级市/市辖区/普通县/县级市/自治县',
    parent_code       VARCHAR(12) DEFAULT NULL
                      COMMENT '上级区划代码',
    city_code         VARCHAR(12) DEFAULT NULL
                      COMMENT '所属地级市代码；市本级填自身',
    province_code     VARCHAR(12)
                      GENERATED ALWAYS AS (CONCAT(LEFT(region_code, 2), '0000')) STORED
                      COMMENT '省级代码，由区划码前两位派生',
    is_analysis_unit  TINYINT NOT NULL DEFAULT 0
                      COMMENT '1-县域分析单元（含东莞 441900/中山 442000 市本级）',
    is_active         TINYINT NOT NULL DEFAULT 1
                      COMMENT '1-现行有效',
    start_year        SMALLINT DEFAULT NULL
                      COMMENT '区划起始年份（沿革用，未知留空）',
    end_year          SMALLINT DEFAULT NULL
                      COMMENT '区划终止年份（仍有效留空）',
    PRIMARY KEY (region_code),
    KEY idx_region_parent (parent_code),
    KEY idx_region_city (city_code),
    KEY idx_region_province_unit (province_code, is_analysis_unit, region_level),
    CONSTRAINT ck_region_level CHECK (region_level IN (1, 2, 3)),
    CONSTRAINT ck_region_span CHECK (
        end_year IS NULL OR start_year IS NULL OR end_year >= start_year
    ),
    CONSTRAINT ck_region_active CHECK (is_active IN (0, 1)),
    CONSTRAINT ck_region_unit CHECK (is_analysis_unit IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='行政区划维度：省/市/县区、县域分析单元、区划沿革';

-- ----------------------------------------------------------------------------
-- 2. 来源渠道维度
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_source_channel (
    channel_code     VARCHAR(20) NOT NULL
                     COMMENT '来源渠道编码：yearbook/bulletin/news_corpus/third_party/expert/none',
    channel_name     VARCHAR(50) NOT NULL
                     COMMENT '来源渠道名称',
    default_quality  TINYINT NOT NULL
                     COMMENT '默认质量等级：1 年鉴/公报，2 部门公开数据，3 新闻/第三方估算，4 待核验',
    description      VARCHAR(200) DEFAULT NULL
                     COMMENT '渠道说明',
    PRIMARY KEY (channel_code),
    CONSTRAINT ck_channel_quality CHECK (default_quality BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='来源渠道维度：回答“数据怎么来的”，与“谁发布的”分开';

-- ----------------------------------------------------------------------------
-- 3. 三级指标维度（不含权重）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_index (
    level3_code          VARCHAR(20) NOT NULL
                         COMMENT '三级指标稳定编码，如 A101；join/upsert 依据',
    level2_code          VARCHAR(10) NOT NULL
                         COMMENT '二级指标编码，如 A1',
    level2_name          VARCHAR(50) NOT NULL
                         COMMENT '二级指标名称',
    level1_code          VARCHAR(10) NOT NULL
                         COMMENT '一级维度编码：A/B/C/D',
    level1_name          VARCHAR(50) NOT NULL
                         COMMENT '一级维度名称',
    level3_name          VARCHAR(100) NOT NULL
                         COMMENT '三级指标统计口径名',
    is_county_applicable TINYINT NOT NULL DEFAULT 1
                         COMMENT '1-县域适用，0-需替代或不适用',
    county_replacement   VARCHAR(100) DEFAULT NULL
                         COMMENT '县域替代口径建议（若不适用）',
    status               ENUM('active', 'retired', 'draft') NOT NULL DEFAULT 'active'
                         COMMENT '指标状态：active 现行，retired 退役，draft 草案',
    sort_order           SMALLINT NOT NULL DEFAULT 0
                         COMMENT '同一二级指标内的展示顺序',
    note                 VARCHAR(255) DEFAULT NULL
                         COMMENT '备注',
    PRIMARY KEY (level3_code),
    UNIQUE KEY uk_index_level2_name (level2_code, level3_name),
    KEY idx_index_level1 (level1_code),
    KEY idx_index_level2 (level2_code),
    KEY idx_index_status (status),
    CONSTRAINT ck_index_level1 CHECK (level1_code IN ('A', 'B', 'C', 'D')),
    CONSTRAINT ck_index_county CHECK (is_county_applicable IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='三级指标维度：一/二级归属，不含权重（权重在表外配置）';

-- ----------------------------------------------------------------------------
-- 4. 指标口径/单位维度
-- ----------------------------------------------------------------------------
-- 一个三级指标可以有多个口径，例如：
--   A101 count=家（主口径）、A101 density=家/万人
--   D102 count=个（主口径）、D102 coverage=%
-- 每个指标必须恰好有一个 is_primary=1 的口径（由 primary_guard 唯一键强制）。
CREATE TABLE IF NOT EXISTS dim_index_measure (
    level3_code    VARCHAR(20) NOT NULL
                   COMMENT '三级指标编码',
    measure_code   VARCHAR(20) NOT NULL
                   COMMENT '口径编码：count/amount/share/growth/density/index/score/coverage',
    measure_name   VARCHAR(50) NOT NULL
                   COMMENT '口径名称',
    standard_unit  VARCHAR(30) NOT NULL
                   COMMENT '标准单位：家/亿元/%/分/指数值/人年…',
    value_kind     ENUM('numeric', 'score', 'qualitative', 'boolean') NOT NULL DEFAULT 'numeric'
                   COMMENT '值类型：数值/评分/定性/布尔',
    is_primary     TINYINT NOT NULL DEFAULT 0
                   COMMENT '1-主口径；每个指标恰好一个',
    sort_order     SMALLINT NOT NULL DEFAULT 0
                   COMMENT '同一指标内的口径顺序',
    note           VARCHAR(255) DEFAULT NULL
                   COMMENT '备注',
    -- 生成列：主口径时等于 level3_code，非主口径为 NULL；
    -- 利用 MySQL 唯一键允许多个 NULL 的特性，实现“每指标最多一个主口径”。
    primary_guard  VARCHAR(20)
                   GENERATED ALWAYS AS (IF(is_primary = 1, level3_code, NULL)) STORED,
    PRIMARY KEY (level3_code, measure_code),
    UNIQUE KEY uk_measure_primary (primary_guard),
    -- 注意：level3_code 被生成列 primary_guard 引用，MySQL 不允许该列上的
    -- ON UPDATE CASCADE（会报 1215 Cannot add foreign key constraint）；
    -- 且 level3_code 本就是稳定业务码，不应变更，故用 RESTRICT。
    CONSTRAINT fk_measure_index FOREIGN KEY (level3_code)
        REFERENCES dim_index (level3_code)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT ck_measure_primary CHECK (is_primary IN (0, 1)),
    CONSTRAINT ck_measure_unit CHECK (standard_unit <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='指标口径/单位维度：解决同一指标存在多种单位或定性值的问题';

-- ----------------------------------------------------------------------------
-- 5. 事实表：一行一个最终观测值（轻量溯源）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_index_value (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT
                        COMMENT '自增主键',
    region_code         VARCHAR(12) NOT NULL
                        COMMENT '行政区划代码',
    year                SMALLINT NOT NULL
                        COMMENT '数据所属年份（不是文章发布年份）',
    level3_code         VARCHAR(20) NOT NULL
                        COMMENT '三级指标编码',
    measure_code        VARCHAR(20) NOT NULL
                        COMMENT '口径编码',
    context_type        ENUM('actual', 'planned', 'target', 'decline', 'unknown')
                        NOT NULL DEFAULT 'actual'
                        COMMENT '值语境：实际值/计划值/目标值/下降或注销事件/未知',
    source_channel      VARCHAR(20) NOT NULL
                        COMMENT '来源渠道：yearbook/bulletin/news_corpus/third_party/expert/none',
    source_authority    ENUM('official', 'media', 'third_party', 'expert', 'unknown')
                        NOT NULL DEFAULT 'unknown'
                        COMMENT '发布方权威性',
    source_ref          VARCHAR(200) NOT NULL DEFAULT ''
                        COMMENT '来源文档：年鉴名/公报名/批次号；新闻聚合可空',
    obs_status          ENUM('present', 'missing', 'not_applicable', 'suppressed')
                        NOT NULL DEFAULT 'present'
                        COMMENT '观测状态：有值/缺失/结构不适用/保密',
    value_num           DECIMAL(20, 4) DEFAULT NULL
                        COMMENT '原始数值，按 value_unit 解释；不做库内归一化',
    value_text          VARCHAR(255) DEFAULT NULL
                        COMMENT '定性值或原始数值串（如 约2355家）',
    value_unit          VARCHAR(30) DEFAULT NULL
                        COMMENT '实际观测单位，可能与标准单位不同（如 万元 vs 亿元）',
    data_quality        TINYINT DEFAULT NULL
                        COMMENT '质量：1 年鉴/公报，2 部门公开数据，3 新闻/第三方估算，4 待核验',
    n_sources           SMALLINT DEFAULT NULL
                        COMMENT '聚合时参与来源数',
    value_min           DECIMAL(20, 4) DEFAULT NULL
                        COMMENT '多来源最小值',
    value_max           DECIMAL(20, 4) DEFAULT NULL
                        COMMENT '多来源最大值',
    agreement           TINYINT DEFAULT NULL
                        COMMENT '多来源是否一致：1 一致，0 不一致',
    evidence_ref        TEXT NULL
                        COMMENT '证据 article_id，多个用逗号分隔',
    source_publish_date DATE DEFAULT NULL
                        COMMENT '来源文档发布日期',
    chosen_reason       VARCHAR(255) DEFAULT NULL
                        COMMENT '最终值选取理由',
    remark              VARCHAR(500) DEFAULT NULL
                        COMMENT '备注（缺失原因、口径说明等）',
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        COMMENT '录入时间',
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP
                        COMMENT '更新时间',
    PRIMARY KEY (id),
    -- 轻量溯源：一个 (地区, 年, 指标, 口径, 上下文) 只有一行最终值。
    -- 不保留年鉴值、公报值、新闻值三行并存；多来源信息放在 n_sources/value_min/
    -- value_max/agreement/evidence_ref 里。
    UNIQUE KEY uk_fact_cell (region_code, year, level3_code, measure_code, context_type),
    KEY idx_fact_year_index_region (year, level3_code, region_code),
    KEY idx_fact_index_region_year (level3_code, region_code, year),
    KEY idx_fact_region_year (region_code, year),
    KEY idx_fact_measure (level3_code, measure_code),
    KEY idx_fact_channel (source_channel),
    KEY idx_fact_channel_quality (source_channel, data_quality),
    CONSTRAINT fk_fact_region FOREIGN KEY (region_code)
        REFERENCES dim_region (region_code)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_fact_measure FOREIGN KEY (level3_code, measure_code)
        REFERENCES dim_index_measure (level3_code, measure_code)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_fact_channel FOREIGN KEY (source_channel)
        REFERENCES dim_source_channel (channel_code)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT ck_fact_year CHECK (year BETWEEN 1990 AND 2100),
    CONSTRAINT ck_fact_quality CHECK (
        data_quality IS NULL OR data_quality BETWEEN 1 AND 4
    ),
    CONSTRAINT ck_fact_agreement CHECK (agreement IS NULL OR agreement IN (0, 1)),
    -- 有值行必须有 value_num 或 value_text；缺失/不适用/保密行必须两者都为空。
    CONSTRAINT ck_fact_present CHECK (
        (obs_status = 'present' AND (value_num IS NOT NULL OR value_text IS NOT NULL))
        OR (obs_status IN ('missing', 'not_applicable', 'suppressed')
            AND value_num IS NULL AND value_text IS NULL)
    ),
    -- 有值行必须记录质量；非有值行不允许带质量。
    CONSTRAINT ck_fact_quality_present CHECK (
        (obs_status = 'present' AND data_quality IS NOT NULL)
        OR (obs_status <> 'present' AND data_quality IS NULL)
    ),
    -- 数值型观测必须带实际单位。
    CONSTRAINT ck_fact_unit CHECK (
        obs_status <> 'present' OR value_num IS NULL OR value_unit IS NOT NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='指标事实表：一行一个最终观测值，含轻量溯源元数据';
