-- ============================================================================
-- TechFin · 90 自检查询
-- ============================================================================
-- 依赖：00—40 已执行（50 可选）。
-- 本文件只读，不修改数据。可在 DataGrip 中整段执行，逐条看结果。
-- ============================================================================

SET NAMES utf8mb4;
USE techfin;

-- ----------------------------------------------------------------------------
-- 1. 区划
-- ----------------------------------------------------------------------------
SELECT '广东区划总行数' AS 检查项, COUNT(*) AS 结果
FROM dim_region WHERE province_code = '440000'
UNION ALL
SELECT '广东县域分析单元（应为 124）', COUNT(*)
FROM dim_region WHERE province_code = '440000' AND is_analysis_unit = 1
UNION ALL
SELECT '广东 region_type 为 NULL（应为 0）', COUNT(*)
FROM dim_region WHERE province_code = '440000' AND region_type IS NULL
UNION ALL
SELECT '广东非省级 city_code 为 NULL（应为 0）', COUNT(*)
FROM dim_region
WHERE province_code = '440000' AND region_level > 1 AND city_code IS NULL;

-- 东莞/中山必须被算进县域分析单元
SELECT region_code, region_name, region_level, is_analysis_unit
FROM dim_region
WHERE region_code IN ('441900', '442000');

-- ----------------------------------------------------------------------------
-- 2. 指标与口径
-- ----------------------------------------------------------------------------
SELECT '现行三级指标数（应为 35）' AS 检查项, COUNT(*) AS 结果
FROM dim_index WHERE status = 'active'
UNION ALL
SELECT '口径数（应为 37）', COUNT(*)
FROM dim_index_measure
UNION ALL
SELECT '无主口径指标数（应为 0）', COUNT(*)
FROM dim_index i
LEFT JOIN dim_index_measure m
  ON m.level3_code = i.level3_code AND m.is_primary = 1
WHERE i.status = 'active' AND m.level3_code IS NULL
UNION ALL
SELECT '多主口径指标数（应为 0）', COUNT(*)
FROM (
    SELECT level3_code
    FROM dim_index_measure
    WHERE is_primary = 1
    GROUP BY level3_code
    HAVING COUNT(*) > 1
) t
UNION ALL
SELECT '宽表映射列数（应为 35）', COUNT(*)
FROM v_wide_column_map;

-- 按一级维度统计指标数
SELECT level1_code, level1_name, COUNT(*) AS 指标数
FROM dim_index
WHERE status = 'active'
GROUP BY level1_code, level1_name
ORDER BY level1_code;

-- ----------------------------------------------------------------------------
-- 3. 事实表完整性
-- ----------------------------------------------------------------------------
SELECT '事实表行数' AS 检查项, COUNT(*) AS 结果 FROM fact_index_value;

-- 孤儿：区划不存在
SELECT '孤儿区划（应为 0）' AS 检查项, COUNT(*) AS 结果
FROM fact_index_value v
LEFT JOIN dim_region r ON r.region_code = v.region_code
WHERE r.region_code IS NULL;

-- 孤儿：指标口径不存在
SELECT '孤儿指标口径（应为 0）' AS 检查项, COUNT(*) AS 结果
FROM fact_index_value v
LEFT JOIN dim_index_measure m
  ON m.level3_code = v.level3_code AND m.measure_code = v.measure_code
WHERE m.level3_code IS NULL;

-- 轻量溯源：同一格最多一行（唯一键应已拦住）
SELECT region_code, year, level3_code, measure_code, context_type, COUNT(*) AS n
FROM fact_index_value
GROUP BY region_code, year, level3_code, measure_code, context_type
HAVING n > 1;

-- 百分比指标范围检查：标准单位为 % 的值必须在 0—100
SELECT v.id, v.region_code, v.year, v.level3_code, v.measure_code,
       v.value_num, v.value_unit
FROM fact_index_value v
JOIN dim_index_measure m
  ON m.level3_code = v.level3_code AND m.measure_code = v.measure_code
WHERE m.standard_unit = '%'
  AND v.obs_status = 'present'
  AND v.value_num IS NOT NULL
  AND (v.value_unit <> '%' OR v.value_num NOT BETWEEN 0 AND 100);

