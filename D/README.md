# 科技金融指数研究 · D 科技金融生态环境

本目录是一级维度 **D（科技金融生态环境）** 的采集主题集合，按二级指标分成两个**独立自洽**的主题目录：

| 目录 | 二级指标 | 三级指标 | 默认检索计划（县级） |
|---|---|---|---|
| `D/D1` | 政策与服务环境 | D101 科技金融政策完备度（专家打分）、D102 科技金融服务中心/网点覆盖情况 | 124×2×3 = **744 条** |
| `D/D2` | 人力与创新环境 | D201 R&D人员全时当量、D202 普通高等学校数量、D203 数字经济发展指数 | 124×3×3 = **1116 条** |

无地区检索式分别为 D1 **24 条**、D2 **36 条**。

## 关键设计

- 两个目录与 `C/C1`（及 `A/A1`、`B/B1`）**代码同源**，只替换
  `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README；
  完整流水线说明见 [`A/A1/README.md`](../A/A1/README.md)。
- 地区口径沿用广东省 **124 个县级采集单元**，`QUERIES_PER_REGION=3`；百度限流下必须
  `--offset/--limit/--cities` 分批 + 续跑台账多轮补齐。
- **D101 只采语料**：专家打分需要研究团队按评分细则人工赋分，爬虫只能保证政策语料
  （省/市政策文件、政策解读、通知公告、搜索引擎召回的区县政策）完整可追溯。
- **D203 只采语料**：数字经济指数是外部指数，没有官方县级序列；中国信通院官网
  整域 412，国家数据局“数字中国”列表页 404，均不可作为官方列表源。
- **D102 抽 count 与 coverage**：`%`/`％` 在 D1 自己的 `extract_config.py` 里映射为
  `coverage`；覆盖率只在原文明确写“覆盖率达 X%”时抽取，不自动用 count/124 计算。
  “家/户”不产可入库点（DB 标准单位是“个”）。
- **D201 抽 人年**：`万人年` 按量级换算为 `125000` 人年；`R&D人员 10 万人` 是人员数，
  不产点。县级官方数据基本不存在，省级数据更可靠。
- **D202 抽 所**：只认普通高等学校/普通高校/高等院校数量；在校生、招生、教职工、
  成人高校不产点。教育部名单只到地级市，县级归属需人工。
- **D101/D203 仍保留在 `INDEX_KEYWORDS`**，参与清洗相关性判定；但不写入
  `extract_config.INDICATOR_SCOPE`，不产数据点、不入库。
- **`POINTS_PREFIX="points_"`**：D1/D2 的每轮产物用通用前缀，与 loader 默认发现
  `points_resolved.csv` / `points_delta.csv` 的逻辑一致；证据级累积库仍是
  `techfin_d1_points.csv` / `techfin_d2_points.csv`。
- 数据库无需改动：`db/21_seed_index.sql` 已含 D101/D102/D201/D202/D203 与 6 条
  measure，`db/30_views.sql`/`40_staging.sql` 也已含 D 列。

## 官方来源渠道

| 主题 | 源 |
|---|---|
| D1 | 广东省科技厅（通知公告/政策文件/政策解读/政务动态）、广东省地方金融管理局（通知公告/政策法规/工作动态/地方金融） |
| D2 | 广东省统计局（统计公报/统计年鉴/统计快讯）、广东省教育厅（数据发布/公示公告）、广东省科技厅·通知公告、科技部·科技统计报告、教育部·名单公告、广东省工信厅·通知公告 |

以上列表页均已用 curl 实测返回 200 与带日期的行；分页规律见各主题的
`config/topic_config.py::SOURCES` 注释。中国信通院（412）、教育部 A03 栏目（403）、
国家数据局“数字中国”列表页（404）未列入。

## 口径限制（必读）

1. **D101 无法由爬虫产出分数**：需要人工/专家评分。
2. **D203 无官方县级指数**：数值需研究团队另行采用统一面板数据。
3. **D201 县级官方数据基本不存在**：统计年鉴只公布全省总量，分市数据散见于附件/
   统计快讯；县级只能靠零星新闻，且“人”≠“人年”。
4. **D202 县级归属需人工**：教育部名单只到地级市。
5. **D102 没有省级统一的县级服务中心/服务站名单**：count 主要来自新闻；覆盖率只在
   原文明确写出时抽取；分中心/服务站口径可能前后不一致，入库后建议人工复核。
6. **D102 的“家/户”不可入库**：DB 标准单位是“个”，loader 会以 `UNIT_MISMATCH` 拒绝。
7. **县域适用性元数据未改**：DB 中 D101/D102/D201/D202/D203 都是
   `is_county_applicable=1`；D201/D202 的县级行 loader 会允许，但 `region_from=query/none`
   会被 `REGION_UNVERIFIED` 拒绝。如研究设计要求县域不适用，需另开 migration。

## 快速开始

```bash
cd E:\Python\bug\D\D1

python run_all.py --list                       # 看默认处理链
python collect/TechFin-bug.py --list-plan       # 零请求，查看检索计划
python collect/TechFin-bug.py --offset 0 --limit 3   # 小样本采集
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
```

D2 把 `D1` 换成 `D2` 即可。D2 的省级数据更可靠，建议先用 `--no-region`：

```bash
cd E:\Python\bug\D\D2
python collect/TechFin-bug.py --no-region --limit 12
```

入库为可选后处理：

```bash
cd E:\Python\bug\D\D1
python load/techfin_load_to_db.py --dry-run --only-level3 D102
cd E:\Python\bug\D\D2
python load/techfin_load_to_db.py --dry-run --only-level3 D201,D202
```
