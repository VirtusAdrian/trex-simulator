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
