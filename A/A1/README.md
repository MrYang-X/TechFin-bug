# 科技金融指数研究（主题目录 A/A1）

本目录是一个**完整自洽的采集主题**：① 列表采集（搜索引擎 + 官方源）→ ② 二级抓取
（正文 + 文字型附件）→ ④ 语料清洗/提纯 → ③ 数值数据点抽取（规则 + LLM），
外加印证/质量报告脚本与主题配置，产物全部落在本目录下的输出根 `techfin_common_output/`。

通用的爬虫机械（目录分配、日志、日期解析、清洗、导出、累积合并）在**仓库根**
的 `crawler_common.py`，与主题无关，不要在本目录里复制一份。

---

## (a) 各 py 的依赖关系

**没有任何脚本互相 import。** 各脚本是对等关系，只通过**文件**耦合——
这点最容易误解，所以先把依赖图看清楚。

```
crawler_common.py    （仓库根；叶子模块：通用工具，不 import 任何本地模块）
topic_config.py      （本目录；叶子模块：本主题常量，不 import 任何本地模块）
extract_config.py    （本目录；叶子模块：③ 的抽取规则/地区别名/LLM 契约，不 import 本地模块）
        ▲
        │ import
        ├── TechFin-bug.py              ① 搜索引擎爬虫（百度 + Bing + 360）
        ├── techfin_authoritative.py    ① 官方源爬虫（广东科技厅 / 科技部）
        ├── verify_corpus.py            印证报告（不采集，只读语料）
        ├── techfin_fetch_body.py       ② 二级抓取：正文 + 文字型附件（读①语料，写正文缓存）
        ├── techfin_curate.py           ④ 语料清洗/提纯（读①②，写 curated_*）
        ├── techfin_extract_points.py   ③ 数值抽取：规则 + LLM（读正文缓存 + 清洗清单，写数据点 CSV）
        └── verify_points.py            ③验 数据点质量报告（不采集，只读数据点）
```

数据流（脚本之间**只通过文件交互**，谁都不调用谁）：

```
TechFin-bug.py            ──写──→ <OUT_ROOT>/<日期-次数>/search_*
                                   <OUT_ROOT>/tech_fin_news.csv      ← 累积总库

techfin_authoritative.py  ──写──→ <OUT_ROOT>/<日期-次数>/official_*
                                   <OUT_ROOT>/techfin_official.csv   ← 累积总库
                                          │
techfin_fetch_body.py     ──读①──→ 上面两个累积总库
                          ──写──→ <OUT_ROOT>/body_cache/           ← 正文 JSON/TXT + 附件二进制
                                          │
techfin_curate.py         ──读①②─→ 累积总库 + body_cache/（纯本地，不发请求）
                          ──写──→ <OUT_ROOT>/curated_corpus.csv     ← 文章级干净语料（超集视图）
                                   <OUT_ROOT>/curated_uncertain.csv ← 官方泛化标题待确认队列
                                   <OUT_ROOT>/curated_dropped.csv   ← 被剔除条目（含原因）
                                          │
techfin_extract_points.py ──读②④─→ body_cache/ + curated_corpus.csv
                          ──写──→ <OUT_ROOT>/techfin_a101_points.csv（证据级数据点，跨轮累积）
                                   <OUT_ROOT>/<日期-次数>/points_resolved.csv（聚合级）
verify_points.py          ──读③──→ 数据点 CSV ──写──→ <最新运行目录>/points_validation.md
verify_corpus.py          ──读①②─→ 两个累积总库 ──写──→ <最新运行目录>/corpus_verification.md
```

> ④ 是「提纯器」不是「必经关卡」：③ 读不到 `curated_corpus.csv` 时行为完全不变（读 body_cache 全量）；
> ② 加 `--from-curated` 才只抓 ④ 挑出的队列。

