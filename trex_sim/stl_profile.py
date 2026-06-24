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
