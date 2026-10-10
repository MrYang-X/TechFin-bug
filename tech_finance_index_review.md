# `tech_finance_index.sql` 表结构评审报告

**评审对象**：县域科技金融指数研究的数据库存储层（`tech_finance_index.sql`，库名 `tech_finance_county`）
**研究口径**：搜索引擎多源采集 + 官方统计源印证，测算广东省 124 个县级分析单元的科技金融指数
**评审日期**：2026-10-04

**版本沿革**（本文档覆盖两次评审）：

| 版本 | 库名 | 说明 |
|---|---|---|
| 2024 版（46 行→246 行） | `tech_finance_index` | 最初的三表结构，见"附录：2024 版遗留问题" |
| 你的新版（113 行） | `tech_finance_county` | 你自行改写，转向"县域适用性标记"，见第二~四节 |
| 当前版 | `tech_finance_county` | 在你的新版基础上补齐缺口，见第五节 |

---

## 一、总体结论

**你的新版在"县域研究适配"这个方向上，明显好于我上一轮给的修订版。**

它抓住了我漏掉的一件事：县域研究真正的瓶颈不是"表能不能存数"，而是**哪些指标在县级根本拿不到、拿不到该用什么替代**。为此你加了 `region_type`（市辖区和普通县在县域研究里差异极大）、`is_county_applicable` + `county_replacement`（不只标不可得，还给出替代口径）、`data_quality`、区划沿革三段。

但也相对你自己先前确认过的需求**回退了三处，并留下一处会直接导致 join 失败的断链**。这些已在当前版补齐。

---

## 二、你新版的优点（已全部保留）

| 设计 | 为什么有用 |
|---|---|
| **`region_type`**（普通县 / 县级市 / 市辖区 / 自治县） | 县域研究里"市辖区"与"普通县"的经济结构、财政自主权、数据可得性差异极大，不做区分会把两类完全不同的对象混在一起排名。已按名称后缀自动分类并补全 148 行 |
| **`is_county_applicable` + `county_replacement`** | 这是本版最有价值的设计。它把"县级不可得"从一个数据问题升级成一个**研究设计决策**——不只说"科创板上市企业数量在县里没有"，还说"改用新三板/区域股权交易中心挂牌企业数量"。6 条指标标了不可得并给出替代口径 |
| **`data_quality`**（1-官方年鉴 / 2-公报 / 3-调研估算 / 4-缺失待补） | 一列同时表达来源与质量，比"只存数值"实用得多。它让"三档数据混在一张表里"这件事变得可解释 |
| **`is_active` / `start_year` / `end_year`** | 多年面板必然撞上区划变更（2014 年从化/增城撤市设区、2012 年清新撤县设区…），有沿革字段才能把历史数据对上码 |
| **`city_code`** | 跨县按地级市分组比对时的便利字段 |

---

## 三、三处回退 + 一处断链（当前版已修）

### 回退 1 · 双源并存没了

`UNIQUE KEY uk_region_year_index (region_code, year, index_id)` 规定一个地区、一年、一个指标只能有一行，`data_quality` 又是单值列。这让"人工从统计年鉴填报的官方值"与"从新闻语料抽取的估算值"**无法并存**——你先前明确要求过两者都有。

> **已修**：唯一键改为 `(region_code, year, index_id, source_type)`，新增 `source_type`。`data_quality` 保留不动，两者分工：`source_type` 答"这行数据哪来的"（唯一键判别器，低基数），`data_quality` 答"它多可信"（排序用）。

### 回退 2 · 复合单位没拆

原 5 条指标把两个度量塞在同一行（`家/亿元`、`亿元/%`、`起/亿元`），而 `index_value DECIMAL(20,4)` 是单列——**装不下两个数**，也没法统一赋权。

> **已修**：拆成单一单位的独立指标，30 → 35 条。你的 `is_county_applicable` / `county_replacement` 随拆分**机械传播**（拆出的两行继承同一替代建议，未改任何研究判断）。

### 回退 3 · `county_replacement` 不可执行

替代口径只是文字建议，**没有对应的 `dim_index` 行，事实表里记不下它的值**。也就是说这个字段目前只能给人看，不能进计算。

> **未修（按你的选择保留）**。要让替代值真能入库，需要把替代口径也建成指标行并标注"替代"关系。你先前在选项里没勾这一项。

### 断链 · `topic_config.py` 与新 SQL 对不上

`topic_config.py` 当时是我改的 **35 条拆分版**（带 `level3_code`），而你的新 SQL 是 **30 条未拆分版**（靠 `level2_code + level3_name` 关联）。那 5 组拆分指标的 CSV 会**直接 join 不上**——爬虫产出 `私募股权/创业投资机构数量`，而库里只有 `私募股权/创业投资机构数量与管理资本量`。