> 为什么 ②③ 分开：**抓取最贵、最易被限流**。把"抓取"与"抽取"解耦后，改抽取规则（`extract_config.py`）
> 只需重跑 ③，**不必重抓政府网站**——`body_cache/` 跨轮复用正是为此。②③ 都是"纯读上游文件、
> 只写自己的产物"，与 `verify_corpus.py` 同款解耦模式。

> 为什么脚本开头有一段"向上找 `crawler_common.py`"的代码：Python 只把**脚本自身
> 所在目录**放进 `sys.path`，**不会**自动包含父目录。本脚本在 `A/A1/`，而通用工具在
> 仓库根，所以必须先把仓库根加进 `sys.path` 才能 import。那段代码是逐级向上找，
> 不写死层数，因此 `A/A1`、`B/B2` 或更深的目录都能用。

### 文件清单（作用 / 输入 / 输出）

| 文件 | 作用 | 输入 | 输出 |
|---|---|---|---|
| `crawler_common.py`（仓库根） | 通用机械：目录/日志/日期/清洗/导出/累积合并/请求头/反爬识别/编码探测；**主题无关** | —（被各脚本 import） | —（函数库） |
| `topic_config.py` | 本主题常量：指标体系 `INDEX_KEYWORDS`、地区 `GUANGDONG_REGIONS`、噪声源、采集节奏、产物契约 | —（被 import） | — |
| `extract_config.py` | ③ 抽取契约：指标词族 / 数值·动词正则 / 地区别名 / LLM prompt+schema+model | —（被 import） | — |
| `TechFin-bug.py` | ①-a 搜索引擎采集（百度 / Bing / 360） | 检索计划（`INDEX_KEYWORDS` × `GUANGDONG_REGIONS`） | `search_*` + `tech_fin_news.csv` |
| `techfin_authoritative.py` | ①-b 官方源采集（广东科技厅 / 科技部） | 官方源清单 `SOURCES` | `official_*` + `techfin_official.csv` |
| `techfin_fetch_body.py` | ② 二级抓取：HTML 正文 + 文字型附件 | ① 累积总库 | `body_cache/` |
| `techfin_curate.py` | ④ 语料清洗/提纯：相关性 + 规范化 + 去重 + 分级 | ①②（累积总库 + 正文缓存） | `curated_corpus.csv` / `curated_uncertain.csv` / `curated_dropped.csv` |
| `techfin_extract_points.py` | ③ 数值抽取：规则 + LLM | ②④（正文缓存 + 清洗清单） | `techfin_a101_points.csv` + `points_resolved.csv` |
| `verify_corpus.py` | 交叉印证报告（只读，不采集） | ① 两份语料 | `corpus_verification.md` |
| `verify_points.py` | ③验 数据点质量报告（只读） | ③ 数据点 CSV | `points_validation.md` |

> 各脚本参数见 `python <脚本> --help`；`--output-dir <路径>` 可临时改输出根（相对路径锚定本目录）。

---

## (b) 怎么用

```bash
cd E:\Python\bug\A\A1

# —— ① 列表采集（产出 URL 语料，进累积总库）——
python TechFin-bug.py                 # ①-a 搜索引擎语料（默认县级走百度，见下）
python techfin_authoritative.py       # ①-b 官方政策源语料
python verify_corpus.py               # 交叉印证报告（读 ① 的两份语料）

# —— ② 二级抓取：正文 + 文字型附件（落 body_cache/，跨轮复用）——
python techfin_fetch_body.py --limit 30     # 先抓小切片看效果
python techfin_fetch_body.py                # 抓全部；可反复重跑，已成功的自动跳过

# —— ④ 语料清洗/提纯：相关性过滤 + 规范化 + 去重 + 分级（纯本地，零请求）——
python techfin_curate.py                    # 生成 curated_corpus.csv / curated_uncertain.csv + 报告
python techfin_fetch_body.py --from-curated # ② 只抓 ④ 挑出的队列（相关但无正文 + 待确认）

# —— ③ 数值抽取：从正文/附件抽 A101 数据点（读 ④ 清洗清单；缺失则全量）——
python techfin_extract_points.py --extractor rule     # 只跑规则层（零 LLM 成本，可离线）
python techfin_extract_points.py --extractor hybrid   # 规则 + LLM 兜底（默认，需 Claude API）
python verify_points.py                               # 数据点质量报告
```

