"""
科技金融指数研究 · 一键按序跑（A/A1 根目录入口）

按流水线顺序调用各子目录脚本，省去记命令：①采集 → ②抓取 → ⑤OCR → ④清洗 → ③抽取 → ③验报告。

- 默认只跑【处理链】（fetch → ocr → clean → extract → verify），**不跑采集器**——
  采集受百度 IP 限流，通常单独多轮跑。要一并跑采集加 `--collect`。
- `--only <key...>` 只跑指定步；`--skip <key...>` 跳过指定步；`--limit N` 透传给支持它的步。
- 各步为**子进程**（与其它脚本一样"只通过文件耦合"，不 import 业务模块）；任一步非 0 退出即中止。

步 key：collect-search / collect-official / fetch / ocr / clean / extract / verify

用法：
  python run_all.py                     # 处理链：fetch→ocr→clean→extract→verify
  python run_all.py --collect           # 先跑两个采集器，再跑处理链
  python run_all.py --only clean extract
  python run_all.py --skip ocr --limit 5
  python run_all.py --list              # 只打印将按序执行的步骤
"""

import argparse
import subprocess
import sys
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parent   # 本文件就在 A/A1 根，即主题目录

# 顺序即执行顺序；limit=True 的步支持 `--limit N`
STEPS = [
    {"key": "collect-search",   "label": "①-a 搜索引擎采集", "script": "collect/TechFin-bug.py",           "limit": True},
    {"key": "collect-official", "label": "①-b 官方源采集",   "script": "collect/techfin_authoritative.py", "limit": False},
    {"key": "fetch",            "label": "② 二级抓取",        "script": "fetch/techfin_fetch_body.py",      "limit": True},
    {"key": "ocr",              "label": "⑤ OCR 补全",        "script": "fetch/techfin_ocr.py",             "limit": True},
    {"key": "clean",            "label": "④ 语料清洗",        "script": "clean/techfin_curate.py",          "limit": False},
    {"key": "extract",          "label": "③ 数值抽取",        "script": "extract/techfin_extract_points.py", "limit": True},
    {"key": "verify",           "label": "③验 数据点报告",    "script": "report/verify_points.py",          "limit": False},
]
DEFAULT_KEYS = ["fetch", "ocr", "clean", "extract", "verify"]
VALID_KEYS = [s["key"] for s in STEPS]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="A/A1 一键按序跑")
    ap.add_argument("--collect", action="store_true",
                    help="连同两个采集器一起跑（默认不跑；采集受百度限流，通常单独多轮跑）")
    ap.add_argument("--only", nargs="*", choices=VALID_KEYS, metavar="KEY",
                    help=f"只跑指定步，可选：{', '.join(VALID_KEYS)}")
    ap.add_argument("--skip", nargs="*", choices=VALID_KEYS, metavar="KEY", default=[],
                    help="跳过指定步")
    ap.add_argument("--limit", type=int, help="透传给支持它的步（fetch/ocr/extract/collect-search）")
    ap.add_argument("--list", action="store_true", help="只打印将按序执行的步骤，不执行")
    return ap.parse_args(argv)


def selected_keys(args):
    if args.only:
        keys = list(args.only)
    else:
        keys = (["collect-search", "collect-official"] if args.collect else []) + DEFAULT_KEYS
    return [k for k in keys if k not in set(args.skip or [])]


def main(argv=None):
    args = parse_args(argv)
    keys = selected_keys(args)
    steps = [s for s in STEPS if s["key"] in keys]     # 按 STEPS 原始顺序

    print("=" * 60)
    print("一键按序跑 · 将执行：")
    for s in steps:
        extra = "  [--limit N]" if (args.limit and s["limit"]) else ""
        print(f"  · {s['label']:16s} → {s['script']}{extra}")
    print("=" * 60)
    if args.list:
        return 0

    for i, s in enumerate(steps, 1):
        cmd = [sys.executable, str(TOPIC_DIR / s["script"])]
        if args.limit and s["limit"]:
            cmd += ["--limit", str(args.limit)]
        print(f"\n######## [{i}/{len(steps)}] {s['label']} ########")
        print("$ " + " ".join(cmd))
        rc = subprocess.call(cmd, cwd=str(TOPIC_DIR))
        if rc != 0:
            print(f"\n[中止] 第 {i} 步 `{s['key']}` 退出码 {rc}；后续步骤不再执行。")
            return rc
    print(f"\n[完成] {len(steps)} 步全部成功。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
