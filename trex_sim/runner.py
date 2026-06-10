"""
TRex ASTF test runner.

Uploads the TCP traffic profile, starts the TRex daemon,
runs the test, collects statistics, and tears down.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from .ssh import SSHClient
from .profile import generate_profile
from .deploy import TREX_INSTALL_DIR
from . import display as D

REMOTE_PROFILE = "/tmp/trex_tcp_profile.py"
TREX_BIN = f"{TREX_INSTALL_DIR}/t-rex-64"
ASTF_CONSOLE = f"{TREX_INSTALL_DIR}/trex-console"


@dataclass
class TestStats:
    duration: int = 0
    tx_pps: float = 0.0
    rx_pps: float = 0.0
    tx_bps: float = 0.0
    rx_bps: float = 0.0
    tx_pkts: int = 0
    rx_pkts: int = 0
    tx_bytes: int = 0
    rx_bytes: int = 0
    active_flows: int = 0
    established_flows: int = 0
    closed_flows: int = 0
    errors: int = 0
    drops: int = 0
    raw_lines: list[str] = field(default_factory=list)

    def to_display_dict(self) -> dict:
        def fmt_bps(bps: float) -> str:
            if bps >= 1e9:
                return f"{bps/1e9:.2f} Gbps"
            if bps >= 1e6:
                return f"{bps/1e6:.2f} Mbps"
            return f"{bps/1e3:.2f} Kbps"

        def fmt_pps(pps: float) -> str:
            if pps >= 1e6:
                return f"{pps/1e6:.2f} Mpps"
            return f"{pps/1e3:.2f} Kpps"

        return {
            "测试时长 (s)":          str(self.duration),
            "发送速率":               fmt_pps(self.tx_pps),
            "接收速率":               fmt_pps(self.rx_pps),
            "发送带宽":               fmt_bps(self.tx_bps),
            "接收带宽":               fmt_bps(self.rx_bps),
            "总发送包数":             f"{self.tx_pkts:,}",
            "总接收包数":             f"{self.rx_pkts:,}",
            "总发送字节":             f"{self.tx_bytes:,} B",
            "总接收字节":             f"{self.rx_bytes:,} B",
            "活跃 TCP 流":           f"{self.active_flows:,}",
            "已建立 TCP 流 (累计)":   f"{self.established_flows:,}",
            "已关闭 TCP 流 (累计)":   f"{self.closed_flows:,}",
            "丢包数":                 f"{self.drops:,}",
            "错误数":                 f"{self.errors:,}",
        }


def _parse_stats_json(line: str, stats: TestStats):
    """Try to parse a JSON stats line emitted by TRex."""
    try:
        data = json.loads(line)
    except Exception:
        return

    # TRex JSON stats structure varies by version; handle common keys
    if "traffic" in data:
        t = data["traffic"]
        stats.tx_pps = float(t.get("tx_pps", stats.tx_pps))
        stats.rx_pps = float(t.get("rx_pps", stats.rx_pps))
        stats.tx_bps = float(t.get("tx_bps", stats.tx_bps))
        stats.rx_bps = float(t.get("rx_bps", stats.rx_bps))

    if "flow_stats" in data:
        fs = data["flow_stats"]
        stats.active_flows = int(fs.get("active_flows", stats.active_flows))
        stats.established_flows = int(fs.get("open_flows", stats.established_flows))
        stats.closed_flows = int(fs.get("closed_flows", stats.closed_flows))

    if "global" in data:
        g = data["global"]
        stats.tx_pkts = int(g.get("tx_pkts", stats.tx_pkts))
        stats.rx_pkts = int(g.get("rx_pkts", stats.rx_pkts))
        stats.tx_bytes = int(g.get("tx_bytes", stats.tx_bytes))
        stats.rx_bytes = int(g.get("rx_bytes", stats.rx_bytes))
        stats.drops = int(g.get("drops", stats.drops))
        stats.errors = int(g.get("err_pkts", stats.errors))


def _parse_stats_text(line: str, stats: TestStats):
    """Fallback: parse human-readable TRex output with regex."""
    patterns = {
        "tx_pps":  r"Tx pps\s*:\s*([\d.]+)",
        "rx_pps":  r"Rx pps\s*:\s*([\d.]+)",
        "tx_bps":  r"Tx bps L2\s*:\s*([\d.]+)",
        "rx_bps":  r"Rx bps L2\s*:\s*([\d.]+)",
        "active":  r"active\s+flows\s*:\s*(\d+)",
        "opens":   r"open\s+flows\s*:\s*(\d+)",
        "closed":  r"closed\s+flows\s*:\s*(\d+)",
        "drops":   r"drops\s*:\s*(\d+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, line, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if key == "tx_pps":   stats.tx_pps = val
            elif key == "rx_pps": stats.rx_pps = val
            elif key == "tx_bps": stats.tx_bps = val
            elif key == "rx_bps": stats.rx_bps = val
            elif key == "active": stats.active_flows = int(val)
            elif key == "opens":  stats.established_flows = int(val)
            elif key == "closed": stats.closed_flows = int(val)
            elif key == "drops":  stats.drops = int(val)


def start_daemon(ssh: SSHClient, cores: int):
    """Start TRex in ASTF daemon mode."""
    D.info("停止已有 TRex 进程…")
    ssh.exec("pkill -f 't-rex-64' 2>/dev/null || true")
    time.sleep(2)

    # Privilege escalation is handled centrally by SSHClient (sudo wrapping
    # when the login user is non-root); no explicit sudo here.
    cmd = (
        f"cd {TREX_INSTALL_DIR} && "
        f"{TREX_BIN} --astf -i --no-scapy-server "
        f"-c {cores} --cfg /etc/trex_cfg.yaml "
        f"> /tmp/trex_daemon.log 2>&1 &"
    )
    D.info(f"启动 TRex ASTF 守护进程（{cores} 核心）…")
    ssh.exec(cmd)
    time.sleep(5)

    # Verify it started
    code, out, _ = ssh.exec("pgrep -f 't-rex-64'")
    if code != 0:
        _, log, _ = ssh.exec("tail -20 /tmp/trex_daemon.log")
        raise RuntimeError(f"TRex 守护进程启动失败:\n{log}")
    D.success(f"TRex 守护进程已启动 (PID: {out.strip()})")


def run_astf_test(ssh: SSHClient, duration: int, multiplier: float, stats: TestStats):
    """Drive TRex via trex-console automation script."""
    automation = f"""\