> **②③④ 与①的关系**：② 读 ① 的累积总库、写正文缓存；④ 读 ①②、写清洗清单；③ 读正文缓存 + 清洗清单、写数据点。
> 顺序 ①→②→④→③；②③④ 各自可独立反复重跑（② 靠缓存跳过已抓、③ 靠 LLM 缓存跳过已调、④ 全量幂等重算）。
> **④ 是提纯器不是必经关卡**：③/② 读不到 `curated_*` 时行为完全不变。改抽取规则只动 `extract_config.py` 重跑 ③。

### 运行顺序（一条主链 + 旁路）

**主链：`① 采集 → ② 抓正文 → ④ 清洗 → ③ 抽取`**

1. **① 采集**（`TechFin-bug.py` / `techfin_authoritative.py`）：两者**顺序任意、可只跑其一**，
   共用同一个输出根与"当日第 N 次爬取"的序号，各占一个 `-N` 目录
   （只跑前者得 `2026.9.27-1/` 只有 `search_*`；紧接着跑后者得 `2026.9.27-2/` 只有 `official_*`），
   靠文件名前缀区分，**目录里不会混淆**。
2. **② 抓正文**（`techfin_fetch_body.py`）：读 ① 的累积总库；可反复重跑，已成功的自动跳过。
3. **④ 清洗**（`techfin_curate.py`）：读 ①②，产出干净语料与待确认队列（纯本地、零请求）。
4. **③ 抽取**（`techfin_extract_points.py`）：读 ②④。**④ 是可选的**——读不到 `curated_corpus.csv`
   时降级为读 `body_cache/` 全量、行为完全不变。

**旁路（报告脚本，输入就绪即可随时跑）**：`verify_corpus.py` 读 ①②；`verify_points.py` 读 ③。

**补抓闭环（提高召回）**：`techfin_fetch_body.py --from-curated`（只抓 ④ 挑出的"相关但无正文 + 待确认"
队列）→ 重跑 `④` 复判 → 重跑 `③`。

**输出位置**：**各脚本**都支持 `--output-dir <路径>` 临时改输出位置；不传就用 `topic_config.OUTPUT_ROOT`。
相对路径锚定到**本目录**（不是当前工作目录），也可写绝对路径放到仓库外。
**注意：换了输出位置，累积总库（含正文缓存）也跟着走，会在新位置从零开始累积**，原数据不会自动搬过去。

### ①-a 搜索引擎采集是"指标化采集"：从检索词到指标 + 地区

`TechFin-bug.py` 现在采集的不是一堆扁平关键词，而是**指标体系 × 地区口径**的检索计划
（由 `topic_config.INDEX_KEYWORDS` + `GUANGDONG_REGIONS` 展平而来）。`INDEX_KEYWORDS`
已补全 4 个一级维度 / 12 个二级指标 / **35 条三级指标**；但**命令行默认只采 A1**
（`--level2` 默认 `["A1"]`），默认口径是
**A1 三级指标 × 广东省 124 个县级单元 ≈ 1116 条检索式**（每条指标取前 3 条检索式，
见 `QUERIES_PER_REGION`）。

> 为什么默认不全量：百度资讯按 IP 限流。全量 35 指标 × 124 单元会远超额度，必须靠
> `--level2` / `--cities` / `--offset` 分批推进。例如 `--level2 A1 A2 A3` 采完 A 维度，
> `--cities 广州市 深圳市` 按市分区。另可下调 `QUERIES_PER_REGION` 直接减少总量。

