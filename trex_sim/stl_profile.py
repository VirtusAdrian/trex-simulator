"""STL 流量：单元矩阵（方向/分组 × 包长）与远程自动化脚本生成。"""
import json
from dataclasses import dataclass, asdict
from .topo import DIR_BY_NUM, GROUPS


@dataclass
class Stream:
    dir_num: int
    tx: int
    rx: int
    pg_id: int


@dataclass
class Cell:
    label: str
    size: int
    streams: list


def line_rate_pps(size_bytes: int, link_gbps: int = 100) -> float:
    """L1 线速 pps：含 20B 帧间隙+前导（IFG 12 + preamble 8）。"""
    return link_gbps * 1e9 / ((size_bytes + 20) * 8)


def build_cells(mode: str, sizes: list) -> list:
    cells = []
    pg = 1
    for size in sizes:
        if size < 64:
            raise ValueError(f"包长 {size} 小于以太网最小帧 64B，不支持")
        if mode == "seq":
            for num, (tx, rx) in DIR_BY_NUM.items():
                cells.append(Cell(f"dir{num}-{size}B", size, [Stream(num, tx, rx, pg)]))
                pg += 1
        elif mode == "group":
            for gnum, dirnums in GROUPS.items():
                streams = []
                for num in dirnums:
                    tx, rx = DIR_BY_NUM[num]
                    streams.append(Stream(num, tx, rx, pg))
                    pg += 1
                cells.append(Cell(f"grp{gnum}-{size}B", size, streams))
        else:
            raise ValueError(f"未知 mode: {mode}（仅 seq|group）")
    return cells


def cells_to_json(cells: list) -> str:
    return json.dumps([{"label": c.label, "size": c.size,
                        "streams": [asdict(s) for s in c.streams]} for c in cells])


_STL_TEMPLATE = '''\
import sys, time, json
sys.path.insert(0, "__TREX_DIR__/automation/trex_control_plane/interactive")
from trex.stl.api import STLClient, STLStream, STLPktBuilder, STLTXCont, STLFlowLatencyStats
from scapy.all import Ether, IP, UDP

CELLS = json.loads("""__CELLS_JSON__""")
DEST_MACS = json.loads("""__DEST_MACS_JSON__""")
RATE = float(__RATE__)
DURATION = __DURATION__

def make_pkt(dst_mac, size):
    base = Ether(dst=dst_mac) / IP(src="16.0.0.1", dst="48.0.0.1") / UDP(sport=1025, dport=12)
    pad = max(0, size - len(base) - 4)
    return STLPktBuilder(pkt=base / ("x" * pad))

c = STLClient(server="127.0.0.1")
for _try in range(15):
    try:
        c.connect()
        break
    except Exception:
        time.sleep(1)
else:
    c.connect()
try:
    all_ports = c.get_all_ports()
    c.reset(ports=all_ports)
    c.set_port_attr(ports=all_ports, promiscuous=True)
    print("TREX_STATUS:READY", flush=True)
    for cell in CELLS:
        size = cell["size"]
        tx_ports = sorted({s["tx"] for s in cell["streams"]})
        c.reset(ports=all_ports)
        for s in cell["streams"]:
            stream = STLStream(
                packet=make_pkt(DEST_MACS[s["tx"]], size),
                mode=STLTXCont(percentage=RATE),
                flow_stats=STLFlowLatencyStats(pg_id=s["pg_id"]))
            c.add_streams(stream, ports=[s["tx"]])
        c.clear_stats()
        c.start(ports=tx_ports, duration=DURATION, force=True)
        c.wait_on_traffic(timeout=DURATION + 30)
        stats = c.get_stats()
        out = {"label": cell["label"], "size": size, "streams": []}
        for s in cell["streams"]:
            fs = stats.get("flow_stats", {}).get(s["pg_id"], {})
            lat = stats.get("latency", {}).get(s["pg_id"], {})
            tx_pkts = fs.get("tx_pkts", {}).get("total", 0)
            rx_pkts = fs.get("rx_pkts", {}).get("total", 0)
            txp = stats.get(s["tx"], {})
            rxp = stats.get(s["rx"], {})
            lt = lat.get("latency", {})
            out["streams"].append({
                "dir": s["dir_num"], "pg": s["pg_id"],
                "tx_pkts": tx_pkts, "rx_pkts": rx_pkts,
                "tx_bps": txp.get("tx_bps", 0.0), "rx_bps": rxp.get("rx_bps", 0.0),
                "tx_pps": txp.get("tx_pps", 0.0), "rx_pps": rxp.get("rx_pps", 0.0),
                "lat_avg": lt.get("average", 0.0), "lat_max": lt.get("total_max", 0.0),
                "lat_jitter": lt.get("jitter", 0.0)})
        print("TREX_CELL:" + json.dumps(out), flush=True)
    print("TREX_DONE", flush=True)
finally:
    c.disconnect()
'''


def render_stl_runner_script(cells: list, dest_macs: list, trex_dir: str,
                             rate_percent: int = 100, duration: int = 20) -> str:
    return (_STL_TEMPLATE
            .replace("__TREX_DIR__", trex_dir)
            .replace("__CELLS_JSON__", cells_to_json(cells))
            .replace("__DEST_MACS_JSON__", json.dumps(dest_macs))
            .replace("__RATE__", str(float(rate_percent)))
            .replace("__DURATION__", str(int(duration))))
