# 科技金融指数研究 · B 科技金融活动强度

本目录是一级维度 **B（科技金融活动强度）** 的采集主题集合，按二级指标分成四个**独立自洽**的主题目录：

| 目录 | 二级指标 | 三级指标 | 默认检索计划（县级） |
|---|---|---|---|
| `B/B1` | 债权融资活跃度 | B101 科技企业贷款余额、B102 贷款余额增速、B103 知识产权质押融资额、B104 信用贷款占比 | 124×4×3 = **1488 条** |
| `B/B2` | 股权融资活跃度 | B201 VC/PE 投资案例数、B202 投资金额、B203 上市科技企业数量、B204 募资额 | 124×4×3 = **1488 条** |
| `B/B3` | 政策性融资效果 | B301 科技保险保费收入、B302 创业担保贷款发放额 | 124×2×3 = **744 条** |
| `B/B4` | 金融科技应用 | B401 移动支付交易额、B402 数字普惠金融指数、B403 大数据/AI 风控应用 | 124×3×3 = **1116 条** |

## 关键设计

- 四个目录与 `A/A1` **代码同源**，只替换 `config/topic_config.py`、`config/extract_config.py`、
  `tests/` 与 README；完整流水线说明（采集→抓正文→OCR→清洗→抽取→可选入库）见
  [`A/A1/README.md`](../A/A1/README.md)。
- 地区口径沿用广东省 **124 个县级采集单元**，`QUERIES_PER_REGION = 3`；百度限流下必须
  `--offset/--limit/--cities` 分批 + 续跑台账多轮补齐。
- **B402/B403 只采语料，不做数值抽取、不入库**：B402 数字普惠金融指数来自北京大学面板数据
  （不在新闻里）；B403 是定性评分。两者保留在 `INDEX_KEYWORDS` 里参与清洗相关性判定，
  但不写入 `extract_config.INDICATOR_SCOPE`。
- **县域适用性**（以 `db/21_seed_index.sql` 为准）：B201-B204、B301、B403
  `is_county_applicable=0`。县级采集仍会展开，但 loader 默认以 `COUNTY_NOT_APPLICABLE`
  拒绝；需要代理值时显式加 `--allow-county-not-applicable`。B2 的县域替代口径
  （天使轮/A轮融资、新三板/区域股权交易中心挂牌）已写入 `county_queries`。
- 金额单位在抽取层保留原文，**由 loader 统一换算**到 `dim_index_measure.standard_unit`。
- `B102` 使用 `growth` 口径：抽取脚本的 `_metric_of()` 对「scope.metrics == {"growth"} 且单位为 %」
  返回 `growth`，避免落到 `share`。该泛化已同步到 A/A1-A3。

## 快速开始

```bash
cd E:\Python\bug\B\B1

python run_all.py --list                       # 看默认处理链
python collect/TechFin-bug.py --list-plan       # 零请求，查看检索计划
python collect/TechFin-bug.py --offset 0 --limit 3   # 小样本采集
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
```

入库为可选后处理，默认严格模式可能 0 行写入；B2/B3 的县级不适用指标需显式放行：

```bash
python load/techfin_load_to_db.py --dry-run --allow-county-not-applicable
```

## 官方源

四个主题共用：广东省统计局·统计公报、中国人民银行广东省分行·金融数据、广东省地方金融管理局
通知公告/工作动态。B1 追加广东省市场监督管理局·通知公告（知识产权质押）、广东省科技厅·通知公告；
B2/B3 追加广东省科技厅·通知公告。

国家金融监督管理总局（SPA）与广东证监局（列表由 JS 渲染）静态抓取不可用，未列入。
