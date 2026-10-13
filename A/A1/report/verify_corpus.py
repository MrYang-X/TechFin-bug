"""
科技金融指数研究 —— 语料交叉印证报告

把两份语料放在一起比对，回答"搜索引擎语料在各时间段是否覆盖充分、有无系统性遗漏"。

语料来源：默认在本主题的**输出根目录**下自动取最新一轮运行目录里的累积快照
    <OUT_ROOT>/<日期-次数>/search_cumulative.csv    搜索引擎语料（百度 + Bing + 360）
    <OUT_ROOT>/<日期-次数>/official_cumulative.csv  官方源语料（广东科技厅/科技部）
若一个运行目录都没有，则回退读输出根目录下的累积总库。
可用 --output-dir 指定别的输出根（相对路径锚定到本脚本所在目录）。

匹配规则：链接精确相等，或标题归一化后（去标点、去来源后缀）相似度 ≥ 阈值。

输出：控制台摘要 + corpus_verification.md，**写进被验证的那一轮目录**。
"""

import argparse
import difflib
import os
import re
import sys
from pathlib import Path

import pandas as pd

# ---- 引导：通用工具在仓库根，而本脚本在 A/A1/（原因见 TechFin-bug.py 同段注释）
# 向上找两个锚点：仓库根（crawler_common.py）与主题目录（config/topic_config.py）。
# 脚本进了子目录后，"脚本自身目录"不再是配置所在，故必须显式定位这两处。
_REPO_ROOT = _TOPIC_DIR = None
for _p in Path(__file__).resolve().parents:
    if _REPO_ROOT is None and (_p / "crawler_common.py").exists():
        _REPO_ROOT = _p
    if _TOPIC_DIR is None and (_p / "config" / "topic_config.py").exists():
        _TOPIC_DIR = _p
    if _REPO_ROOT and _TOPIC_DIR:
        break
if not (_REPO_ROOT and _TOPIC_DIR):
    raise RuntimeError("找不到 crawler_common.py（仓库根）或 config/topic_config.py（主题目录）")
sys.path.insert(0, str(_REPO_ROOT))             # → crawler_common
sys.path.insert(0, str(_TOPIC_DIR / "config"))  # → topic_config / extract_config

# 本脚本所在目录同样要在 sys.path 上：正常 `python 脚本.py` 时 Python 会自动把
# 脚本目录放进 sys.path[0]，但用 importlib / IDE runner / 测试框架加载时【不会】，
# 那时 import topic_config 会失败。显式补一次，让脚本对启动方式不敏感。
# （配置目录已由上面的主题目录引导加入 sys.path）

import topic_config as cfg  # noqa: E402  （须在 sys.path 引导之后导入）
from crawler_common import (  # noqa: E402
    RUN_DIR_RE,
    clean_text,
    resolve_output_root,
)

# ==================== 配置区 ====================

# 语料快照名、累积总库名、报告名 —— 全部来自主题配置
CUMULATIVE_NAMES = cfg.CUMULATIVE_NAMES
ACCUMULATOR_FILES = cfg.ACCUMULATOR_FILES
REPORT_NAME = cfg.REPORT_NAME

# 标题相似度阈值（0~1）。0.8 实测较稳：能容忍标点/来源后缀差异，
# 又不会把"科技金融"和"金融科技"这种字序不同的两篇错配成同一篇。
SIMILARITY_THRESHOLD = 0.8

# 官方语料默认是【全量保存】的（含大量与科技金融无关的政策新闻），
# 若把全量都算进"仅官方源"，报告里会淹没真正有意义的部分。
# 故默认只把"命中检索词"的官方条目计入统计，同时另行标注全量数字。
OFFICIAL_TOPIC_ONLY = True

# 报告中"两源都有"最多列出多少条
MAX_LIST = 40


# ==================== 语料来源定位 ====================

def _dir_key(name):
    """把运行目录名转成可比较的排序键；不是运行目录则返回 None"""
    m = RUN_DIR_RE.match(name)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def latest_run_dir(kind, out_root):
    """在输出根下找含指定累积快照的最新运行目录；没有则返回 None"""
    best_key, best_dir = None, None
    try:
        names = os.listdir(out_root)
    except OSError:
        return None
    for name in names:
        key = _dir_key(name)
        if key is None:
            continue
        path = os.path.join(out_root, name)
        if not os.path.exists(os.path.join(path, CUMULATIVE_NAMES[kind])):
            continue
        if best_key is None or key > best_key:
            best_key, best_dir = key, path
    return best_dir


