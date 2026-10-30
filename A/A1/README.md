# 科技金融指数研究（主题目录 A/A1）

本目录是一个**完整自洽的采集主题**，脚本按功能分目录：
`config/`（配置）· `collect/`（① 采集）· `fetch/`（② 抓取 + ⑤ OCR）· `clean/`（④ 清洗）·
`extract/`（③ 抽取）· `report/`（印证 / 质量 / 评估）· `load/`（⑦ 可选入库 + 交互入口）。
流水线 **① → ② → ⑤ → ④ → ③**，⑦ 是可选后处理；产物按生命周期分成两个根：
- **`data/`**：流水线数据根（采集累积库、正文缓存、各阶段运行目录、评估）；
- **`reports/db_load/`**：⑦ 入库报告根（与爬虫数据完全分离，独立 `YYYY.M.D-N` 序号）。

旧的 `techfin_common_output/` 只读保留，新流水线不再写入；`run_all.py` 可按序一键跑主链。

通用的爬虫机械（目录分配、日志、日期解析、清洗、导出、累积合并）在**仓库根**
的 `crawler_common.py`，与主题无关，不要在本目录里复制一份。

---

## (a) 各 py 的依赖关系

没有任何脚本互相 import。 各脚本是对等关系，只通过文件耦合

```
crawler_common.py      （仓库根；叶子模块：通用工具，不 import 任何本地模块）
config/topic_config.py    （叶子模块：本主题常量，不 import 任何本地模块）
config/extract_config.py  （叶子模块：③ 的抽取规则/地区别名/LLM 契约，不 import 本地模块）
config/paths.py           （路径契约：import topic_config + crawler_common，统一数据根/报告根/清单）
config/region_rules.py    （地区识别：import topic_config + extract_config，③④ 共用）
        ▲
        │ import
        ├── collect/TechFin-bug.py             ① 搜索引擎爬虫（百度 + Bing + 360）
        ├── collect/techfin_authoritative.py   ① 官方源爬虫（广东科技厅 / 科技部）
        ├── report/verify_corpus.py            印证报告（不采集，只读语料）
        ├── fetch/techfin_fetch_body.py        ② 二级抓取：正文 + 文字型附件
        ├── fetch/techfin_ocr.py               ⑤ OCR 补全：给扫描件 PDF 补文本
        ├── clean/techfin_curate.py            ④ 语料清洗/提纯（读①②，写 curated_*）
        ├── extract/techfin_extract_points.py  ③ 数值抽取：规则 + LLM
        ├── load/techfin_load_to_db.py         ⑦ CSV → MySQL 入库（读 ③ + db/，可选后处理）
        ├── load/techfin_load_to_db_interactive.py  ⑦ 交互式入口（菜单 + 子进程调用 loader）
        ├── report/verify_points.py            ③验 数据点质量报告（只读）
        └── report/techfin_eval.py             ⑥ 评估闭环：标注模板 + precision/recall（只读）
```

数据流（脚本之间**只通过文件交互**，谁都不调用谁）：

```
collect/TechFin-bug.py            ──写──→ data/<日期-次数>/search_*
                                           data/tech_fin_news.csv             ← 累积总库

collect/techfin_authoritative.py  ──写──→ data/<日期-次数>/official_*
                                           data/techfin_official.csv          ← 累积总库
                                                  │
fetch/techfin_fetch_body.py       ──读①──→ 上面两个累积总库
                                  ──写──→ data/body_cache/                  ← 正文 JSON/TXT + 附件二进制
                                                  │
fetch/techfin_ocr.py              ──读②──→ body_cache/（纯本地，不发请求）
                                  ──写──→ 回写 body_cache/                 ← 扫描件附件补出 .txt
                                                  │
clean/techfin_curate.py           ──读①②─→ 累积总库 + body_cache/（纯本地，不发请求）
                                  ──写──→ data/curated_corpus.csv           ← 文章级干净语料
                                           data/curated_uncertain.csv      ← 官方泛化标题待确认队列
                                           data/curated_dropped.csv        ← 被剔除条目
                                                  │
extract/techfin_extract_points.py ──读②④─→ body_cache/ + curated_corpus.csv
                                  ──写──→ data/techfin_a101_points.csv（证据级，跨轮累积）
                                           data/<日期-次数>/points_resolved.csv（聚合级）
load/techfin_load_to_db.py        ──读③④─→ points_resolved.csv + techfin_a101_points.csv + curated_corpus.csv
                                  ──写──→ MySQL fact_index_value（可选后处理）
                                           reports/db_load/<日期-次数>/load_report.md / load_errors.csv / load_log
report/verify_points.py           ──读③──→ 数据点 CSV ──写──→ data/<最新运行目录>/points_validation.md
report/verify_corpus.py           ──读①②─→ 两个累积总库 ──写──→ data/<最新运行目录>/corpus_verification.md
```

> **脚本可在任意工作目录执行**：每个脚本都向上定位 `crawler_common.py`（仓库根）与
> `config/topic_config.py`（主题目录 A/A1），输出根锚定到 A/A1——不依赖当前工作目录。

> ④ 是「提纯器」不是「必经关卡」：③ 读不到 `curated_corpus.csv` 时行为完全不变（读 body_cache 全量）；
> ② 加 `--from-curated` 才只抓 ④ 挑出的队列。

> 为什么 ②③ 分开：**抓取最贵、最易被限流**。把"抓取"与"抽取"解耦后，改抽取规则（`config/extract_config.py`）
> 只需重跑 ③，**不必重抓政府网站**——`body_cache/` 跨轮复用正是为此。②③ 都是"纯读上游文件、
> 只写自己的产物"，与 `verify_corpus.py` 同款解耦模式。

