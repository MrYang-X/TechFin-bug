# 科技金融指数研究 · C2 经济发展贡献

> **本目录与 `B/B1` 代码同源**（`config/` 之外的所有业务脚本保持一致），只替换了
> `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明见 [`A/A1/README.md`](../../A/A1/README.md)；
> C 维度总览见 [`C/README.md`](../README.md)。

## 指标

| 编码 | 指标 | metric / 落库单位 | 口径限制 |
|---|---|---|---|
| C201 | 高新技术产业产值占规上工业产值比重 | share / % | 只认「高新技术产业产值占比」；本次不接受高技术制造业增加值代理 |
| C202 | 科技服务业增加值 | amount / 亿元 | 接受「科学研究和技术服务业增加值」代理；拒绝营业收入、占比、增速、企业数量 |

## 检索词

每个指标 12 条检索式，带地区采集时取前 3 条：

- C201：`高新技术产业产值 占比`、`高新技术产业 产值 比重`、`高新技术产业产值占规上工业`
- C202：`科技服务业增加值`、`科技服务业 增加值`、`科学研究和技术服务业增加值`

默认计划：124 个县级单元 × 2 指标 × 3 = **744 条**；无地区检索式 24 条。

## 官方源

- 广东省统计局·统计公报
- 广东省科学技术厅·通知公告 / 政策文件
- 工信部火炬中心·技术市场（高新技术企业/技术市场统计）
- 科技部·科技统计报告

## 口径说明

- **C202 是代理口径**：国民经济核算没有「科技服务业」这个行业门类，官方增加值科目是
  「科学研究和技术服务业」。本次按用户确认接受该代理；入库证据会保留原文，研究结论需注明。
- **C201 口径不统一**：国家统计局没有「高新技术产业」统一分类，C201 多为省级口径，
  跨省不可直接比较；本次不接受「高技术制造业增加值占比」代理。
- 「比重同比提高 1.2 个百分点」不产 C201 点（变化量不是水平值）。

## 快速开始

```bash
cd E:\Python\bug\C\C2

python collect/TechFin-bug.py --list-plan
python collect/TechFin-bug.py --offset 0 --limit 3
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
```
