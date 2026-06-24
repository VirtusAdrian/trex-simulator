"""离线验证：run-4dir 纯逻辑（无需 SSH / DPDK / 真实流量）。
用法: python tests/verify_offline_stl.py ; 退出码 0 全过。"""
from trex_sim import topo

# 端口对连：NUMA 对齐顺序 [card1.p0, card1.p1, card2.p0, card2.p1] -> (0,2),(1,3)
assert topo.peer_index(0) == 2 and topo.peer_index(2) == 0
assert topo.peer_index(1) == 3 and topo.peer_index(3) == 1
assert topo.DIR_BY_NUM[1] == (0, 2) and topo.DIR_BY_NUM[2] == (2, 0)
assert topo.DIR_BY_NUM[3] == (1, 3) and topo.DIR_BY_NUM[4] == (3, 1)
assert topo.GROUPS == {1: [1, 3], 2: [2, 4]}
assert topo.is_mellanox("mlx5_core") and not topo.is_mellanox("ice")
ports = [topo.PortInfo(n, f"pci{i}", i % 2, f"mac{i}", "mlx5_core")
         for i, n in enumerate(topo.NIC_NAMES_DEFAULT)]
assert topo.dest_macs(ports) == ["mac2", "mac3", "mac0", "mac1"]
print("TOPO_OK", topo.NIC_NAMES_DEFAULT)

# detect_ports：用 FakeSSH 回放 ethtool / sysfs 输出
class _FakeSSH:
    def __init__(self, table): self.table = table
    def exec(self, cmd, timeout=60, raw=False):
        for key, out in self.table.items():
            if key in cmd:
                return 0, out, ""
        return 1, "", "no match"

def _mk_table():
    t = {}
    pci = {"enp65s0f0np0": "0000:41:00.0", "enp65s0f1np1": "0000:41:00.1",
           "enp161s0f0np0": "0000:a1:00.0", "enp161s0f1np1": "0000:a1:00.1"}
    numa = {"enp65s0f0np0": "0", "enp65s0f1np1": "0",
            "enp161s0f0np0": "1", "enp161s0f1np1": "1"}
    mac = {"enp65s0f0np0": "aa:00", "enp65s0f1np1": "aa:01",
           "enp161s0f0np0": "bb:00", "enp161s0f1np1": "bb:01"}
    for n in topo.NIC_NAMES_DEFAULT:
        t[f"ethtool -i {n}"] = f"driver: mlx5_core\nbus-info: {pci[n]}\n"
        t[f"/sys/class/net/{n}/device/numa_node"] = numa[n] + "\n"
        t[f"/sys/class/net/{n}/address"] = mac[n] + "\n"
    return t

ports = topo.detect_ports(_FakeSSH(_mk_table()), topo.NIC_NAMES_DEFAULT)
assert [p.pci for p in ports] == ["0000:41:00.0", "0000:41:00.1", "0000:a1:00.0", "0000:a1:00.1"]
assert [p.numa for p in ports] == [0, 0, 1, 1]
assert [p.driver for p in ports] == ["mlx5_core"] * 4
assert topo.dest_macs(ports) == ["bb:00", "bb:01", "aa:00", "aa:01"]
print("DETECT_PORTS_OK")

from trex_sim import deploy
assert deploy.pick_cores([2, 4, 6, 8, 10], 3) == [2, 4, 6]
cfg = deploy.render_trex_cfg_4port(
    pcis=["0000:41:00.0", "0000:41:00.1", "0000:a1:00.0", "0000:a1:00.1"],
    dp_sock0=[2, 4, 6, 8], dp_sock1=[34, 36, 38, 40],
    master_id=0, latency_id=1)
assert "port_limit: 4" in cfg
assert "0000:41:00.0" in cfg and "0000:a1:00.1" in cfg
assert "master_thread_id: 0" in cfg and "latency_thread_id: 1" in cfg
assert "socket: 0" in cfg and "socket: 1" in cfg
assert "threads: [2, 4, 6, 8]" in cfg and "threads: [34, 36, 38, 40]" in cfg
print("CFG4_OK len", len(cfg))