```bash
python TechFin-bug.py --list-plan --limit 6      # 零请求，先看清要打哪些检索式与路由
python TechFin-bug.py --cities 广州市 深圳市      # 只采指定地级市（分区推进）
python TechFin-bug.py --offset 0 --limit 20      # 分批切片，绕开百度限流
python TechFin-bug.py --no-region                # 不带地区前缀（此时百度+Bing+360 三源并用）
python TechFin-bug.py --sources baidu            # 临时只用一个源（如先验证百度是否可用）
python TechFin-bug.py --fresh                    # 忽略续跑台账，全量重采
```

**为什么必须分批。** 百度资讯按 IP 限流，而 1116 条检索式远超额度——**单轮采不全是
常态，不是故障**。完整性靠"放慢节奏 + 切片 + 累积 + 重跑"：
`--offset/--limit` 把计划切成小片，每片跑完结果合并进同一个累积总库（见 (c)），
隔一段时间重跑即可补齐。每轮结尾的 `[覆盖率]` 会报各源成功了几条检索式，**缺口始终可见**。
**重跑会读续跑台账**（`tech_fin_search_progress.csv`，在输出根目录；列
`source, plan_key, status, n_items, ts`），跳过已成功采过的 (源, 检索式)，只补缺口——
省下最稀缺的百度额度。注意**台账只是"进度标记"，本身不含数据/出处链接**：数据与链接在
**本轮运行目录**里（`search_delta.csv` 的 `url` 列、`search_links.txt`），而且是
**采集过程中增量写入**的（每采完一条检索式写一次，中断也不丢）。

**放慢节奏 + 分批是关键（2026-10 实测）。** 百度限流是**速率**敏感而非次数敏感：1s 连打
几次即被封（同 IP 下 www/news/m 各站返回同一张验证页），而间隔 **≥20s 可连续采集**——
实测 20s 间隔下 15 条里成功 14 条，且**唯一一次限流在第 14 条出现、等待约 8 分钟后第 15 条
又恢复**（限流是暂时的，等得住就恢复）。故 `MIN_DELAY/MAX_DELAY` 已抬到 20/35s，
百度退避阶梯设为 `[60,180,300]`（覆盖该恢复时间）。

**推荐工作流（务实做法）：** 一轮跑 ~40 条就打住，隔 15 分钟以上再跑下一轮——
靠 `--offset/--limit` 切片、靠续跑台账自动跳过已采的，多轮累积把 1116 条补齐。
不要指望一轮跑完（1116 条即 8 小时，且百度会在途中周期性限流）。
`--fresh` 可忽略台账全量重采。

**为什么县级只能靠百度（换源收益极小）。** 实测：对"荔湾区 高新技术企业"这种
"县级地区 + 指标词"检索式，**只有百度严格做 AND**（返回 10 条里 8 条两词兼有，
经 `is_relevant` 全过）；**Bing 网页搜索与 360 只做松散匹配**——Bing 返回的 10 条里
地区词命中 8 条、但指标词命中 **0** 条，全被 `is_relevant` 过滤（通过率 0%）；360 县级
同样近 0。它们在**无地区**检索式上略有增益（Bing 约 1%、360 约 4%）。
因此 `SOURCES_BY_REGION_LEVEL` 让**县级只走百度**，无地区才三源并用；
`ENABLED_SOURCES` 仍列全三源备用。Google 网络不可达，Bing 新闻垂直（`news/search`、
`?format=RSS`）已失效，搜狗第 2 次请求即返 200+验证页——均不可用。

**按源隔离退避（多源改造的核心）。** 每个源有**独立的退避额度与跳闸状态**
（`topic_config.BACKOFF_BY_SOURCE`）：某源被限流只跳过该源（额度用尽则"跳闸"停用），
其余源继续，不再像旧版那样一个源限流就拖垮整轮。

