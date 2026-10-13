"""
科技金融指数研究 · ② 二级抓取：正文 + 文字型附件（三段流水线的第二段）

职责：从①产出的 URL 语料（tech_fin_news.csv / techfin_official.csv）出发，对每条链接
二次请求，抓取 **HTML 正文** 与 **文字型附件（PDF/doc/docx/xls/xlsx，不含扫描件 OCR）**，
把结果落进【跨轮持久缓存】body_cache/。

为什么单独成段、且缓存放输出根而不放运行目录：
  - 抽取规则（③ techfin_extract_points.py）会高频迭代；把"抓取"与"抽取"解耦，
    改规则时**不必重抓政府网站**（抓取最贵、最易被限流）。缓存跨轮复用正是为此。
  - 与①一样，本脚本**不 import 任何业务脚本**，只读①产出的 CSV、只写自己的产物，
    通过文件耦合（见 README 的依赖图）。

依赖（缺省均可降级，不阻断流程）：
  pip install trafilatura pdfplumber python-docx openpyxl xlrd olefile
  - trafilatura 缺失 → 降级 BeautifulSoup(html.parser) 正文启发式（bs4 已随①安装）
  - python-docx 缺失 → 用 zipfile 读 word/document.xml 正则兜底（零依赖）
  - pdfplumber 缺失 → PDF 正文留空并标记，不静默丢
  本机限制：无 brotli，故请求头沿用 crawler_common.get_random_headers（只声明 gzip,deflate），
  不要自定义 Accept-Encoding。

用法：
  python techfin_fetch_body.py --limit 30        # 先抓小切片看效果
  python techfin_fetch_body.py                   # 抓全部（现有语料 400+ 条，约 20 分钟）
  python techfin_fetch_body.py --kinds official  # 只抓官方源语料
  python techfin_fetch_body.py --refresh         # 忽略缓存全量重抓
"""

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib import robotparser

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---- 引导：通用工具在仓库根，而本脚本在 A/A1/。Python 只把【脚本自身目录】
# ---- 放进 sys.path，父目录不会自动可见，故向上逐级找 crawler_common.py。
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

# （配置目录已由上面的主题目录引导加入 sys.path）

import topic_config as cfg  # noqa: E402
from crawler_common import (  # noqa: E402
    append_progress,
    blocking_reason,
    get_random_headers,
    guess_encoding,
    resolve_output_root,
    resolve_run_dir,
    run_logging,
)

# 抓取状态里被视为"已成功、可跳过重抓"的一档
OK_STATUS = "ok"

# 本轮索引表列（body_cache 的清单，便于人工核对每条抓成什么样）
INDEX_COLS = [
    "article_id", "status", "http_status", "page_type",
    "body_chars", "body_method",
    "n_attachments", "n_attach_ok", "n_attach_scanned",
    "fetch_url", "title", "fetch_time",
]


# ==================== 惰性依赖（缺失即降级，不阻断） ====================

def _try_import(name):
    """尝试导入可选依赖，失败返回 None（调用方据此走降级路径）。"""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


# ==================== 小工具 ====================

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def pick_url(row):
    """抓取用链接：优先 real_url，其次 url（与 crawler_common._link_col 同义）。
    注意跳过缺失值——两库列不一致，concat 后会出现 NaN，而 str(nan)=="nan" 会被误当链接。"""
    for c in ("real_url", "url"):
        v = row.get(c)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ("nan", "none"):
            return s
    return ""