> 为什么脚本开头有一段"向上找两根锚点"的代码：Python 只把**脚本自身所在目录**放进 `sys.path`，
> 不包含父目录。脚本现在在子目录（如 `collect/`），而通用工具在**仓库根**、配置在**主题目录的
> `config/`**，故脚本逐级向上定位这两处（`crawler_common.py` 与 `config/topic_config.py`），
> 分别加进 `sys.path`，并把**主题目录 A/A1** 作为输出锚点。写成"逐级向上找"故不写死层数，
> `A/A1`、`B/B2` 或更深的目录都能用。

### PyCharm 未解析 `paths` 的原因与解决办法

**现象**：在 PyCharm 中打开 `run_all.py`，`from paths import pipeline_root` 这一行会报两条红标：
`未解析的引用 'paths'` 和 `未解析的引用 'pipeline_root'`。`topic_config`、`extract_config`、
`region_rules`，以及测试里跨目录导入的 `techfin_*` 属于同一类问题。

**原因**：`config/paths.py` 位于 `A/A1/config/`，脚本靠运行时的
`sys.path.insert(0, str(_TOPIC_DIR / "config"))` 才把该目录加入模块搜索路径。PyCharm 的静态分析
不会执行 `sys.path.insert`，`A/A1/config` 又不在项目的源根里；而 `.idea/` 被 `.gitignore` 整体忽略，
在 IDE 里把 `config` 手动标成 Sources root 只对本机有效，无法随仓库分发。

**解决办法**：在这类动态 `import` 语句的**紧邻上一行**加 `# noinspection PyUnresolvedReferences`。

```python
# noinspection PyUnresolvedReferences
from paths import pipeline_root  # noqa: E402
```

```python
# noinspection PyUnresolvedReferences
from paths import (  # noqa: E402
    finish_run_manifest,
    new_pipeline_run,
    pipeline_root,
)
```

该注释只对 PyCharm 的静态检查生效，不改变任何运行时行为。放置规则：

- 必须紧贴被抑制的 import 上一行，中间不要再隔其它注释；`# noqa: E402` 保留在 import 行上。
- 多行 `from ... import (...)` 放在 `from` 行的上一行（不要放进括号里）。
- `try` 块/函数内的惰性导入放在该 import 的上一行，缩进与 import 对齐。
- `crawler_common` 位于仓库根、可正常解析，无需注释。
- 如果本机同时把 `A/A1/config` 标成了 Sources root，这些注释会被提示为「冗余抑制」弱警告；
  仓库统一以注释方案为准，可移除源根或忽略该提示。

新增或搬运脚本时请照此办理。**自检**：`run_all.py` 中的 `from paths import pipeline_root` 不应再有
红标，且 `python run_all.py --list` 仍能正常执行。

### 文件清单（作用 / 输入 / 输出）

| 文件 | 作用 | 输入 | 输出 |
|---|---|---|---|
| `../crawler_common.py`（仓库根） | 通用机械：目录/日志/日期/清洗/导出/累积合并/请求头/反爬识别/编码探测；**主题无关** | —（被各脚本 import） | —（函数库） |
| `config/topic_config.py` | 本主题常量：指标体系 `INDEX_KEYWORDS`、地区 `GUANGDONG_REGIONS`、噪声源、采集节奏、产物契约 | —（被 import） | — |
| `config/extract_config.py` | ③ 抽取契约：指标词族 / 数值·动词正则 / 地区别名 / LLM prompt+schema+model | —（被 import） | — |
| `collect/TechFin-bug.py` | ①-a 搜索引擎采集（百度 / Bing / 360） | 检索计划（`INDEX_KEYWORDS` × `GUANGDONG_REGIONS`） | `search_*` + `tech_fin_news.csv` |
| `collect/techfin_authoritative.py` | ①-b 官方源采集（广东科技厅 / 科技部） | 官方源清单 `SOURCES` | `official_*` + `techfin_official.csv` |
| `fetch/techfin_fetch_body.py` | ② 二级抓取：HTML 正文 + 文字型附件 | ① 累积总库 | `body_cache/` |
| `fetch/techfin_ocr.py` | ⑤ OCR 补全：给扫描件 PDF 补文本（本地 PaddleOCR） | ② 正文缓存 | 写回 `body_cache/`（附件 `.txt` + JSON 字段） |
| `clean/techfin_curate.py` | ④ 语料清洗/提纯：相关性 + 规范化 + 去重 + 分级 | ①②（累积总库 + 正文缓存） | `curated_corpus.csv` / `curated_uncertain.csv` / `curated_dropped.csv` |
| `extract/techfin_extract_points.py` | ③ 数值抽取：规则 + LLM | ②④（正文缓存 + 清洗清单） | `techfin_a101_points.csv` + `points_resolved.csv` |
| `load/techfin_load_to_db.py` | ⑦ CSV → MySQL 入库（可选后处理）：严格校验 + 幂等 upsert | ③ 证据/聚合级 + ④ 文章级 + `db/` 维度表 | MySQL `fact_index_value`；`load_report.md` / `load_errors.csv` / `load_log` |
| `load/techfin_load_to_db_interactive.py` | ⑦ 交互式入库入口：菜单询问数据库/输入/过滤/高级项，默认预演 | 用户输入 + loader | 子进程调用 loader；沿用其报告与错误清单 |
| `report/verify_corpus.py` | 交叉印证报告（只读，不采集） | ① 两份语料 | `corpus_verification.md` |
| `report/verify_points.py` | ③验 数据点质量报告（只读） | ③ 数据点 CSV | `points_validation.md` |
| `report/techfin_eval.py` | ⑥ 评估闭环：标注模板 + precision/recall 报告（只读） | ③ 数据点 CSV + ② 缓存 | `eval/label_precision.csv` / `eval/eval_report.md` |
| `run_all.py` | 一键按序跑（按 ①→②→⑤→④→③→报告 调子进程） | — | — |

> 各脚本参数见 `python <子目录>/<脚本> --help`；`--output-dir <路径>` 可临时改数据根
> （相对路径锚定主题目录）。⑦ loader 另有 `--report-root` 改入库报告根。

---

## (b) 怎么用