**锚点为什么默认不生效。** 主题锚点（`topic_config.ANCHOR_TERMS`）只在**不带地区**的
检索式上启用。带地区时不再叠加——实测显示地区词本身已是强约束，再叠主题锚点属于
重复约束，代价是把真数据杀掉：同一轮里被锚点丢弃的正是"荔湾区现有高新技术企业 302 家"
"科技型中小企业申报"这类 A1 指标的核心语料。`ANCHOR_TERMS` 置空即全局关闭。

### 产物长什么样

```
techfin_common_output/
├── tech_fin_news.csv          ← ① 累积总库（搜索引擎，跨轮读写）
├── techfin_official.csv       ← ① 累积总库（官方源，跨轮读写）
├── tech_fin_search_progress.csv  ← ① 续跑台账（进度标记，非数据：source/plan_key/status/n_items/ts）
├── body_cache/                ← ② 正文缓存（跨轮复用；正文 JSON/TXT + 附件二进制）
│   ├── body_progress.csv          ② 续跑台账（source="body", plan_key=<article_id>）
│   └── ab/ab12cd34ef56.json       正文+元数据+附件索引（按 article_id 前 2 位分桶）
├── a101_llm_cache/            ← ③ LLM 结果缓存（key=article_id+prompt_version+model）
├── a101_llm_calls.jsonl       ← ③ LLM 调用审计（逐次追加，保证可复现）
├── techfin_a101_points.csv    ← ③ 证据级数据点累积总库（一行 = 一个数据点）
├── curated_corpus.csv         ← ④ 文章级干净语料（超集视图：规范化+相关性+分级+事件分组）
├── curated_uncertain.csv      ← ④ 官方泛化标题待确认队列（供 ② --from-curated 补抓）
├── curated_dropped.csv        ← ④ 被剔除条目（含原因，便于复核）
└── 2026.9.27-1/               ← 每轮运行目录（各脚本共用当日序号，靠前缀区分）
    ├── search_*                    ①-a TechFin-bug.py 本轮产物
    ├── official_* / corpus_verification.md   ①-b / verify_corpus.py
    ├── body_log / body_index.csv   ② techfin_fetch_body.py 本轮产物
    ├── curated_log / curated_report.md / curated_corpus.csv   ④ techfin_curate.py 本轮产物
    └── points_log / points_delta.csv / points_resolved.csv / points_validation.md   ③ / ③验
```

日志由脚本自己写（`*_log`），**不需要再用 shell 重定向 `> run.log`**；
好处是跑崩时的 traceback 也会落进日志。

### ②③ 二级抓取与数值抽取（新增流水线）

**② `techfin_fetch_body.py` —— 抓正文与文字型附件。** 对 ① 语料里每条 url 二次请求，
抽 HTML 正文（trafilatura，缺则 bs4 兜底）与**文字型附件**（PDF/doc/docx/xls/xlsx，按 magic bytes
分派解析）。结果落 `body_cache/`，**跨轮复用**（这是"改抽取规则不重抓网页"的关键）。要点：

- **编码**：政府站多为 GBK，用 `crawler_common.guess_encoding` 探测（不再硬编码 UTF-8）。
- **礼貌与合规**：按主机限速（`HOST_MIN_INTERVAL`）+ 遵守 robots.txt（`ROBOTS_ENFORCE`）。
- **扫描件 PDF 不做 OCR**：正文密度过低判为扫描件并标记 `is_scanned`（缺口在报告里可见）。
- **断点续跑**：已成功（status=ok）跳过；失败/被拦的重跑自动重试。

**③ `techfin_extract_points.py` —— 抽 A101 数值数据点。** 读 `body_cache/`（**纯读，不发请求**）：
- **规则层**（零成本）正则匹配"XX 区新增高新技术企业 123 家"等；**LLM 层**（Claude Sonnet 5.5，
  结构化输出）在提到指标时兜底，专门区分 `actual`（实际/累计/现有）与 `planned/target`（拟/计划/目标）。