def host_of(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _norm_block(text):
    """规整多行文本：压缩行内空白、合并多余空行（保留段落换行）。"""
    if not text:
        return ""
    text = text.replace("\u3000", " ").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _cjk_count(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def _decode_bytes(raw):
    """把二进制尽力解成文本：先 utf-8，再 gb18030，最后忽略错误。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _sniff(head8):
    """按 magic bytes 判真实文件类型，不迷信后缀。"""
    if head8.startswith(b"%PDF"):
        return "pdf"
    if head8.startswith(b"PK\x03\x04"):
        return "zip"                      # docx/xlsx/pptx 都是 zip 容器
    if head8.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole2"                     # 老式 .doc / .xls
    return "unknown"


def atomic_write_bytes(path, data):
    """先写 .tmp 再 os.replace，避免中断留下半个文件。"""
    tmp = str(path) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def atomic_write_text(path, text):
    atomic_write_bytes(path, (text or "").encode("utf-8"))


# ==================== 礼貌抓取：按主机限速 + robots ====================

def polite_wait(host, host_state):
    """同一主机两次请求间隔 ≥ HOST_MIN_INTERVAL（带抖动），避免打爆政府站。"""
    last = host_state.get(host, 0.0)
    gap = cfg.HOST_MIN_INTERVAL - (time.time() - last)
    if gap > 0:
        time.sleep(gap + 0.3 * (hash(host) % 3))
    host_state[host] = time.time()


def robots_allowed(url, session, robots_cache):
    """查 robots.txt（每主机缓存一次）。未启用或取不到 robots 时一律放行。"""
    if not cfg.ROBOTS_ENFORCE:
        return True
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    rp = robots_cache.get(base)
    if rp is None:
        rp = robotparser.RobotFileParser()
        try:
            r = session.get(base + "/robots.txt",
                            headers=get_random_headers(referer=base + "/"),
                            timeout=10)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])              # 取不到 robots → 视为允许
        robots_cache[base] = rp
    try:
        return rp.can_fetch("*", url)
    except Exception:
        return True


def fetch_html(url, session, host_state, robots_cache, timeout=15):
    """
    抓取单个网页。返回 (resp, None) 或 (None, 失败原因)。

    不做 blocking_reason 判定——那要在确认是 HTML 之后（PDF 响应体不含 <html>,
    会被误判为解码失败）。编码用 guess_encoding 探测，绝不写死 utf-8。
    """
    if not url:
        return None, "空链接"
    if not robots_allowed(url, session, robots_cache):
        return None, "robots 禁止"
    host = host_of(url)
    last_err = ""
    for attempt in range(3):
        polite_wait(host, host_state)
        try:
            resp = session.get(url, headers=get_random_headers(referer=url),
                               timeout=timeout, allow_redirects=True)
        except requests.RequestException as e:
            last_err = f"请求异常：{type(e).__name__}"
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, last_err
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        resp.encoding = guess_encoding(resp)
        return resp, None
    return None, last_err


# ==================== 正文抽取 ====================

def _bs4_main_text(html):
    """BeautifulSoup 兜底：去脚本/导航/页脚，优先 article/main，否则取 body。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer",
                     "aside", "form", "noscript"]):
        tag.decompose()
    for container in soup.find_all(["article", "main"]):
        t = container.get_text("\n", strip=True)
        if _cjk_count(t) > 200:
            return t
    body = soup.body or soup
    return body.get_text("\n", strip=True)


def extract_main_text(html):
    """
    抽取网页正文。返回 (正文文本, method)。trafilatura 优先，缺失/空则 bs4 兜底。
    """
    if not html:
        return "", "empty"
    traf = _try_import("trafilatura")
    if traf is not None:
        try:
            text = traf.extract(html, output_format="txt", include_comments=False,
                                include_tables=True, favor_recall=True)
            if text and _cjk_count(text) > 50:
                return _norm_block(text), "trafilatura"
        except Exception:
            pass
    try:
        text = _bs4_main_text(html)
    except Exception:
        return "", "empty"
    return _norm_block(text), ("bs4" if _cjk_count(text) > 50 else "empty")


# ==================== 附件发现 / 下载 / 解析 ====================