```bash
cd E:\Python\bug\A\A1

# —— 一键按序跑（推荐入口；无参数进交互菜单，带参数则按参数执行）——
python run_all.py                     # 无参数 → 交互菜单；一路回车 = 默认处理链
python run_all.py --no-prompt         # 跳过菜单，直接跑默认处理链（脚本/CI）
python run_all.py --collect           # 先跑两个采集器，再跑处理链
python run_all.py --list              # 只看将执行哪些步

# —— ① 列表采集（产出 URL 语料，进累积总库）——
python collect/TechFin-bug.py                 # ①-a 搜索引擎语料（默认县级走百度，见下）
python collect/techfin_authoritative.py       # ①-b 官方政策源语料
python report/verify_corpus.py                # 交叉印证报告（读 ① 的两份语料）

# —— ② 二级抓取：正文 + 文字型附件（落 body_cache/，跨轮复用）——
python fetch/techfin_fetch_body.py --limit 30     # 先抓小切片看效果
python fetch/techfin_fetch_body.py                # 抓全部；可反复重跑，已成功的自动跳过

# —— ⑤ OCR 补全：给扫描件 PDF 补文本（本地 PaddleOCR；首次需先下模型，见 §⑤）——
python fetch/techfin_ocr.py --limit 2             # 先跑 2 个附件看效果
python fetch/techfin_ocr.py                       # 补全部扫描件；可反复重跑，已 OCR 的自动跳过

# —— ④ 语料清洗/提纯：相关性过滤 + 规范化 + 去重 + 分级（纯本地，零请求）——
python clean/techfin_curate.py                    # 生成 curated_corpus.csv / curated_uncertain.csv + 报告
python fetch/techfin_fetch_body.py --from-curated # ② 只抓 ④ 挑出的队列（相关但无正文 + 待确认）

# —— ③ 数值抽取：从正文/附件抽 A101 数据点（读 ④ 清洗清单；缺失则全量）——
python extract/techfin_extract_points.py --extractor rule     # 只跑规则层（零 LLM 成本，可离线）
python extract/techfin_extract_points.py --extractor hybrid   # 规则 + LLM 兜底（默认，需 Claude API）
python report/verify_points.py                                # 数据点质量报告

# —— ⑦ 数据入库/迁移（可选后处理；先执行仓库根 db/ 建库脚本）——
pip install "PyMySQL[rsa]==1.1.2"                             # 或 pip install -r requirements.txt
set TECHFIN_DB_PASSWORD=your_password                         # Windows；Linux/macOS 用 export
python load/techfin_load_to_db.py --dry-run                    # 先预演，不写库
python load/techfin_load_to_db.py                              # 写入默认库 techfin（最新一轮 resolved）
python load/techfin_load_to_db.py --database techfin_test \
    --host 127.0.0.1 --port 3306 --user root --ask-password    # 指定目标库（密码交互输入）
python load/techfin_load_to_db.py \
    --input-file data/2026.10.19-6/points_resolved.csv   # 指定输入
python load/techfin_load_to_db.py --all-runs --only-level3 A101           # 合并所有轮次/只入 A101
python load/techfin_load_to_db.py --allow-partial-batch                   # 当前数据可写入 1 行（2355）
python load/techfin_load_to_db.py --self-test                             # 纯函数自测
python load/techfin_load_to_db.py --dump-mapping                          # 查看指标口径/单位映射
python load/techfin_load_to_db_interactive.py                             # 交互式菜单（默认预演）
```

> **无参数 = 交互菜单**：对 7 个步骤逐个询问 y/n，直接回车采用方括号内默认值
> （fetch/ocr/clean/extract/verify 为 Y，两个采集器为 N），因此一路回车与原有的默认处理链完全等价。
> 菜单末尾可再选择"只打印计划"（等价 `--list`）与统一的 `--limit N`；全部选 N 会直接取消。
> **带任意命令行参数时不会进入菜单**，行为与以前一致；脚本/CI 可加 `--no-prompt` 强制跳过菜单。
> Ctrl+C 或输入 EOF 会取消且不执行任何步骤。

> **②③④ 与①的关系**：② 读 ① 的累积总库、写正文缓存；④ 读 ①②、写清洗清单；③ 读正文缓存 + 清洗清单、写数据点。
> 顺序 ①→②→④→③；②③④ 各自可独立反复重跑（② 靠缓存跳过已抓、③ 靠 LLM 缓存跳过已调、④ 全量幂等重算）。
> **④ 是提纯器不是必经关卡**：③/② 读不到 `curated_*` 时行为完全不变。改抽取规则只动 `config/extract_config.py` 重跑 ③。

### 运行顺序（一条主链 + 旁路）

**主链：`① 采集 → ② 抓正文 → ⑤ OCR → ④ 清洗 → ③ 抽取`**

1. **① 采集**（`collect/TechFin-bug.py` / `collect/techfin_authoritative.py`）：两者**顺序任意、可只跑其一**，
   共用同一个输出根与"当日第 N 次爬取"的序号，各占一个 `-N` 目录
   （只跑前者得 `2026.9.27-1/` 只有 `search_*`；紧接着跑后者得 `2026.9.27-2/` 只有 `official_*`），
   靠文件名前缀区分，**目录里不会混淆**。
2. **② 抓正文**（`fetch/techfin_fetch_body.py`）：读 ① 的累积总库；可反复重跑，已成功的自动跳过。
3. **⑤ OCR 补全**（`fetch/techfin_ocr.py`）：读 ② 缓存，给**扫描件 PDF** 补出文本并写回缓存（纯本地、零请求）。
4. **④ 清洗**（`clean/techfin_curate.py`）：读 ①②，产出干净语料与待确认队列（纯本地、零请求）。
5. **③ 抽取**（`extract/techfin_extract_points.py`）：读 ②④。**④ 是可选的**——读不到 `curated_corpus.csv`
   时降级为读 `body_cache/` 全量、行为完全不变。