> **已修**：SQL 向 Python 对齐，`dim_index` 采用 35 条拆分版 + `level3_code`。

---

## 四、你新版里需要复核的两处研究判断

这两处是**内容**问题而非结构问题，我按原样保留，但建议你过一遍：

1. **"标适用、又给替代口径"的写法自相矛盾**。`county_replacement` 的定义是「若不适用」，但有 4 条指标同时 `is_county_applicable = 1` **且**填了 `county_replacement`：
   - `B101/B102 科技企业贷款余额(及增速)` → 标 1，却给了"县级农商行/农信社科技贷款数据"
   - `B402 数字普惠金融指数` → 标 1，却给了"北大数字普惠金融指数（县域层面）"
   - `D101 科技金融政策完备度` → 标 1，却给了"县级科技金融政策文本评分"

   看语义，这几条的 `county_replacement` 更像是"**数据获取渠道建议**"而非"替代指标"。要么把字段语义拆成两个（`data_channel` / `county_replacement`），要么把这几条的 `is_county_applicable` 改为 0。

2. **`A201 本外币存款余额` 标 `is_county_applicable = 1`**。县级人行存贷款数据通常不公开，实际一般只能取到市级。这条我按你的标注保留，但请核实你手上的数据源是否真能到县。

---

## 五、当前版做了什么

### `dim_region`

- **补全区划数据**：原版只有 8 行示例（清远 6 + 成都 2），广东 124 个县域分析单元里只有 6 个有码。现补到 **148 行**（广东 21 市 + 122 县区 + 省级，附四川 4 行示例）
- `region_type` 按名称后缀自动分类，**无一 NULL**（市辖区 65 / 普通县 35 / 地级市 22 / 县级市 21 / 自治县 3 / 省级 2）
- `city_code` 自动派生（市本级填自身，县区填所属市）
- **补 `is_analysis_unit`**：东莞 `441900`、中山 `442000` 是不设区地级市，`region_level = 2`，但**本身即县域分析单元**。任何 `WHERE region_level = 3` 都会把它们**静默**丢掉——124 变 122，不报错。判据从此统一为 `is_analysis_unit = 1`
- 补 `region_full_name`：`441502` 常用名"城区"，报表会与别市城区混淆，全称"汕尾市城区"
- 补 `CHECK`（`region_level` 取值、`start_year/end_year` 先后）
- `start_year` 只填了有把握的几个（清远系沿用你给的值），其余留 NULL 待补——**编造年份比留空更危险**

### `dim_index`

- 拆分为 **35 条**（详见第三节回退 2）
- 补 `level3_code` 稳定编码 + `UNIQUE`：`level3_name` 是中文口径名，一改就被当成新指标，历史数据断链
- 补 `level2_weight` / `level3_weight`：原版只有一级权重，加权合成算不出来
- 保留 `is_county_applicable` / `county_replacement`

### `fact_tech_finance_data`

- 唯一键三元组 → **四元组**（含 `source_type`），双源并存落地
- 补 `evidence_ref`：轻量溯源自 CSV 的 `article_id`（逗号分隔）
- 补 `idx_year_index (year, index_id, region_code)`：合成指数的热点是"某年 × 某指标 → 全部区县"的跨区截面，原唯一键以 `region_code` 前导，`WHERE year = ?` 走不到索引
- `idx_index_id` 由 `(index_id)` 扩为 `(index_id, year)`
- `TIMESTAMP` → `DATETIME`（避开 2038 上限与 MySQL 5.6.5 版本依赖）
- 补 `CHECK`（年份范围、`data_quality` 取值）
- 文末附 `ON DUPLICATE KEY UPDATE` 幂等写入模板

### `fact_index_score`（新增）

研究的最终产出是"县域科技金融指数"这个加权合成值，原版没有位置放它。塞进事实表会污染原始观测层——合成值是"总指数/一级维度/二级指标"粒度，生命周期也不同（原始值录入即存在，合成值权重一变就整体重算）。

表中含 `coverage_ratio`（指标覆盖率）与 `n_indicators_used`——**县域面板大面积缺失下，可比性必须能被解释**。

### Python 侧

- `A/A1/topic_config.py` 去掉了我上一轮加的 `min_region_level`（未被任何代码消费，且新版用 `is_county_applicable` 表达同一件事，两套机制并存必然漂移）。`level3_code` 保留
- `is_county_applicable` / `county_replacement` 是**指标级研究元数据，不进 CSV**（对每条语料行都是噪声），只活在 DB 的 `dim_index` 里

---

## 六、仍未解决 / 需人工处理