def find_attachments(html, base_url):
    """从 <a href> 里找文字型附件。返回 [{url, filename, ext}]（按 url 去重）。"""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "#")):
            continue
        full = urljoin(base_url, href)
        path = urlparse(full).path.lower()
        ext = os.path.splitext(path)[1]
        if ext not in cfg.ATTACHMENT_EXTS:
            continue
        if full in seen:
            continue
        seen.add(full)
        fname = os.path.basename(path) or ("attachment" + ext)
        out.append({"url": full, "filename": fname, "ext": ext})
        if len(out) >= cfg.MAX_ATTACHMENTS_PER_PAGE:
            break
    return out


def download_attachment(att, session, dest_dir, host_state, robots_cache):
    """下载附件到 dest_dir，返回含本地路径与字节数的字典（含 status）。"""
    url = att["url"]
    rec = {"url": url, "filename": att["filename"], "ext": att["ext"],
           "path": "", "sha1": "", "bytes": 0, "status": ""}
    if not robots_allowed(url, session, robots_cache):
        rec["status"] = "robots"
        return rec
    host = host_of(url)
    polite_wait(host, host_state)
    try:
        resp = session.get(url, headers=get_random_headers(referer=url),
                           timeout=30, stream=True, allow_redirects=True)
    except requests.RequestException as e:
        rec["status"] = f"异常:{type(e).__name__}"
        return rec
    if resp.status_code != 200:
        rec["status"] = f"HTTP {resp.status_code}"
        return rec
    chunks, total = [], 0
    try:
        for chunk in resp.iter_content(65536):
            total += len(chunk)
            if total > cfg.BODY_MAX_BYTES:
                resp.close()
                rec["status"] = "超限"
                return rec
            chunks.append(chunk)
    except requests.RequestException as e:
        rec["status"] = f"读取异常:{type(e).__name__}"
        return rec
    data = b"".join(chunks)
    if len(data) < cfg.ATTACH_MIN_BYTES:
        rec["status"] = "过小(疑错误页)"
        return rec
    sha1 = hashlib.sha1(data).hexdigest()
    # 用真实类型纠正后缀（政府站常见 .doc 实为 zip/docx）
    kind = _sniff(data[:8])
    real_ext = {"pdf": ".pdf", "zip": att["ext"] if att["ext"] in (".docx", ".xlsx") else ".zip",
                "ole2": att["ext"]}.get(kind, att["ext"])
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{sha1[:12]}{real_ext}")
    atomic_write_bytes(path, data)
    rec.update({"path": path, "sha1": sha1, "bytes": len(data), "status": "ok",
                "kind": kind})
    return rec


