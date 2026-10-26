-- ============================================================================
-- TechFin · 30 视图
-- ============================================================================
-- 依赖：10_schema.sql、20_seed_region.sql、21_seed_index.sql
--
-- 视图定位：
--   v_index_actual     只含 actual + present 的便捷入口（分析、合成只读它）
--   v_index_long       长表 + 中文明细 + 层级 + 来源，DataGrip 日常查看用
--   v_wide_column_map  宽表列名 → 指标口径的映射（替代“每省一张列映射表”）
--   v_wide_guangdong   广东省宽表视图（只读，查看/导出用；不可编辑）
--
-- 重要：宽表视图只用于展示和导出，不作为系统记录。新增/修改数据请写
--       fact_index_value 或 stg_wide_guangdong。
-- ============================================================================

SET NAMES utf8mb4;
USE techfin;

-- ----------------------------------------------------------------------------
-- 1. 只读分析入口：实际值 + 有值
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_index_actual AS
SELECT *
FROM fact_index_value
WHERE context_type = 'actual'
  AND obs_status = 'present';

-- ----------------------------------------------------------------------------
-- 2. 长表明细视图：DataGrip 日常查询用
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_index_long AS
SELECT
    v.id,
    r.province_code,
    r.city_code,
    r.region_code,
    r.region_name,
    r.region_full_name,
    r.region_level,
    r.region_type,
    r.is_analysis_unit,
    v.year,
    v.level3_code,
    i.level3_name,
    i.level2_code,
    i.level2_name,
    i.level1_code,
    i.level1_name,
    v.measure_code,
    m.measure_name,
    m.standard_unit,
    m.value_kind,
    v.context_type,
    v.obs_status,
    v.value_num,
    v.value_text,
    v.value_unit,
    v.source_channel,
    c.channel_name,
    v.source_authority,
    v.source_ref,
    v.source_publish_date,
    v.data_quality,
    v.n_sources,
    v.value_min,
    v.value_max,
    v.agreement,
    v.evidence_ref,
    v.chosen_reason,
    v.remark,
    v.created_at,
    v.updated_at
FROM fact_index_value v
JOIN dim_region r
  ON r.region_code = v.region_code
JOIN dim_index i
  ON i.level3_code = v.level3_code
JOIN dim_index_measure m
  ON m.level3_code = v.level3_code
 AND m.measure_code = v.measure_code
LEFT JOIN dim_source_channel c
  ON c.channel_code = v.source_channel;

-- ----------------------------------------------------------------------------
-- 3. 宽表列映射：列名 = level3_code，含义/单位在这里查
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_wide_column_map AS
SELECT
    i.level3_code       AS column_name,
    i.level3_code,
    i.level3_name,
    i.level1_code,
    i.level1_name,
    i.level2_code,
    i.level2_name,
    m.measure_code,
    m.measure_name,
    m.standard_unit,
    m.value_kind
FROM dim_index i
JOIN dim_index_measure m
  ON m.level3_code = i.level3_code
 AND m.is_primary = 1
WHERE i.status = 'active'
ORDER BY i.level2_code, i.sort_order;

-- ----------------------------------------------------------------------------
-- 4. 广东省宽表视图（只读）
-- ----------------------------------------------------------------------------
-- 每行 = 地区 × 年份；每列 = 一个三级指标的主口径。
-- 一个地区某年没有任何数据时，year 为 NULL，仍保留地区行。
-- 同一格的多来源已被聚合为一个最终值；溯源请查 v_index_long。
-- 列名 A101/A102/... 的含义与单位见 v_wide_column_map。
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
