"""STL 4 口测试编排：解析、判定、汇总、运行。"""
import json
from typing import Optional
from . import topo, deploy, display as D
from . import restore as restore_mod
from .stl_profile import build_cells, render_stl_runner_script


def loss_pct(tx_pkts: int, rx_pkts: int) -> float:
    if tx_pkts <= 0:
        return 100.0
    return round(max(0, tx_pkts - rx_pkts) / tx_pkts * 100.0, 4)


def parse_cell_line(line: str) -> Optional[dict]:
    if not line.startswith("TREX_CELL:"):
        return None
    try:
        return json.loads(line[len("TREX_CELL:"):])
    except Exception:
        return None


def judge_cell(cell: dict, loss_thresh: float) -> dict:
    streams = []
    verdict = "PASS"
    for s in cell.get("streams", []):
        lp = loss_pct(s.get("tx_pkts", 0), s.get("rx_pkts", 0))
        ok = lp <= loss_thresh and s.get("tx_pkts", 0) > 0
        if not ok:
            verdict = "FAIL"
        streams.append({**s, "loss_pct": lp, "verdict": "PASS" if ok else "FAIL"})
    return {"label": cell.get("label", ""), "size": cell.get("size", 0),
            "streams": streams, "verdict": verdict}


def _g(bps):  # bps -> Gbps 字符串
    return f"{bps / 1e9:.2f}"


def _m(pps):  # pps -> Mpps 字符串
    return f"{pps / 1e6:.2f}"


def summarize(judged: list) -> tuple:
    rows = []
    overall = "PASS"
    for cell in judged:
        for s in cell["streams"]:
            if s["verdict"] != "PASS":
                overall = "FAIL"
            rows.append({
                "包长": str(cell["size"]),
                "方向": str(s["dir"]),
                "接收Gbps": _g(s.get("rx_bps", 0.0)),
                "Mpps": _m(s.get("rx_pps", 0.0)),
                "丢包%": str(s.get("loss_pct", 0.0)),
                "时延us(平均/最大)": f"{s.get('lat_avg', 0.0):.1f}/{s.get('lat_max', 0.0):.1f}",
                "判定": s["verdict"],
            })
    if not rows:
        overall = "FAIL"
    return rows, overall


REMOTE_STL_SCRIPT = "/tmp/trex_stl_4dir.py"


def run_4dir(ssh, names, mode, sizes, duration, rate_percent, loss_thresh,
             cores_per_socket, trex_dir, dry_run=False):
    D.step("探测 4 口拓扑（PCI/NUMA/MAC/驱动）")
    ports = topo.detect_ports(ssh, names)
    for p in ports:
        D.info(f"{p.name}  pci={p.pci}  numa={p.numa}  drv={p.driver}")
    dmacs = topo.dest_macs(ports)

    D.step("生成 4 口 / 双 socket 配置")
    phys = deploy.detect_socket_phys_cores(ssh)
    dp0 = deploy.pick_cores([c for c in phys.get(0, []) if c not in (0, 1)], cores_per_socket)
    dp1 = deploy.pick_cores(phys.get(1, []), cores_per_socket)
    deploy.write_trex_config_4port(ssh, [p.pci for p in ports], dp0, dp1)

    cells = build_cells(mode, sizes)
    script = render_stl_runner_script(cells, dmacs, trex_dir, rate_percent, duration)
    ssh.put_content(script, REMOTE_STL_SCRIPT)
    D.success(f"STL 自动化脚本已上传 {REMOTE_STL_SCRIPT}（{len(cells)} 个单元）")

    if dry_run:
        return {"cells": len(cells), "dry_run": True, "ports": [p.name for p in ports]}

    D.step("启动 TRex STL 守护进程")
    ssh.exec("pkill -f 't-rex-64' 2>/dev/null || true")
    ssh.exec(f"cd {trex_dir} && ./t-rex-64 -i --no-scapy-server -c {cores_per_socket} "
             f"--cfg /etc/trex_cfg.yaml > /tmp/trex_daemon.log 2>&1 &")
    import time as _t
    _t.sleep(8)
    code, _, _ = ssh.exec("pgrep -f 't-rex-64'")
    if code != 0:
        _, log, _ = ssh.exec("tail -20 /tmp/trex_daemon.log")
        raise RuntimeError(f"TRex 启动失败:\n{log}")

    D.step(f"执行 STL 线速测试（{mode}，包长 {sizes}，每单元 {duration}s）")
    judged = []

    def on_line(line):
        cell = parse_cell_line(line)
        if cell is not None:
            jc = judge_cell(cell, loss_thresh)
            judged.append(jc)
            for s in jc["streams"]:
                D.info(f"{jc['label']} dir{s['dir']}: rx={_g(s.get('rx_bps', 0))}Gbps "
                       f"loss={s['loss_pct']}% -> {s['verdict']}")
        elif line.strip():
            D.stream_line(line)

    ssh.exec_stream(f"python3 {REMOTE_STL_SCRIPT}", on_line=on_line,
                    timeout=len(cells) * (duration + 40) + 120)

    D.step("还原（停 TRex；mlx5 无需 rebind）")
    restore_mod.restore_ports(ssh, ports)

    rows, overall = summarize(judged)
    return {"cells": len(cells), "dry_run": False, "rows": rows, "overall": overall}
