"""Terminal output helpers."""

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BLUE = "\033[34m"
DIM = "\033[2m"


def info(msg: str):
    print(f"  {CYAN}>{RESET} {msg}")


def success(msg: str):
    print(f"  {GREEN}✔{RESET} {msg}")


def warn(msg: str):
    print(f"  {YELLOW}⚠{RESET}  {msg}")


def error(msg: str):
    print(f"  {RED}✘{RESET} {msg}", flush=True)


def step(msg: str):
    print(f"\n{BOLD}{BLUE}[{msg}]{RESET}")


def header(msg: str):
    width = 60
    print(f"\n{BOLD}{'═' * width}{RESET}")
    print(f"{BOLD}  {msg}{RESET}")
    print(f"{BOLD}{'═' * width}{RESET}")


def stream_line(line: str):
    print(f"  {DIM}│{RESET} {line}")


def result_table(stats: dict):
    width = 60
    print(f"\n{BOLD}{'─' * width}{RESET}")
    print(f"{BOLD}  测试结果摘要{RESET}")
    print(f"{BOLD}{'─' * width}{RESET}")
    for key, value in stats.items():
        print(f"  {CYAN}{key:<28}{RESET} {BOLD}{value}{RESET}")
    print(f"{BOLD}{'─' * width}{RESET}\n")


def stl_table(rows: list):
    """打印 STL 结果矩阵（rows = summarize() 的行 dict 列表）。"""
    if not rows:
        print("  (无结果)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    header = "  ".join(f"{c:<{widths[c]}}" for c in cols)
    print(f"\n{BOLD}{header}{RESET}")
    print("─" * len(header))
    for r in rows:
        line = "  ".join(f"{str(r[c]):<{widths[c]}}" for c in cols)
        color = GREEN if r.get("判定") == "PASS" else RED
        print(f"{color}{line}{RESET}")
