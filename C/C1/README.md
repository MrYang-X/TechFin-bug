# 科技金融指数研究 · C1 科技创新产出

> **本目录与 `B/B1` 代码同源**（`config/` 之外的所有业务脚本保持一致），只替换了
> `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明（依赖关系、采集分批、OCR、入库门禁等）见
> [`A/A1/README.md`](../../A/A1/README.md)；C 维度总览见 [`C/README.md`](../README.md)。

## 指标

| 编码 | 指标 | metric / 落库单位 | 口径限制 |
|---|---|---|---|
| C101 | PCT 国际专利申请量 | count / 件 | 只抽 PCT 申请量；拒绝授权量、有效量、非 PCT 发明专利 |
| C102 | 技术合同成交额 | amount / 亿元 | 只认成交额/成交金额；拒绝技术交易额、合同数、增速 |
| C103 | 每万人发明专利拥有量 | density / 件/万人 | 只认每万人口径；拒绝高价值、申请量、授权量、绝对量 |

## 检索词

每个指标 12 条检索式，带地区采集时取前 3 条：

- C101：`PCT国际专利申请量`、`PCT专利申请量`、`PCT国际专利`
- C102：`技术合同成交额`、`技术合同 成交额`、`技术合同成交金额`
- C103：`每万人发明专利拥有量`、`万人发明专利拥有量`、`每万人口发明专利拥有量`

C101 的 `is_county_applicable=0`，带县级采集时使用 `county_queries` 短词
（`PCT专利申请`、`PCT国际专利`、`国际专利 申请`）；县级行默认被 loader 拒绝，
需要代理值时显式加 `--allow-county-not-applicable`。

默认计划：124 个县级单元 × 3 指标 × 3 = **1116 条**；无地区检索式 36 条。

## 官方源

- 广东省统计局·统计公报
- 广东省科学技术厅·通知公告 / 政策文件
- 广东省市场监督管理局·通知公告（知识产权）
- 国家知识产权局·统计（月度分省专利/PCT/有效发明专利，首页无静态分页）
- 工信部火炬中心·技术市场（技术合同成交额；HTTPS 会重置，使用 HTTP）
- 科技部·科技统计报告（年度报告，部分行无日期）

## 关键实现

- C103 的典型写法是「每万人发明专利拥有量 52.3 件」。`extract/techfin_extract_points.py`
  的 `_adapt_unit()` 已从「只处理 A204」泛化为按 `metrics == {"density"}` 判断，
  把 `件` 归一为 `件/万人`；数字本身是每万人口径，不做乘除。
- 金额在抽取层保留原文单位（亿元/万元/万亿元），换算只在 loader 做。

## 快速开始

```bash
cd E:\Python\bug\C\C1

python collect/TechFin-bug.py --list-plan
python collect/TechFin-bug.py --offset 0 --limit 3
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
```

百度县级检索按 `--cities` / `--offset` / `--limit` 分批，利用
`data/tech_fin_search_progress.csv` 续跑。
