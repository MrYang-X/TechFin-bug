# 科技金融指数研究 · D2 人力与创新环境

> **本目录与 `A/A1`/`B/B1`/`C/C1` 代码同源**（`config/` 之外的所有业务脚本保持一致），
> 只替换了 `config/topic_config.py`、`config/extract_config.py`、`tests/` 与 README。
> 完整流水线说明（依赖关系、采集分批、OCR、入库门禁等）见
> [`A/A1/README.md`](../../A/A1/README.md)；D 维度总览见 [`D/README.md`](../README.md)。

## 指标

| 编码 | 指标 | metric / 落库单位 | 口径限制 |
|---|---|---|---|
| D201 | R&D人员全时当量 | amount / 人年 | 只认“全时当量（人年）”；“R&D人员 10 万人”是人员数，不产点 |
| D202 | 普通高等学校数量 | count / 所 | 只认普通高等学校/普通高校/高等院校数量；在校生、招生、教职工、成人高校不产点 |
| D203 | 数字经济发展指数 | index / 指数值 | **只采语料，不产数值点**（外部指数，无官方县级序列） |

### 口径风险（重要）

- **D201 县级官方数据基本不存在**：《广东统计年鉴》只公布全省总量；分市
  “R&D人员折合全时当量”散见于省统计局互动交流附件/统计快讯，**没有县级表**。
  县级 D201 只能靠零星新闻，且要区分“人”与“人年”。
- **D202 县级归属需人工**：教育部全国高等学校名单只给“所在地（地级市）”，
  无县区字段；省教育统计公报/年鉴也只到市。县级“高校数量”需按校区地址人工归属。
- **D203 无官方县级指数**：中国信通院官网整域 412（WAF），国家数据局“数字中国”
  列表页 404；现有广东地市/区县数字经济指数报告只覆盖部分区县。D203 只采语料，
  数值需研究团队另行采用统一面板数据。
- DB 中 D201/D202/D203 的 `is_county_applicable=1`，loader 会允许县级行，但
  `region_from=query/none` 的县级行会被 `REGION_UNVERIFIED` 拒绝；如研究设计认为
  县域不适用，需另开 migration 置 0。

## 检索词

每个指标 12 条检索式，带地区采集时取前 3 条：

- D201：`R&D人员全时当量`、`研发人员全时当量`、`R&D人员 折合全时当量`
- D202：`普通高等学校 数量`、`普通高校 数量`、`高等学校 名单`
- D203：`数字经济发展指数`、`数字经济指数`、`中国数字经济发展指数`

默认计划：124 个县级单元 × 3 指标 × 3 = **1116 条**；无地区检索式 **36 条**。

## 官方源

- 广东省统计局·统计公报
- 广东省统计局·广东统计年鉴
- 广东省统计局·统计快讯（含科技经费投入统计公报）
- 广东省教育厅·数据发布（教育事业发展统计公报）
- 广东省教育厅·公示公告（高校设置/名单）
- 广东省科学技术厅·通知公告
- 科技部·科技统计报告（部分行无日期，`allow_no_date=True`）
- 教育部·名单公告（含全国高等学校名单，`max_pages=3`）
- 广东省工业和信息化厅·通知公告（D203 数字经济语料）

中国信通院（`caict.ac.cn`）整域 412、教育部 A03 栏目 403、国家数据局“数字中国”
列表页 404，均不列入。上线前用 `--limit-pages 1` 复核每个新源；若返回 0 条/乱码，
把该源 `enabled=False`。

## 关键实现

- **D203 不写入 `extract_config.INDICATOR_SCOPE`**：只采语料；LLM schema 的
  `indicator.enum` 只有 `D201`、`D202`。
- **`人年` 的本地映射**：`D/D2/config/extract_config.py` 的 `UNIT_METRIC` 增加
  `"人年": "amount"`，使 `NUM_UNIT_RE` / `_metric_of()` / `validate_points()` 贯通；
  `12.5万人年` 由既有量级逻辑换算为 `125000` 人年。loader 的 `_UNIT_TOKENS` 已含
  `人年`，`unit_compatible("人年","人年")` 直接通过。
- **LLM 的“万人年”约定**：prompt 要求 `value=125000`、`unit=人年`、`value_text`
  保留 `"12.5万人年"`，避免证据级 `value_num` 与 loader 解析出的主值不一致。
- **`stock_default_actual=True`**：让“全时当量 12.5 万人年”“高校 16 所”这类
  无动词存量句按 `actual` 处理。
- **`POINTS_PREFIX="points_"`**：与 loader 默认发现逻辑一致；证据级累积库仍是
  `data/techfin_d2_points.csv`。

## 产物

- `data/tech_fin_news.csv`、`data/techfin_official.csv`（跨轮累积）
- `data/body_cache/`（正文缓存，跨轮复用）
- `data/techfin_d2_points.csv`（证据级数据点累积库）
- `data/d2_llm_cache/`、`data/d2_llm_calls.jsonl`
- `<运行目录>/corpus_verification_d2.md`（印证报告）

## 快速开始

```bash
cd E:\Python\bug\D\D2

python collect/TechFin-bug.py --list-plan --limit 6
python collect/TechFin-bug.py --list-plan --no-region --limit 6
python collect/TechFin-bug.py --offset 0 --limit 3
python collect/techfin_authoritative.py --limit-pages 1
python fetch/techfin_fetch_body.py --limit 3
python clean/techfin_curate.py
python extract/techfin_extract_points.py --extractor rule --limit 3
python tests/test_accuracy_regression.py
```

D201/D202 的省级数据更可靠，建议先用 `--no-region` 跑无地区检索式：

```bash
python collect/TechFin-bug.py --no-region --limit 12
```

入库为可选后处理：

```bash
python load/techfin_load_to_db.py --dry-run --only-level3 D201,D202
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
