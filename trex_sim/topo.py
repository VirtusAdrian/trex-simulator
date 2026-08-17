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


def _one(ssh, cmd) -> str:
    code, out, _ = ssh.exec(cmd)
    return out.strip() if code == 0 else ""


def detect_ports(ssh, names: list) -> list:
    """按给定（已 NUMA 对齐）顺序读取每口 PCI/NUMA/MAC/驱动。"""
    ports = []
    for n in names:
        info = _one(ssh, f"ethtool -i {n}")
        pci = driver = ""
        for line in info.splitlines():
            if line.startswith("bus-info:"):
                pci = line.split(":", 1)[1].strip()
            elif line.startswith("driver:"):
                driver = line.split(":", 1)[1].strip()
        numa_s = _one(ssh, f"cat /sys/class/net/{n}/device/numa_node")
        mac = _one(ssh, f"cat /sys/class/net/{n}/address")
        try:
            numa = int(numa_s)
        except ValueError:
            numa = -1
        if not pci or not mac:
            raise RuntimeError(f"网卡 {n} 探测不全（pci='{pci}' mac='{mac}'）；请确认 4 口存在")
        ports.append(PortInfo(name=n, pci=pci, numa=numa, mac=mac, driver=driver))
    if len(ports) != 4:
        raise RuntimeError(f"需要恰好 4 个网口，实得 {len(ports)}")
    return ports


def iface_ipv4(ssh, name: str) -> str:
    """返回该网口的首个 IPv4/掩码；无则空串。"""
    code, out, _ = ssh.exec(f"ip -4 -o addr show dev {name}")
    if code != 0:
        return ""
    for line in out.splitlines():
        parts = line.split()
        for i, tok in enumerate(parts):
            if tok == "inet" and i + 1 < len(parts):
                return parts[i + 1]
    return ""


def default_route_iface(ssh) -> str:
    """返回默认路由(管理口)网卡名；取不到则空串。"""
    code, out, _ = ssh.exec("ip route show default")
    if code != 0:
        return ""
    for line in out.splitlines():
        parts = line.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    return ""


def assert_ports_safe(ssh, ports):
    """拒绝对带 IP 或是管理口的网卡做 DPDK 打流（会影响设备可达）。"""
    mgmt = default_route_iface(ssh)
    for p in ports:
        if p.name == mgmt:
            raise RuntimeError(f"网口 {p.name} 是默认路由(管理口)，用它做 DPDK 打流会断开设备可达；请检查对连关系。")
        ip4 = iface_ipv4(ssh, p.name)
        if ip4:
            raise RuntimeError(f"网口 {p.name} 带有 IP {ip4}；用它做 DPDK 打流可能影响可达，请先清掉该口 IP 或确认对连关系。")
