# 科技金融指数研究（主题目录 A/A1）

本目录是一个**完整自洽的采集主题**：两个采集脚本 + 一份印证报告脚本 + 主题配置，
产物全部落在本目录下的输出根 `techfin_common_output/`。

通用的爬虫机械（目录分配、日志、日期解析、清洗、导出、累积合并）在**仓库根**
的 `crawler_common.py`，与主题无关，不要在本目录里复制一份。

---

## (a) 四个 py 的依赖关系

**没有任何脚本互相 import。** 三个脚本是对等关系，只通过**文件**耦合——
这点最容易误解，所以先把依赖图看清楚。

```
crawler_common.py    （仓库根；叶子模块：通用工具，不 import 任何本地模块）
topic_config.py      （本目录；叶子模块：本主题的常量，不 import 任何本地模块）
        ▲
        │ import（三个脚本都 import 这两个）
        ├── TechFin-bug.py              搜索引擎爬虫（百度 + Bing + 360）
        ├── techfin_authoritative.py    官方源爬虫（广东科技厅 / 科技部）
        └── verify_corpus.py            印证报告（不采集，只读语料）
```

数据流（脚本之间**只通过文件交互**，谁都不调用谁）：

```
TechFin-bug.py            ──写──→ <OUT_ROOT>/<日期-次数>/search_*
                                   <OUT_ROOT>/tech_fin_news.csv      ← 累积总库

techfin_authoritative.py  ──写──→ <OUT_ROOT>/<日期-次数>/official_*
                                   <OUT_ROOT>/techfin_official.csv   ← 累积总库

verify_corpus.py          ──读──→ 上面两个累积总库（或最新运行目录里的快照）
                          ──写──→ <最新运行目录>/corpus_verification.md
```

> 为什么脚本开头有一段"向上找 `crawler_common.py`"的代码：Python 只把**脚本自身
> 所在目录**放进 `sys.path`，**不会**自动包含父目录。本脚本在 `A/A1/`，而通用工具在
> 仓库根，所以必须先把仓库根加进 `sys.path` 才能 import。那段代码是逐级向上找，
> 不写死层数，因此 `A/A1`、`B/B2` 或更深的目录都能用。

---

## (b) 怎么用

```bash
cd E:\Python\bug\A\A1

python TechFin-bug.py               # ① 采集搜索引擎语料（默认县级走百度，见下）
python techfin_authoritative.py     # ② 采集官方政策源语料
python verify_corpus.py             # ③ 生成交叉印证报告
```

### ① 是"指标化采集"：从检索词到指标 + 地区

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
**重跑会读续跑台账**（`tech_fin_search_progress.csv`），跳过已成功采过的 (源, 检索式)，
只补缺口——省下最稀缺的百度额度。

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

- **① 和 ② 顺序任意，也可以只跑其中一个。** 两者共用同一个输出根，因此共用
  "当日第 N 次爬取"的序号，各自占一个 `-N` 目录：
  只跑 ① 得到 `2026.9.27-1/`（里面只有 `search_*`）；紧接着跑 ② 得到 `2026.9.27-2/`
  （只有 `official_*`）。靠文件名前缀区分是哪个脚本产出的，**目录里不会混淆**。
- **③ 必须在 ①② 都跑过之后。** 它依赖两份语料都存在，缺哪份会提示先跑对应的采集脚本。
- 三个脚本都支持 `--output-dir <路径>` 临时改输出位置；不传就用 `topic_config.py`
  里的 `OUTPUT_ROOT`。相对路径锚定到**本目录**（不是当前工作目录），也可以写绝对路径
  把数据放到仓库外。**注意：换了输出位置，累积总库也跟着走，会在新位置从零开始累积**，
  原来的数据不会自动搬过去。

### 产物长什么样

```
techfin_common_output/
├── tech_fin_news.csv          ← 累积总库（搜索引擎，跨轮读写）
├── techfin_official.csv       ← 累积总库（官方源，跨轮读写）
├── 2026.9.27-1/               ← 第 1 次爬取（TechFin-bug.py）
│   ├── search_delta.csv            本轮采集（去重清洗后）
│   ├── search_cumulative.csv       本轮结束时的累积快照
│   ├── search_links.txt            链接清单，便于人工翻看
│   ├── search_url_mapping.csv      链接映射
│   └── search_log                  本轮完整日志
└── 2026.9.27-2/               ← 第 2 次爬取（techfin_authoritative.py）
    ├── official_delta.csv
    ├── official_cumulative.csv
    ├── official_links.txt
    ├── official_log
    └── corpus_verification.md      印证报告（由 ③ 写入被验证的那一轮）
```

日志由脚本自己写（`*_log`），**不需要再用 shell 重定向 `> run.log`**；
好处是跑崩时的 traceback 也会落进日志。

---

## (c) 累积总库的更新时机与去重规则

**更新时机**：不是流式实时，而是**每轮跑完在写出阶段一次性写入**。
中途崩溃、或本轮没采到数据时，**总库完全不动**，不会被写坏。

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
| 同一文章链接带不同追踪参数（如微信 `&scene=27`） | 会被当作两条 | 链接字符串不同 |
| 360 的相对日期（"1天前"） | 重跑会**改写已存条目的 `date`** | `keep="last"` 的副作用；不新增重复，但会覆盖。`date_url`（链接内嵌日期）稳定，不受影响 |
| 某轮只采到重复条目 | 总库条数不变 | 日志里的"净新增 0 条"就是这个意思 |

每轮日志结尾会同时报两个数，**不要混淆**：

```
[归档] 本轮采集 70 条，其中相对累积总库净新增 3 条 | 累积 111 条
```
搜索引擎每轮会把同样的头条再返回一遍，所以"采集 70 条"可能只带来"净新增 3 条"。

---

## (d) 新增一个主题要做什么

照抄本目录新建一个二级目录（如 `A/A2`），复制 `topic_config.py` 与三个脚本，然后**只改三处**：

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
     `SEARCH_PROGRESS`（续跑台账）、`SEARCH_PREFIX` / `OFFICIAL_PREFIX`、`TOPIC_LABEL`
2. **两个爬虫里与该主题站点相关的解析函数**（例如 `extract_source_date`、`extract_360_item`、
   `extract_bing_item`、`parse_list_rows` 的行容器启发式）——目标站变了就要跟着改。
3. 输出根目录名（就是 `topic_config.OUTPUT_ROOT` 那一项）。

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
