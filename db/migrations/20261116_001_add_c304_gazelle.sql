-- ============================================================================
-- 20261116_001 · 新增 C304 瞪羚企业数量
-- ============================================================================
-- 背景：C3「企业成长性」原 C301 是「"独角兽"、"瞪羚"企业数量」的合并指标，
--       两个不同榜单/认定口径的数共用同一 (region_code, year, level3_code,
--       measure_code, context_type) 主键会互相覆盖。本次拆为：
--         C301 独角兽企业数量
--         C304 瞪羚企业数量（新增）
--
-- 适用：已经跑过 db/00—90 基线的现有库。
--       全新库直接跑基线即可（21_seed_index / 30_views / 40_staging 已同步）。
--
-- 幂等：可重复执行。MySQL 8 不支持 ADD COLUMN IF NOT EXISTS，故用
--       information_schema + PREPARE 做条件加列。
-- ============================================================================

SET NAMES utf8mb4;
USE techfin;

-- ----------------------------------------------------------------------------
-- 1. 指标：C301 改名，新增 C304
-- ----------------------------------------------------------------------------
INSERT INTO dim_index
(level3_code, level2_code, level2_name, level1_code, level1_name,
 level3_name, is_county_applicable, county_replacement, status, sort_order, note) VALUES
('C301','C3','企业成长性','C','科技金融产出效能','独角兽企业数量',1,NULL,'active',1,NULL),
('C304','C3','企业成长性','C','科技金融产出效能','瞪羚企业数量',1,NULL,'active',4,NULL)
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
-- 2. 口径：C304/count（家）
-- ----------------------------------------------------------------------------
INSERT INTO dim_index_measure
(level3_code, measure_code, measure_name, standard_unit, value_kind, is_primary, sort_order) VALUES
('C304','count','企业数量','家','numeric',1,1)
ON DUPLICATE KEY UPDATE
    measure_name  = VALUES(measure_name),
    standard_unit = VALUES(standard_unit),
    value_kind    = VALUES(value_kind),
    is_primary    = VALUES(is_primary),
    sort_order    = VALUES(sort_order);

