-- ============================================================================
-- TechFin · 50 可选：指数合成结果
-- ============================================================================
-- 依赖：10_schema.sql
--
-- 说明：
--   - 本项目约定“权重、归一化、合成方法全部在表外（Python + 配置文件）”。
--   - 本文件不是核心表；只有需要把合成结果回存数据库时才执行。
--   - fact_index_run 记录可复现性：weight_scheme + config_sha256 + norm_method +
--     source_policy；不存权重数值本身，避免数据库成为第二权威。
--   - fact_index_score 按 run_id 版本化，换权重/换方法新建 run，不覆盖旧结果。
-- ============================================================================

SET NAMES utf8mb4;
USE techfin;

-- ----------------------------------------------------------------------------
-- 1. 计算批次
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_index_run (
    run_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT
                   COMMENT '计算批次主键',
    calc_version   VARCHAR(20) NOT NULL
                   COMMENT '合成算法版本',
    weight_scheme  VARCHAR(50) NOT NULL
                   COMMENT '权重方案标识，如 weights_v1',
    config_sha256  CHAR(64) NOT NULL
                   COMMENT '权重/归一化配置文件的 SHA-256，用于复现',
    source_policy  ENUM('official_only', 'official_first', 'blend')
                   NOT NULL DEFAULT 'official_first'
                   COMMENT '来源取舍策略：仅官方/官方优先/混合',
    norm_method    VARCHAR(20) NOT NULL
                   COMMENT '归一化方法，如 minmax/zscore',
    started_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                   COMMENT '开始时间',
    finished_at    DATETIME DEFAULT NULL
                   COMMENT '完成时间',
    note           VARCHAR(500) DEFAULT NULL
                   COMMENT '备注',
    PRIMARY KEY (run_id),
    UNIQUE KEY uk_run (calc_version, weight_scheme, config_sha256, source_policy, norm_method)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='指数合成批次：记录权重方案与配置哈希，不存权重值';

-- ----------------------------------------------------------------------------
-- 2. 合成得分
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_index_score (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT
                      COMMENT '主键',
    run_id            BIGINT UNSIGNED NOT NULL
                      COMMENT '计算批次',
    region_code       VARCHAR(12) NOT NULL
                      COMMENT '行政区划代码',
    year              SMALLINT NOT NULL
                      COMMENT '年份',
    node_level        TINYINT NOT NULL
                      COMMENT '0-总指数 1-一级维度 2-二级指标',
    node_code         VARCHAR(20) NOT NULL
                      COMMENT '节点编码：TOTAL / A / A1 …',
    node_name         VARCHAR(50) NOT NULL
                      COMMENT '节点名称',
    score             DECIMAL(12, 6) NOT NULL
                      COMMENT '归一化加权后的合成得分',
    rank_in_year      INT DEFAULT NULL
                      COMMENT '同年同层级内排名',
    n_indicators_used SMALLINT DEFAULT NULL
                      COMMENT '实际参与合成的三级指标数',
    coverage_ratio    DECIMAL(5, 4) DEFAULT NULL
                      COMMENT '指标覆盖率 0~1',
    computed_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                      COMMENT '计算时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_score (run_id, region_code, year, node_code),
    KEY idx_score_query (run_id, year, node_code, score),
    CONSTRAINT fk_score_run FOREIGN KEY (run_id)
        REFERENCES fact_index_run (run_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_score_region FOREIGN KEY (region_code)
        REFERENCES dim_region (region_code)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT ck_score_node_level CHECK (node_level IN (0, 1, 2)),
    CONSTRAINT ck_score_coverage CHECK (
        coverage_ratio IS NULL OR coverage_ratio BETWEEN 0 AND 1
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='指数合成结果：按 run_id 版本化的派生缓存';

-- 若不需要回存合成结果，不执行本文件即可，核心事实表与视图不受影响。