import sys, time, json
sys.path.insert(0, '{TREX_INSTALL_DIR}/automation/trex_control_plane/interactive')
from trex.astf.api import ASTFClient

c = ASTFClient(server='127.0.0.1')
c.connect()
c.reset()
c.load_profile('{REMOTE_PROFILE}')

print('TREX_STATUS:RUNNING', flush=True)
c.start(mult={multiplier}, duration={duration}, nc=True)

interval = 2
elapsed = 0
while elapsed < {duration}:
    time.sleep(interval)
    elapsed += interval
    stats = c.get_stats()
    print('TREX_STATS:' + json.dumps(stats), flush=True)

c.stop()
time.sleep(1)
final = c.get_stats()
print('TREX_FINAL:' + json.dumps(final), flush=True)
c.disconnect()
"""
    ssh.put_content(automation, "/tmp/trex_run.py")

    collected_final = {}

    def on_line(line: str):
        stats.raw_lines.append(line)
        if line.startswith("TREX_STATUS:"):
            D.success("TRex 流量已启动")
        elif line.startswith("TREX_STATS:"):
            try:
                data = json.loads(line[len("TREX_STATS:"):])
                _extract_live_stats(data, stats)
                _print_live(stats)
            except Exception:
                pass
        elif line.startswith("TREX_FINAL:"):
            try:
                collected_final.update(json.loads(line[len("TREX_FINAL:"):]))
            except Exception:
                pass
        else:
            D.stream_line(line)

    exit_code = ssh.exec_stream(
        f"python3 /tmp/trex_run.py",
        on_line=on_line,
        timeout=duration + 120,
    )

    if collected_final:
        _extract_live_stats(collected_final, stats)

    if exit_code != 0:
        raise RuntimeError("TRex 测试脚本异常退出，请检查日志")


def _extract_live_stats(data: dict, stats: TestStats):
    """Extract stats from TRex Python API stats dict."""
    try:
        g = data.get("global", {})
        stats.tx_pps = float(g.get("tx_pps", stats.tx_pps))
        stats.rx_pps = float(g.get("rx_pps", stats.rx_pps))
        stats.tx_bps = float(g.get("tx_bps", stats.tx_bps))
        stats.rx_bps = float(g.get("rx_bps", stats.rx_bps))
        stats.tx_pkts = int(g.get("tx_pkts", stats.tx_pkts))
        stats.rx_pkts = int(g.get("rx_pkts", stats.rx_pkts))
        stats.tx_bytes = int(g.get("tx_bytes", stats.tx_bytes))
        stats.rx_bytes = int(g.get("rx_bytes", stats.rx_bytes))
        stats.drops = int(g.get("m_total_tx_err", stats.drops))

        flow = data.get("flow_stats", {})
        stats.active_flows = int(flow.get("active_flows", stats.active_flows))
        stats.established_flows = int(flow.get("open_flows", stats.established_flows))
        stats.closed_flows = int(flow.get("closed_flows", stats.closed_flows))
    except Exception:
        pass


_last_live_print = 0.0


def _print_live(stats: TestStats):
    global _last_live_print
    now = time.time()
    if now - _last_live_print < 2:
        return
    _last_live_print = now

    def fmt_bps(b):
        if b >= 1e9: return f"{b/1e9:.2f} Gbps"
        if b >= 1e6: return f"{b/1e6:.2f} Mbps"
        return f"{b/1e3:.2f} Kbps"

    def fmt_pps(p):
        if p >= 1e6: return f"{p/1e6:.2f} Mpps"
        return f"{p/1e3:.2f} Kpps"

    D.info(
        f"TX {fmt_pps(stats.tx_pps)} / {fmt_bps(stats.tx_bps)}  "
        f"RX {fmt_pps(stats.rx_pps)} / {fmt_bps(stats.rx_bps)}  "
        f"活跃流: {stats.active_flows:,}"
    )


def run_test(
    ssh: SSHClient,
    duration: int,
    multiplier: float,
    cores: int,
    client_ip_start: str,
    client_ip_end: str,
    server_ip_start: str,
    server_ip_end: str,
    cps: int,
    payload_size: int,
    reqs_per_conn: int = 100,
) -> TestStats:
    stats = TestStats(duration=duration)

    D.step("生成 TCP ASTF 流量 Profile")
    profile_code = generate_profile(
        client_ip_start=client_ip_start,
        client_ip_end=client_ip_end,
        server_ip_start=server_ip_start,
        server_ip_end=server_ip_end,
        cps=cps,
        payload_size=payload_size,
        reqs_per_conn=reqs_per_conn,
    )
    ssh.put_content(profile_code, REMOTE_PROFILE)
    D.success(f"Profile 已上传至 {REMOTE_PROFILE}")

    D.step("启动 TRex 守护进程")
    start_daemon(ssh, cores)

    D.step(f"执行 TCP 流量测试（时长 {duration}s，倍率 {multiplier}x，核心数 {cores}）")
    run_astf_test(ssh, duration, multiplier, stats)

    D.step("停止 TRex 守护进程")
    ssh.exec("pkill -f 't-rex-64' 2>/dev/null || true")
    D.success("TRex 已停止")

    return stats
