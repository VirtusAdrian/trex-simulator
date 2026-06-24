"""STL 4 口测试编排：解析、判定、汇总、运行。"""
import json
from typing import Optional


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