- **有 LLM 结果时以 LLM 为准**（规则层在真实政府文本上假阳性较多，仅作交叉印证与离线回退）。
- 输出两份 CSV：证据级 `techfin_a101_points.csv`（一行 = 一个数据点，含原文 `evidence_snippet`）
  与聚合级 `<运行目录>/points_resolved.csv`（一行 = 一个 地区×年份×指标 格）。
- **列与 SQL 事实表对齐**：聚合级列可直取 `fact_tech_finance_data`（`level3_code`→`index_id`、
  `canonical_value`→`index_value`、`authority`→`data_quality`、`source_article_ids`→`evidence_ref`）。
- **"拟/减少"标记而非丢弃**：`context_type` 为 `planned/target/decline` 的数据点照样入库，
  聚合层默认只把 `actual` 选为主值。抽取规则/地区别名/LLM 契约集中在 `extract_config.py`。

**③验 `verify_points.py`** 只读数据点 CSV → 写 `points_validation.md`（维度分布、置信度、
地区覆盖、**多源冲突组**、缺口统计、随机抽检样本）。

**依赖**：`pip install trafilatura pdfplumber python-docx openpyxl xlrd olefile anthropic`
（缺哪个就降级：trafilatura→bs4、docx→zipfile、PDF/xls→跳过并标记；缺 `anthropic` 则
`--extractor rule` 纯规则模式可离线跑完全流程）。

### ④ 语料清洗/提纯（`techfin_curate.py`）

纯本地、零请求；把 ① 的原始语料经漏斗收敛成干净语料：
**噪声源 → 空/废标题 → 同链接(去追踪参数)去重 → 相关性提纯 → 地区归一 → 质量分级 → 事件分组标注**。
核心判断：① 里**检索语料已被 `is_relevant` 筛过**，④ 只做规范化/去重/分级；**官方语料是零过滤全量保存**
（446 条里仅约 25-33 条与指标相关），④ 承担相关性提纯主力。

- **相关性打分**：指标词族命中 标题 +3 / 摘要 +2 / 正文 +3 / 锚点 +1 / 地区 +1，`>=3` 判相关（记 `rel_level`/`rel_score`）。
- **官方泛化标题**（含"名单/公示/认定/公布"但无词族、无正文）不硬删也不硬留 → `curated_uncertain.csv` 待 ② 补正文复判。
- **"高企"形容词消歧**（成本/物价…高企）已内置，为铺到 124 县预留。
- **质量分级 `quality_grade` 1-4**（对齐 SQL `data_quality`）：1 年鉴公报 / 2 官方公报 / 3 媒体 / 4 相关待补。
- **链接去重**只删白名单追踪参数（微信 `scene`、`utm_*` 等），保留内容参数与路径、保留原 `url` 可复核；
  **同事件转载并存是多来源特性**，只做 `event_group_id` 标注、不合并。
- 产物 `curated_corpus.csv`（文章级超集视图，46 列）+ `curated_report.md`（漏斗计数、回归哨兵、抽检样本）。
- **反哺**：「相关但无正文」+ `curated_uncertain.csv` = ② 的 `--from-curated` 待抓队列；
  ③ 默认只抽 `curated_corpus.csv` 里提纯通过的文章、用 `quality_grade` 作 `data_quality`、并回填官方缺失的指标/地区列。

---

## (c) 累积总库的更新时机与去重规则

**更新时机**：**采集过程中增量写入**——每采完一条检索式，就把"当前已采的全部条目"
落盘一次（本轮增量 `search_delta.csv` + 累积快照 + 累积总库 + 链接清单），而不是跑完
才写。因此**中途 Ctrl-C 也不丢已采数据**。
并且**续跑台账只在数据落盘成功后才记**（台账 ⟸ 数据）——否则会出现"台账记了 ok、
数据却不存在"的不一致，重跑时跳过它 → 数据永久缺失。
（注：`techfin_authoritative.py` 仍是"跑完一次性写"。）