def resolve_corpus(kind, out_root):
    """
    返回 (语料文件路径, 所在运行目录或 None)。

    优先取输出根下最新一轮运行目录里的快照；没有运行目录则回退读输出根下的
    累积总库（此时第二个返回值为 None）。两条分支都锚定在 out_root 之下——
    修掉了旧版回退分支返回裸文件名（相对当前工作目录）的不一致。
    """
    run_dir = latest_run_dir(kind, out_root)
    if run_dir:
        return os.path.join(run_dir, CUMULATIVE_NAMES[kind]), run_dir
    return os.path.join(out_root, ACCUMULATOR_FILES[kind]), None


# ==================== 标题归一化与匹配 ====================

# 标题尾部常见的来源/栏目后缀： "_网易订阅"、" - 未央网"、"｜金融时报"、" - NIFD 官方网站"
# 限制在 10 字符以内且不含数字：来源名都很短，而"|凝聚金融资源促进科技创新"
# 这类是正文片段（实测 12 字符），误当后缀剔除会造成不同文章被判为同一篇。
_TITLE_SUFFIX_RE = re.compile(r"[\|｜\-–—_—]+\s*([^|｜\-–—_—\d]{1,10})$")
_NON_WORD_RE = re.compile(r"[\W_]+")   # 非单词字符 + 下划线（\w 本身包含下划线）


def strip_source_suffix(t):
    return _TITLE_SUFFIX_RE.sub("", t)


def normalize_title(title, strip_suffix=False):
    """
    标题归一化，用于跨源比对。

    strip_suffix=False 只去标点/空白；True 额外去掉尾部来源后缀。
    两种形态都保留是因为后缀剔除是启发式的：它能救回"标题_网易订阅"，
    但也可能误删真实内容（实测含下划线的标题会被吃成很短）。
    故匹配时对两种形态都算相似度、取最大——这样剔除只会帮忙，不会致漏。
    """
    t = clean_text(title)
    if strip_suffix:
        t = strip_source_suffix(t)
    t = _NON_WORD_RE.sub("", t)
    return t.lower()


def title_variants(title):
    """返回标题的两种归一化形态（去重后）"""
    full = normalize_title(title, strip_suffix=False)
    core = normalize_title(title, strip_suffix=True)
    return {full, core} if core != full else {full}


def load_corpus(path):
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    df["title"] = df["title"].astype(str)
    return df


def match_corpora(search_df, official_df, threshold=SIMILARITY_THRESHOLD):
    """
    返回 (pairs, only_search_idx, only_official_idx)。

    pairs 为 [(搜索行号, 官方行号, 匹配方式, 相似度)]。
    依次尝试：链接精确 → 标题归一化精确 → 标题相似度（两种形态取最大）。
    """
    norm_to_idx = {}
    norms = []
    url_to_idx = {}
    for idx, row in official_df.iterrows():
        for v in title_variants(row["title"]):
            if v:
                norm_to_idx.setdefault(v, idx)
                norms.append(v)
        url = str(row.get("url", "")).strip()
        if url:
            url_to_idx.setdefault(url, idx)

    pairs, only_search = [], []
    matched_official = set()

    for s_idx, s_row in search_df.iterrows():
        s_url = str(s_row.get("url", "")).strip()
        variants = title_variants(s_row["title"])

        hit, how, score = None, "", 0.0
        if s_url and s_url in url_to_idx:
            hit, how, score = url_to_idx[s_url], "链接一致", 1.0
        else:
            for v in variants:                       # 归一化后完全一致
                if v and v in norm_to_idx:
                    hit, how, score = norm_to_idx[v], "标题一致", 1.0
                    break
            if hit is None:
                best_v, best_r = None, 0.0
                for v in variants:                   # 相似度，取两种形态最大
                    if not v:
                        continue
                    close = difflib.get_close_matches(v, norms, n=1,
                                                      cutoff=threshold)
                    if close:
                        r = difflib.SequenceMatcher(None, v, close[0]).ratio()
                        if r > best_r:
                            best_v, best_r = close[0], r
                if best_v is not None:
                    hit, how, score = norm_to_idx[best_v], "标题相似", best_r

        if hit is None:
            only_search.append(s_idx)
        else:
            matched_official.add(hit)
            pairs.append((s_idx, hit, how, round(score, 3)))

    only_official = [i for i in official_df.index if i not in matched_official]
    return pairs, only_search, only_official


