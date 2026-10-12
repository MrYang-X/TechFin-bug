"""
爬虫通用工具库（与主题无关）

被各主题目录（如 A/A1/）下的采集脚本共用。这里只放**任何主题都用得上**的机械：
运行目录与日志、HTTP 请求头、日期解析、文本清洗、去重与导出、累积合并。

**本模块不允许 import 任何主题配置**（那会造成反向依赖）。凡是由主题决定的
东西（检索词、核心词/限定词、噪声源黑名单、采集节奏）一律作为参数传入——
见 is_relevant() / is_noise_source() / random_delay() 的签名。

主题相关配置放在各主题目录的 topic_config.py；各采集脚本用文件头的一段
引导代码把仓库根加入 sys.path 后 import 本模块（脚本在 A/A1/，本模块在仓库根，
Python 只会把脚本自身目录放进 sys.path，父目录不会自动可见）。
"""

import hashlib
import os
import random
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

# ==================== 输出根目录 ====================

# 主题配置没给 OUTPUT_ROOT 时的兜底目录名
DEFAULT_OUTPUT_ROOT = "output"


def resolve_output_root(script_dir, configured, override=None):
    """
    解析输出根目录，返回 pathlib.Path（目录会被创建）。

    优先级：命令行 override > 配置 configured。
    相对路径一律**锚定到脚本所在目录**，而不是当前工作目录——
    这样从任何地方执行（`cd C:\\ && python E:\\...\\TechFin-bug.py`），
    产物都稳定落在主题目录下，不会撒到 CWD 里。绝对路径则原样使用，
    便于把输出放到仓库外。

    configured 为空时用 DEFAULT_OUTPUT_ROOT。
    """
    chosen = override or configured or DEFAULT_OUTPUT_ROOT
    path = Path(chosen)
    if not path.is_absolute():
        path = Path(script_dir) / path
    path.mkdir(parents=True, exist_ok=True)
    return path


# ==================== 运行目录 ====================

# 运行目录名：YYYY.M-D（月、日不补零，N 为当天第 N 次爬取）
RUN_DIR_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})-(\d+)$")


def run_dir_name(now, n):
    """
    手工拼日期串，**不要用 strftime("%-m")**：
    Windows 不支持 %- 指令，会得到 "2025.01.01" 而不是 "2025.1.1"。
    """
    return f"{now.year}.{now.month}.{now.day}-{n}"


def resolve_run_dir(base_dir=".", now=None):
    """
    返回当天的下一个运行目录（形如 2025.1.1-1），创建后返回其路径。

    扫描 base_dir 下已有的同日目录取最大 N 加一；同一主题的多个采集脚本共用
    同一个输出根，因此**天然共用当日序号**（先跑搜索引擎爬虫得 -1，再跑官方源
    爬虫得 -2），不需要额外的计数器文件，靠文件名前缀区分是谁产出的。

    不同主题的输出根互相独立，故各自从 -1 开始，互不干扰。

    已存在的同日目录只用于取最大值，永远新建下一个编号——
    上一轮跑崩留下的半成品目录不会被覆盖。
    """
    now = now or datetime.now()
    max_n = 0
    try:
        names = os.listdir(base_dir)
    except OSError:
        names = []
    for name in names:
        m = RUN_DIR_RE.match(name)
        if not m:
            continue
        y, mo, d, n = (int(g) for g in m.groups())
        if (y, mo, d) == (now.year, now.month, now.day):
            max_n = max(max_n, n)

    path = os.path.join(base_dir, run_dir_name(now, max_n + 1))
    os.makedirs(path, exist_ok=True)
    return path


# ==================== 日志 ====================