**去重键**：`article_id = md5(标题 + 链接)[:16]`，合并时 `keep="last"`。
因此**完全相同的"标题 + 链接"不会重复入库**，且新一轮的解析结果会覆盖旧的。

**新增列与新旧语料混存**：指标化采集给搜索引擎语料加了
`level1_code / level1_name / level2_code / level2_name / level3_code / level3_name /
unit / region_code / region_name / indicators` 这几列。**在这之前采的语料没有这些列**，
合并时由 `merge_with_existing` 的 `fillna("")` 清成空串——**旧行指标列为空是正确语义**，
它们本就不对应任何一条指标。下游做指标分析时用 `level3_code != ""` 过滤即可。

一篇文章可能同时印证多条指标：单值的 `level3_code` / `level3_name` 取"首个命中"（便于聚合），
`indicators` 列用 `+` 累积全部（如 `A1:高新技术企业数量/密度+A1:R&D经费支出占GDP比重`），
与 `engines` 的多源叠加同款。

> **`level3_code` 才是 join 键**（如 `A101`），`level3_name` 只是给人和报表看的显示名。
> 指标中文名一旦修订（改口径、加限定词）就会变，而编码不变——所以与
> `tech_finance_index.sql` 的 `dim_index` 关联时务必用 `level3_code`，不要用中文名。

**去重的边界**（务必了解，否则会把下面几种情况误判成 bug）：

| 情况 | 结果 | 说明 |
|---|---|---|
| 同一事件被不同媒体转载 | **并存为多条** | 标题/链接都不同 → article_id 不同。这是**特性**：印证研究正需要多来源 |
| 同一文章链接带不同追踪参数（如微信 `&scene=27`） | ① 可能当作两条，**④ 会按去追踪参数后的链接折叠** | 链接字符串不同；④ 的 `url_dedup_key` 归一后清掉这层噪声 |
| 360 的相对日期（"1天前"） | 重跑会**改写已存条目的 `date`** | `keep="last"` 的副作用；不新增重复，但会覆盖。`date_url`（链接内嵌日期）稳定，不受影响 |
| 某轮只采到重复条目 | 总库条数不变 | 日志里的"净新增 0 条"就是这个意思 |

每轮日志结尾会同时报两个数，**不要混淆**：

```
[归档] 本轮采集 70 条，其中相对累积总库净新增 3 条 | 累积 111 条
```
搜索引擎每轮会把同样的头条再返回一遍，所以"采集 70 条"可能只带来"净新增 3 条"。

---

## (d) 新增一个主题要做什么

照抄本目录新建一个二级目录（如 `A/A2`），复制 `topic_config.py`、`extract_config.py`
与各脚本（含 `techfin_fetch_body.py` / `techfin_curate.py` / `techfin_extract_points.py`），
然后**只改四处**：

