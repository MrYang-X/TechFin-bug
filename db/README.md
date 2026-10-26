# TechFin 数据库脚本说明

本目录是科技金融指数研究的数据库基线（baseline）。目标是把“地区 × 年份 × 指标”的原始观测可靠地存下来，并支持：

- 同一地区、同一指标存多年面板；
- 每个值保留轻量溯源（来源渠道、发布方、质量、证据 article_id、来源数量、一致性）；
- DataGrip 手工编辑、Excel/CSV 批量导入、Python 管线写入三种方式共用一套表；
- 权重、归一化、指数合成全部在表外（Python + 配置文件）实现。

数据库：MySQL 8.0.16+（本机实测 8.0.43），库名 `techfin`，字符集 `utf8mb4`。

---

## 1. 为什么不是“一省一张宽表”

最初设想是“每个省一张表，一行一个地区，一列一个指标，另建一张列映射表”。这个方案适合展示，不适合作为系统记录，原因如下：

1. **MySQL 中 schema = database**，不能在一个库里再套“省 schema”；按省建 schema 实际是建 31 个数据库。
2. **宽表格子放不下轻量溯源元数据**：每个值还要记录来源渠道、质量、证据 article_id、来源数量、一致性、最小/最大值；宽表要么为每个指标再加五六列，要么丢失信息。
3. **年份维度会爆炸**：年份做成列是 35 × 6 = 210 列起；按“省 × 年”拆表则是全国 310 张表。
4. **跨省查询退化为 UNION ALL**：新增省份要改所有查询、视图和报表。
5. **列名 → 指标无法用外键约束**：改列名/删列/拼写错误会让映射静默失效。
6. **指标变更要 ALTER 所有省表**；长表只需在 `dim_index` 插一行。

本目录的方案是：**单库 + 全局维度表 + 长表事实表 + 每省宽表视图/暂存表**。宽表仍然存在，但只做“入口”和“出口”，系统记录永远是 `fact_index_value`。

---

## 2. 文件清单与执行顺序

在项目根目录执行（DataGrip 中也可以逐个文件打开执行）：

```bash
mysql -u root -p --default-character-set=utf8mb4 < db/00_database.sql
mysql -u root -p --default-character-set=utf8mb4 < db/10_schema.sql
mysql -u root -p --default-character-set=utf8mb4 < db/20_seed_region.sql
mysql -u root -p --default-character-set=utf8mb4 < db/21_seed_index.sql
mysql -u root -p --default-character-set=utf8mb4 < db/30_views.sql
mysql -u root -p --default-character-set=utf8mb4 < db/40_staging.sql
mysql -u root -p --default-character-set=utf8mb4 < db/50_optional_score.sql   # 可选
mysql -u root -p --default-character-set=utf8mb4 < db/90_checks.sql
```

| 文件 | 作用 |
|---|---|
| `00_database.sql` | 创建 `techfin` 库、字符集、会话时区；不删除已有库 |
| `10_schema.sql` | 5 张核心表：`dim_region`、`dim_source_channel`、`dim_index`、`dim_index_measure`、`fact_index_value` |
| `20_seed_region.sql` | 广东 21 市 + 122 个县级单位、东莞/中山市本级；附四川 4 行示例 |
| `21_seed_index.sql` | 35 条三级指标、37 条口径、6 个来源渠道 |
| `30_views.sql` | `v_index_actual`、`v_index_long`、`v_wide_column_map`、`v_wide_guangdong` |
| `40_staging.sql` | `import_batch`、`stg_wide_guangdong`（宽表录入暂存） |
| `50_optional_score.sql` | 可选：`fact_index_run` / `fact_index_score`，回存合成结果 |
| `90_checks.sql` | 只读自检查询 |

**注意**：项目根目录的 `tech_finance_index_migration.sql` 是旧结构的增量迁移脚本，第一条就是 `ALTER TABLE`，**空库不能执行**。新库只使用本目录脚本；旧文件保留作历史参考。

---