-- 有值行但缺质量（CHECK 应已拦住）
SELECT COUNT(*) AS 有值但缺质量
FROM fact_index_value
WHERE obs_status = 'present' AND data_quality IS NULL;

-- 缺失/不适用/保密行却带值（CHECK 应已拦住）
SELECT COUNT(*) AS 非有值行却带值
FROM fact_index_value
WHERE obs_status <> 'present'
  AND (value_num IS NOT NULL OR value_text IS NOT NULL);

-- ----------------------------------------------------------------------------
-- 4. 来源与覆盖
-- ----------------------------------------------------------------------------
SELECT source_channel, COUNT(*) AS 行数,
       MIN(data_quality) AS 最优质,
       MAX(data_quality) AS 最差值
FROM fact_index_value
GROUP BY source_channel
ORDER BY 行数 DESC;

SELECT i.level3_code, i.level3_name,
       COUNT(DISTINCT v.region_code) AS 覆盖地区数,
       COUNT(v.id) AS 观测数
FROM dim_index i
LEFT JOIN fact_index_value v
  ON v.level3_code = i.level3_code
 AND v.context_type = 'actual'
 AND v.obs_status = 'present'
WHERE i.status = 'active'
GROUP BY i.level3_code, i.level3_name
ORDER BY 覆盖地区数, i.level3_code;

-- ----------------------------------------------------------------------------
-- 4.1 准确度专项（CSV 抽取/入库质量）
-- ----------------------------------------------------------------------------
-- A103 应为 share/%；出现 count/家 即为系统性错标，应为 0
SELECT 'A103+count 错配（应为 0）' AS 检查项, COUNT(*) AS 结果
FROM fact_index_value
WHERE level3_code = 'A103' AND measure_code = 'count';

-- 区县事实缺证据（不应出现）
SELECT '区县事实缺 evidence_ref' AS 检查项, COUNT(*) AS 结果
FROM fact_index_value v
JOIN dim_region r ON r.region_code = v.region_code AND r.region_level = 3
WHERE v.obs_status = 'present'
  AND (v.evidence_ref IS NULL OR v.evidence_ref = '');

-- 低置信年份（year_confidence < 0.5；loader 默认已拒绝，正常应为 0）
SELECT '低置信年份行（应为 0）' AS 检查项, COUNT(*) AS 结果
FROM fact_index_value
WHERE obs_status = 'present'
  AND remark LIKE '%year_confidence=%'
  AND CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(remark, 'year_confidence=', -1), ';', 1)
           AS DECIMAL(5,2)) < 0.5;

-- 拆分/汇总口径混入事实表（应为 0）
SELECT 'breakdown/subset 混入' AS 检查项, COUNT(*) AS 结果
FROM fact_index_value
WHERE remark LIKE '%aggregation_type=breakdown%'
   OR remark LIKE '%aggregation_type=subset%';

-- 地区来源分布：query/scope_* 越多，人工复核优先级越高
SELECT CASE
         WHEN remark LIKE '%region_from=query%' THEN 'query'
         WHEN remark LIKE '%region_from=scope_%' THEN 'scope_*'
         ELSE 'text/other'
       END AS 地区来源,
       COUNT(*) AS 行数
FROM fact_index_value
GROUP BY 地区来源
ORDER BY 行数 DESC;

-- 每个县的覆盖度与最近更新时间
SELECT r.region_code, r.region_name,
       COUNT(v.id) AS 观测数,
       MAX(v.updated_at) AS 最近更新
FROM dim_region r
LEFT JOIN fact_index_value v
  ON v.region_code = r.region_code
 AND v.context_type = 'actual'
 AND v.obs_status = 'present'
WHERE r.province_code = '440000' AND r.region_level = 3
GROUP BY r.region_code, r.region_name
ORDER BY 观测数, r.region_code;

-- ----------------------------------------------------------------------------
-- 5. 视图抽查
-- ----------------------------------------------------------------------------
-- 宽表视图：列名含义见 v_wide_column_map
SELECT * FROM v_wide_guangdong LIMIT 5;

-- 长表视图：带中文明细与溯源
SELECT * FROM v_index_long LIMIT 5;
