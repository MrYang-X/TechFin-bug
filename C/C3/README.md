# 科技金融指数研究 · C3 企业成长性

> **本目录与 `B/B1` 代码同源**（`config/` 之外的所有业务脚本保持一致），只替换了
> `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明见 [`A/A1/README.md`](../../A/A1/README.md)；
> C 维度总览见 [`C/README.md`](../README.md)。

## 指标

| 编码 | 指标 | metric / 落库单位 | 口径限制 |
|---|---|---|---|
| C301 | 独角兽企业数量 | count / 家 | 只认独角兽；拒绝瞪羚、估值、营收/利润、增长率、新增/减少 |
| C304 | 瞪羚企业数量 | count / 家 | 只认瞪羚；拒绝独角兽、营收/利润、增长率、新增/减少 |
| C302 | 科技企业营收增长率 | growth / % | 只认营业收入/营收增长；拒绝利润类词、占比/比重 |
| C303 | 科技企业利润增长率 | growth / % | 只认利润/净利润/利润总额增长；拒绝营收类词、占比/比重 |

## 检索词

每个指标 12 条检索式，带地区采集时取前 3 条：

- C301：`独角兽企业数量`、`独角兽企业 数量`、`独角兽企业 家`
- C304：`瞪羚企业数量`、`瞪羚企业 数量`、`瞪羚企业 家`
- C302：`高新技术企业 营收 增长`、`高新技术企业 营业收入 增长`、`科技企业 营收 增长`
- C303：`高新技术企业 利润 增长`、`科技企业 利润 增长`、`高新技术企业 净利润 增长`

默认计划：124 个县级单元 × 4 指标 × 3 = **1488 条**；无地区检索式 48 条。

## 官方源

- 广东省统计局·统计公报
- 广东省科学技术厅·通知公告 / 政策文件
- 工信部火炬中心·技术市场（高新技术企业统计）
- 科技部·科技统计报告

## C301/C304 拆分

原数据库 C301 是「"独角兽"、"瞪羚"企业数量」的合并指标。两个不同榜单/认定口径的
数共用同一 `(region_code, year, level3_code, measure_code, context_type)` 主键会互相覆盖，
因此本次拆为：

- C301 独角兽企业数量（原 C301 改名）
- C304 瞪羚企业数量（新增）

同步文件：`db/21_seed_index.sql`、`db/30_views.sql`、`db/40_staging.sql`、
`db/90_checks.sql`、`db/migrations/20261116_001_add_c304_gazelle.sql`。
已有库先执行 migration，再跑 `db/90_checks.sql`。

## 口径说明

- 「独角兽、瞪羚企业共 X 家」是合并口径，`extract_config.COMBINED_UNICORN_GAZELLE_RE`
  会拦截，不分配给 C301 或 C304；「独角兽企业20家、瞪羚企业35家」仍分别归属。
- 估值（亿美元）、营业收入（亿元）、新增数量都不产 C301/C304 数量点；
  「潜在独角兽企业」也不并入 C301；只有带 % 的数字才产 C302/C303。
- 长城战略咨询 GEI 官网是 SPA、胡润是单篇报道页，静态列表抓不到，
  官方源只覆盖政府/火炬中心；榜单数量主要靠搜索引擎语料。

## 快速开始

```bash
cd E:\Python\bug\C\C3

python collect/TechFin-bug.py --list-plan
python collect/TechFin-bug.py --offset 0 --limit 3
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
```