## 3. 表关系

```
dim_region (1) ─────< (N) fact_index_value (N) >───── (1) dim_index_measure (N) >── (1) dim_index
dim_source_channel (1) ─────< (N) fact_index_value

import_batch (1) ─────< (N) stg_wide_guangdong        -- 宽表暂存，loader 拆成长表
```

- `fact_index_value` 不直接外键到 `dim_index`，而是外键到 `dim_index_measure(level3_code, measure_code)`；后者再外键到 `dim_index`。
- `province_code` 是 `dim_region` 的生成列（`region_code` 前两位 + `0000`），不单独维护。

---

## 4. 核心字段语义

### 4.1 `fact_index_value` 的唯一键

```sql
UNIQUE KEY uk_fact_cell (region_code, year, level3_code, measure_code, context_type)
```

表示：**一个地区、一年、一个指标、一个口径、一种值语境，只有一行最终值**。

这是“轻量溯源”模式：

- 不保留年鉴值、公报值、新闻值三行并存；
- 多来源信息放在同一行的 `n_sources / value_min / value_max / agreement / evidence_ref / chosen_reason`；
- 如果将来需要同格多来源并列比较，把 `source_channel + source_ref` 加入唯一键即可升级。

### 4.2 `source_channel` / `source_authority` / `data_quality`

三个字段回答三个不同问题：

| 字段 | 回答 | 取值示例 |
|---|---|---|
| `source_channel` | 数据怎么来的 | `yearbook` 统计年鉴、`bulletin` 政府公报/部门数据、`news_corpus` 新闻抽取、`third_party` 第三方、`expert` 专家、`none` |
| `source_authority` | 谁发布的 | `official` 官方、`media` 媒体、`third_party` 第三方、`expert` 专家、`unknown` |
| `data_quality` | 这个值多可信 | 1 年鉴/公报，2 部门公开数据，3 新闻/第三方估算，4 待核验 |

典型组合：官方文件被新闻转载后抽取 → `source_channel='news_corpus'`、`source_authority='official'`、`data_quality=2`。

### 4.3 `context_type`

| 值 | 含义 |
|---|---|
| `actual` | 已发生的实际值 |
| `planned` | 计划值 |
| `target` | 目标值（`year` 是目标年份，不是文章年份） |
| `decline` | 减少/注销/迁出等实际事件（不是负数） |
| `unknown` | 无法判断 |

分析、合成、报表**只读 `v_index_actual`**（`context_type='actual' AND obs_status='present'`），避免计划/目标值污染。

### 4.4 `obs_status`

| 值 | 含义 |
|---|---|
| `present` | 有值；`value_num` 或 `value_text` 至少一个非空 |
| `missing` | 应该有但没采到/未公布 |
| `not_applicable` | 结构性不适用（如县级没有该指标） |
| `suppressed` | 有数据但保密/不公开 |

再区分“没有行”和“有行但缺失”：

- **没有行**：尚未采集/尚未录入；
- **`missing` 行**：明确知道缺；
- **`not_applicable` 行**：结构上不该有。

`data_quality` 只描述 `present` 的值；非 `present` 行必须为 `NULL`（由 CHECK 强制）。

### 4.5 `value_unit` 与 `standard_unit`

- `dim_index_measure.standard_unit`：该口径的标准单位，如 `亿元`、`家/万人`、`%`。
- `fact_index_value.value_unit`：本次实际观测单位，如 `万元`、`家`。
- **数据库不做单位换算**。loader / Python 计算层负责把 `value_num` 按 `value_unit → standard_unit` 归一化。

### 4.6 `measure_code`

同一个三级指标可能有多个口径：

| 指标 | 口径 | 标准单位 | 是否主口径 |
|---|---|---|---|
| A101 高新技术企业数量/密度 | `count` | 家 | 是 |
| A101 | `density` | 家/万人 | 否 |
| D102 科技金融服务中心/网点覆盖情况 | `count` | 个 | 是 |
| D102 | `coverage` | % | 否 |