**旁路（报告脚本，输入就绪即可随时跑）**：`report/verify_corpus.py` 读 ①②；`report/verify_points.py` 读 ③；
`report/techfin_eval.py` 抽 ③ 的数据点做人工标注评估。

**补抓闭环（提高召回）**：`fetch/techfin_fetch_body.py --from-curated`（只抓 ④ 挑出的"相关但无正文 + 待确认"
队列）→ 重跑 `④` 复判 → 重跑 `③`。

**输出位置**：各脚本都支持 `--output-dir <路径>` 临时改**数据根**，不传就用
`topic_config.DATA_ROOT`（默认 `data/`）。⑦ loader 另有 `--report-root <路径>` 改**入库报告根**
（默认 `reports/db_load/`），报告不再写进爬虫运行目录。
相对路径锚定到**本目录**（不是当前工作目录），也可写绝对路径放到仓库外。
**注意：换了数据根，累积总库（含正文缓存）也跟着走，会在新位置从零开始累积**，原数据不会自动搬过去。
旧的 `techfin_common_output/` 只读保留，新流水线不再写入。

### ①-a 搜索引擎采集是"指标化采集"：从检索词到指标 + 地区

`collect/TechFin-bug.py` 现在采集的不是一堆扁平关键词，而是**指标体系 × 地区口径**的检索计划
（由 `topic_config.INDEX_KEYWORDS` + `GUANGDONG_REGIONS` 展平而来）。`INDEX_KEYWORDS`
已补全 4 个一级维度 / 12 个二级指标 / **35 条三级指标**；但**命令行默认只采 A1**
（`--level2` 默认 `["A1"]`），默认口径是
**A1 三级指标 × 广东省 124 个县级单元 ≈ 1116 条检索式**（每条指标取前 3 条检索式，
见 `QUERIES_PER_REGION`）。

> 为什么默认不全量：百度资讯按 IP 限流。全量 35 指标 × 124 单元会远超额度，必须靠
> `--level2` / `--cities` / `--offset` 分批推进。例如 `--level2 A1 A2 A3` 采完 A 维度，
> `--cities 广州市 深圳市` 按市分区。另可下调 `QUERIES_PER_REGION` 直接减少总量。