1. **`county_replacement` 依然不可执行** —— 替代口径没有对应的 `dim_index` 行，事实表记不下它的值。按你的选择保留原设计。
2. **权重值待校准** —— 二三级权重是估算值（如 A1=40/A2=35/A3=25），正式测算前需用 AHP 或熵值法确定。
3. **5 条复合指标的历史事实数据无法自动迁移** —— 旧行只存了一个 `index_value`，脚本无从判断那是金额还是增速。迁移脚本会把有数据的旧行保留下来并输出人工重录清单。
4. **`start_year` 大面积留空** —— 见第五节，需要按实际区划沿革补。
5. **`topic_config.py` 补全后必须重跑爬虫** —— 现有 CSV 没有 `level3_code` 列，补跑前 CSV → SQL 的 join 演练无法完成。
6. **爬虫侧的根本缺口** —— 现有爬虫**一行指标数值都产不出来**。它只产条目级语料 + 指标归属标签（`indicators` 是"这篇文章被哪条检索式命中"的叠加），没有正文全文，只有搜索页摘要片段。`index_value` 的数据得靠新写的填报/抽取管线。**表结构改好了，数据不会自己出现。**

---

## 七、验证记录

在**隔离的临时 MySQL 8.0.43 实例**（独立数据目录 + 3399 端口，不触碰你的正式库）上实测。

**首建脚本**
- 执行 exit 0，零错误零告警
- 4 张表建成；`dim_region` 148 行；**广东县域分析单元 = 124** ✓；`region_type` 无一 NULL ✓
- `dim_index` 35 条，`level3_code` 唯一；12 个二级组合的**三级权重合计均为 100.00** ✓
- 县域不适用指标 9 条（沿用你的标注，拆分后机械传播）

**行为验证**
| 测试 | 结果 |
|---|---|
| 同格插 `official` + `news` 两行 | 都成功，双来源并存 ✓ |
| 重复 upsert 官方值 | 走 UPDATE（68 → 71），不报 1062 ✓ |
| `data_quality = 9` | 被 `ck_data_quality` 拒绝 ✓ |
| `region_level = 9` | 被 `ck_region_level` 拒绝 ✓ |
| 插入未知 `region_code` | 被外键拒绝 ✓ |
| `EXPLAIN ... WHERE year = 2023` | `type=ref`、`key=idx_year_index` ✓ |

**两侧对齐（最关键）**
`dim_index` 的 35 行与 `topic_config.INDEX_KEYWORDS` 展平结果按 `level3_code` 逐条比对，`level3_name` / `unit` / `level2_code` **零差异** ✓

**迁移脚本**
在"你的 113 行结构 + 5 条存量事实行"上实测：
- 执行 exit 0，零错误；区划 8 → 124 个广东县域单元，事实行 5 → 5 **零丢失**
- 3 条无数据引用的复合指标被自动删除，2 条有数据的保留并列入人工重录清单
- 迁移路径与首建路径产出的表结构**逐列 diff 完全一致** ✓

---

## 八、执行指引

你的库是空的，**直接重跑首建脚本即可**：

```bash
mysql -u <user> -p --default-character-set=utf8mb4 < tech_finance_index.sql
```

迁移脚本是给"已经用旧结构录了数据、又不想清库"的场景备的：

```bash
mysqldump -u <user> -p --default-character-set=utf8mb4 --single-transaction \
          tech_finance_county > backup_$(date +%F).sql   # 先备份
mysql -u <user> -p --default-character-set=utf8mb4 < tech_finance_index_migration.sql
```

---

## 附录：2024 版遗留问题（已全部在新版解决，保留备查）

最初那份 246 行的版本（库名 `tech_finance_index`）另有一批独立问题：

- **脚本性质是重建而非迁移**：`DROP TABLE` + `CREATE` + 全量 `INSERT`，对已有数据的库重跑即数据清零，且 `dim_index` 自增 id 漂移会让存量事实行**静默指向错误指标**。→ 已拆成首建脚本 + 迁移脚本
- 无 `level3_code`，唯一键依赖中文名
- 无二三级权重、无正/逆向标记、无归一化方法字段
- `region_code VARCHAR(12)` 实际只用 6 位
- 无 `SET NAMES utf8mb4`；表级无显式 `CHARSET/COLLATE`（外键列排序规则不匹配时报 ERROR 3780）
- `DROP TABLE` 无 `SET FOREIGN_KEY_CHECKS=0` 保护
- 无幂等 upsert；无 CHECK 约束
- 无缺失值语义（三种 NULL 分不清）
- 无年份前导索引
- 区划表 `441502` 名"城区"有歧义；无县域分析单元标记；无区划沿革
- 指标数据只有 A/B/C/D 30 条，而 `topic_config.INDEX_KEYWORDS` 当时**只有 A1 三条**——字段名对齐了，内容没对齐