宽表视图 `v_wide_guangdong` 只展开主口径；次口径仍可通过 `fact_index_value` / `v_index_long` 查询。

### 4.7 `is_analysis_unit`

- `region_level=3` 的全部区县 + 东莞 `441900`、中山 `442000` 市本级 = **124 个广东县域分析单元**。
- 取县域统一用 `is_analysis_unit = 1`，**不要写 `region_level = 3`**，否则会静默丢掉东莞和中山。
- 城市级查询（如广州市 `440100`）**不要加** `is_analysis_unit = 1`，否则广州本身会被过滤。

---

## 5. 常用查询

### 5.1 广州市 R&D 经费支出占 GDP 比重（逐年）

```sql
SELECT year, value_num, value_unit,
       source_channel, source_ref, data_quality, evidence_ref
FROM fact_index_value
WHERE region_code = '440100'
  AND level3_code = 'A103'
  AND measure_code = 'share'
  AND context_type = 'actual'
  AND obs_status = 'present'
ORDER BY year;
```

### 5.2 广州白云区 A1 下各指标（没有数据也列出指标名）

```sql
SELECT i.level3_code, i.level3_name,
       m.measure_code, m.standard_unit,
       v.year, v.value_num, v.value_unit,
       v.source_channel, v.data_quality, v.evidence_ref
FROM dim_index i
JOIN dim_index_measure m
  ON m.level3_code = i.level3_code AND m.is_primary = 1
LEFT JOIN fact_index_value v
  ON v.level3_code = i.level3_code
 AND v.measure_code = m.measure_code
 AND v.region_code = '440111'
 AND v.year = 2024
 AND v.context_type = 'actual'
 AND v.obs_status = 'present'
WHERE i.level2_code = 'A1'
ORDER BY i.sort_order, m.sort_order;
```

### 5.3 某年某指标全省县域截面

```sql
SELECT r.province_code, r.region_code, r.region_name,
       v.value_num, v.value_unit, v.data_quality
FROM fact_index_value v
JOIN dim_region r ON r.region_code = v.region_code
WHERE v.level3_code = 'A101'
  AND v.measure_code = 'count'
  AND v.year = 2024
  AND v.context_type = 'actual'
  AND v.obs_status = 'present'
  AND r.is_analysis_unit = 1
ORDER BY r.province_code, v.value_num DESC;
```

### 5.4 查看宽表

```sql
-- 广东省宽表：列名 A101... 的含义/单位在 v_wide_column_map
SELECT * FROM v_wide_guangdong LIMIT 100;

-- 列映射
SELECT * FROM v_wide_column_map ORDER BY level2_code, level3_code;

-- 带中文明细与溯源的长表
SELECT * FROM v_index_long
WHERE region_code = '440106' AND year = 2024
ORDER BY level2_code, level3_code;
```

---

## 6. 录入方式

### 6.1 DataGrip 直接编辑长表（少量修正）

1. 打开 `fact_index_value`。
2. 过滤 `region_code = '440106' AND year = 2024`，主口径正好 35 行（A101/D102 如果还录了次口径，会额外多出几行）。
3. 修改 `value_num`、`value_unit`、`source_channel`、`source_authority`、`data_quality`、`evidence_ref` 等。
4. `obs_status` 保持 `present`；`context_type` 保持 `actual`。
5. 不要改 `region_code`、`level3_code`、`measure_code`，除非确实要纠正指标归属。

### 6.2 Excel/CSV 宽表导入

1. 先建批次：

```sql
INSERT INTO import_batch
(batch_name, file_name, default_source_channel, default_source_authority,
 default_data_quality, default_source_ref)
VALUES ('2026-10 广东统计年鉴补录', 'gd_2026.xlsx',
        'yearbook', 'official', 1, '2026年广东统计年鉴');
```

