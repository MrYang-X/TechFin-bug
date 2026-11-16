# 科技金融指数研究 · C 科技金融产出效能

本目录是一级维度 **C（科技金融产出效能）** 的采集主题集合，按二级指标分成三个**独立自洽**的主题目录：

| 目录 | 二级指标 | 三级指标 | 默认检索计划（县级） |
|---|---|---|---|
| `C/C1` | 科技创新产出 | C101 PCT 国际专利申请量、C102 技术合同成交额、C103 每万人发明专利拥有量 | 124×3×3 = **1116 条** |
| `C/C2` | 经济发展贡献 | C201 高新技术产业产值占规上工业产值比重、C202 科技服务业增加值 | 124×2×3 = **744 条** |
| `C/C3` | 企业成长性 | C301 独角兽企业数量、C304 瞪羚企业数量、C302 科技企业营收增长率、C303 科技企业利润增长率 | 124×4×3 = **1488 条** |

合计 **3348 条**县级检索式；按百度约 27s/条，一轮约 25 小时。必须用
`--offset/--limit/--cities` 分批 + 累积库多轮补齐。

## 关键设计

- 三个目录与 `B/B1` **代码同源**，只替换 `config/topic_config.py`、
  `config/extract_config.py`、`tests/` 与 README；完整流水线说明（采集→抓正文→OCR→清洗→抽取→可选入库）
  见 [`A/A1/README.md`](../A/A1/README.md)。
- 地区口径沿用广东省 **124 个县级采集单元**，`QUERIES_PER_REGION = 3`。
- **C301/C304 拆分**：原 C301 是「"独角兽"、"瞪羚"企业数量」合并指标，两个不同榜单的数
  共用同一主键会互相覆盖。现拆为 C301 独角兽、C304 瞪羚；`db/21_seed_index.sql` 等基线
  与 `db/migrations/20261116_001_add_c304_gazelle.sql` 已同步。
- **C302/C303 保持拆分**：营收增长率与利润增长率是两个指标，词族与非目标主语互斥。
- **C103 密度口径**：典型写法「每万人发明专利拥有量 52.3 件」，抽取脚本会把单位
  `件` 归一为 `件/万人`（`_adapt_unit` 已从 A204 硬编码泛化为通用密度口径）。
- **官方源全量保存**：`SAVE_ALL_ITEMS=True`，相关性提纯交给清洗阶段。

## 官方来源渠道

| 源 | 栏目 | 用于 |
|---|---|---|
| 广东省统计局·统计公报 | `stats.gd.gov.cn/tjgb/` | C1/C2/C3 |
| 广东省科学技术厅·通知公告/政策文件 | `gdstc.gd.gov.cn/zwgk_n/` | C1/C2/C3 |
| 广东省市场监督管理局·通知公告 | `amr.gd.gov.cn/zwgk/tzgg/` | C1（知识产权） |
| 国家知识产权局·统计 | `cnipa.gov.cn/col/col61/` | C1（月度分省 PCT/有效发明专利） |
| 工信部火炬中心·技术市场 | `chinatorch.org.cn/jssc/` | C1/C2/C3（技术合同、高企） |
| 科技部·科技统计报告 | `most.gov.cn/.../kjtjbg/` | C1/C2/C3 |

长城战略咨询 GEI 官网是 SPA、胡润是单篇报道页，静态列表抓不到，故不作为官方列表源，
由搜索引擎检索词（`独角兽企业 榜单`、`瞪羚企业 名单` 等）覆盖。

## 口径限制

- **C101 县级不适用**：`db/21_seed_index.sql` 中 `is_county_applicable=0`；县级行默认被
  loader 以 `COUNTY_NOT_APPLICABLE` 拒绝，需要代理值时显式加 `--allow-county-not-applicable`。
- **C102 只认技术合同成交额/成交金额**，不接受「技术交易额」（不同口径）。
- **C103 只认每万人发明专利拥有量**，不接受「高价值发明专利」「申请量」「授权量」。
- **C201 只认高新技术产业产值占比**，本次不接受「高技术制造业增加值占比」代理。
- **C202 接受代理**：「科学研究和技术服务业增加值」是官方统计口径下最接近的代理；
  「营业收入」不产 C202 点。
- **C301/C304 合并口径不分配**：「独角兽、瞪羚企业共 X 家」由 `reject_pattern` 拦截；
  分列表述「独角兽企业20家、瞪羚企业35家」仍分别归属。

## 快速开始

```bash
cd E:\Python\bug\C\C1

python run_all.py --list                       # 看默认处理链
python collect/TechFin-bug.py --list-plan       # 零请求，查看检索计划
python collect/TechFin-bug.py --offset 0 --limit 3   # 小样本采集
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
```

C2、C3 把 `C1` 换成对应目录即可。入库为可选后处理：

```bash
python load/techfin_load_to_db.py --dry-run
```