# ==================== 报告 ====================

def year_of(value):
    m = re.match(r"(\d{4})", str(value or ""))
    return m.group(1) if m else ""


# 政策文件名：《...》
_DOC_RE = re.compile(r"《([^》]{4,60})》")

# 覆盖度对比用的主题词。注意官方源极少用"科技金融"字面词来命名
# （实测 84 条搜索语料里"科技金融"命中 76 次，而 600 条官方标题里只有 4 次），
# 故对比时除字面词外还列了几个官方口径常用的近义表述。
TOPIC_TERMS = ["科技金融", "金融科技", "科创", "科技型中小企业", "科技成果转化",
               "高新技术企业", "研发经费", "研发投入", "R&D"]


def doc_names(df):
    """提取标题与摘要中《》引用的政策文件名"""
    names = set()
    for col in ("title", "summary"):
        if col in df.columns:
            for t in df[col]:
                names.update(_DOC_RE.findall(str(t)))
    return names


def diagnose(search_df, official_df):
    """
    说明"两源交集"到底有多少——把 0 匹配的原因摆出来，
    而不是只给一个数字让读者猜是没匹配上还是真没有。
    """
    su = {str(x).strip() for x in search_df.get("url", []) if str(x).strip()}
    ou = {str(x).strip() for x in official_df.get("url", []) if str(x).strip()}
    sd, od = doc_names(search_df), doc_names(official_df)

    # 最高标题相似度：看离阈值到底差多远
    onorm = [normalize_title(t) for t in official_df["title"]]
    best = 0.0
    for st in search_df["title"]:
        sn = normalize_title(st)
        if not sn or not onorm:
            continue
        close = difflib.get_close_matches(sn, onorm, n=1, cutoff=0.3)
        if close:
            best = max(best, difflib.SequenceMatcher(None, sn, close[0]).ratio())

    return {
        "url_inter": len(su & ou),
        "doc_inter": len(sd & od),
        "doc_search": len(sd),
        "doc_official": len(od),
        "best_title_sim": best,
    }