2. 把宽表数据导入 `stg_wide_guangdong`：一行一个 `(region_code, year)`，指标列 `A101…D203`。所有指标列都是 `VARCHAR`，可以放“约2355家”“4.2%”“N/A”等脏值。
3. 运行 loader（下一阶段实现）：
   - 校验 `region_code` 是否存在；
   - 校验指标与主口径是否存在；
   - 解析数值/单位，校验 `%` 在 0—100；
   - `context_type` 分流；
   - 写入 `fact_index_value`，`load_status` 置 `loaded` 或 `error`。
4. 失败行留在暂存表，按 `error_msg` 修正后重跑。

### 6.3 Python 管线直接写入

建议使用 `INSERT ... ON DUPLICATE KEY UPDATE`，以 `(region_code, year, level3_code, measure_code, context_type)` 为幂等键：

```sql
INSERT INTO fact_index_value
(region_code, year, level3_code, measure_code, context_type,
 source_channel, source_authority, source_ref, obs_status,
 value_num, value_unit, data_quality, evidence_ref)
VALUES
('440106', 2024, 'A103', 'share', 'actual',
 'yearbook', 'official', '2025年广东统计年鉴', 'present',
 3.6, '%', 1, NULL)
ON DUPLICATE KEY UPDATE
    value_num         = VALUES(value_num),
    value_unit        = VALUES(value_unit),
    source_channel    = VALUES(source_channel),
    source_authority  = VALUES(source_authority),
    source_ref        = VALUES(source_ref),
    obs_status        = VALUES(obs_status),
    data_quality      = VALUES(data_quality),
    evidence_ref      = VALUES(evidence_ref);
```

> MySQL 8.0.20+ 对 `VALUES()` 有弃用警告，但行为不变。若想消除警告，可改用
> `INSERT ... VALUES (...) AS new ON DUPLICATE KEY UPDATE value_num = new.value_num`。

---

## 7. 权重、归一化与合成

- 本目录**没有权重列**。权重唯一权威是表外配置文件，建议：
  ```
  config/weights/weights_v1.yaml
  ```
  内容包括 `scheme_id`、版本、一级/二级/三级权重、归一化方法、来源策略。
- 合成在 Python 中完成，数据库只提供原始值。
- 如果需要把合成结果回存数据库，执行 `50_optional_score.sql`：
  - `fact_index_run` 记录 `weight_scheme`、`config_sha256`、`norm_method`、`source_policy`；
  - `fact_index_score` 按 `run_id` 版本化，换权重/方法新建 run，不覆盖旧结果。
- 不建议在 `dim_index` 里再放一份权重；那会形成第二权威，必然与配置文件漂移。

---

## 8. 从现有 CSV 导入前的必读

现有数据在 `A/A1/techfin_common_output/`，其中：

- `points_resolved.csv`：聚合级，一行一个 `(region_code, level3_code, stat_year)` 格，列可直接映射到事实表；
- `points_delta.csv`：证据级，保留逐篇文章的抽取结果、单位、语境、原文片段。

**不要直接盲导 `points_resolved.csv`**，已发现至少一个错配：

```
440100 / A103 / 2019 / metric=count / canonical_value=12099
```

原文是“2019 年广州有效期内的高新技术企业有 12099 家”，属于 A101，不是 A103。盲导会得到“A103 = 12099%”。

loader 必须做：

1. `value_unit` 与 `standard_unit` 一致性校验；
2. 百分比指标范围校验（0—100）；
3. `context_type` 分流（actual/planned/target/decline/unknown）；
4. `canonical_value` 为空的行不要写成 0，应写 `obs_status='missing'` 或跳过；
5. CSV 用 `utf-8-sig` 读取（文件带 BOM）；
6. `points_resolved.csv` 没有 `context_type` 和 `unit`，应以 `points_delta.csv` 为准，或在 loader 中标记 `unknown` 并拒绝进入 `v_index_actual`。

---

## 9. 扩展省份

不需要新建事实表。步骤：

1. 在 `dim_region` 增加该省的省/市/县行（`region_code` 全国唯一，`province_code` 自动生成）。
2. 复制暂存表：
   ```sql
   CREATE TABLE stg_wide_sichuan LIKE stg_wide_guangdong;
   ```
