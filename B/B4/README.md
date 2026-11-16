# 科技金融指数研究 · B4 金融科技应用

> **本目录与 `A/A1` 代码同源**（`config/` 之外的所有业务脚本保持一致），只替换了
> `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明见 [`A/A1/README.md`](../../A/A1/README.md)；B 维度总览见 [`B/README.md`](../README.md)。

## B4 主题速览

- 指标：**B401** 移动支付交易额（`amount`，亿元）、**B402** 数字普惠金融指数（`index`，指数值）、
  **B403** 大数据/AI 在风控中的应用情况（`score`，定性/评分）。
- **B402/B403 只采语料，不做数值抽取、不入库**：
  - B402 的数值来自北京大学数字普惠金融指数（省/市/县面板数据），不在新闻里；
  - B403 是定性指标，强行让 LLM 打分不可复核。
  两者保留在 `INDEX_KEYWORDS` 里参与清洗相关性判定，但不写入 `extract_config.INDICATOR_SCOPE`，
  LLM schema 的 `indicator.enum` 也只有 `B401`。
- 默认采集：`DEFAULT_LEVEL2 = ["B4"]`；县级计划 124 单元 × 3 指标 × 3 条 = **1116 条**，
  无地区计划 **32 条**。其中 B402/B403 的 744 条只用于语料采集。
- 产物：`data/techfin_b4_points.csv`、`data/points_b4_*`、`data/b4_llm_cache/`、
  `data/b4_llm_calls.jsonl`、`data/corpus_verification_b4.md`。
- 官方源：广东省统计局·统计公报、人民银行广东省分行·金融数据、广东省地方金融管理局
  通知公告/工作动态。

### 抽取口径

| 指标 | metric | 源单位 | 落库标准单位 | 关键区分 |
|---|---|---|---|---|
| B401 | amount | 元/万元/亿元/万亿元 | 亿元 | 只抽交易金额；不抽交易笔数/业务量/用户数 |
| B402 | —（只采语料） | — | — | 不产数据点 |
| B403 | —（只采语料） | — | — | 不产数据点 |

- "移动支付交易笔数3亿笔"不产 B401 点；"移动支付交易额5000亿元"产 B401。
- B402/B403 的清洗词族必须包含**无空格**短词（如"智能风控""大数据风控""数字普惠金融指数"），
  否则 `techfin_curate.build_indicator_families()` 取不到词族，整篇文章会被判为不相关。

### B4 检索词（县级前 3 / 无地区尾巴）

| 指标 | 县级前 3 | 无地区数值尾巴（节选） |
|---|---|---|
| B401 | 移动支付 交易额 / 移动支付 交易规模 / 移动支付 金额 | 移动支付业务金额、移动支付 交易 亿元、移动支付 业务量、移动支付 交易笔数、移动支付 金额 增长、农村地区 移动支付、移动支付 交易 万亿元 |
| B402 | 数字普惠金融指数 / 北京大学 数字普惠金融 / 数字普惠金融 县域 | 数字普惠金融、北大数字普惠金融、数字普惠金融 指数、普惠金融 指数、数字普惠金融 发展、数字普惠金融 指数 排名、数字普惠金融 指数 2024/2025 |
| B403 | 大数据 风控 应用 / 人工智能 风控 / 金融科技 风险管理 | 智能风控、大数据风控、AI 风控、人工智能 风险控制、金融科技 风控、数字化风控、模型风控、大数据 风控 金融 |

## 常用命令

```bash
cd E:\Python\bug\B\B4

python collect/TechFin-bug.py --list-plan --limit 6
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
