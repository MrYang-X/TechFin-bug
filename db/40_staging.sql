-- ============================================================================
-- TechFin · 40 宽表暂存层
-- ============================================================================
-- 依赖：10_schema.sql、21_seed_index.sql
--
-- 用途：
--   - 承接 DataGrip / Excel / CSV 的“一行一个地区、一列一个指标”宽表录入。
--   - 暂存表列全部用 VARCHAR，允许“约2355家”“4.2%”“N/A”等脏值。
--   - 由后续 loader（Python）校验并拆成长表写入 fact_index_value。
--   - 暂存表不是系统记录，装载成功后可清空。
--
-- 注意：
--   - stg_wide_guangdong 故意不建 region_code 外键：它是脏数据入口，
--     非法区划码要能被保留下来供人工修正，而不是被数据库直接拒绝。
--   - 指标列的列名就是 level3_code，含义/单位见 v_wide_column_map。
--   - 每个指标列只对应“主口径”（A101=家、D102=个）。次口径（A101 家/万人、
--     D102 %）不进入宽表，需通过长表或单独模板录入。
-- ============================================================================

SET NAMES utf8mb4;
USE techfin;

-- ----------------------------------------------------------------------------
-- 1. 导入批次
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_batch (
    batch_id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT
                             COMMENT '批次主键',
    batch_name               VARCHAR(100) NOT NULL
                             COMMENT '批次名称，如 2026-10-广东统计年鉴补录',
    file_name                VARCHAR(255) DEFAULT NULL
                             COMMENT '源文件名',
    file_sha256              CHAR(64) DEFAULT NULL
                             COMMENT '源文件 SHA-256，用于去重',
    default_source_channel   VARCHAR(20) NOT NULL DEFAULT 'news_corpus'
                             COMMENT '批次默认来源渠道',
    default_source_authority ENUM('official', 'media', 'third_party', 'expert', 'unknown')
                             NOT NULL DEFAULT 'unknown'
                             COMMENT '批次默认发布方权威性',
    default_data_quality     TINYINT DEFAULT NULL
                             COMMENT '批次默认质量等级；为空时 loader 按渠道默认值填',
    default_source_ref       VARCHAR(200) NOT NULL DEFAULT ''
                             COMMENT '批次默认来源文档名',
    imported_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                             COMMENT '导入时间',
    note                     VARCHAR(500) DEFAULT NULL
                             COMMENT '备注',
    PRIMARY KEY (batch_id),
    KEY idx_batch_channel (default_source_channel),
    CONSTRAINT fk_batch_channel FOREIGN KEY (default_source_channel)
        REFERENCES dim_source_channel (channel_code)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT ck_batch_quality CHECK (
        default_data_quality IS NULL OR default_data_quality BETWEEN 1 AND 4
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='批量导入批次：文件、默认来源与质量';

-- ----------------------------------------------------------------------------
-- 2. 广东省宽表暂存
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stg_wide_guangdong (
    row_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT
                      COMMENT '行主键',
    batch_id          BIGINT UNSIGNED DEFAULT NULL
                      COMMENT '所属导入批次',
    region_code       VARCHAR(12) NOT NULL
                      COMMENT '行政区划代码（loader 校验）',
    region_name       VARCHAR(50) DEFAULT NULL
                      COMMENT '区划名称（辅助核对，可为空）',
    year              SMALLINT NOT NULL
                      COMMENT '数据年份',
    context_type      ENUM('actual', 'planned', 'target', 'decline', 'unknown')
                      NOT NULL DEFAULT 'actual'
                      COMMENT '值语境',
    source_channel    VARCHAR(20) DEFAULT NULL
                      COMMENT '本行来源渠道；为空则用批次默认值',
    source_authority  ENUM('official', 'media', 'third_party', 'expert', 'unknown')
                      DEFAULT NULL
                      COMMENT '本行权威性；为空则用批次默认值',
    data_quality      TINYINT DEFAULT NULL
                      COMMENT '本行质量；为空则用批次默认值',
    source_ref        VARCHAR(200) DEFAULT NULL
                      COMMENT '本行来源文档；为空则用批次默认值',
    evidence_ref      TEXT NULL
                      COMMENT '本行证据 article_id，多个用逗号分隔',

    -- 35 个三级指标（主口径），列名 = level3_code
    A101 VARCHAR(100) DEFAULT NULL,
    A102 VARCHAR(100) DEFAULT NULL,
    A103 VARCHAR(100) DEFAULT NULL,
    A201 VARCHAR(100) DEFAULT NULL,
    A202 VARCHAR(100) DEFAULT NULL,
    A203 VARCHAR(100) DEFAULT NULL,
    A204 VARCHAR(100) DEFAULT NULL,
    A301 VARCHAR(100) DEFAULT NULL,
    A302 VARCHAR(100) DEFAULT NULL,
    B101 VARCHAR(100) DEFAULT NULL,
    B102 VARCHAR(100) DEFAULT NULL,
    B103 VARCHAR(100) DEFAULT NULL,
    B104 VARCHAR(100) DEFAULT NULL,
    B201 VARCHAR(100) DEFAULT NULL,
    B202 VARCHAR(100) DEFAULT NULL,
    B203 VARCHAR(100) DEFAULT NULL,
    B204 VARCHAR(100) DEFAULT NULL,
    B301 VARCHAR(100) DEFAULT NULL,
    B302 VARCHAR(100) DEFAULT NULL,
    B401 VARCHAR(100) DEFAULT NULL,
    B402 VARCHAR(100) DEFAULT NULL,
    B403 VARCHAR(100) DEFAULT NULL,
    C101 VARCHAR(100) DEFAULT NULL,
    C102 VARCHAR(100) DEFAULT NULL,
    C103 VARCHAR(100) DEFAULT NULL,
    C201 VARCHAR(100) DEFAULT NULL,
    C202 VARCHAR(100) DEFAULT NULL,
    C301 VARCHAR(100) DEFAULT NULL,
    C302 VARCHAR(100) DEFAULT NULL,
    C303 VARCHAR(100) DEFAULT NULL,
    D101 VARCHAR(100) DEFAULT NULL,
    D102 VARCHAR(100) DEFAULT NULL,
    D201 VARCHAR(100) DEFAULT NULL,
    D202 VARCHAR(100) DEFAULT NULL,
    D203 VARCHAR(100) DEFAULT NULL,

    load_status       ENUM('pending', 'loaded', 'error') NOT NULL DEFAULT 'pending'
                      COMMENT '装载状态',
    error_msg         VARCHAR(500) DEFAULT NULL
                      COMMENT '装载失败原因',
    loaded_at         DATETIME DEFAULT NULL
                      COMMENT '装载时间',
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                      COMMENT '录入时间',
    PRIMARY KEY (row_id),
    KEY idx_stg_batch (batch_id),
    KEY idx_stg_region_year (region_code, year),
    KEY idx_stg_status (load_status),
    CONSTRAINT fk_stg_batch FOREIGN KEY (batch_id)
        REFERENCES import_batch (batch_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT ck_stg_year CHECK (year BETWEEN 1990 AND 2100),
    CONSTRAINT ck_stg_quality CHECK (
        data_quality IS NULL OR data_quality BETWEEN 1 AND 4
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='广东省宽表暂存：Excel/CSV/DataGrip 录入入口，由 loader 拆成长表';

-- 其他省份复制本表结构，改名为 stg_wide_<province> 即可。
-- 例如：CREATE TABLE stg_wide_sichuan LIKE stg_wide_guangdong;
