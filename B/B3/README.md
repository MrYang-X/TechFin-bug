# 科技金融指数研究 · B3 政策性融资效果

> **本目录与 `A/A1` 代码同源**（`config/` 之外的所有业务脚本保持一致），只替换了
> `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明见 [`A/A1/README.md`](../../A/A1/README.md)；B 维度总览见 [`B/README.md`](../README.md)。

## B3 主题速览

- 指标：**B301** 科技保险保费收入（`amount`，亿元）、**B302** 创业担保贷款发放额（`amount`，亿元）。
- 默认采集：`DEFAULT_LEVEL2 = ["B3"]`；县级计划 124 单元 × 2 指标 × 3 条 = **744 条**，
  无地区计划 **21 条**。
- **B301 县域不适用**（`is_county_applicable=0`）：县级几乎不会有公开的科技保险保费数据；
  入库默认拒绝县级行，需要代理值时显式加 `--allow-county-not-applicable`。
- 产物：`data/techfin_b3_points.csv`、`data/points_b3_*`、`data/b3_llm_cache/`、
  `data/b3_llm_calls.jsonl`、`data/corpus_verification_b3.md`。
- 官方源：广东省统计局·统计公报、人民银行广东省分行·金融数据、广东省地方金融管理局
  通知公告/工作动态、广东省科技厅·通知公告。

### 抽取口径

| 指标 | metric | 源单位 | 落库标准单位 | 关键区分 |
|---|---|---|---|---|
| B301 | amount | 元/万元/亿元/万亿元 | 亿元 | 只抽保费收入；不抽赔付/赔款/保险金额/保额 |
| B302 | amount | 元/万元/亿元/万亿元 | 亿元 | 只抽发放额/累计发放/投放；不抽余额/贴息资金 |

- "科技保险赔付支出2亿元"不产 B301 点；"创业担保贷款余额120亿元"不产 B302 点。
- 金额单位在抽取层保留原文，由 loader 统一换算。
- B302 的检索词与抽取词族都刻意不含"余额"，避免把存量当发放额。

### B3 检索词（县级前 3 / 无地区尾巴）

| 指标 | 县级前 3 | 无地区数值尾巴（节选） |
|---|---|---|
| B301 | 科技保险 保费收入 / 科技保险 保费 / 科技保险 试点 | 科技保险保费、科技保险 保费 亿元、科技保险 原保险保费、科技保险 承保、科技保险 覆盖面、科技保险 试点 城市、科技保险 保费 增长 |
| B302 | 创业担保贷款 发放 / 创业担保贷款 金额 / 创业担保贷款 | 创业担保贷款发放额、创业担保贷款 累计发放、创业担保贷款 新增发放、创业担保贷款 投放、创业担保贷款 发放 亿元、创业担保贷款 贴息、创业担保贷款 笔数 |

## 常用命令

```bash
cd E:\Python\bug\B\B3

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
