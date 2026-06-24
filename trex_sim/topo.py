"""固定 4 口 / 2 对拓扑映射（NUMA 对齐）。"""
from dataclasses import dataclass

# NUMA 对齐顺序：idx 0,1,2,3 = [card1.p0, card1.p1, card2.p0, card2.p1]
# 物理对连 (0<->2),(1<->3)
NIC_NAMES_DEFAULT = ["enp65s0f0np0", "enp65s0f1np1", "enp161s0f0np0", "enp161s0f1np1"]

# 方向号 -> (tx_idx, rx_idx)，对齐 iperf 四方向
DIR_BY_NUM = {1: (0, 2), 2: (2, 0), 3: (1, 3), 4: (3, 1)}
# 分组号 -> 方向号列表（组1=方向1+3，组2=方向2+4）
GROUPS = {1: [1, 3], 2: [2, 4]}


def peer_index(idx: int) -> int:
    """对连端口索引：4 口布局下 (0,2)(1,3) 互为对端。"""
    return idx ^ 2


def is_mellanox(driver: str) -> bool:
    return driver.startswith("mlx5")


@dataclass
class PortInfo:
    name: str
    pci: str
    numa: int
    mac: str
    driver: str


def dest_macs(ports: list) -> list:
    """每个 TX 口的目的 MAC = 其对端口的 MAC。"""
    return [ports[peer_index(i)].mac for i in range(len(ports))]
