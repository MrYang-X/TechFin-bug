# 科技金融指数研究 · D1 政策与服务环境

> **本目录与 `A/A1`/`B/B1`/`C/C1` 代码同源**（`config/` 之外的所有业务脚本保持一致），
> 只替换了 `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明（依赖关系、采集分批、OCR、入库门禁等）见
> [`A/A1/README.md`](../../A/A1/README.md)；D 维度总览见 [`D/README.md`](../README.md)。

## 指标

| 编码 | 指标 | metric / 落库单位 | 处理方式 |
|---|---|---|---|
| D101 | 科技金融政策完备度（专家打分） | score / 分 | **只采语料，不产数值点**；分数由研究团队按评分细则人工赋分 |
| D102 | 科技金融服务中心/网点覆盖情况 | count / 个（主口径）；coverage / %（次口径） | 抽服务中心/服务站/网点数量与原文明确写出的覆盖率 |

### D102 口径限制

- 只抽“科技金融服务中心/分中心/服务站/工作站/服务网点”的数量，不抽服务企业数、
  贷款/授信金额、活动场次、项目数。
- 覆盖率只认原文明确写的“覆盖率达 X%”；**爬虫不会用 count/124 自动计算覆盖率**。
- 原文用“家/户”计数时，规则层不产点（DB 标准单位是“个”，loader 的
  `unit_compatible("家","个")` 会硬拒）；LLM 兜底会把“家”规范为“个”，
  `value_text` 保留原文。因此“家”存在召回缺口，但不会产生不可入库的数据点。
- 没有省级统一的县级服务中心/服务站名单，县级 count 主要来自新闻与地市报道；
  “分中心/服务站”的口径在不同年份的报道里可能不一致，入库后建议人工复核。

## 检索词

每个指标 12 条检索式，带地区采集时取前 3 条：

- D101：`科技金融 政策`、`科技金融 实施方案`、`科技金融 若干措施`
- D102：`科技金融服务中心`、`科技金融 分中心`、`科技金融服务站`

默认计划：124 个县级单元 × 2 指标 × 3 = **744 条**；无地区检索式 **24 条**。

## 官方源

- 广东省科学技术厅·通知公告
- 广东省科学技术厅·政策文件（部门规范性文件）
- 广东省科学技术厅·政策解读
- 广东省科学技术厅·政务动态
- 广东省地方金融管理局·通知公告
- 广东省地方金融管理局·政策法规
- 广东省地方金融管理局·工作动态
- 广东省地方金融管理局·地方金融

以上均为静态列表页（`index_{n}.html` 分页，`first_page_no=2`），已用 curl 实测返回
带日期的列表行。省级官方源只覆盖省/市政策；县级政策文本靠搜索引擎的
“区县名 + 科技金融 政策”召回。

## 关键实现

- **D101 不写入 `extract_config.INDICATOR_SCOPE`**：与 B402/B403 一致，只保留在
  `INDEX_KEYWORDS` 中参与清洗相关性判定；LLM schema 的 `indicator.enum` 只有 `D102`。
- **coverage 的本地映射**：`D/D1/config/extract_config.py` 的 `UNIT_METRIC` 把
  `%` 与全角 `％` 映射为 `coverage`。该映射只在本主题进程内生效，不改共享抽取脚本，
  也不影响 A/B/C。
- **`stock_default_actual=True`**：让“服务中心 12 个”这类无动词存量句按 `actual`
  处理，否则聚合层 `canonical_value` 为空。
- **`POINTS_PREFIX="points_"`**：每轮产物为 `points_delta.csv` / `points_resolved.csv` /
  `points_dropped.csv`，与 loader 默认发现逻辑一致；证据级累积库仍是
  `data/techfin_d1_points.csv`。

## 产物

- `data/tech_fin_news.csv`、`data/techfin_official.csv`（跨轮累积）
- `data/body_cache/`（正文缓存，跨轮复用）
- `data/techfin_d1_points.csv`（证据级数据点累积库）
- `data/d1_llm_cache/`、`data/d1_llm_calls.jsonl`
- `<运行目录>/corpus_verification_d1.md`（印证报告）

## 快速开始

```bash
cd E:\Python\bug\D\D1

python collect/TechFin-bug.py --list-plan --limit 6
python collect/TechFin-bug.py --list-plan --no-region --limit 6
python collect/TechFin-bug.py --offset 0 --limit 3
python collect/techfin_authoritative.py --limit-pages 1
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
```

百度县级检索按 `--cities` / `--offset` / `--limit` 分批，利用
`data/tech_fin_search_progress.csv` 续跑。入库为可选后处理：

```bash
python load/techfin_load_to_db.py --dry-run --only-level3 D102
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