1. **`topic_config.py`** —— 这是主要工作：
   - 官方源打标词：`AUTH_KEYWORDS`（官方源爬虫据此给条目打 `keyword` 标签与统计；
     `SAVE_ALL_ITEMS=True` 时不参与筛选入库）。**它由 `INDEX_KEYWORDS` 派生**（取各三级
     指标实际启用的前 `QUERIES_PER_REGION` 条检索式中的短词 + `AUTH_EXTRA_TERMS`），
     改检索词即自动跟随。**搜索引擎的检索词【不在】这里**——它们在各三级指标的
     `queries` 里，见下一条 `INDEX_KEYWORDS`
   - 相关性约束：`CORE_TERMS` / `QUALIFIER_TERMS`（仅当检索词整串落在
     `QUALIFIER_TERMS` 里时才生效；本主题留空、机制停用）
   - 指标体系：`INDEX_KEYWORDS`（字段名对齐 `tech_finance_index.sql` 的 `dim_index`；
     每条三级指标带 `level3_code` 稳定编码与 `min_region_level` 可得层级。**权重不在
     这里维护**——爬虫不消费权重，唯一权威是 SQL 的 `dim_index` 表，避免两处漂移）
     与 `ANCHOR_TERMS` / `QUERIES_PER_REGION`；地区口径 `GUANGDONG_REGIONS` /
     `REGION_QUERY_NAME_OVERRIDES`（换省份就是换这两个常量）
   - 噪声源：`NOISE_DOMAINS` / `NOISE_SOURCES` / `NOISE_URL_SUFFIXES`
   - 采集范围与节奏：`ENABLED_SOURCES` / `SOURCES_BY_REGION_LEVEL`（按检索式粒度选源）、
     `BACKOFF_BY_SOURCE` / `DELAY_BY_SOURCE`（按源隔离的退避与间隔）、
     `MAX_PAGES_PER_KEYWORD` / `MAX_PAGES_BY_SOURCE` / `MIN_DELAY` / `MAX_DELAY` /
     `OFFICIAL_DELAY` / `SAVE_ALL_ITEMS`
   - 官方源清单：`SOURCES`（**栏目要选"真正发布指标内容"的**：现行是广东省科技厅
     通知公告/政策文件 + 科技部通知通告；指向"新闻/发布会"栏目的源标题里不会有指标词，
     打标会全空，详见该常量上方的实测注释）
   - 产物契约：`OUTPUT_ROOT`（换个输出根名）、`SEARCH_ACCUMULATOR` / `OFFICIAL_ACCUMULATOR`、
     `SEARCH_PROGRESS`（续跑台账）、`SEARCH_PREFIX` / `OFFICIAL_PREFIX`、`TOPIC_LABEL`；
     以及 ②③④ 的契约：`BODY_CACHE_DIR` / `BODY_PREFIX` / `BODY_PROGRESS`、
     `POINTS_ACCUMULATOR` / `POINTS_PREFIX` / `LLM_CACHE_DIR` / `LLM_CALLS_LOG`、
     `CURATED_ACCUMULATOR` / `CURATED_UNCERTAIN` / `CURATED_DROPPED` / `CURATED_PREFIX`
2. **两个爬虫里与该主题站点相关的解析函数**（例如 `extract_source_date`、`extract_360_item`、
   `extract_bing_item`、`parse_list_rows` 的行容器启发式）——目标站变了就要跟着改。
3. 输出根目录名（就是 `topic_config.OUTPUT_ROOT` 那一项）。
4. **`extract_config.py`**（③ 的抽取契约）：指标词族 `A101_TERMS`、数值/动词正则、
   地区别名 `REGION_ALIASES`、LLM 的 `SYSTEM_PROMPT` / `LLM_SCHEMA` / `LLM_MODEL` /
   `LLM_TRIGGER_MODE`。换指标（不只 A101）就改这里，改完重跑 ③ 即可、**不用重抓网页**。

**不需要碰 `crawler_common.py`。** 这正是把它拆出去的目的：通用机械一份，主题差异各管各的。
不同主题的输出根互相独立，因此**当日序号互不干扰**（`resolve_run_dir` 只扫本主题的输出根，
`A/A2` 的第一次爬取同样叫 `-1`）。

### 一个已知限制

各主题的配置模块都叫 `topic_config`，`sys.path` 又是全局的，因此
**同一个 Python 进程里不能同时加载两个主题**（后加载的会拿到先加载的配置）。
按本文档的用法（每个脚本一次独立进程）不会遇到；若你以后想写一个
"一次跑完所有主题"的调度脚本，得改成子进程调用，或者把配置模块改成
带主题名的唯一名字（如 `topic_config_a1.py`）。

本目录的 `README.md` 本身也可以照抄给新主题，改动里面的路径与关键词即可。