-- ----------------------------------------------------------------------------
-- 3. 暂存表加 C304 列（存在则跳过）
-- ----------------------------------------------------------------------------
SET @col_exists := (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'stg_wide_guangdong'
      AND column_name = 'C304'
);
SET @ddl := IF(
    @col_exists = 0,
    'ALTER TABLE stg_wide_guangdong ADD COLUMN C304 VARCHAR(100) DEFAULT NULL AFTER C303',
    'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 4. 重建广东省宽表视图（与 db/30_views.sql 保持一致，新增 C304）
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_wide_guangdong AS
SELECT
    r.region_code,
    r.region_name,
    r.region_full_name,
    r.region_level,
    r.city_code,
    v.year,

    MAX(CASE WHEN v.level3_code = 'A101' AND v.measure_code = 'count'    THEN v.value_num END) AS A101,
    MAX(CASE WHEN v.level3_code = 'A102' AND v.measure_code = 'count'    THEN v.value_num END) AS A102,
    MAX(CASE WHEN v.level3_code = 'A103' AND v.measure_code = 'share'    THEN v.value_num END) AS A103,

    MAX(CASE WHEN v.level3_code = 'A201' AND v.measure_code = 'amount'   THEN v.value_num END) AS A201,
    MAX(CASE WHEN v.level3_code = 'A202' AND v.measure_code = 'count'    THEN v.value_num END) AS A202,
    MAX(CASE WHEN v.level3_code = 'A203' AND v.measure_code = 'amount'   THEN v.value_num END) AS A203,
    MAX(CASE WHEN v.level3_code = 'A204' AND v.measure_code = 'density'  THEN v.value_num END) AS A204,

    MAX(CASE WHEN v.level3_code = 'A301' AND v.measure_code = 'share'    THEN v.value_num END) AS A301,
    MAX(CASE WHEN v.level3_code = 'A302' AND v.measure_code = 'amount'   THEN v.value_num END) AS A302,

    MAX(CASE WHEN v.level3_code = 'B101' AND v.measure_code = 'amount'   THEN v.value_num END) AS B101,
    MAX(CASE WHEN v.level3_code = 'B102' AND v.measure_code = 'growth'   THEN v.value_num END) AS B102,
    MAX(CASE WHEN v.level3_code = 'B103' AND v.measure_code = 'amount'   THEN v.value_num END) AS B103,
    MAX(CASE WHEN v.level3_code = 'B104' AND v.measure_code = 'share'    THEN v.value_num END) AS B104,

    MAX(CASE WHEN v.level3_code = 'B201' AND v.measure_code = 'count'    THEN v.value_num END) AS B201,
    MAX(CASE WHEN v.level3_code = 'B202' AND v.measure_code = 'amount'   THEN v.value_num END) AS B202,
    MAX(CASE WHEN v.level3_code = 'B203' AND v.measure_code = 'count'    THEN v.value_num END) AS B203,
    MAX(CASE WHEN v.level3_code = 'B204' AND v.measure_code = 'amount'   THEN v.value_num END) AS B204,

    MAX(CASE WHEN v.level3_code = 'B301' AND v.measure_code = 'amount'   THEN v.value_num END) AS B301,
    MAX(CASE WHEN v.level3_code = 'B302' AND v.measure_code = 'amount'   THEN v.value_num END) AS B302,

    MAX(CASE WHEN v.level3_code = 'B401' AND v.measure_code = 'amount'   THEN v.value_num END) AS B401,
    MAX(CASE WHEN v.level3_code = 'B402' AND v.measure_code = 'index'    THEN v.value_num END) AS B402,
    MAX(CASE WHEN v.level3_code = 'B403' AND v.measure_code = 'score'    THEN v.value_num END) AS B403,

    MAX(CASE WHEN v.level3_code = 'C101' AND v.measure_code = 'count'    THEN v.value_num END) AS C101,
    MAX(CASE WHEN v.level3_code = 'C102' AND v.measure_code = 'amount'   THEN v.value_num END) AS C102,
    MAX(CASE WHEN v.level3_code = 'C103' AND v.measure_code = 'density'  THEN v.value_num END) AS C103,

    MAX(CASE WHEN v.level3_code = 'C201' AND v.measure_code = 'share'    THEN v.value_num END) AS C201,
    MAX(CASE WHEN v.level3_code = 'C202' AND v.measure_code = 'amount'   THEN v.value_num END) AS C202,

    MAX(CASE WHEN v.level3_code = 'C301' AND v.measure_code = 'count'    THEN v.value_num END) AS C301,
    MAX(CASE WHEN v.level3_code = 'C302' AND v.measure_code = 'growth'   THEN v.value_num END) AS C302,
    MAX(CASE WHEN v.level3_code = 'C303' AND v.measure_code = 'growth'   THEN v.value_num END) AS C303,
    MAX(CASE WHEN v.level3_code = 'C304' AND v.measure_code = 'count'    THEN v.value_num END) AS C304,

    MAX(CASE WHEN v.level3_code = 'D101' AND v.measure_code = 'score'    THEN v.value_num END) AS D101,
    MAX(CASE WHEN v.level3_code = 'D102' AND v.measure_code = 'count'    THEN v.value_num END) AS D102,

    MAX(CASE WHEN v.level3_code = 'D201' AND v.measure_code = 'amount'   THEN v.value_num END) AS D201,
    MAX(CASE WHEN v.level3_code = 'D202' AND v.measure_code = 'count'    THEN v.value_num END) AS D202,
    MAX(CASE WHEN v.level3_code = 'D203' AND v.measure_code = 'index'    THEN v.value_num END) AS D203

FROM dim_region r
LEFT JOIN v_index_actual v
  ON v.region_code = r.region_code
WHERE r.province_code = '440000'
GROUP BY
    r.region_code,
    r.region_name,
    r.region_full_name,
    r.region_level,
    r.city_code,
    v.year
ORDER BY
    r.city_code,
    r.region_code,
    v.year;

-- ----------------------------------------------------------------------------
-- 5. 自检（应输出 36 / 38）
-- ----------------------------------------------------------------------------
SELECT '现行三级指标数（应为 36）' AS 检查项, COUNT(*) AS 结果
FROM dim_index WHERE status = 'active'
UNION ALL
SELECT '口径数（应为 38）', COUNT(*)
FROM dim_index_measure;
