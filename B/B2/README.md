# 科技金融指数研究 · B2 股权融资活跃度

> **本目录与 `A/A1` 代码同源**（`config/` 之外的所有业务脚本保持一致），只替换了
> `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明见 [`A/A1/README.md`](../../A/A1/README.md)；B 维度总览见 [`B/README.md`](../README.md)。

## B2 主题速览

- 指标：**B201** VC/PE 投资案例数（`count`，起）、**B202** VC/PE 投资金额（`amount`，亿元）、
  **B203** 科创板/创业板等上市科技企业数量（`count`，家）、**B204** 募资额（`amount`，亿元）。
- 默认采集：`DEFAULT_LEVEL2 = ["B2"]`；县级计划 124 单元 × 4 指标 × 3 条 = **1488 条**，
  无地区计划 **49 条**。
- **B201-B204 县域不适用**（`is_county_applicable=0`）：县级使用 `county_queries` 替代口径
  （天使轮/A轮融资；新三板/区域股权交易中心挂牌）；入库默认拒绝县级行，需要代理值时显式加
  `--allow-county-not-applicable`，loader 会标记 `county_replacement=1`。
- 产物：`data/techfin_b2_points.csv`、`data/points_b2_*`、`data/b2_llm_cache/`、
  `data/b2_llm_calls.jsonl`、`data/corpus_verification_b2.md`。
- 官方源：广东省统计局·统计公报、人民银行广东省分行·金融数据、广东省地方金融管理局
  通知公告/工作动态、广东省科技厅·通知公告。

### 抽取口径

| 指标 | metric | 源单位 | 落库标准单位 | 关键区分 |
|---|---|---|---|---|
| B201 | count | 起 | 起 | 只抽案例数；不抽金额/规模/募资 |
| B202 | amount | 元/万元/亿元/万亿元 | 亿元 | 只抽投资金额；不抽案例数 |
| B203 | count | 家 | 家 | 只抽上市企业数量；不抽募资/市值 |
| B204 | amount | 元/万元/亿元/万亿元 | 亿元 | 只抽募资额；不抽企业数量 |

- 一句里同时出现数量与金额时会产两个点，如"科创板上市企业5家，募资20亿元" →
  B203=5 家、B204=20 亿元。
- **不把"笔"加进 `UNIT_METRIC`**：loader 的 `unit_compatible` 没有"笔→起"兼容，加了会在
  入库阶段被拒；B201 只认"起"。
- 金额单位在抽取层保留原文，由 loader 统一换算。

### B2 检索词（县级前 3 / 无地区尾巴）

| 指标 | 县级前 3 | 无地区数值尾巴（节选） |
|---|---|---|
| B201 | 天使轮 融资 案例 / A轮 融资 案例 / 股权融资 案例数（替代口径） | 股权投资 案例数、创业投资 案例数、VC PE 投资 案例、投资案例数、融资案例数、天使轮融资、A轮融资、私募股权 投资 案例 |
| B202 | 天使轮 融资金额 / A轮 融资金额 / 股权融资 金额（替代口径） | 股权投资 金额、创业投资 投资金额、VC PE 投资额、股权投资金额、投资金额 亿元、股权投资 规模、A轮融资金额 |
| B203 | 新三板 挂牌 企业 数量 / 区域股权交易中心 挂牌 / 新三板挂牌企业（替代口径） | 科创板 上市 企业 数量、创业板 上市 企业 数量、科创板上市企业、创业板上市企业、新增上市企业、上市企业数量、科技型企业 上市 |
| B204 | 新三板 挂牌 募资 / 区域股权交易中心 募资 / 挂牌企业 募资（替代口径） | 科创板 首发募资、创业板 首发募资、上市 募资 规模、科创板 募资额、创业板 募资额、IPO 募资额、首发募资额 |

## 常用命令

```bash
cd E:\Python\bug\B\B2

python collect/TechFin-bug.py --list-plan --limit 6
python collect/TechFin-bug.py --offset 0 --limit 3
python collect/techfin_authoritative.py --limit-pages 1
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py

# 县级代理值入库（默认会被 COUNTY_NOT_APPLICABLE 拒绝）
python load/techfin_load_to_db.py --dry-run --allow-county-not-applicable
```

## 与 A/A1 同步

业务代码与 `A/A1` 同源。A/A1 修改以下文件时，需要同步复制到本目录
（只复制代码，不要复制 `data/`、`reports/`、`techfin_common_output/`）：

```
collect/TechFin-bug.py
collect/techfin_authoritative.py
fetch/techfin_fetch_body.py
fetch/techfin_ocr.py
clean/techfin_curate.py
extract/techfin_extract_points.py
load/techfin_load_to_db.py
load/techfin_load_to_db_interactive.py
report/verify_corpus.py
report/verify_points.py
report/techfin_eval.py
run_all.py
config/paths.py
config/region_rules.py
```