3. 复制宽表视图，改 `WHERE r.province_code = '510000'`：
   ```sql
   -- 以 v_wide_guangdong 为模板，改视图名为 v_wide_sichuan
   ```
4. 数据仍然写入同一个 `fact_index_value`，跨省查询直接 join `dim_region`。

不要按省建 database/schema，也不要按省复制 `fact_index_value`。

---

## 10. 验证

执行 `90_checks.sql`，重点看：

| 检查 | 期望 |
|---|---|
| 广东县域分析单元 | 124 |
| 广东 `region_type` 为 NULL | 0 |
| 现行三级指标数 | 35 |
| 口径数 | 37 |
| 无主口径指标 / 多主口径指标 | 0 / 0 |
| 孤儿区划 / 孤儿指标口径 | 0 / 0 |
| 同一格重复行 | 0 |
| 百分比越界 | 0 |
| 有值但缺质量 / 非有值却带值 | 0 / 0 |

另外抽查：

```sql
-- 东莞/中山必须在县域分析单元里
SELECT region_code, region_name, region_level, is_analysis_unit
FROM dim_region WHERE region_code IN ('441900','442000');

-- 广州市 A103 应能查出逐年数据（当前库为空时结果为 0 行）
SELECT * FROM v_index_long
WHERE region_code = '440100' AND level3_code = 'A103';
```

---

## 11. 与旧脚本的关系

| 旧文件 | 状态 | 处理 |
|---|---|---|
| `tech_finance_index_migration.sql` | 工作区现存 | **旧结构的增量迁移脚本**，假设旧表已存在；不要在新库 `techfin` 执行，保留作历史参考 |
| `tech_finance_index.sql` | 已从工作区删除（仅 git HEAD） | 旧首建脚本，含旧权重列；不要恢复后直接执行 |
| `A/A1/config/topic_config.py` | 现存 | 35 条指标、检索词、区划的权威来源；`dim_index` 种子与它对齐 |
| `A/A1/config/extract_config.py` | 现存 | `context_type`、`metric`、`unit` 的语义来源 |

新库的唯一基线是本目录 `db/00—90`。未来结构变更请新增 `db/migrations/YYYYMMDD_NNN_描述.sql`，不要直接改 `10_schema.sql` 后重跑。

---

## 12. 常见问题

**Q：为什么 DataGrip 里看不到“广东省表”？**
A：看 `v_wide_guangdong` 视图；它是只读的。要编辑请用 `stg_wide_guangdong` 或 `fact_index_value`。

**Q：为什么宽表视图不能直接改？**
A：它用了 `GROUP BY + MAX(CASE WHEN ...)` 做透视，MySQL 不允许更新这类视图。宽表是展示/导出层，不是系统记录。

**Q：为什么没有“每省一张列映射表”？**
A：`dim_index` + `v_wide_column_map` 已经提供全局映射；数据库无法对“列名”建外键，按省复制映射表只会产生 31 份会漂移的副本。

**Q：`value_num` 为什么不直接存归一化后的值？**
A：数据库只存原始观测值 + 实际单位；归一化依赖权重方案和归一化方法，属于表外计算，换方案时不应改原始数据。

**Q：一个值有多个来源怎么办？**
A：当前是轻量溯源：一个最终值 + `n_sources / value_min / value_max / agreement / evidence_ref / chosen_reason`。需要同格并列多来源时再升级唯一键。

**Q：库名可以继续用 `tech_finance_county` 吗？**
A：可以，结构不受影响；把 `00_database.sql` 的库名改掉，并在 DataGrip 中切换 schema 即可。本项目默认 `techfin`。

---

## 13. 变更记录

- 2026-10-23：建立 baseline。单库 `techfin`；长表事实 + 全局维度 + 轻量溯源；广东区划与 35 条指标种子；广东省宽表视图/暂存表；权重外置。
