"""
科技金融指数研究 · 一键按序跑（A/A1 根目录入口）

按流水线顺序调用各子目录脚本，省去记命令：①采集 → ②抓取 → ⑤OCR → ④清洗 → ③抽取 → ③验报告。

- 默认只跑【处理链】（fetch → ocr → clean → extract → verify），**不跑采集器**——
  采集受百度 IP 限流，通常单独多轮跑。要一并跑采集加 `--collect`。
- **无任何参数**（例如直接点 IDE 运行按钮）时进入中文交互菜单，对 7 个步骤逐个询问 y/n；
  直接回车取默认值（处理链 5 步 Y、两个采集器 N），一路回车 = 下面默认的处理链。
  菜单末尾还可选择"只打印计划"（等价 `--list`）与统一的 `--limit N`。
  Ctrl+C 或输入 EOF 会安全取消，不执行任何步骤。
- 带任意命令行参数时**不进入菜单**，行为与以前一致；脚本/CI 可加 `--no-prompt` 显式跳过菜单。
- `--only <key...>` 只跑指定步；`--skip <key...>` 跳过指定步；`--limit N` 透传给支持它的步。
- 各步为**子进程**（与其它脚本一样"只通过文件耦合"，不 import 业务模块）；任一步非 0 退出即中止。

步 key：collect-search / collect-official / fetch / ocr / clean / extract / verify

用法：
  python run_all.py                     # 无参数 → 交互菜单；一路回车 = 处理链
  python run_all.py --no-prompt         # 跳过菜单，直接跑默认处理链（脚本/CI）
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
    ap.add_argument("--no-prompt", action="store_true",
                    help="无参数时也跳过交互菜单，直接跑默认处理链（脚本/CI 用）")
    # 说明：--no-prompt 无需专门的运行时分支——它出现时 argv 非空，天然绕过菜单。
    return ap.parse_args(argv)


def selected_keys(args):
    if args.only:
        keys = list(args.only)
    else:
        keys = (["collect-search", "collect-official"] if args.collect else []) + DEFAULT_KEYS
    return [k for k in keys if k not in set(args.skip or [])]


def ask_yes_no(question, default):
    """询问一次 y/n；直接回车返回 default。

    EOFError / KeyboardInterrupt 不在这里捕获，由 main() 统一处理，
    保证任何一步中断都不会留下半套选择并误跑。
    """
    hint = "Y/n" if default else "y/N"
    while True:
        ans = input(f"{question} [{hint}] ").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes", "是"):
            return True
        if ans in ("n", "no", "否"):
            return False
        print("  请输入 y 或 n（直接回车 = 默认值）。")


def ask_limit():
    """询问统一的 --limit；空回车表示不限制。返回正整数或 None。"""
    while True:
        ans = input("是否统一限制每步处理条数 --limit？直接回车 = 不限制，否则输入正整数：").strip()
        if not ans:
            return None
        try:
            n = int(ans)
        except ValueError:
            print("  请输入正整数，或直接回车跳过。")
            continue
        if n <= 0:
            print("  条数必须大于 0。")
            continue
        return n


def build_argv_from_menu():
    """逐步 y/n 选择，返回等价 argv；一个步骤都没选时返回 None。

    默认值与 DEFAULT_KEYS 对齐：处理链 5 步默认 Y、两个采集器默认 N，
    因此一路回车完全等价于 `python run_all.py` 原有的默认行为。
    """
    print("=" * 60)
    print("一键按序跑 · 交互式选择（直接回车 = 方括号内的默认值）")
    print("=" * 60)

    keys = []
    for s in STEPS:
        default = s["key"] in DEFAULT_KEYS
        extra = "（受百度 IP 限流，通常单独多轮跑）" if s["key"] == "collect-search" else ""
        if ask_yes_no(f"{s['label']}（{s['key']}）是否执行？{extra}", default):
            keys.append(s["key"])

    if not keys:
        return None                                  # 全选 N：直接取消

    # 显式列出选中的 key，复用 parse_args 的 choices 校验与 selected_keys 的排序。
    # 绝不能生成裸 "--only"：nargs="*" 的空列表会被 `if args.only:` 当成未指定。
    argv = ["--only", *keys]

    # 只有选中了至少一个支持 --limit 的步骤时才询问
    if any(s["limit"] for s in STEPS if s["key"] in keys):
        n = ask_limit()
        if n is not None:
            argv += ["--limit", str(n)]

    if ask_yes_no("只打印执行计划、不实际运行（--list）？", False):
        argv.append("--list")

    return argv


def main(argv=None):
    # 统一成 list，才能可靠判断"是否完全无参数"
    argv = list(sys.argv[1:]) if argv is None else list(argv)

    if not argv:                                     # 只在完全无参数时进入交互菜单
        try:
            argv = build_argv_from_menu()
        except (EOFError, KeyboardInterrupt):
            print("\n[取消] 未完成选择，未执行任何步骤。")
            return 130
        if argv is None:
            print("\n[取消] 未选择任何步骤，未执行。")
            return 0

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
        try:
            rc = subprocess.call(cmd, cwd=str(TOPIC_DIR))
        except KeyboardInterrupt:
            print("\n[中止] 用户中断，后续步骤不再执行。")
            return 130
        if rc != 0:
            print(f"\n[中止] 第 {i} 步 `{s['key']}` 退出码 {rc}；后续步骤不再执行。")
            return rc
    print(f"\n[完成] {len(steps)} 步全部成功。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
