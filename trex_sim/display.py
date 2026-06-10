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