```bash
python collect/TechFin-bug.py --list-plan --limit 6      # 零请求，先看清要打哪些检索式与路由
python collect/TechFin-bug.py --cities 广州市 深圳市      # 只采指定地级市（分区推进）
python collect/TechFin-bug.py --offset 0 --limit 20      # 分批切片，绕开百度限流
python collect/TechFin-bug.py --no-region                # 不带地区前缀（此时百度+Bing+360 三源并用）
python collect/TechFin-bug.py --sources baidu            # 临时只用一个源（如先验证百度是否可用）
python collect/TechFin-bug.py --fresh                    # 忽略续跑台账，全量重采
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

**①-b 官方源与覆盖率。** 官方源改用 `guess_encoding` 处理 GBK/GB2312，避免 `apparent_encoding`
误判；新增 `official_progress.csv`（`source/page_url/status/n_items/ts`）按"源+页 URL"断点续跑，
重跑跳过已成功页，`--fresh` 全量重采、`--limit-pages N` 分批。①-a 额外输出
`<运行目录>/coverage_report.json`、`coverage_by_region.csv`、`coverage_latest.json`，
逐检索式标记 `ok/ok_zero/failed/skipped_resume/tripped/not_run`——**未执行/失败不等于该地区没有数据**。

### 产物长什么样

```
A/A1/
├── data/                          ← 流水线数据根（生成物，默认不纳入 git）
│   ├── tech_fin_news.csv          ← ① 累积总库（搜索引擎，跨轮读写）
│   ├── techfin_official.csv       ← ① 累积总库（官方源，跨轮读写）
│   ├── tech_fin_search_progress.csv  ← ① 搜索续跑台账
│   ├── official_progress.csv      ← ① 官方源分页台账（source/page_url/status/n_items/ts）
│   ├── coverage_latest.json       ← ① 最近一轮逐检索式覆盖率汇总
│   ├── coverage_by_region.csv     ← ① 按县级单元的覆盖率明细
│   ├── body_cache/                ← ② 正文缓存（跨轮复用；正文 JSON/TXT + 附件二进制）
│   │   ├── body_progress.csv          ② 续跑台账（source="body", plan_key=<article_id>）
│   │   └── ab/ab12cd34ef56.json       正文+元数据+附件索引（按 article_id 前 2 位分桶）
│   ├── a101_llm_cache/            ← ③ LLM 结果缓存（key=article_id+prompt_version+model）
│   ├── a101_llm_calls.jsonl       ← ③ LLM 调用审计（逐次追加，含 usage/成本/错误）
│   ├── techfin_a101_points.csv    ← ③ 证据级数据点累积总库（一行 = 一个数据点）
│   ├── curated_corpus.csv         ← ④ 文章级干净语料（含 indicator_codes_all/region_from 等）
│   ├── curated_uncertain.csv      ← ④ 官方泛化标题待确认队列（供 ② --from-curated 补抓）
│   ├── curated_dropped.csv        ← ④ 被剔除条目（含原因，便于复核）
│   ├── eval/                      ← ⑥ 评估闭环 + 覆盖率历史
│   └── 2026.9.27-1/               ← 每阶段运行目录（YYYY.M.D-N，靠前缀区分阶段）
│       ├── _run.json                  阶段/脚本/参数/输入输出指纹/状态（目录自描述）
│       ├── coverage_report.json        ① 逐检索式采集状态
│       ├── search_* / official_*       ① 本轮产物
│       ├── body_log / body_index.csv   ② 本轮产物
│       ├── ocr_log / ocr_index.csv     ⑤ 本轮产物（含 low_conf_pages/truncated）
│       ├── curated_*                   ④ 本轮产物
│       ├── points_delta.csv            ③ 证据级
│       ├── points_resolved.csv         ③ 聚合级
│       ├── points_dropped.csv          ③ 被丢弃点审计（指标/口径/单位/子句不符）
│       ├── extraction_report.md        ③ LLM 尝试/成功/失败/token/成本/是否降级
│       └── points_validation.md        ③验 质量报告
├── reports/
│   └── db_load/                   ← ⑦ 入库报告根（独立序号，不与爬虫混合）
│       └── 2026.9.27-1/
│           ├── load_log
│           ├── load_report.md
│           ├── load_errors.csv
│           ├── load_manifest.json     输入 SHA-256/参数/漏斗/目标库（不含密码）
│           └── _run.json
└── techfin_common_output/         ← 旧产物根：只读保留，新代码不再写入
```

日志由脚本自己写（`*_log`），**不需要再用 shell 重定向 `> run.log`**；
好处是跑崩时的 traceback 也会落进日志。每个运行目录的 `_run.json` 记录阶段、脚本、参数、
输入输出 SHA-256 与状态，数据量大后也能一眼分清"这个目录是谁跑出来的"。

### ②③ 二级抓取与数值抽取（新增流水线）

**② `fetch/techfin_fetch_body.py` —— 抓正文与文字型附件。** 对 ① 语料里每条 url 二次请求，
抽 HTML 正文（trafilatura，缺则 bs4 兜底）与**文字型附件**（PDF/doc/docx/xls/xlsx，按 magic bytes
分派解析）。结果落 `body_cache/`，**跨轮复用**（这是"改抽取规则不重抓网页"的关键）。要点：

- **编码**：政府站多为 GBK，用 `crawler_common.guess_encoding` 探测（不再硬编码 UTF-8）。
- **礼貌与合规**：按主机限速（`HOST_MIN_INTERVAL`）+ 遵守 robots.txt（`ROBOTS_ENFORCE`）。
- **扫描件 PDF 不做 OCR**：正文密度过低判为扫描件并标记 `is_scanned`（缺口在报告里可见）。
- **断点续跑**：已成功（status=ok）跳过；失败/被拦的重跑自动重试。
- **可追溯**：`--save-html` 额外保存 `body_cache/raw_html/<aid>.html.gz`，便于换抽取器后重放；
  附件同时记录绝对路径与相对路径（`path_rel/text_rel`），换数据根后仍可定位。

**③ `extract/techfin_extract_points.py` —— 抽 A101 数值数据点。** 读 `body_cache/`（**纯读，不发请求**）：
- **规则层**（零成本）正则匹配"XX 区新增高新技术企业 123 家"等；**LLM 层**（Claude Sonnet 5.5，
  结构化输出）在提到指标时兜底，专门区分 `actual`（实际/累计/现有）与 `planned/target`（拟/计划/目标）。
- **有 LLM 结果时以 LLM 为准**（规则层在真实政府文本上假阳性较多，仅作交叉印证与离线回退）。
- **A103 错标根治**：规则层只产 `A101`，不再继承文章级 `level3_code`；再做指标-口径-单位一致性校验，
  不合格点写入 `<运行目录>/points_dropped.csv`，不静默。
- **地区按数字锚点判定**：取数字前 20/后 10 字窗口；`全市` 映射到父级市码，`query/none` 降为低置信；
  loader 默认拒绝无文本地区证据的县级行。
- **子句级数字绑定**：指标词与数字必须同子句；"分别有/各区"等拆分值标 `aggregation_type=breakdown`，
  不参与主值；"254 家、421 家、57 家"不会再全部算成 A101。
- **年份置信**：`doc` 只取同段落前 300 字内最近的年，不再用全文第一个年；新增
  `year_confidence/year_evidence`，低置信年份不产主值。
- **LLM 失败可见**：缺凭据/认证失败默认退出码 3，不再静默降级；`--allow-rule-fallback` 才允许纯规则继续；
  `a101_llm_calls.jsonl` 记录 usage/延迟/成本/错误，`<运行目录>/extraction_report.md` 汇总。
  LLM 点的 `evidence_snippet` 会在正文/附件中定位，定位不到标 `evidence_verified=0`，
  loader 默认拒绝（`--allow-unverified-evidence` 可放行）。
- 输出两份 CSV：证据级 `techfin_a101_points.csv`（一行 = 一个数据点，含原文 `evidence_snippet`）
  与聚合级 `<运行目录>/points_resolved.csv`（一行 = 一个 地区×年份×指标 格）。
- **列与 SQL 事实表对齐**：聚合级列可直取 `fact_tech_finance_data`（`level3_code`→`index_id`、
  `canonical_value`→`index_value`、`authority`→`data_quality`、`source_article_ids`→`evidence_ref`）。
- **"拟/减少"标记而非丢弃**：`context_type` 为 `planned/target/decline` 的数据点照样入库，
  聚合层默认只把 `actual` 选中主值。抽取规则/地区别名/LLM 契约集中在 `config/extract_config.py`。

**③验 `report/verify_points.py`** 只读数据点 CSV → 写 `points_validation.md`（维度分布、置信度、
地区覆盖、**多源冲突组**、缺口统计、随机抽检样本）。

**依赖**：`pip install trafilatura pdfplumber python-docx openpyxl xlrd olefile anthropic`
（缺哪个就降级：trafilatura→bs4、docx→zipfile、PDF/xls→跳过并标记；缺 `anthropic` 则
`--extractor rule` 纯规则模式可离线跑完全流程）。

### ④ 语料清洗/提纯（`clean/techfin_curate.py`）

纯本地、零请求；把 ① 的原始语料经漏斗收敛成干净语料：
**噪声源 → 空/废标题 → 同链接(去追踪参数)去重 → 相关性提纯 → 地区归一 → 质量分级 → 事件分组标注**。
核心判断：① 里**检索语料已被 `is_relevant` 筛过**，④ 只做规范化/去重/分级；**官方语料是零过滤全量保存**
（446 条里仅约 25-33 条与指标相关），④ 承担相关性提纯主力。

- **相关性打分**：指标词族命中 标题 +3 / 摘要 +2 / 正文 +3 / 锚点 +1 / 地区 +1，`>=3` 判相关（记 `rel_level`/`rel_score`）。
- **官方泛化标题**（含"名单/公示/认定/公布"但无词族、无正文）不硬删也不硬留 → `curated_uncertain.csv` 待 ② 补正文复判。
- **"高企"形容词消歧**（成本/物价…高企）已内置，为铺到 124 县预留。
- **质量分级 `quality_grade` 1-4**（对齐 SQL `data_quality`）：1 年鉴公报 / 2 官方公报 / 3 媒体 / 4 相关待补。
- **指标码不再盲取 `codes[0]`**：多词族命中时按"标题 > 摘要 > 正文"选主指标，全部命中写入
  `indicator_codes_all` 供复核；官方语料缺列时按此回填。
- **地区来源细分**：`region_from=title/summary/body/meta`，标题/摘要的市级口径不会被正文深层区县覆盖。
- **近重复标注**：同 (地区, 指标) 桶内用 64 位 SimHash 比较，汉明距离 ≤3 的后来者标
  `is_near_dup=1 / near_dup_of / near_dup_score`；**只标注、不删除**，多源转载仍保留。
- **链接去重**只删白名单追踪参数（微信 `scene`、`utm_*` 等），保留内容参数与路径、保留原 `url` 可复核；
  **同事件转载并存是多来源特性**，只做 `event_group_id` 标注、不合并。
- 产物 `curated_corpus.csv`（文章级超集视图，含新增审计列）+ `curated_report.md`（漏斗计数、回归哨兵、抽检样本）。
- **反哺**：「相关但无正文」+ `curated_uncertain.csv` = ② 的 `--from-curated` 待抓队列；
  ③ 默认只抽 `curated_corpus.csv` 里提纯通过的文章、用 `quality_grade` 作 `data_quality`、并回填官方缺失的指标/地区列。

### ⑤ OCR 补全（`fetch/techfin_ocr.py`）

政府公报/名单大量是**扫描件 PDF**，② 只标记 `is_scanned=True`、不取内容 → ⑤ 用**本地 PaddleOCR**
把扫描件补出文本，**写回 `body_cache/`**（附件同名 `.txt` + 更新 JSON 字段）。纯本地、零请求。

- **只看扫描件**：挑 `attachments[]` 里 `is_scanned=True` 的 `.pdf`；已 OCR（`ocr_applied`）的跳过，`--refresh` 可重跑。
- **流程**：`pypdfium2` 把每页渲染成图（`OCR_DPI`）→ `PaddleOCR.predict` → 取每页 `rec_texts` 拼接。
- **写回字段**：`text_method="paddleocr"`、`is_scanned=False`、`ocr_applied=True`、`ocr_pages`、`ocr_dpi`、`ocr_avg_conf`；
  新增 `ocr_page_conf`（逐页置信度）、`ocr_low_conf_pages`（<0.8 的页码）、`ocr_truncated`（超过 `OCR_MAX_PAGES`）。
  `ocr_index.csv` 同步输出 `low_conf_pages/truncated`，低置信页进入人工复核队列。
- **下游**：③ 读附件 `.txt` 的逻辑**自动吃到** OCR 文本（无需改 ③）。
- **前置（一次性，模型需下载）**：
  ```bash
  set PADDLE_PDX_MODEL_SOURCE=BOS        # 默认源是 huggingface；BOS 最稳
  python -c "from paddleocr import PaddleOCR; PaddleOCR(lang='ch', ocr_version='PP-OCRv5', device='cpu', text_detection_model_name='PP-OCRv5_mobile_det', text_recognition_model_name='PP-OCRv5_mobile_rec', use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)"
  ```
  模型落到 `~/.paddlex/official_models/`，之后离线可用（可整目录备份）。
- **性能**：默认用 **mobile 轻量模型**（server 版在 CPU 上单页数十秒~分钟，mobile 实测 ~4s/页）；
  `OCR_MAX_PAGES` 限单附件页数（防超大件）。CPU 慢是常态，靠"只处理扫描件 + 只跑一次 + 缓存"控成本。
- **依赖**：`pip install paddleocr paddlepaddle pypdfium2`（本机已装）；缺则脚本给出中文提示。
- **配置原理、踩坑与换机迁移**：见 [`OCR配置与排错.md`](OCR配置与排错.md)（含 3.x ↔ 2.x API 对照与排错对照表）；
  依赖版本清单见仓库根 [`requirements.txt`](../../requirements.txt)。

### ⑥ 评估闭环（`report/techfin_eval.py`）

把"精确"变成**可度量、可复现**的闭环。纯本地、只读数据点 + 缓存；产物落 `<OUT_ROOT>/eval/`
（**固定目录**，标注文件跨轮复用）。

1. `python report/techfin_eval.py --make-template 20` → `eval/label_precision.csv`：抽样数据点 + 证据片段 +
   ±200 字上下文 + 空列 `人工标签`(correct/wrong/partial) / `人工年限` / `备注`。
2. 人工填标 → `python report/techfin_eval.py --score` → `eval/eval_report.md`：整体/分组 **precision**、
   **年份正确率**、标注覆盖率。
3. （可选）`--make-recall N` / `--score-recall`：抽文章通读，估 **recall**。

**要点**：抽样 seed 固定（42），**改 prompt/规则后重跑同一抽样即可对比好坏**（配合 ③ 的 LLM 缓存）；
改 prompt 时记得 **bump `extract_config.PROMPT_VERSION`**，否则 ③ 命中旧 LLM 缓存、改动不生效。

### ⑦ CSV → MySQL 入库（可选后处理，`load/techfin_load_to_db.py`）

**定位**：不属于默认主链。它需要数据库凭据、人工复核报告，**`run_all.py` 不会调用它**；
主链产物准备好后手动执行。

**前置**：

1. 在仓库根按顺序执行数据库脚本：
   `db/00_database.sql → 10_schema.sql → 20_seed_region.sql → 21_seed_index.sql → 40_staging.sql`
   （详见 [`db/README.md`](../../db/README.md)）。
2. 安装驱动：`pip install "PyMySQL[rsa]==1.1.2"`（已写入 `requirements.txt`）。
3. 设置密码：`TECHFIN_DB_PASSWORD`，或运行时加 `--ask-password`。**不要把密码写进命令行或 README。**

**输入**：

- **主输入**：最新一个含 `points_resolved.csv` 的运行目录（当前是 `2026.10.19-6`，不是只有报告的 `-7`）。
  `--input-file` 可指定文件，`--all-runs` 可合并所有轮次。
- **证据级 sidecar**：同运行目录 `points_delta.csv` 或根 `techfin_a101_points.csv`，用于补
  `unit`、`context_type`、`publish_date`、`evidence_snippet`。
- **文章级 lookup**：`curated_corpus.csv`，用于补标题、来源、日期。
- **不读**：`*_progress.csv`、`*_delta.csv`、`*_cumulative.csv`、`*_log`、`.md` 报告、
  `*_links.txt`、`body_cache/**`；根目录与运行目录的 4 组完全重复文件只取一份。

**严格过滤（默认）**：

- 只入 `actual` + 有值 + 指标/单位匹配 + 非假阳性 + 地区一致的行；
- **A103 默认阻断**：当前 23/23 个证据点都是 `count/家`，与 `share/%` 定义不符；
  如未来有合法 A103（`share/%/actual/0—100`），用 `--allow-level3 A103` 显式放开；
- 空值**不写 0**，默认跳过；`--include-missing` 才写 `obs_status='missing'`；
- `planned/target/decline/unknown` 默认跳过；`--include-non-actual` 才写入；
- 拒绝“10 个工作日”、服务企业/名单/孵化器假阳性、补充公布/更名/搬迁批次、
  `全市/全省/其他区县/试验区` 等地区串区、`doc` 低置信年份；
- **新增地区置信门禁**：`region_from=query/none/scope_national` 的县级行默认拒绝
  （`REGION_UNVERIFIED`），`--allow-region-query` 才放行；
- **新增拆分口径门禁**：`aggregation_type=breakdown/subset` 默认拒绝
  （`AGGREGATE_VALUE`），`--allow-breakdown` 才放行；
- **新增年份置信门禁**：`year_confidence < 0.5` 默认拒绝（`YEAR_LOW_CONFIDENCE`）；
- **新增指标状态门禁**：`dim_index.status` 非 `active` 默认拒绝（`INDEX_INACTIVE`）；
- 当前数据严格模式预期：**输入 20 → 通过 0 → 写入 0 → 拒绝 10 / 跳过 10**；
  `--allow-partial-batch` 可写入 2355 这一行。

**数据库与幂等**：

- `--database` 默认 `techfin`；`--host/--port/--user` 可配；密码只从环境变量或交互读取。
- 唯一键 `(region_code, year, level3_code, measure_code, context_type)`，
  `INSERT ... ON DUPLICATE KEY UPDATE`；重复执行不会产生重复行。
- `--upsert-policy overwrite|better|skip`：默认覆盖；`better` 只在质量不差于旧值时更新。
- `--write-mode transaction|chunked`：默认 `transaction`，全部成功才提交；任一行 DB 错误时
  整体回滚（`--on-db-error abort`）。大数据量可切 `chunked`（允许部分写入）。
- `--skip-imported` 按 `file_sha256` 跳过 `import_batch` 中已成功导入的文件；`--force` 强制重导。
- `missing` 行用 `ON DUPLICATE KEY UPDATE id=id`，**不会覆盖已有 present 行**。

**单位**：存实际观测单位（家/户/万元/亿元/%），**不做跨单位换算**；`value_text` 保留原文；
`standard_unit` 在 `dim_index_measure`，下游计算层需要时再归一化。

**报告**：默认写入**独立的入库报告根** `reports/db_load/<YYYY.M.D-N>/`
（与爬虫数据根 `data/` 完全分离，序号也独立）：

```
load_log
load_report.md       漏斗、拒绝/跳过原因、写入明细、警告、字段截断、复现命令
load_errors.csv      row_no/error_code/error_detail/evidence_snippet/row_json/suggestion
load_manifest.json   输入文件/SHA-256/参数快照/维度行数/漏斗统计/目标库（不含密码）
_run.json            阶段/脚本/参数/状态
```

`--report-root` 可改报告根；`--report-dir` 可精确指定某个目录（会覆盖同名报告）。

**常用参数**：

```bash
python load/techfin_load_to_db.py --dry-run                 # 预演
python load/techfin_load_to_db.py --limit 5                 # 只处理前 5 行
python load/techfin_load_to_db.py --only-level3 A101        # 只入 A101
python load/techfin_load_to_db.py --province-code 440000    # 只入广东
python load/techfin_load_to_db.py --include-missing         # 空值写 missing
python load/techfin_load_to_db.py --allow-partial-batch     # 放行补充公布/更名/搬迁批次
python load/techfin_load_to_db.py --allow-approx            # 放行约数
python load/techfin_load_to_db.py --allow-low-confidence-year
python load/techfin_load_to_db.py --allow-region-query       # 放行无文本地区证据的县级行
python load/techfin_load_to_db.py --allow-breakdown          # 放行拆分口径
python load/techfin_load_to_db.py --allow-inactive-index     # 放行 retired/draft 指标
python load/techfin_load_to_db.py --allow-unverified-evidence # 放行未定位到证据的 LLM 点
python load/techfin_load_to_db.py --write-mode chunked       # 允许部分写入
python load/techfin_load_to_db.py --skip-imported            # 按文件 SHA-256 跳过已导入
python load/techfin_load_to_db.py --upsert-policy better
python load/techfin_load_to_db.py --fail-on-error           # 有 hard 拒绝/DB 错误时退出 1
python load/techfin_load_to_db.py --self-test               # 纯函数自测，不连库
python load/techfin_load_to_db.py --dump-mapping            # 打印 DB 中的口径/单位映射
```

**交互式入口**：不想记参数时运行：

```bash
python load/techfin_load_to_db_interactive.py
```

默认预演；逐步询问数据库、输入来源、过滤策略和高级选项；执行前显示完整 loader 命令并二次确认；
密码用 `getpass` 或环境变量读取，不进入命令行；通过子进程调用 loader，报告里的复现命令准确。

选择“最新一轮 / 指定运行目录 / 指定文件”只决定**输入**；报告始终在
`reports/db_load/<YYYY.M.D-N>/` 下新建，不再写回输入目录，也不会覆盖同名 `load_*` 文件。
“合并所有轮次”同样新建入库报告目录；高级选项可用 `--report-dir` 指定固定目录（会覆盖同名报告）。

> 在 PyCharm 的 **Run 窗口** 中，隐藏密码输入可能不可用；请改用 **Terminal**，
> 或先设置环境变量 `TECHFIN_DB_PASSWORD`。脚本会在非交互终端自动改用可见输入并给出提示。

> 默认严格模式当前可能是 **0 行写入**——这是数据质量结论（A103 系统性错配、空值、
> 假阳性、地区串区），不是脚本故障。先看 `load_report.md` 与 `load_errors.csv` 再决定
> 是否用 `--allow-*` 放行。

---

## (c) 累积总库的更新时机与去重规则

**更新时机**：**采集过程中增量写入**——每采完一条检索式，就把"当前已采的全部条目"
落盘一次（本轮增量 `search_delta.csv` + 累积快照 + 累积总库 + 链接清单），而不是跑完
才写。因此**中途 Ctrl-C 也不丢已采数据**。
并且**续跑台账只在数据落盘成功后才记**（台账 ⟸ 数据）——否则会出现"台账记了 ok、
数据却不存在"的不一致，重跑时跳过它 → 数据永久缺失。
（注：`collect/techfin_authoritative.py` 仍是"跑完一次性写"数据，但新增分页进度台账
`official_progress.csv`，重跑按"源+页 URL"跳过已成功页。）

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

**目录分离与版本管理**：流水线数据根 `data/` 与入库报告根 `reports/db_load/` 默认被
`.gitignore` 忽略（体积大、可再生）；如需把根级累积库纳入版本管理，按 `.gitignore`
注释里的白名单改为只跟踪 `data/*.csv`、`data/*.jsonl`。旧 `techfin_common_output/`
只读保留、不跟踪。切换数据根后累积库会从零开始，原目录不会自动搬移。

**数据库入库（⑦）**：以 `(region_code, year, level3_code, measure_code, context_type)` 为幂等键
`INSERT ... ON DUPLICATE KEY UPDATE`，重复跑不会产生重复行；`missing` 行只补空、
**不会覆盖已有 present 行**。

---

## (d) 离线回归（不联网、不连库）

本次改造附带一个可重复的准确度回归，覆盖 A103 强制、地区锚点、子句级数字绑定、
拆分口径、短别名阻断、相关性词距、URL 标题回退、loader 辅助函数：

```bash
python tests/test_accuracy_regression.py
```

`tests/fixtures/` 下是小型只读样本（几篇正文 JSON + 两份小累积库），不含 189MB 附件缓存。
测试不写正式 `data/`；如需手工复现，可用
`--output-dir tests/fixtures/data_cases` 跑 ③。

## (e) 新增一个主题要做什么

照抄本目录新建一个二级目录（如 `A/A2`），**复制整个 `A/A1` 目录结构**（各子目录
`config/ collect/ fetch/ clean/ extract/ report/ load/` + `run_all.py` + README），然后**主要改以下五处**：

1. **`config/topic_config.py`** —— 这是主要工作：
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
   - 产物契约：`DATA_ROOT`（流水线数据根，默认 `data`）、`LOAD_REPORT_ROOT`（入库报告根，
     默认 `reports/db_load`）、`OUTPUT_ROOT`（`DATA_ROOT` 的兼容别名）、`SEARCH_ACCUMULATOR` / `OFFICIAL_ACCUMULATOR`、
     `SEARCH_PROGRESS` / `OFFICIAL_PROGRESS`（续跑台账）、`SEARCH_PREFIX` / `OFFICIAL_PREFIX`、`TOPIC_LABEL`；
     以及 ②③④ 的契约：`BODY_CACHE_DIR` / `BODY_PREFIX` / `BODY_PROGRESS`、
     `POINTS_ACCUMULATOR` / `POINTS_PREFIX` / `LLM_CACHE_DIR` / `LLM_CALLS_LOG`、
     `CURATED_ACCUMULATOR` / `CURATED_UNCERTAIN` / `CURATED_DROPPED` / `CURATED_PREFIX`
2. **两个爬虫里与该主题站点相关的解析函数**（例如 `extract_source_date`、`extract_360_item`、
   `extract_bing_item`、`parse_list_rows` 的行容器启发式）——目标站变了就要跟着改。
3. 数据根目录名（就是 `topic_config.DATA_ROOT` 那一项）。
4. **`config/extract_config.py`**（③ 的抽取契约）：指标词族 `A101_TERMS`、数值/动词正则、
   地区别名 `REGION_ALIASES`、LLM 的 `SYSTEM_PROMPT` / `LLM_SCHEMA` / `LLM_MODEL` /
   `LLM_TRIGGER_MODE`。换指标（不只 A101）就改这里，改完重跑 ③ 即可、**不用重抓网页**。
5. **`load/techfin_load_to_db.py`（可选）**：只改 `--database`/连接默认值与 `--block-level3` 策略；
   指标/单位映射从目标库的 `dim_index_measure` 读取，不硬编码 A101/A103。新主题仍建议共用
   仓库根的 `db/` schema，不要按主题复制一套数据库。

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