def build_report(search_df, official_df, pairs, only_search, only_official,
                 diag=None, search_path=None, official_path=None):
    search_total = len(search_df)
    official_total = len(official_df)

    # 官方侧用于统计的子集（默认只看命中检索词的，见 OFFICIAL_TOPIC_ONLY）
    if OFFICIAL_TOPIC_ONLY and "keyword" in official_df.columns:
        topic_mask = official_df["keyword"].astype(str).str.strip().ne("")
        official_scope = official_df[topic_mask]
        only_official_scoped = [i for i in only_official
                                if i in set(official_scope.index)]
    else:
        official_scope = official_df
        only_official_scoped = only_official

    lines = []
    lines.append("# 语料交叉印证报告\n")
    lines.append(f"- 搜索引擎语料：`{search_path}` —— {search_total} 条")
    lines.append(f"- 官方政策源语料：`{official_path}` —— {official_total} 条")
    lines.append(f"- 匹配规则：链接一致，或标题归一化后相似度 ≥ {SIMILARITY_THRESHOLD}\n")

    lines.append("## 总体\n")
    lines.append(f"- **两源都有**：{len(pairs)} 条")
    lines.append(f"- **仅搜索源**：{len(only_search)} 条")
    lines.append(f"- **仅官方源**：{len(only_official_scoped)} 条"
                 f"（全量口径下为 {len(only_official)} 条）\n")

    # 结论：两源交集究竟是不是真的为空
    if diag:
        lines.append("### 为什么两源交集这么少\n")
        lines.append(f"- 链接完全相同的条目：**{diag['url_inter']} 条**")
        lines.append(f"- 引用的政策文件名（《...》）交集：**{diag['doc_inter']} 个**"
                     f"（搜索源共 {diag['doc_search']} 个，官方源共 "
                     f"{diag['doc_official']} 个）")
        lines.append(f"- 两源最相似的标题对，相似度为 **{diag['best_title_sim']:.3f}**"
                     f"（阈值 {SIMILARITY_THRESHOLD}）\n")
        if diag["url_inter"] == 0 and diag["best_title_sim"] < SIMILARITY_THRESHOLD:
            lines.append("> **结论：两源在文章层面是不相交的，这是真实结果而非匹配失败。**")
            lines.append("> 两份语料是**互补**关系而非重叠关系——搜索引擎语料是媒体对"
                         "科技金融的报道，官方语料是政策与会议通稿，二者很少是同一篇文章。")
            lines.append("> 因此本报告的价值在于**覆盖度与时间线的对照**（见下表），"
                         "而不是“同一篇被两源都报”的逐一核对。\n")

    lines.append("## 主题词覆盖对比\n")
    lines.append("| 主题词 | 搜索源命中 | 官方源命中 |")
    lines.append("|---|---|---|")
    for term in TOPIC_TERMS:
        ns = int(search_df["title"].astype(str).str.contains(term, regex=False).sum())
        if "summary" in search_df.columns:
            ns += int(search_df["summary"].astype(str)
                      .str.contains(term, regex=False).sum())
        no = int(official_df["title"].astype(str).str.contains(term, regex=False).sum())
        lines.append(f"| {term} | {ns} | {no} |")
    lines.append("\n> 注意官方源标题几乎不用“科技金融”这类字面词（多用会议/政策命名），"
                 "故其命中数天然偏低，不能据此判断官方源“不相关”。\n")

    # 两源都有
    lines.append("## 两源都有（可逐一核对）\n")
    if not pairs:
        lines.append("_无 —— 两源在文章层面不相交，见上文说明_\n")
    else:
        lines.append("| 搜索引擎标题 | 官方源标题 | 匹配 | 相似度 |")
        lines.append("|---|---|---|---|")
        for s_idx, o_idx, how, score in pairs[:MAX_LIST]:
            st = str(search_df.loc[s_idx, "title"])[:44].replace("|", "/")
            ot = str(official_df.loc[o_idx, "title"])[:44].replace("|", "/")
            lines.append(f"| {st} | {ot} | {how} | {score} |")
        if len(pairs) > MAX_LIST:
            lines.append(f"\n_（仅列出前 {MAX_LIST} 条，共 {len(pairs)} 条）_")
        lines.append("")

    # 按年统计
    lines.append("## 按年覆盖情况\n")
    lines.append("| 年份 | 搜索源 | 官方源(全量) | 官方源(命中检索词) | 两源都有 | 仅搜索源 |")
    lines.append("|---|---|---|---|---|---|")

    sy = search_df["date"].astype(str).str[:4]
    oy_all = official_df["date"].astype(str).str[:4]
    oy = official_scope["date"].astype(str).str[:4]
    pair_s = set(p[0] for p in pairs)
    os_set = set(only_search)

    years = sorted({y for y in list(sy) + list(oy_all) if re.match(r"\d{4}$", str(y))})
    for y in years:
        n_s = int((sy == y).sum())
        n_oa = int((oy_all == y).sum())
        n_o = int((oy == y).sum())
        n_both = sum(1 for i in pair_s if str(search_df.loc[i, "date"])[:4] == y)
        n_os = sum(1 for i in os_set if str(search_df.loc[i, "date"])[:4] == y)
        lines.append(f"| {y} | {n_s} | {n_oa} | {n_o} | {n_both} | {n_os} |")
    lines.append("")

    # 指标 / 地区覆盖（A1 指标化采集新增的列）。旧语料没有这些列、或列全为空时
    # 整节跳过——那种情况下显示一张空表只会让人误以为"没采到"。
    if "level3_name" in search_df.columns:
        sub = search_df[search_df["level3_name"].fillna("").astype(str)
                        .str.strip().ne("")]
        if len(sub):
            lines.append("## 各三级指标覆盖\n")
            lines.append("| 三级指标 | 条数 | 覆盖县级单元数 |")
            lines.append("|---|---|---|")
            for name, n in sub["level3_name"].value_counts().items():
                regs = None
                if "region_name" in sub.columns:
                    r = sub.loc[sub["level3_name"] == name, "region_name"]
                    regs = r[r.fillna("").astype(str).str.strip().ne("")].nunique()
                lines.append(f"| {name} | {n} | "
                             f"{regs if regs is not None else '-'} |")
            lines.append("")
            if "region_name" in sub.columns:
                got = sub["region_name"].fillna("").astype(str).str.strip()
                got = set(got[got.ne("")])
                total = len(cfg.guangdong_regions())
                if total:
                    lines.append(f"**地区覆盖**：{len(got)}/{total} 个县级单元有语料。"
                                 "县级新闻语料天然稀疏，大量单元为 0 是数据的客观"
                                 "状况，不等于采集失败——先看上文日志的源覆盖率"
                                 "区分二者。\n")

    # 语料构成
    lines.append("## 语料构成\n")
    if "engine" in search_df.columns:
        dist = search_df["engine"].replace("", "(空)").value_counts().to_dict()
        lines.append("**搜索引擎语料（engine）**："
                     + "，".join(f"{k} {v} 条" for k, v in dist.items()) + "\n")
    dist = official_df["source"].replace("", "(空)").value_counts().to_dict()
    lines.append("**官方源语料（source）**："
                 + "，".join(f"{k} {v} 条" for k, v in dist.items()) + "\n")

    return "\n".join(lines)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · 语料交叉印证："
                    f"比对搜索引擎语料与官方政策源语料")
    ap.add_argument("--output-dir",
                    help="输出根目录（覆盖 topic_config.OUTPUT_ROOT）。"
                         "相对路径锚定到本脚本所在目录")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print("=" * 60)
    print(f"{cfg.TOPIC_LABEL} · 语料交叉印证报告")
    print("=" * 60)

    topic_dir = _TOPIC_DIR
    out_root = resolve_output_root(topic_dir, cfg.OUTPUT_ROOT, args.output_dir)
    print(f"输出根目录：{out_root}")

    search_path, search_dir = resolve_corpus("search", out_root)
    official_path, official_dir = resolve_corpus("official", out_root)

    print(f"搜索源语料：{search_path}"
          + ("" if search_dir else "（回退：输出根下的累积总库）"))
    print(f"官方源语料：{official_path}"
          + ("" if official_dir else "（回退：输出根下的累积总库）"))

    search_df = load_corpus(search_path)
    official_df = load_corpus(official_path)
    if search_df is None or official_df is None:
        missing = [f for f, d in ((search_path, search_df),
                                  (official_path, official_df)) if d is None]
        print(f"\n[错误] 缺少语料文件：{'、'.join(missing)}")
        print("       请先运行 TechFin-bug.py 与 techfin_authoritative.py")
        return

    pairs, only_search, only_official = match_corpora(search_df, official_df)
    diag = diagnose(search_df, official_df)

    print(f"\n搜索源 {len(search_df)} 条 | 官方源 {len(official_df)} 条")
    print(f"  两源都有：{len(pairs)} 条")
    print(f"  仅搜索源：{len(only_search)} 条")
    print(f"  仅官方源：{len(only_official)} 条（官方为全量保存，含大量无关政策新闻）")
    print(f"\n  交集诊断：链接相同 {diag['url_inter']} 条 | "
          f"政策文件名交集 {diag['doc_inter']} 个 | "
          f"最高标题相似度 {diag['best_title_sim']:.3f}（阈值 {SIMILARITY_THRESHOLD}）")
    if not pairs and diag["url_inter"] == 0:
        print("  => 两源在文章层面不相交，属真实结果而非匹配失败：")
        print("     搜索引擎语料是媒体报道，官方语料是政策/会议通稿，二者互补而非重叠。")
        print("     报告的价值在覆盖度与时间线对照，而非逐篇核对。")

    # 报告写进被验证的目录：两份语料分属不同轮次时，取较新的那一轮
    candidates = []
    for d in (search_dir, official_dir):
        if not d:
            continue
        key = _dir_key(os.path.basename(os.path.normpath(d)))
        candidates.append((key or (0, 0, 0, 0), d))
    target_dir = max(candidates)[1] if candidates else None
    report_path = (os.path.join(target_dir, REPORT_NAME) if target_dir
                   else os.path.join(out_root, REPORT_NAME))

    report = build_report(search_df, official_df, pairs, only_search,
                          only_official, diag,
                          search_path=search_path, official_path=official_path)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[输出] 印证报告 → {report_path}")

    if pairs:
        print("\n[两源都有] 前5条：")
        for s_idx, o_idx, how, score in pairs[:5]:
            print(f"  · [{how} {score}] {str(search_df.loc[s_idx, 'title'])[:40]}")
            print(f"      官方：{str(official_df.loc[o_idx, 'title'])[:40]}")


if __name__ == "__main__":
    main()