class Tee:
    """同时写控制台与日志文件"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for st in self.streams:
            try:
                st.write(data)
                st.flush()
            except UnicodeEncodeError:
                # Windows 控制台默认 cp936，遇到生僻字会抛错；
                # 不能因为打日志把爬虫搞挂，退回可替换编码写
                try:
                    st.write(data.encode(st.encoding or "utf-8",
                                         "replace").decode(
                        st.encoding or "utf-8", "replace"))
                    st.flush()
                except Exception:
                    pass
            except ValueError:
                # 文件已关闭（收尾阶段的 print）
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


@contextmanager
def run_logging(log_path):
    """
    把 stdout 同时写进 log_path，退出时恢复。

    用 try/finally 保证异常时也恢复，并把 traceback 落进日志——
    这解决了"跑崩了就什么都没记下"的问题（外部 shell 重定向做不到这点）。
    """
    original = sys.stdout
    log_file = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(original, log_file)
    try:
        yield log_path
    except BaseException:
        # 先把 traceback 写进日志，再交给上层
        print("\n" + "=" * 60)
        print("[异常] 运行中断，以下为 traceback：")
        traceback.print_exc(file=sys.stdout)
        print("=" * 60)
        raise
    finally:
        sys.stdout = original
        try:
            log_file.close()
        except Exception:
            pass


# ==================== 请求 ====================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
    "Gecko/20100101 Firefox/121.0",
]


def get_random_headers(referer="https://news.baidu.com/"):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        # 不要声明 br：本机未安装 brotli，requests 无法解码 brotli 响应体，
        # resp.text 会静默变成二进制乱码（状态码仍为 200），导致解析出 0 条结果。
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": referer,
    }


def guess_encoding(resp):
    """
    从响应中探测正确编码，返回规范化编码名（如 "gb18030" / "utf-8"）。

    搜索引擎源（百度/Bing/360）固定 UTF-8，旧版对此直接写死 resp.encoding="utf-8"
    并无问题；但政府网站大量使用 GB2312/GBK，写死 UTF-8 会把正文解成乱码。
    依次尝试：
      1) HTTP 头 Content-Type 的 charset；
      2) 响应体前 4KB 的 <meta charset> / <meta http-equiv="Content-Type">；
      3) resp.apparent_encoding（底层 charset-normalizer，已随 requests 安装）；
      4) 兜底 utf-8。

    并把 gb2312/gbk/gb18030 统一归到 **gb18030**——它是 GBK/GB2312 的超集，
    能解全部简体旧编码，避免"用 GB2312 去解 GBK 出乱码"。头里常见错误声明
    latin-1/iso-8859-1 的中文页，则改交给自动猜测。
    """
    # 1) HTTP 头
    ctype = resp.headers.get("Content-Type", "") or ""
    m = re.search(r"charset=[\"']?([\w-]+)", ctype, re.I)
    enc = m.group(1) if m else None

    # 2) HTML meta（同时覆盖 <meta charset="gbk"> 与 content="text/html; charset=gbk"）
    if not enc:
        head = (resp.content or b"")[:4096]
        m = re.search(rb"charset=[\"']?([\w-]+)", head, re.I)
        if m:
            enc = m.group(1).decode("ascii", "ignore")

    # 3) 自动猜测（charset-normalizer）
    if not enc:
        enc = resp.apparent_encoding

    # 4) 兜底
    enc = (enc or "utf-8").strip().lower()

    if enc in ("gb2312", "gbk", "gb18030"):
        return "gb18030"
    if enc in ("iso-8859-1", "latin-1", "latin1", "windows-1252"):
        # 对中文页这是常见的错误声明，交给自动猜测
        return (resp.apparent_encoding or "utf-8").lower()
    return enc


def random_delay(min_delay, max_delay):
    """采集节奏由主题配置决定，故不设默认值（旧版的模块级默认已移除）"""
    time.sleep(random.uniform(min_delay, max_delay))


# ==================== 文本与相关性 ====================

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", str(text))
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return text


def is_relevant(text, keyword, core_terms, qualifier_terms=(),
                anchor_terms=(), required_terms=()):
    """
    判断一条结果是否与当前检索词相关。检索词由主题配置传入，本模块不认识任何
    具体词——所以它对主题无感，新增主题只需改配置。

    - 分词：keyword 按空白切分，要求【全部 token 命中】。检索式是多词的
      （如"南山区 高新技术企业"），搜索引擎按 AND 语义返回，正文里不会出现
      整串字面量，故不能按整串包含判断——否则多词检索式会被静默过滤成 0 条。
      无空格的旧关键词分词后与原"整串包含"完全等价，行为不变。
    - 限定词（qualifier_terms）：必须与任一核心词共现，否则算噪声。
      例：科技金融主题里"发展指数"单独检索会命中教育强国指数、人类发展指数、
      商贸零售上市公司发展指数等无关内容，故要求与"科技金融/金融科技"共现。
    - 锚点（anchor_terms）：非空时，命中后还须与任一锚点共现。供"指标检索式"用
      ——指标词（如"高新技术企业"）过于泛化，要求与主题锚点共现才算数。
      默认空元组 = 不做此约束，旧的调用方行为完全不变。
    - 必需词（required_terms）：非空时，命中后还须命中其中任一词。供"松散匹配的
      源"补齐约束——如 360 对"荔湾区 高新技术企业"只做松散匹配，返回条目的
      标题/摘要里可能没有"荔湾区"，若不剔除就会把外区噪声当成本区结果入库。
      默认空元组 = 不做此约束。
    """
    if not text:
        return False
    tokens = [t for t in keyword.split() if t]
    hit = bool(tokens) and all(t in text for t in tokens)
    if hit and keyword in qualifier_terms:
        hit = any(c in text for c in core_terms)
    if hit and anchor_terms:
        hit = any(a in text for a in anchor_terms)
    if hit and required_terms:
        hit = any(t in text for t in required_terms)
    return hit


def is_noise_source(url, source, noise_domains=(), noise_sources=(),
                    noise_url_suffixes=()):
    """过滤百科词条、行情页、厂商专题页、PDF等非新闻来源（黑名单由主题配置传入）"""
    if source and any(s in source for s in noise_sources):
        return True
    if url:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if any(netloc == d or netloc.endswith("." + d) for d in noise_domains):
            return True
        if any(parsed.path.lower().endswith(s) for s in noise_url_suffixes):
            return True
    return False


def generate_article_id(title, url):
    """去重键：标题 + 链接。同一篇在跨轮/跨源下都得到同一个 id"""
    raw = f"{title}{url}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:16]


def normalize_url(url, base="https://www.baidu.com"):
    """规范化链接：补全相对路径、去除多余空白"""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base + url
    return url


# ==================== 日期解析 ====================

def _safe_date(year, month, day):
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_date(raw, now=None):
    """
    把多种日期写法归一化为 YYYY-MM-DD，无法识别返回 ""。
    覆盖：2025年12月23日 / 4月17日（无年份）/ 2025-12-23 / 2025/12/23 /
          3小时前 / 2天前 / 昨天
    """
    if not raw:
        return ""
    now = now or datetime.now()
    text = clean_text(raw)

    # 2025年12月23日
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return _safe_date(y, mo, d)

    # 2025-12-23 / 2025/12/23 / 2025.12.23
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return _safe_date(y, mo, d)

    # 4月17日（无年份）：按当前年补全，若晚于今天说明是去年
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        candidate = _safe_date(now.year, mo, d)
        if candidate and candidate > now.strftime("%Y-%m-%d"):
            return _safe_date(now.year - 1, mo, d)
        return candidate

    # 相对时间
    m = re.search(r"(\d+)\s*分钟前", text)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*小时前", text)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*天前", text)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    if "昨天" in text:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if "今天" in text or "刚刚" in text:
        return now.strftime("%Y-%m-%d")

    return ""


def looks_like_date(text):
    """
    判断文本是否像日期，用于把日期元素和来源元素区分开。

    要求"含数字"且"含日期特征"——否则会把来源名误判成日期：
    人民日报（含"日"）、经济日报、前瞻产业研究院（含"前"）都不含数字，
    故不会被误判。
    """
    text = text or ""
    if parse_date(text):
        return True
    return bool(re.search(r"\d", text) and re.search(r"年|月|日", text))


# 链接里内嵌的发布日期，两种常见写法：
#   显式分隔： /2026-09-24/ 、 /2026/09/24/
#   紧凑8位： t20250407_193359.html（gov.cn）、 20251223A03X7M00（腾讯）
# 紧凑式用前后数字边界（lookaround）约束，避免在长数字串中间误截。
_URL_DATE_PATTERNS = [
    re.compile(r"/((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?=[/_.-]|$)"),
    re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)"),
]


def date_from_url(url):
    """
    从链接中提取发布日期（YYYY-MM-DD），取不到返回 ""。

    用途：部分搜索引擎只给相对时间（如 360 的"1天前"），而链接本身常内嵌
    真实发布日期（实测 360 的结果有 28/50 可提取）。结果写入独立的 date_url 列，
    不覆盖 date，便于交叉核对。
    """
    if not url:
        return ""
    path = urlparse(url).path or url
    for pat in _URL_DATE_PATTERNS:
        m = pat.search(path)
        if m:
            y, mo, d = (int(g) for g in m.groups())
            got = _safe_date(y, mo, d)
            if got:
                return got
    return ""


# ==================== 反爬拦截识别 ====================

# 硬特征：出现即判定被拦截（正常结果页几乎不会含这些整串）。
# 各源反爬页常以 HTTP 200 伪装成功（搜狗第 2 次请求即 200 + antispider 跳转页），
# 故识别【只看内容特征、不看状态码】——否则会被当作"真没结果"静默丢弃。
_HARD_BLOCK_MARKERS = (
    "百度安全验证", "安全验证", "wappass",                  # 百度
    "请输入验证码", "请输入以下验证码",                     # 百度/通用
    "antispider", "Redirecting to",                        # 搜狗（200 + 跳转验证页）
    "访问过于频繁", "请求过于频繁", "人机验证", "滑动验证",  # 360/通用
)
# 软特征：仅当页面很短时才判定。验证页多为几百字节~几 KB，正常结果页远大于此；
# 加长度闸门是为避免误杀正文里恰好含"验证码"三字的正常新闻。
_SOFT_BLOCK_MARKERS = ("验证码", "verify", "captcha", "安全检测",
                       "异常请求", "请稍后重试")
_SMALL_PAGE_CHARS = 3000


def blocking_reason(resp):
    """
    判断响应是否被反爬拦截或解码异常，返回原因字符串（正常则返回 ""）。

    正常结果页含 <html 与结果节点；被拦截时各源均返回验证/跳转页，
    且常以 HTTP 200 伪装成功。这里只看内容特征，分两档：
      - 硬特征命中即拦（百度"安全验证"、搜狗"antispider"、360"访问过于频繁"等）。
      - 软特征仅在"短页面 + 验证词"时拦，避免误杀正常新闻。
    另外：未安装 brotli 而声明了 br 时，resp.text 会是二进制乱码，一并识别。
    这是为了把"被拦截"和"真的没结果"区分开——否则会静默返回空。
    本模块主题无关，只做纯文本特征，不含任何站点选择器。
    """
    body = resp.text or ""
    if not body:
        return ""
    for marker in _HARD_BLOCK_MARKERS:
        if marker in body:
            return f"触发反爬验证（命中特征：{marker}）"
    if len(body) < _SMALL_PAGE_CHARS:
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        title = m.group(1).strip() if m else ""
        for marker in _SOFT_BLOCK_MARKERS:
            if marker in body or marker in title:
                return f"疑似验证页（短页面 + 特征：{marker}）"
    if "<html" not in body.lower():
        return "响应体非HTML，疑似编码解码失败"
    return ""


# ==================== 清洗与输出 ====================

# 部分来源（如腾讯网、微信公众号）会把域名当标题渲染，
# 例：new.qq.com/rain/a/20251223A03X7M00
_URL_TITLE_RE = re.compile(
    r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\.[A-Za-z]{2,}(?::\d+)?(?:/|$)"
)


def looks_like_url_title(title):
    return bool(_URL_TITLE_RE.match(title or ""))


def make_article(title, url, summary, source, date_raw, keyword, engine):
    """
    构造统一条目（各采集脚本共用，保证 schema 一致）。

    date      = 由 date_raw 归一化的日期
    date_url  = 链接内嵌的日期（可能为空）。单独成列而不覆盖 date，
                便于交叉核对——例如 360 只给相对时间（"1天前"），
                而链接里往往有真实发布日期。
    """
    return {
        "title": title,
        "summary": summary,
        "source": source,
        "date_raw": date_raw,
        "date": parse_date(date_raw),
        "date_url": date_from_url(url),
        "url": url,
        "real_url": "",
        "keyword": keyword,
        "engine": engine,
    }


def clean_dataframe(df):
    if df.empty:
        return df
    original_len = len(df)
    df = df.drop_duplicates(subset=["article_id"], keep="first")
    for col in ["title", "summary", "source"]:
        if col in df.columns:
            df[col] = df[col].apply(clean_text)

    # 标题退化成URL的回退：用摘要开头代替（不额外发请求）
    df["title_is_url"] = df["title"].apply(looks_like_url_title)
    if df["title_is_url"].any():
        mask = df["title_is_url"] & (df["summary"].str.len() > 0)
        df.loc[mask, "title"] = df.loc[mask, "summary"].str[:30]

    df = df[df["title"].str.len() >= 5]
    df["crawl_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[清洗] 原始 {original_len} 条 → 清洗后 {len(df)} 条")
    return df.reset_index(drop=True)


def _link_col(row):
    """兼容新旧字段名：优先真实链接，其次统一 url，最后旧字段 baidu_url"""
    for col in ("real_url", "url", "baidu_url"):
        val = row.get(col)
        if val:
            return val
    return ""


def export_link_list(df, path):
    """
    导出可读性强的链接清单（txt），格式：
        [序号] 标题
              来源 | 日期 | 关键词
              链接
    """
    with open(path, "w", encoding="utf-8") as f:
        for i, row in df.iterrows():
            f.write(f"[{i + 1}] {row['title']}\n")
            meta = " | ".join(filter(None, [row.get("source", ""),
                                           row.get("date", ""),
                                           row.get("keyword", "")]))
            if meta:
                f.write(f"    {meta}\n")
            f.write(f"    {_link_col(row)}\n\n")
    print(f"[输出] 链接清单 → {path}")


def export_url_mapping(df, path):
    """导出链接与真实URL的映射表，便于后续批量解析"""
    url_col = next((c for c in ("url", "baidu_url") if c in df.columns), None)
    cols = [c for c in ["article_id", "title", url_col, "real_url"]
            if c and c in df.columns]
    df[cols].to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[输出] 链接映射 → {path}")


def csv_row_count(path):
    """已有 CSV 的数据行数；文件不存在或读不了返回 0。

    用于区分"本轮采集了多少"与"相对累积总库净新增了多少"——
    两者差别很大：搜索引擎每轮会把同样的头条再返回一遍，
    采集数 71 条可能只带来 22 条净新增。
    """
    if not os.path.exists(path):
        return 0
    try:
        return len(pd.read_csv(path, dtype=str, encoding="utf-8-sig"))
    except Exception:
        return 0


# ==================== 断点续跑台账 ====================

def load_progress(path):
    """
    读取"已完成检索式"台账，返回 {(source, plan_key)} 集合；文件不存在返回空集。

    搜索引擎（尤其百度）按 IP 限流、单轮采不全，需要多轮重跑补齐。若不记录
    哪些检索式已采成功，每轮会把已完成的再打一遍——偏偏百度额度最稀缺，这种
    重打是纯浪费。台账让重跑只补缺口。
    """
    if not os.path.exists(path):
        return set()
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return set()
    if not {"source", "plan_key"}.issubset(df.columns):
        return set()
    return set(zip(df["source"], df["plan_key"]))


def append_progress(path, rows):
    """
    追加若干 (source, plan_key, status, n_items, ts) 到台账（不存在则建表头）。

    逐批落盘而非收尾统一写：某源中途被限流、整轮被 Ctrl-C 时，已完成的记录
    不会丢——这正是断点续跑最需要的性质。`n_items` 是该源该检索式本次新增的
    条数，便于人工核对"跑到哪、有多少"（台账本身不是数据文件，数据在
    search_delta.csv / search_links.txt）。
    """
    if not rows:
        return
    header = not os.path.exists(path)
    df = pd.DataFrame(rows, columns=["source", "plan_key", "status", "n_items", "ts"])
    df.to_csv(path, mode="a", header=header, index=False, encoding="utf-8-sig")


def merge_with_existing(df, path, accumulate=True):
    """
    把本次结果合并进已存在的 CSV（按 article_id 去重，新解析结果优先）。

    去重键是 article_id = md5(标题 + 链接)，keep="last"：完全相同的标题+链接
    不会重复入库，且新一轮的解析结果会覆盖旧的。注意副作用——同一事件被不同
    媒体转载（标题/链接不同）会并存为多条（这是特性，印证研究需要多来源）；
    360 的相对日期每轮解析值会漂移，重跑会改写已存条目的 date。

    搜索引擎按 IP 限流，需要分多次运行补齐；若直接覆盖，一次收获较少的重跑
    会毁掉之前采到的数据。
    """
    if not (accumulate and len(df) and os.path.exists(path)):
        return df
    try:
        old = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception as e:
        print(f"[累积] 读取已有文件失败，按新数据写入：{e}")
        return df
    if not len(old) or "article_id" not in old.columns:
        return df

    # 兼容旧版列名：早期版本把链接列叫 baidu_url，现统一为 url，
    # 否则 concat 会产生两列半空的重复列
    if "url" not in old.columns and "baidu_url" in old.columns:
        old = old.rename(columns={"baidu_url": "url"})

    df = df.astype(str)
    n_new, n_old = len(df), len(old)
    merged = pd.concat([old, df], ignore_index=True)
    merged = merged.drop_duplicates(subset=["article_id"], keep="last")
    # 新旧两版列不完全一致（旧数据没有 engine/date_url 等列），
    # 残留的 NaN 会在 CSV 里变成 "nan" 字样，统一清成空串
    merged = merged.fillna("")
    print(f"\n[累积] 历史 {n_old} 条 + 本次 {n_new} 条 → 合并后 {len(merged)} 条")
    return merged


def print_quality_report(df, stats, title="数据质量"):
    """收尾打印数据质量，便于判断语料是否可用于分析"""
    has_date = df["date"].fillna("").astype(str).str.strip().ne("")
    dated = int(has_date.sum())

    print("\n" + "=" * 60)
    print(f"[{title}]")
    print(f"  语料总数（含历史累积）：{len(df)} 条")
    if stats.get("per_keyword"):
        # 指标化采集的检索式可能多达几百条（地区 × 指标），全列出来没法看。
        # 超过 10 条时只报"有收获的"和收获最多的几条。
        per_kw = stats["per_keyword"]
        hit = [(k, v) for k, v in per_kw.items() if v]
        if len(per_kw) > 10:
            print(f"  本次检索式 {len(per_kw)} 条，其中有收获 {len(hit)} 条")
            top = sorted(hit, key=lambda kv: -kv[1])[:10]
            if top:
                print("  收获最多的检索式：", "，".join(
                    f"{k} {v} 条" for k, v in top))
        else:
            print("  本次各关键词：", "，".join(
                f"{k} {v} 条" for k, v in per_kw.items()))

    if len(df):
        print(f"  日期填充率：{dated}/{len(df)}（{dated / len(df) * 100:.0f}%）")
        if "date_url" in df.columns:
            n_du = int(df["date_url"].fillna("").astype(str).str.strip().ne("").sum())
            if n_du:
                print(f"  其中链接内嵌日期可用：{n_du} 条")
        if "title_is_url" in df.columns:
            n_url = int(df["title_is_url"].astype(str).str.lower()
                        .isin(["true", "1"]).sum())
            print(f"  标题原为URL（已用摘要回退）：{n_url} 条")
        if dated:
            print(f"  日期范围：{df.loc[has_date, 'date'].min()}"
                  f" ~ {df.loc[has_date, 'date'].max()}")

    if stats.get("per_source"):
        print("  本次各来源：", "，".join(
            f"{k} {v} 条" for k, v in stats["per_source"].items()))
    if stats.get("matched_total") is not None:
        print(f"  其中命中检索词：{stats['matched_total']} 条"
              f"（其余为留作交叉比对的条目）")
    if stats.get("engines_merged"):
        print(f"  多源共同命中：{stats['engines_merged']} 条（双源印证信号）")

    print(f"  本次过滤：噪声源 {stats.get('noise_dropped', 0)} 条"
          f" | 不相关 {stats.get('irrelevant_dropped', 0)} 条"
          f" | 采集失败页数 {stats.get('failed_pages', 0)}")
    if stats.get("failed_pages"):
        detail = stats.get("failed_by_source")
        detail = f"（{detail}）" if detail else ""
        print(f"  [注意] 有 {stats['failed_pages']} 页采集失败{detail}"
              "——限流或网络异常，本次未采全；"
              "可隔一段时间重跑，新结果会合并进已有 CSV（不会覆盖）")
    print("=" * 60)