def _text_from_pdf(path):
    pdfplumber = _try_import("pdfplumber")
    if pdfplumber is None:
        return "", "none(缺pdfplumber)", False
    try:
        with pdfplumber.open(path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
    except Exception as e:
        return "", f"pdf异常:{type(e).__name__}", False
    text = _norm_block("\n".join(pages))
    n_pages = max(len(pages), 1)
    # 扫描件识别：正文密度极低 → 判定为图片型 PDF（不做 OCR，只标记，让缺口可见）
    scanned = (len(re.sub(r"\s", "", text)) / n_pages) < 20
    return text, "pdfplumber", scanned


def _text_from_zip(path):
    """zip 容器：按内部结构区分 docx / xlsx，分别抽文本。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "word/document.xml" in names:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
                xml = xml.replace("</w:p>", "\n").replace("</w:tr>", "\n")
                text = unescape(re.sub(r"<[^>]+>", "", xml))
                return _norm_block(text), "zip-docx", False
            if any(n.startswith("xl/") for n in names):
                return _text_from_xlsx(path)
    except Exception as e:
        return "", f"zip异常:{type(e).__name__}", False
    return "", "none(未知zip)", False


def _text_from_xlsx(path):
    openpyxl = _try_import("openpyxl")
    if openpyxl is None:
        return "", "none(缺openpyxl)", False
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    lines.append("\t".join(cells))
        return _norm_block("\n".join(lines)), "openpyxl", False
    except Exception as e:
        return "", f"xlsx异常:{type(e).__name__}", False


def _text_from_ole2(path, ext):
    """老式 OLE2：.xls 用 xlrd；真 .doc 纯 Python 难解，标记为不支持。"""
    xlrd = _try_import("xlrd")
    if xlrd is not None and ext in (".xls", ".et"):
        try:
            wb = xlrd.open_workbook(path)
            lines = []
            for sheet in wb.sheets():
                for r in range(sheet.nrows):
                    cells = [str(c.value) for c in sheet.row(r) if str(c.value).strip()]
                    if cells:
                        lines.append("\t".join(cells))
            return _norm_block("\n".join(lines)), "xlrd", False
        except Exception:
            pass
    return "", "none(真OLE2.doc暂不支持)", False


def _text_from_rtf(raw):
    text = _decode_bytes(raw)
    text = re.sub(r"\\'([0-9a-fA-F]{2})",
                  lambda m: bytes([int(m.group(1), 16)]).decode("gb18030", "ignore"),
                  text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = text.replace("{", "").replace("}", "")
    return _norm_block(text)


def extract_attachment_text(path, ext):
    """抽附件文本。返回 (text, method, is_scanned)。按 magic bytes 分派。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError as e:
        return "", f"读取失败:{type(e).__name__}", False
    kind = _sniff(head)
    if kind == "pdf":
        return _text_from_pdf(path)
    if kind == "zip":
        return _text_from_zip(path)
    if kind == "ole2":
        return _text_from_ole2(path, ext)
    # 非二进制：RTF / Word 存成的 HTML / 纯文本
    try:
        raw = open(path, "rb").read()
    except OSError:
        return "", "none", False
    head_txt = raw[:4096].decode("latin-1", "ignore")
    if head_txt.lstrip().startswith("{\\rtf"):
        return _text_from_rtf(raw), "rtf", False
    low = head_txt.lower()
    if "<html" in low or "msoNormal".lower() in low or "<?xml" in low:
        return _norm_block(_bs4_main_text(_decode_bytes(raw))), "html", False
    return "", "none", False


# ==================== 逐条处理 ====================

def process_one(row, session, cache_dir, host_state, robots_cache, refresh):
    """
    处理一条语料：抓 HTML → 抽正文 → 找附件 → 下载 → 抽附件文本 →
    组装缓存 JSON 并原子落盘。返回 (索引行 dict, status)。
    """
    aid = str(row["article_id"])
    url = pick_url(row)
    bucket = os.path.join(cache_dir, aid[:2])
    json_path = os.path.join(bucket, f"{aid}.json")
    os.makedirs(bucket, exist_ok=True)

    fetch_url = url
    final_url = ""
    http_status = ""
    page_type = "html"
    body_text, body_method = "", "empty"
    status = "failed"
    attachments = []

    resp, reason = fetch_html(url, session, host_state, robots_cache)
    if resp is None:
        status = "blocked" if ("robots" in reason or "反爬" in reason) else "failed"
        print(f"    [失败] {reason} ← {url[:80]}")
    else:
        final_url = resp.url
        http_status = resp.status_code
        ctype = (resp.headers.get("Content-Type", "") or "").lower()
        is_pdf = ("application/pdf" in ctype) or urlparse(final_url).path.lower().endswith(".pdf")
        if is_pdf:
            page_type = "pdf"
            att_dir = os.path.join(cache_dir, "attach", aid)
            os.makedirs(att_dir, exist_ok=True)
            sha1 = hashlib.sha1(resp.content).hexdigest()
            path = os.path.join(att_dir, f"{sha1[:12]}.pdf")
            atomic_write_bytes(path, resp.content)
            body_text, body_method, scanned = extract_attachment_text(path, ".pdf")
            attachments.append({"url": final_url, "filename": os.path.basename(
                urlparse(final_url).path) or "inline.pdf", "ext": ".pdf",
                "path": path, "sha1": sha1, "bytes": len(resp.content),
                "status": "ok", "text_path": path[:-4] + ".txt",
                "text_chars": len(body_text), "text_method": body_method,
                "is_scanned": scanned})
            atomic_write_text(path[:-4] + ".txt", body_text)
            status = OK_STATUS if body_text else "no_body"
        else:
            blocked = blocking_reason(resp)
            if blocked:
                status = "blocked"
                print(f"    [拦截] {blocked} ← {url[:80]}")
            else:
                body_text, body_method = extract_main_text(resp.text)
                # 找附件并下载
                att_dir = os.path.join(cache_dir, "attach", aid)
                for att in find_attachments(resp.text, final_url):
                    rec = download_attachment(att, session, att_dir, host_state, robots_cache)
                    if rec.get("status") == "ok":
                        txt, tmethod, scanned = extract_attachment_text(
                            rec["path"], rec["ext"])
                        txt_path = os.path.join(att_dir, rec["sha1"][:12] + ".txt")
                        atomic_write_text(txt_path, txt)
                        rec.update({"text_path": txt_path, "text_chars": len(txt),
                                    "text_method": tmethod, "is_scanned": scanned})
                    else:
                        rec.update({"text_path": "", "text_chars": 0,
                                    "text_method": "", "is_scanned": False})
                    attachments.append(rec)
                status = OK_STATUS if body_text else "no_body"

    n_ok = sum(1 for a in attachments if a.get("status") == "ok")
    n_scan = sum(1 for a in attachments if a.get("is_scanned"))
    meta = {k: str(row.get(k, "") or "") for k in (
        "article_id", "title", "url", "real_url", "source", "date",
        "keyword", "level3_code", "level3_name", "unit",
        "region_code", "region_name")}
    meta["kind"] = str(row.get("kind", "") or "")

    obj = {
        "article_id": aid,
        "status": status,
        "http_status": http_status,
        "page_type": page_type,
        "fetch_url": fetch_url,
        "final_url": final_url,
        "body_text": body_text[:500000],       # 防御性截断，正常政府页远小于此
        "body_method": body_method,
        "body_chars": len(body_text),
        "attachments": attachments,
        "meta": meta,
        "fetch_time": now_str(),
    }
    atomic_write_text(os.path.join(bucket, f"{aid}.txt"), body_text)
    atomic_write_text(json_path, json.dumps(obj, ensure_ascii=False, indent=1))

    index_row = {
        "article_id": aid, "status": status, "http_status": http_status,
        "page_type": page_type, "body_chars": len(body_text),
        "body_method": body_method, "n_attachments": len(attachments),
        "n_attach_ok": n_ok, "n_attach_scanned": n_scan,
        "fetch_url": fetch_url, "title": meta["title"], "fetch_time": obj["fetch_time"],
    }
    return index_row, status


# ==================== 主流程 ====================

def load_ok_ids(progress_path):
    """
    读续跑台账，返回**已成功（status=ok）**的 plan_key 集合。

    与 crawler_common.load_progress 的区别：后者返回全部记录（含 failed/blocked），
    若用它做跳过判据，一次网络抖动导致的失败会被永久跳过、永不补抓。这里只跳 ok，
    失败的会在重跑时自动重试。
    """
    if not os.path.exists(progress_path):
        return set()
    try:
        df = pd.read_csv(progress_path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return set()
    if not {"source", "plan_key", "status"}.issubset(df.columns):
        return set()
    return set(df.loc[df["status"] == OK_STATUS, "plan_key"])


def load_targets(out_root, kinds=("search", "official"), limit=None, offset=0):
    """读两个累积总库，拼成待抓清单（按 article_id 去重）。"""
    frames = []
    for kind in kinds:
        path = out_root / cfg.ACCUMULATOR_FILES[kind]
        if not path.exists():
            print(f"[跳过] 累积总库不存在：{path}")
            continue
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
        df["kind"] = kind
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    # 两库列不一致（官方库没有 real_url / level3_code 等），concat 会补出 NaN——
    # 必须在【合并之后】再 fillna，否则 missing 列残留 NaN 会被 pick_url 当成 "nan"。
    df = df.fillna("")
    df = df.drop_duplicates(subset=["article_id"], keep="first")
    df["target_url"] = df.apply(pick_url, axis=1)
    df = df[df["target_url"].str.strip().ne("")]
    if offset:
        df = df.iloc[offset:]
    if limit:
        df = df.iloc[:limit]
    return df.reset_index(drop=True)


def load_curated_queue(out_root):
    """
    ④ 产出的"优先补抓队列"：curated_corpus.csv 里【相关但尚无正文】的文章
    + curated_uncertain.csv 里【官方泛化标题待确认】的文章。返回 article_id 集合。
    用于 --from-curated：让 ② 只抓高相关的几十条，不再盲抓整库。
    """
    ids = set()
    p1 = out_root / cfg.CURATED_ACCUMULATOR
    if p1.exists():
        try:
            df = pd.read_csv(p1, dtype=str, encoding="utf-8-sig").fillna("")
            if "article_id" in df.columns:
                if "has_body" in df.columns:
                    no_body = ~df["has_body"].astype(str).str.lower().isin(["true", "1"])
                    ids |= set(df.loc[no_body, "article_id"])
                else:
                    ids |= set(df["article_id"])
        except Exception:
            pass
    p2 = out_root / cfg.CURATED_UNCERTAIN
    if p2.exists():
        try:
            df = pd.read_csv(p2, dtype=str, encoding="utf-8-sig").fillna("")
            if "article_id" in df.columns:
                ids |= set(df["article_id"])
        except Exception:
            pass
    return {i for i in ids if str(i).strip()}


def run_once(run_dir, paths, cache_dir, targets, refresh=False):
    print("=" * 60)
    print(f"{cfg.TOPIC_LABEL} · ② 二级抓取（正文 + 文字型附件）")
    print("=" * 60)
    print(f"待抓条数：{len(targets)}")
    print(f"缓存目录：{cache_dir}")
    print(f"产物目录：{run_dir}")

    progress_path = os.path.join(cache_dir, cfg.BODY_PROGRESS)
    done = load_ok_ids(progress_path)
    if done and not refresh:
        print(f"续跑台账：{progress_path}（已成功条数 {len(done)}，将跳过；失败条目会重试；--refresh 可全量重抓）")

    session = requests.Session()
    host_state, robots_cache = {}, {}
    index_rows = []
    stats = {"ok": 0, "no_body": 0, "blocked": 0, "failed": 0, "error": 0,
             "skipped": 0, "attach_ok": 0, "scanned": 0}

    def flush_index():
        pd.DataFrame(index_rows, columns=INDEX_COLS).to_csv(
            paths["index"], index=False, encoding="utf-8-sig")

    interrupted = False
    try:
        for i, row in targets.iterrows():
            aid = str(row["article_id"])
            json_path = os.path.join(cache_dir, aid[:2], f"{aid}.json")
            if not refresh and aid in done and os.path.exists(json_path):
                stats["skipped"] += 1
                continue
            try:
                index_row, status = process_one(
                    row, session, cache_dir, host_state, robots_cache, refresh)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                status = "error"
                index_row = {"article_id": aid, "status": status, "title": str(row.get("title", "")),
                             "fetch_url": pick_url(row), "fetch_time": now_str()}
                print(f"    [异常] {type(e).__name__}: {e}")
            index_rows.append(index_row)
            flush_index()                      # 先落盘
            append_progress(progress_path,    # 后记台账（台账 ⟸ 数据）
                            [("body", aid, status, index_row.get("n_attachments", 0), now_str())])
            stats[status] = stats.get(status, 0) + 1
            stats["attach_ok"] += index_row.get("n_attach_ok", 0) or 0
            stats["scanned"] += index_row.get("n_attach_scanned", 0) or 0
            print(f"[{i + 1}/{len(targets)}] {status:8s} "
                  f"正文 {index_row.get('body_chars', 0):>6} 字 | "
                  f"附件 {index_row.get('n_attachments', 0)}"
                  f"（成功 {index_row.get('n_attach_ok', 0)}） | {aid}")
    except KeyboardInterrupt:
        interrupted = True
        print("\n[中断] 收到 Ctrl-C；已抓数据已随过程增量落盘。")

    flush_index()
    _print_summary(stats, len(targets), interrupted)
    return stats


def _print_summary(stats, total, interrupted):
    print("\n" + "-" * 60)
    print(f"[汇总] 待抓 {total} 条")
    print(f"  成功 {stats['ok']} | 无正文 {stats['no_body']} | 被拦截 {stats['blocked']}"
          f" | 失败 {stats['failed'] + stats['error']} | 跳过(已抓) {stats['skipped']}")
    print(f"  附件下载成功 {stats['attach_ok']} 个，其中扫描件(未OCR) {stats['scanned']} 个")
    if interrupted:
        print("  [注意] 本轮中断；重跑会跳过已成功条目、自动补缺口。")
    print("  [提示] 扫描件 PDF 不做 OCR（按方案约定），正文为空的多为 JS 渲染页，"
          "缺口在 points_validation.md 里可见。")
    print("-" * 60)


def artifact_paths(run_dir):
    p = cfg.BODY_PREFIX
    return {
        "log": os.path.join(run_dir, p + "log"),
        "index": os.path.join(run_dir, p + "index.csv"),
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{cfg.TOPIC_LABEL} · ② 二级抓取（正文 + 文字型附件）")
    ap.add_argument("--output-dir",
                    help="输出根目录（覆盖 topic_config.OUTPUT_ROOT）")
    ap.add_argument("--kinds", nargs="*", default=["search", "official"],
                    help="抓哪些语料：search / official（默认两者）")
    ap.add_argument("--limit", type=int, default=None, help="本轮最多抓前 N 条")
    ap.add_argument("--offset", type=int, default=0, help="跳过前 N 条（分批切片）")
    ap.add_argument("--refresh", action="store_true",
                    help="忽略缓存与续跑台账，全量重抓")
    ap.add_argument("--from-curated", action="store_true",
                    help="只抓 ④ 挑出的待抓队列（相关但无正文 + 泛化标题待确认），"
                         "不再盲抓整库；需先运行 techfin_curate.py")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    topic_dir = _TOPIC_DIR
    out_root = resolve_output_root(topic_dir, cfg.OUTPUT_ROOT, args.output_dir)
    cache_dir = str(out_root / cfg.BODY_CACHE_DIR)

    targets = load_targets(out_root, kinds=args.kinds,
                           limit=args.limit, offset=args.offset)
    if getattr(args, "from_curated", False):
        queue = load_curated_queue(out_root)
        before = len(targets)
        targets = targets[targets["article_id"].isin(queue)].reset_index(drop=True)
        print(f"[队列] --from-curated：{before} 条 → 只抓 ④ 挑出的 {len(targets)} 条")
    if targets.empty:
        print("[中止] 没有可抓的语料（请先运行 TechFin-bug.py / techfin_authoritative.py）。")
        return

    run_dir = resolve_run_dir(out_root)
    paths = artifact_paths(run_dir)
    try:
        with run_logging(paths["log"]):
            print(f"输出根目录：{out_root}")
            run_once(run_dir, paths, cache_dir, targets, refresh=args.refresh)
    finally:
        print(f"[归档] 本轮产物目录：{run_dir}")


if __name__ == "__main__":
    main()
