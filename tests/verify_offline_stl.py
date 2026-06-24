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

# 解析 lscpu -p 的 socket->物理核 映射（每物理核取一个逻辑核）
LSCPU = ("# CPU,Core,Socket\n"
         "0,0,0\n64,0,0\n"   # core0 socket0 (+SMT sibling 64)
         "1,1,0\n65,1,0\n"
         "32,32,1\n96,32,1\n"
         "33,33,1\n97,33,1\n")
phys = deploy.parse_phys_cores_by_socket(LSCPU)
assert phys[0] == [0, 1] and phys[1] == [32, 33]
assert "rdma-core" in deploy.MLX_DEPS
print("SOCKCORES_OK", phys)

from trex_sim import stl_profile as sp
assert round(sp.line_rate_pps(64)) == 148809524  # 100G@64B ≈ 148.81 Mpps
seq = sp.build_cells("seq", [64, 1500])
assert len(seq) == 8  # 4 方向 × 2 包长
assert seq[0].streams[0].dir_num == 1 and seq[0].streams[0].tx == 0 and seq[0].streams[0].rx == 2
assert all(len(c.streams) == 1 for c in seq)
pgs = [s.pg_id for c in seq for s in c.streams]
assert len(pgs) == len(set(pgs))  # pg_id 唯一
grp = sp.build_cells("group", [64])
assert len(grp) == 2 and len(grp[0].streams) == 2
assert {s.tx for s in grp[0].streams} == {0, 1}  # 组1 = card1 两口 TX
print("CELLS_OK", len(seq), len(grp))

script = sp.render_stl_runner_script(
    cells=sp.build_cells("seq", [64, 1500]),
    dest_macs=["bb:00", "bb:01", "aa:00", "aa:01"],
    trex_dir="/opt/trex/3.03", rate_percent=100, duration=20)
compile(script, "<stl>", "exec")          # 必须是合法 Python
assert "STLClient" in script and "flow_stats" in script
assert "TREX_CELL:" in script and '"size": 64' in script
print("STL_SCRIPT_OK len", len(script))

from trex_sim import stl_runner as sr
assert sr.loss_pct(1000, 1000) == 0.0
assert sr.loss_pct(1000, 999) == 0.1
assert sr.loss_pct(0, 0) == 100.0
cell = sr.parse_cell_line('TREX_CELL:{"label":"dir1-64B","size":64,"streams":'
                          '[{"dir":1,"pg":1,"tx_pkts":1000,"rx_pkts":1000,'
                          '"tx_bps":9.9e10,"rx_bps":9.9e10,"tx_pps":1.4e8,"rx_pps":1.4e8,'
                          '"lat_avg":5.0,"lat_max":40.0,"lat_jitter":1.0}]}')
assert cell["size"] == 64
assert sr.parse_cell_line("noise") is None
judged = sr.judge_cell(cell, loss_thresh=0.1)
assert judged["verdict"] == "PASS" and judged["streams"][0]["loss_pct"] == 0.0
bad = dict(cell); bad["streams"] = [dict(cell["streams"][0], rx_pkts=900)]
assert sr.judge_cell(bad, loss_thresh=0.1)["verdict"] == "FAIL"
print("JUDGE_OK")

from trex_sim import display
judged = [
    {"label": "dir1-64B", "size": 64, "verdict": "PASS", "streams": [
        {"dir": 1, "loss_pct": 0.0, "rx_bps": 9.9e10, "rx_pps": 1.4e8,
         "lat_avg": 5.0, "lat_max": 40.0, "verdict": "PASS"}]},
    {"label": "dir2-64B", "size": 64, "verdict": "FAIL", "streams": [
        {"dir": 2, "loss_pct": 3.2, "rx_bps": 9.5e10, "rx_pps": 1.3e8,
         "lat_avg": 6.0, "lat_max": 90.0, "verdict": "FAIL"}]},
]
rows, overall = sr.summarize(judged)
assert overall == "FAIL" and len(rows) == 2
assert rows[0]["方向"] == "1" and rows[0]["丢包%"] == "0.0"
display.stl_table(rows)   # 不抛异常即可
print("SUMMARY_OK", overall)

from trex_sim import restore
# mlx5：无需 rebind（bifurcated，留在内核）
assert restore.needs_rebind(["mlx5_core", "mlx5_core", "mlx5_core", "mlx5_core"]) is False
# Intel：需要 rebind
assert restore.needs_rebind(["ice", "ice", "mlx5_core", "ice"]) is True
print("RESTORE_OK")

class _DrySSH(_FakeSSH):
    def __init__(self, table): super().__init__(table); self.uploaded = {}
    def put_privileged(self, content, path): self.uploaded[path] = content
    def put_content(self, content, path): self.uploaded[path] = content

tbl = _mk_table()
tbl["lscpu -p"] = LSCPU
dry = _DrySSH(tbl)
res = sr.run_4dir(dry, names=topo.NIC_NAMES_DEFAULT, mode="seq", sizes=[64],
                  duration=10, rate_percent=100, loss_thresh=0.1,
                  cores_per_socket=2, trex_dir="/opt/trex/3.03", dry_run=True)
assert "/etc/trex_cfg.yaml" in dry.uploaded and "port_limit: 4" in dry.uploaded["/etc/trex_cfg.yaml"]
assert res["cells"] == 4 and res["dry_run"] is True
print("RUN4DIR_DRY_OK")

from trex_sim import cli as _cli
cmds = _cli.cli.commands.keys()
assert "run-4dir" in cmds and "restore" in cmds
print("CLI_OK", sorted(cmds))
