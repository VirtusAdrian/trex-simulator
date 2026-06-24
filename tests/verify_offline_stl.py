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
