"""
科技金融指数研究 · 交互式 CSV → MySQL 入库入口（techfin_load_to_db_interactive.py）

职责：用中文菜单逐步询问数据库、输入来源、过滤策略和高级选项，然后通过**子进程**
调用 `techfin_load_to_db.py`。交互层不复制 loader 的校验/写库逻辑，只负责拼参数、
确认和展示结果。

默认行为：
  - 默认进入“预演（--dry-run）”，不会写库；
  - 实际写入必须单独选择，并在执行前二次确认；
  - 密码只从环境变量 `TECHFIN_DB_PASSWORD` 或 `getpass` 读取，不进入 argv、不打印；
  - 子进程调用保证 loader 报告里的“复现命令”是真实的 loader 参数。

用法：
  python A/A1/load/techfin_load_to_db_interactive.py
  python A/A1/load/techfin_load_to_db_interactive.py --help

退出码：
  0   正常完成/用户主动退出
  1   loader 报告 hard 拒绝或 DB 错误（且用户选了 --fail-on-error）
  2   loader 输入/连接/维度错误（自测/映射路径也可能返回 2）
  130 用户 Ctrl+C / EOF 取消
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# ---- 定位主题目录与 loader ----
_TOPIC_DIR = Path(__file__).resolve().parents[1]          # A/A1
_LOADER = Path(__file__).resolve().parent / "techfin_load_to_db.py"
# 实际根由 config/paths.py 解析（见下方 sys.path 引导后）；这里先给新数据根兜底。
_OUT_ROOT = _TOPIC_DIR / "data"
_LOAD_ROOT = _TOPIC_DIR / "reports" / "db_load"
_OUT_OVERRIDE: str | None = None      # 命令行 --output-dir
_REPORT_OVERRIDE: str | None = None   # 命令行 --report-root

# 复用仓库根 crawler_common 的运行目录正则；找不到时退回本地等价正则
_REPO_ROOT = None
for _p in Path(__file__).resolve().parents:
    if (_p / "crawler_common.py").exists():
        _REPO_ROOT = _p
        break
if _REPO_ROOT is not None:
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_TOPIC_DIR / "config"))  # → topic_config / paths
    try:
        from crawler_common import RUN_DIR_RE  # noqa: E402
    except Exception:
        RUN_DIR_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})-(\d+)$")
    try:
        # noinspection PyUnresolvedReferences
        from paths import load_report_root, pipeline_root  # noqa: E402
        _OUT_ROOT = pipeline_root(_TOPIC_DIR)
        _LOAD_ROOT = load_report_root(_TOPIC_DIR)
    except Exception as exc:  # 新路径模块不可用时退回旧根，保证脚本仍能启动
        print(f"[提示] 无法加载 config/paths.py（{exc}），退回旧输出根。")
else:
    RUN_DIR_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})-(\d+)$")

PASSWORD_ENV = "TECHFIN_DB_PASSWORD"
DEFAULT_DB = os.getenv("TECHFIN_DB_NAME", "techfin")
DEFAULT_HOST = os.getenv("TECHFIN_DB_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("TECHFIN_DB_PORT", "3306"))
DEFAULT_USER = os.getenv("TECHFIN_DB_USER", "root")


# ==================== 输入小工具（风格对齐 run_all.py）====================

def ask_yes_no(question: str, default: bool = False) -> bool:
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


def ask_text(question: str, default: str) -> str:
    ans = input(f"{question} [{default}] ").strip()
    return ans or default


def ask_optional(question: str, hint: str = "直接回车 = 不设置") -> str:
    return input(f"{question}（{hint}）：").strip()


def ask_int(question: str, default: int | None, min_value: int = 1) -> int | None:
    hint = f"[{default}]" if default is not None else "[直接回车 = 不限制]"
    while True:
        ans = input(f"{question} {hint} ").strip()
        if not ans:
            return default
        try:
            value = int(ans)
        except ValueError:
            print("  请输入整数。")
            continue
        if value < min_value:
            print(f"  必须 >= {min_value}。")
            continue
        return value


def ask_choice(question: str, options: list[tuple[str, str]], default: int = 1) -> str:
    print(question)
    for i, (_key, label) in enumerate(options, 1):
        mark = " [默认]" if i == default else ""
        print(f"  {i}) {label}{mark}")
    while True:
        ans = input("请选择编号（直接回车 = 默认）：").strip()
        if not ans:
            return options[default - 1][0]
        try:
            idx = int(ans)
        except ValueError:
            print("  请输入编号。")
            continue
        if 1 <= idx <= len(options):
            return options[idx - 1][0]
        print(f"  编号必须在 1—{len(options)} 之间。")


def is_tty() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def read_password() -> str:
    """
    读取 MySQL 密码。

    为什么不能直接 getpass：
      PyCharm 的 Run 窗口等非 TTY 环境里，getpass 的提示可能写到不可见的 stderr，
      或触发 GetPassWarning 后回退到没有提示的 input，用户会以为程序卡死。
    这里先打印并 flush 明确提示，再按 TTY 情况选择隐藏输入或可见输入。
    """
    print("\n--- 密码 ---")
    print("输入 MySQL 密码(在支持的终端里输入不会回显)")
    print("直接回车 = 空密码。", flush=True)

    if is_tty():
        # 提示已经由上面的 print 输出并 flush；这里传空 prompt，避免提示被 stderr 吞掉
        return getpass.getpass("")

    return input("密码: ")


# ==================== 路径与运行目录 ====================

def run_order(name: str) -> tuple[int, int, int, int]:
    m = RUN_DIR_RE.match(name)
    if not m:
        return (0, 0, 0, 0)
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def count_rows(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def discover_runs() -> list[tuple[str, Path, int]]:
    """返回 [(run_name, points_resolved.csv, 数据行数)]，按日期/序号升序。"""
    runs: list[tuple[str, Path, int]] = []
    if not _OUT_ROOT.exists():
        return runs
    for child in _OUT_ROOT.iterdir():
        if not child.is_dir() or not RUN_DIR_RE.match(child.name):
            continue
        p = child / "points_resolved.csv"
        if p.exists():
            runs.append((child.name, p, count_rows(p)))
    runs.sort(key=lambda x: run_order(x[0]))
    return runs


def resolve_input_path(text: str) -> Path:
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    return _TOPIC_DIR / path


def format_cmd(cmd: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


# ==================== 各段问答 ====================

def collect_db_config() -> tuple[list[str], dict[str, str], dict[str, str]]:
    print("\n--- 数据库连接 ---")
    database = ask_text("数据库名", DEFAULT_DB)
    host = ask_text("主机", DEFAULT_HOST)
    port = ask_int("端口", DEFAULT_PORT, 1) or DEFAULT_PORT
    user = ask_text("用户", DEFAULT_USER)

    env: dict[str, str] = {}
    if PASSWORD_ENV in os.environ:
        if ask_yes_no(f"使用环境变量 {PASSWORD_ENV} 中的密码？", True):
            env[PASSWORD_ENV] = os.environ[PASSWORD_ENV]
        else:
            env[PASSWORD_ENV] = read_password()
    else:
        print(f"[提示] 未检测到 {PASSWORD_ENV}；下一步将要求输入密码。")
        env[PASSWORD_ENV] = read_password()

    args = [
        "--database", database,
        "--host", host,
        "--port", str(port),
        "--user", user,
    ]
    info = {"database": database, "host": host, "port": str(port), "user": user}
    return args, env, info


def collect_input_choice() -> tuple[list[str] | None, str, Path | None]:
    print("\n--- 输入来源 ---")
    choice = ask_choice(
        "输入来源：",
        [
            ("latest", "最新一轮 points_resolved.csv"),
            ("run", "指定运行目录"),
            ("file", "指定 points_resolved.csv 文件"),
            ("all", "合并所有轮次（--all-runs）"),
        ],
        default=1,
    )

    args: list[str] = []
    report_dir: Path | None = None

    if choice == "latest":
        runs = discover_runs()
        if not runs:
            print("[提示] 未找到 points_resolved.csv，请先运行 extract/techfin_extract_points.py。")
            return None, "", None
        # 显式钉住“当前最新一轮”作为输入；报告始终由 loader 在入库报告根下新建
        path = runs[-1][1].parent
        args += ["--input-dir", str(path)]
        label = f"最新一轮 {path.name}"
    elif choice == "run":
        runs = discover_runs()
        if not runs:
            print("[提示] 未找到 points_resolved.csv，请先运行 extract/techfin_extract_points.py。")
            return None, "", None
        options = [(name, f"{name}（{rows} 行）") for name, _path, rows in runs]
        selected = ask_choice("选择运行目录：", options, default=len(options))
        path = _OUT_ROOT / selected
        args += ["--input-dir", str(path)]
        label = f"运行目录 {selected}"
    elif choice == "file":
        while True:
            text = ask_optional("points_resolved.csv 路径", "直接回车 = 返回上一步")
            if not text:
                return None, "", None
            path = resolve_input_path(text)
            if path.is_file():
                args += ["--input-file", str(path)]
                label = f"文件 {path}"
                break
            print(f"  [错误] 文件不存在：{path}")
    elif choice == "all":
        args.append("--all-runs")
        label = "合并所有轮次"
        print("[提示] 合并所有轮次没有单一输入目录，默认新建运行目录；可在高级选项指定报告目录。")
    else:
        label = "最新一轮"

    limit = ask_int("最多处理行数 --limit", None, 1)
    if limit is not None:
        args += ["--limit", str(limit)]
        label += f"，limit={limit}"
    return args, label, report_dir


def collect_filter_args() -> tuple[list[str], str]:
    print("\n--- 过滤策略（默认严格）---")
    args: list[str] = []
    labels: list[str] = []

    if ask_yes_no("允许补充公布/更名/搬迁批次（--allow-partial-batch）？", False):
        args.append("--allow-partial-batch")
        labels.append("批次")
    if ask_yes_no("空值写 missing（--include-missing）？", False):
        args.append("--include-missing")
        labels.append("missing")
    if ask_yes_no("写入 planned/target/decline/unknown（--include-non-actual）？", False):
        args.append("--include-non-actual")
        labels.append("非actual")
    if ask_yes_no("允许约数（--allow-approx）？", False):
        args.append("--allow-approx")
        labels.append("约数")
    if ask_yes_no("允许 doc/window 低置信年份（--allow-low-confidence-year）？", False):
        args.append("--allow-low-confidence-year")
        labels.append("低置信年份")
    if ask_yes_no("放行 A103（--allow-level3 A103，默认阻断）？", False):
        print("  [注意] 只有 share/%/actual/0—100 的合法 A103 才应放行。")
        args += ["--allow-level3", "A103"]
        labels.append("A103")

    only = ask_optional("只处理指定三级指标（逗号分隔，如 A101,A102）")
    if only:
        args += ["--only-level3", only]
        labels.append(f"only={only}")
    province = ask_optional("只处理指定省（如 440000）")
    if province:
        args += ["--province-code", province]
        labels.append(f"province={province}")

    return args, "严格" + (" + " + " + ".join(labels) if labels else "")


def collect_advanced_args(default_report_dir: Path | None) -> tuple[list[str], str, Path | None]:
    print("\n--- 高级选项 ---")
    if not ask_yes_no("是否进入高级选项？", False):
        return [], "默认", default_report_dir

    args: list[str] = []
    labels: list[str] = []

    policy = ask_choice(
        "重复键策略：",
        [
            ("overwrite", "overwrite（覆盖）"),
            ("better", "better（质量不差于旧值才更新）"),
            ("skip", "skip（已存在则跳过）"),
        ],
        default=1,
    )
    args += ["--upsert-policy", policy]
    labels.append(f"upsert={policy}")

    batch = ask_int("每批行数 --batch-size", 500, 1) or 500
    args += ["--batch-size", str(batch)]
    labels.append(f"batch={batch}")

    hint = (
        f"直接回车 = {default_report_dir}"
        if default_report_dir
        else f"直接回车 = 自动新建入库报告目录（{_LOAD_ROOT}）"
    )
    report = ask_optional("报告目录 --report-dir", hint)
    if report:
        report_dir = resolve_input_path(report)
        labels.append(f"report={report_dir}")
    else:
        report_dir = default_report_dir

    points = ask_optional("证据级文件 --points-file", "直接回车 = 自动")
    if points:
        path = resolve_input_path(points)
        if path.is_file():
            args += ["--points-file", str(path)]
            labels.append("points-file")
        else:
            print(f"  [提示] 文件不存在，忽略：{path}")

    curated = ask_optional("文章级文件 --curated-file", "直接回车 = 自动")
    if curated:
        path = resolve_input_path(curated)
        if path.is_file():
            args += ["--curated-file", str(path)]
            labels.append("curated-file")
        else:
            print(f"  [提示] 文件不存在，忽略：{path}")

    if ask_yes_no("存在 hard 拒绝/DB 错误时退出码 1（--fail-on-error）？", False):
        args.append("--fail-on-error")
        labels.append("fail-on-error")

    return args, (" + ".join(labels) if labels else "默认"), report_dir


def build_run(mode: str) -> tuple[list[str], dict[str, str], dict[str, str]] | None:
    """mode = dry | write。返回 (loader argv, 子进程 env, 确认摘要)。"""
    db_args, env, db_info = collect_db_config()

    input_args, input_label, auto_report_dir = collect_input_choice()
    if input_args is None:
        return None

    filter_args, filter_label = collect_filter_args()
    advanced_args, advanced_label, report_dir = collect_advanced_args(auto_report_dir)

    argv: list[str] = []
    if mode == "dry":
        argv.append("--dry-run")
    argv += db_args + input_args + filter_args + advanced_args
    if _OUT_OVERRIDE:
        argv += ["--output-dir", _OUT_OVERRIDE]
    if _REPORT_OVERRIDE:
        argv += ["--report-root", _REPORT_OVERRIDE]
    if report_dir is not None:
        argv += ["--report-dir", str(report_dir)]

    summary = {
        "database": db_info["database"],
        "host": db_info["host"],
        "port": db_info["port"],
        "user": db_info["user"],
        "mode": "预演（--dry-run，不写库）" if mode == "dry" else "实际写入数据库",
        "input": input_label,
        "filter": filter_label,
        "advanced": advanced_label,
        "report": str(report_dir) if report_dir is not None else f"自动新建入库报告目录（{_LOAD_ROOT}）",
    }
    return argv, env, summary


# ==================== 执行与确认 ====================

def confirm(argv: list[str], summary: dict[str, str]) -> bool:
    cmd = [sys.executable, str(_LOADER), *argv]
    print("\n" + "=" * 60)
    print("即将执行：")
    print("  " + format_cmd(cmd))
    print(f"目标库：{summary['user']}@{summary['host']}:{summary['port']}/{summary['database']}")
    print(f"模式：{summary['mode']}")
    print(f"输入：{summary['input']}")
    print(f"过滤：{summary['filter']}")
    print(f"高级：{summary['advanced']}")
    print(f"报告目录：{summary['report']}")
    print("=" * 60)
    if summary["mode"].startswith("实际写入"):
        print("[注意] 你将写入数据库；建议先跑一次预演。")
    return ask_yes_no("确认执行？", True)


def run_loader(argv: list[str], env_overrides: dict[str, str], kind: str = "run") -> int:
    env = os.environ.copy()
    env.update(env_overrides)
    cmd = [sys.executable, str(_LOADER), *argv]
    try:
        rc = subprocess.call(cmd, cwd=str(_TOPIC_DIR), env=env)
    except KeyboardInterrupt:
        print("\n[中止] 用户中断。")
        return 130
    print(f"\n[完成] loader 退出码 {rc}")
    if kind == "run":
        if rc == 0:
            print("[提示] 报告与错误文件的路径见上方 loader 的 [报告] / [错误] 行。")
        elif rc == 1:
            print("[提示] 退出码 1：可能是 hard 拒绝/DB 错误（见 load_errors.csv），"
                  "也可能是 loader 未捕获异常（见上方 traceback）。")
        elif rc == 2:
            print("[提示] 输入/连接/维度错误；请检查路径与数据库配置。")
    return rc


# ==================== 主菜单 ====================

def print_header() -> None:
    print("\n" + "=" * 60)
    print("科技金融指数研究 · 交互式 CSV → MySQL 入库")
    print("=" * 60)
    print(f"loader：{_LOADER}")
    print(f"数据根：{_OUT_ROOT}")
    print(f"入库报告根：{_LOAD_ROOT}")


def menu_loop() -> int:
    last_rc = 0
    while True:
        print_header()
        choice = ask_choice(
            "请选择操作：",
            [
                ("dry", "预演（--dry-run，不写库）"),
                ("write", "实际写入"),
                ("self-test", "纯函数自测（--self-test，不连库）"),
                ("dump", "查看指标口径/单位映射（--dump-mapping，需连库）"),
                ("exit", "退出"),
            ],
            default=1,
        )

        if choice == "exit":
            print("[退出] 未执行任何操作。")
            return 0

        if choice == "self-test":
            return run_loader(["--self-test"], {}, kind="self-test")

        if choice == "dump":
            db_args, env, _info = collect_db_config()
            return run_loader(["--dump-mapping", *db_args], env, kind="dump-mapping")

        built = build_run(choice)
        if built is None:
            continue
        argv, env, summary = built
        if not confirm(argv, summary):
            print("[取消] 未执行。")
            continue

        last_rc = run_loader(argv, env)
        if last_rc == 130:
            return 130
        if not ask_yes_no("是否再运行一次？", False):
            return last_rc


def main(argv: list[str] | None = None) -> int:
    global _OUT_ROOT, _LOAD_ROOT, _OUT_OVERRIDE, _REPORT_OVERRIDE
    parser = argparse.ArgumentParser(
        description="科技金融指数研究 · 交互式 CSV → MySQL 入库入口",
    )
    parser.add_argument("--output-dir", default=None, help="覆盖流水线数据根（topic_config.DATA_ROOT）")
    parser.add_argument("--report-root", default=None, help="覆盖入库报告根（topic_config.LOAD_REPORT_ROOT）")
    args = parser.parse_args(argv)
    _OUT_OVERRIDE = args.output_dir
    _REPORT_OVERRIDE = args.report_root
    if "pipeline_root" in globals():
        _OUT_ROOT = pipeline_root(_TOPIC_DIR, _OUT_OVERRIDE)
        _LOAD_ROOT = load_report_root(_TOPIC_DIR, _REPORT_OVERRIDE)
    if PASSWORD_ENV not in os.environ:
        print(f"[提示] 未检测到 {PASSWORD_ENV}；数据库配置步骤会要求输入密码。")
    try:
        return menu_loop()
    except (EOFError, KeyboardInterrupt):
        print("\n[取消] 未完成选择，未执行任何操作。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
