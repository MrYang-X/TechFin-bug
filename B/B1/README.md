# 科技金融指数研究 · B1 债权融资活跃度

> **本目录与 `A/A1` 代码同源**（`config/` 之外的所有业务脚本保持一致），只替换了
> `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明（依赖关系、采集分批、OCR、入库门禁等）见 [`A/A1/README.md`](../../A/A1/README.md)；
> B 维度总览见 [`B/README.md`](../README.md)。

## B1 主题速览

- 指标：**B101** 科技企业贷款余额（`amount`，亿元）、**B102** 科技企业贷款余额增速（`growth`，%）、
  **B103** 知识产权质押融资额（`amount`，亿元）、**B104** 信用贷款占比（`share`，%）。
- 默认采集：`DEFAULT_LEVEL2 = ["B1"]`；县级计划 124 单元 × 4 指标 × 3 条 = **1488 条**，
  无地区计划 **42 条**。
- 产物：`data/techfin_b1_points.csv`、`data/points_b1_*`、`data/b1_llm_cache/`、
  `data/b1_llm_calls.jsonl`、`data/corpus_verification_b1.md`。
- 官方源：广东省统计局·统计公报、人民银行广东省分行·金融数据、广东省地方金融管理局
  通知公告/工作动态、广东省市场监督管理局·通知公告、广东省科技厅·通知公告。

### 抽取口径

| 指标 | metric | 源单位 | 落库标准单位 | 关键区分 |
|---|---|---|---|---|
| B101 | amount | 元/万元/亿元/万亿元 | 亿元 | 只抽"贷款余额"，不抽增速/增量/占比 |
| B102 | growth | %/％ | % | 只抽增速百分比；不产 B101 |
| B103 | amount | 元/万元/亿元/万亿元 | 亿元 | 只抽融资金额，不抽项目数/笔数 |
| B104 | share | %/％ | % | 只抽占比，不抽信用贷款余额 |

- 金额单位在抽取层保留原文，**由 loader 统一换算**；不要在抽取层预换算。
- **B102 的已知召回缺口**：句子写成"贷款余额1200亿元，同比增长20.5%"时，规则层只能拿到
  B101，B102 依赖 LLM 兜底；`--extractor rule` 纯规则模式会漏这类点。
- **负增长**：`下降 X%` 会被判为 `decline`，loader 默认跳过；需要时加 `--include-non-actual`。
- `B102` 的 growth 口径依赖 `extract/techfin_extract_points.py::_metric_of()` 的通用特例：
  `scope.metrics == {"growth"}` 且单位为 `%` 时返回 `growth`。

### B1 检索词（县级前 3 / 无地区尾巴）

| 指标 | 县级前 3 | 无地区数值尾巴（节选） |
|---|---|---|
| B101 | 科技型企业贷款余额 / 科技企业贷款余额 / 高新技术企业贷款余额 | 科技型中小企业贷款余额、科技贷款余额、科技贷款 余额、科技型企业 贷款余额、科创企业贷款余额、科技企业贷款 余额 亿元、科技型企业贷款 突破 |
| B102 | 科技贷款 增速 / 科技型企业贷款 同比增长 / 高新技术企业贷款 增长 | 科技贷款余额 同比增长、科技型企业贷款增速、科技贷款 增长、科技贷款 增幅、科技型企业贷款 增幅、科创贷款 增速、科技贷款 增速 高于 |
| B103 | 知识产权质押融资 / 专利质押融资 / 知识产权质押 融资金额 | 知识产权质押融资额、知识产权质押贷款、专利质押 融资金额、商标质押 融资、知识产权质押 登记 金额、知识产权质押融资 亿元、专利质押融资额 |
| B104 | 科技型企业 信用贷款 占比 / 科技企业 信用贷款 比例 / 信用贷款 占比 | 信用贷款占比、信用贷款 比重、科技型企业信用贷款占比、高新技术企业 信用贷款 占比、科技贷款 信用贷款 占比、信用贷款 占比 提升 |

## 常用命令

```bash
cd E:\Python\bug\B\B1

python collect/TechFin-bug.py --list-plan --limit 6
python collect/TechFin-bug.py --list-plan --no-region --limit 10
python collect/TechFin-bug.py --offset 0 --limit 3
python collect/techfin_authoritative.py --limit-pages 1
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
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
