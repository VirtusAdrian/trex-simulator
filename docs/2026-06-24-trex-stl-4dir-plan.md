# TRex Stateless 4-Port Line-Rate Test (run-4dir) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TRex Stateless (STL) line-rate test over the fixed 4-port / 2-pair topology (`trex-sim run-4dir`), plus a `restore` subcommand and an `iperf|trex` selector in the remote wrapper.

**Architecture:** New pure-logic modules (topology mapping, STL cell matrix, remote-script generation, stats parsing/judging) are unit-tested offline via a standalone `tests/verify_offline_stl.py` (mirroring `tests/verify_offline.py`). SSH/TRex-runtime glue is deterministic code verified with a `FakeSSH` where possible and on the lab Mellanox box otherwise. The remote bash wrapper shells out to the new `trex-sim run-4dir`.

**Tech Stack:** Python 3 (click, paramiko), TRex STL API (remote), bash (wrapper). Spec: `docs/2026-06-24-trex-stl-4dir-design.md`.

---

## File Structure

- Create `trex_sim/topo.py` — fixed 4-port layout, cabling/peer map, directions/groups, `PortInfo`, `detect_ports`, `dest_macs`, `is_mellanox`.
- Create `trex_sim/stl_profile.py` — `line_rate_pps`, `Stream`/`Cell`, `build_cells`, `render_stl_runner_script` (remote STL automation generator).
- Create `trex_sim/stl_runner.py` — `loss_pct`, `parse_cell_line`, `judge_cell`, `summarize`, `run_4dir` (orchestration).
- Create `trex_sim/restore.py` — `stop_trex`, `restore_ports` (mlx5 no-op).
- Modify `trex_sim/deploy.py` — `render_trex_cfg_4port`, `pick_cores`, `detect_socket_phys_cores`, `write_trex_config_4port`, Mellanox deps, `deploy_4port`.
- Modify `trex_sim/cli.py` — `run-4dir`, `restore` subcommands.
- Modify `trex_sim/display.py` — `stl_table(rows)`.
- Create `tests/verify_offline_stl.py` — offline checks (grown task-by-task).
- Modify `/c/Users/AdrianXu/Downloads/remote_bidir_4dir_iperf.sh` — `--backend [iperf|trex]` selector.
- Modify `README.md` — document `run-4dir`.

Run offline tests with: `python tests/verify_offline_stl.py` (expects `ALL_OFFLINE_STL_CHECKS_PASSED`). Commit messages end with the Co-Authored-By trailer used in this repo.

---

### Task 1: Topology constants & pure maps (`topo.py`)

**Files:**
- Create: `trex_sim/topo.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — create `tests/verify_offline_stl.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `ModuleNotFoundError: No module named 'trex_sim.topo'`

- [ ] **Step 3: Implement `trex_sim/topo.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `TOPO_OK [...]`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/topo.py tests/verify_offline_stl.py
git commit -m "feat(topo): 4-port/2-pair layout, peer map, directions/groups

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Port detection over SSH (`topo.detect_ports`)

**Files:**
- Modify: `trex_sim/topo.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — append to `tests/verify_offline_stl.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `AttributeError: module 'trex_sim.topo' has no attribute 'detect_ports'`

- [ ] **Step 3: Implement `detect_ports`** — append to `trex_sim/topo.py`:

```python
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
```

Note: the test's `ethtool -i {n}` line returns a `driver:`/`bus-info:` blob; `cat /sys/.../numa_node` and `.../address` are matched by substring. Keep these `exec` command strings exactly (`ethtool -i <name>`, `cat /sys/class/net/<name>/device/numa_node`, `cat /sys/class/net/<name>/address`).

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `DETECT_PORTS_OK`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/topo.py tests/verify_offline_stl.py
git commit -m "feat(topo): detect_ports reads PCI/NUMA/MAC/driver over SSH

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 4-port config render + core picker (`deploy.py`)

**Files:**
- Modify: `trex_sim/deploy.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — append:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `AttributeError: module 'trex_sim.deploy' has no attribute 'pick_cores'`

- [ ] **Step 3: Implement** — append to `trex_sim/deploy.py`:

```python
def pick_cores(phys_cores: list, count: int) -> list:
    """从本 socket 物理核列表取前 count 个（已排除 master/latency 与 SMT 兄弟核）。"""
    return list(phys_cores[:count])


def render_trex_cfg_4port(pcis: list, dp_sock0: list, dp_sock1: list,
                          master_id: int = 0, latency_id: int = 1) -> str:
    """生成 4 口 / 双 socket 的 trex_cfg.yaml（STL，mlx5 原地，无 vfio）。"""
    if len(pcis) != 4:
        raise ValueError("需要恰好 4 个 PCI 地址")
    iface_lines = "\n".join(f"        - '{p}'" for p in pcis)
    t0 = ", ".join(str(c) for c in dp_sock0)
    t1 = ", ".join(str(c) for c in dp_sock1)
    return f"""### TRex 配置 (自动生成) — 4 口 STL 线速 / 双 NUMA
- port_limit: 4
  version: 2
  interfaces:
{iface_lines}
  port_info:
    - ip: 1.1.1.1
      default_gw: 1.1.1.2
    - ip: 2.2.2.2
      default_gw: 2.2.2.1
    - ip: 3.3.3.3
      default_gw: 3.3.3.2
    - ip: 4.4.4.4
      default_gw: 4.4.4.3
  memory:
    mbuf_64:    32767
    mbuf_128:   32767
    mbuf_256:   16383
    mbuf_512:   16383
    mbuf_1024:  16383
    mbuf_2048:  16383
    mbuf_9k:    16383
  platform:
    master_thread_id: {master_id}
    latency_thread_id: {latency_id}
    dual_if:
      - socket: 0
        threads: [{t0}]
      - socket: 1
        threads: [{t1}]
"""
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `CFG4_OK len ...`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/deploy.py tests/verify_offline_stl.py
git commit -m "feat(deploy): render 4-port/dual-socket trex_cfg + pick_cores

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Socket core detection + Mellanox deps + write helper (`deploy.py`)

**Files:**
- Modify: `trex_sim/deploy.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — append:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `AttributeError: ... 'parse_phys_cores_by_socket'`

- [ ] **Step 3: Implement** — append to `trex_sim/deploy.py`:

```python
# Mellanox(mlx5) 用 bifurcated 驱动，不绑 vfio；TRex 需 rdma-core/ibverbs
MLX_DEPS = ["rdma-core", "ibverbs-providers", "libibverbs1"]


def parse_phys_cores_by_socket(lscpu_p: str) -> dict:
    """解析 `lscpu -p=CPU,CORE,SOCKET` → {socket: [物理核的首个逻辑核, ...]}。
    每个物理 core 只取第一次出现的逻辑 CPU，跳过 SMT 兄弟核。"""
    seen_core = set()
    by_sock: dict = {}
    for line in lscpu_p.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            cpu, core, sock = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if core in seen_core:
            continue
        seen_core.add(core)
        by_sock.setdefault(sock, []).append(cpu)
    for s in by_sock:
        by_sock[s].sort()
    return by_sock


def detect_socket_phys_cores(ssh) -> dict:
    code, out, _ = ssh.exec("lscpu -p=CPU,CORE,SOCKET")
    if code != 0:
        raise RuntimeError("lscpu 不可用，无法确定 NUMA 物理核拓扑")
    return parse_phys_cores_by_socket(out)


def write_trex_config_4port(ssh, pcis: list, dp_sock0: list, dp_sock1: list,
                            master_id: int = 0, latency_id: int = 1):
    cfg = render_trex_cfg_4port(pcis, dp_sock0, dp_sock1, master_id, latency_id)
    ssh.put_privileged(cfg, "/etc/trex_cfg.yaml")
    D.success("4 口 TRex 配置已写入 /etc/trex_cfg.yaml")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `SOCKCORES_OK {0: [0, 1], 1: [32, 33]}`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/deploy.py tests/verify_offline_stl.py
git commit -m "feat(deploy): socket phys-core detection + Mellanox deps + 4port write

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Cell matrix + line-rate (`stl_profile.py`)

**Files:**
- Create: `trex_sim/stl_profile.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — append:

```python
from trex_sim import stl_profile as sp
assert round(sp.line_rate_pps(64)) == 148809523  # 100G@64B ≈ 148.81 Mpps
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `ModuleNotFoundError: No module named 'trex_sim.stl_profile'`

- [ ] **Step 3: Implement `trex_sim/stl_profile.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `CELLS_OK 8 2`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/stl_profile.py tests/verify_offline_stl.py
git commit -m "feat(stl): cell matrix (direction/group x size) + line-rate pps

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Remote STL automation generator (`stl_profile.render_stl_runner_script`)

**Files:**
- Modify: `trex_sim/stl_profile.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — append:

```python
script = sp.render_stl_runner_script(
    cells=sp.build_cells("seq", [64, 1500]),
    dest_macs=["bb:00", "bb:01", "aa:00", "aa:01"],
    trex_dir="/opt/trex/3.03", rate_percent=100, duration=20)
compile(script, "<stl>", "exec")          # 必须是合法 Python
assert "STLClient" in script and "get_pgid_stats" in script
assert "TREX_CELL:" in script and '"size": 64' in script
print("STL_SCRIPT_OK len", len(script))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `AttributeError: ... 'render_stl_runner_script'`

- [ ] **Step 3: Implement** — append to `trex_sim/stl_profile.py`. The remote body is static; only four values are injected (cells JSON, dest_macs JSON, rate, duration, trex_dir):

```python
_STL_TEMPLATE = '''\
import sys, time, json
sys.path.insert(0, "{trex_dir}/automation/trex_control_plane/interactive")
from trex.stl.api import STLClient, STLStream, STLPktBuilder, STLTXCont, STLFlowLatencyStats
from scapy.all import Ether, IP, UDP

CELLS = json.loads(\'\'\'{cells_json}\'\'\')
DEST_MACS = json.loads(\'\'\'{dest_macs_json}\'\'\')
RATE = "{rate_percent}%"
DURATION = {duration}

def make_pkt(dst_mac, size):
    base = Ether(dst=dst_mac) / IP(src="16.0.0.1", dst="48.0.0.1") / UDP(sport=1025, dport=12)
    pad = max(0, size - len(base) - 4)   # -4: FCS
    return STLPktBuilder(pkt=base / ("x" * pad))

c = STLClient(server="127.0.0.1")
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
                mode=STLTXCont(percentage=float(RATE.rstrip("%"))),
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
    return _STL_TEMPLATE.format(
        trex_dir=trex_dir,
        cells_json=cells_to_json(cells),
        dest_macs_json=json.dumps(dest_macs),
        rate_percent=rate_percent,
        duration=duration)
```

Note: `_STL_TEMPLATE` uses `.format()`, so the only `{...}` in the body are the five named fields. There are no other literal braces in the template (dict literals are avoided; the script reads JSON instead) — keep it that way so `.format()` does not choke.

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `STL_SCRIPT_OK len ...`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/stl_profile.py tests/verify_offline_stl.py
git commit -m "feat(stl): generate remote STL automation script (per-cell pg stats)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Stats parsing & judging (`stl_runner.py`)

**Files:**
- Create: `trex_sim/stl_runner.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — append:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `ModuleNotFoundError: No module named 'trex_sim.stl_runner'`

- [ ] **Step 3: Implement `trex_sim/stl_runner.py`** (parsing/judging portion):

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `JUDGE_OK`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/stl_runner.py tests/verify_offline_stl.py
git commit -m "feat(stl): cell parse + loss-threshold judging

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Summary rows + table render (`stl_runner.summarize`, `display.stl_table`)

**Files:**
- Modify: `trex_sim/stl_runner.py`, `trex_sim/display.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — append:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `AttributeError: ... 'summarize'`

- [ ] **Step 3: Implement** — append to `trex_sim/stl_runner.py`:

```python
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
```

And append to `trex_sim/display.py`:

```python
def stl_table(rows: list):
    """打印 STL 结果矩阵（rows = summarize() 的行 dict 列表）。"""
    if not rows:
        print("  (无结果)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    header = "  ".join(f"{c:<{widths[c]}}" for c in cols)
    print(f"\n{BOLD}{header}{RESET}")
    print("─" * len(header))
    for r in rows:
        line = "  ".join(f"{str(r[c]):<{widths[c]}}" for c in cols)
        color = GREEN if r.get("判定") == "PASS" else RED
        print(f"{color}{line}{RESET}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints a small table then `SUMMARY_OK FAIL`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/stl_runner.py trex_sim/display.py tests/verify_offline_stl.py
git commit -m "feat(stl): summary rows + colored result table

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Restore subcommand logic (`restore.py`)

**Files:**
- Create: `trex_sim/restore.py`
- Test: `tests/verify_offline_stl.py`

- [ ] **Step 1: Write the failing test** — append:

```python
from trex_sim import restore
# mlx5：无需 rebind（bifurcated，留在内核）
assert restore.needs_rebind(["mlx5_core", "mlx5_core", "mlx5_core", "mlx5_core"]) is False
# Intel：需要 rebind
assert restore.needs_rebind(["ice", "ice", "mlx5_core", "ice"]) is True
print("RESTORE_OK")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `ModuleNotFoundError: No module named 'trex_sim.restore'`

- [ ] **Step 3: Implement `trex_sim/restore.py`**

```python
"""测试后还原：停 TRex；Intel/vfio 口 rebind 回内核（Mellanox 无需）。"""
from .topo import is_mellanox
from . import display as D


def needs_rebind(drivers: list) -> bool:
    """只要有任一非 mlx5 口（曾绑 vfio）就需要 rebind。"""
    return any(not is_mellanox(d) for d in drivers)


def stop_trex(ssh):
    ssh.exec("pkill -f 't-rex-64' 2>/dev/null || true")
    D.success("TRex 已停止")


def restore_ports(ssh, ports):
    """ports: topo.PortInfo 列表（含原始 driver）。"""
    stop_trex(ssh)
    drivers = [p.driver for p in ports]
    if not needs_rebind(drivers):
        D.info("全部为 Mellanox(mlx5)，网卡始终在内核，无需 rebind")
        return
    bind = "dpdk-devbind.py"
    for p in ports:
        if is_mellanox(p.driver):
            continue
        code, _, err = ssh.exec(f"{bind} --bind={p.driver} {p.pci} 2>/dev/null || true")
        D.info(f"rebind {p.name} ({p.pci}) -> {p.driver}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `RESTORE_OK`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/restore.py tests/verify_offline_stl.py
git commit -m "feat(restore): stop TRex + driver-aware rebind (mlx5 no-op)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Orchestration (`stl_runner.run_4dir`)

**Files:**
- Modify: `trex_sim/stl_runner.py`
- Test: real-machine (lab box) + a FakeSSH smoke check below.

This is SSH/TRex-runtime glue; offline we only smoke-check that it wires the helpers (no live traffic). Verify fully on the lab box.

- [ ] **Step 1: Write the failing test** — append (smoke: builds config/script via FakeSSH that records uploads and replays canned detect output, but does NOT run TRex — we stop before daemon start by passing `dry_run=True`):

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `AttributeError: ... 'run_4dir'`

- [ ] **Step 3: Implement** — append to `trex_sim/stl_runner.py`:

```python
from . import topo, deploy, display as D
from .stl_profile import build_cells, render_stl_runner_script
from . import restore as restore_mod

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
    total = cores_per_socket * 2 + 2
    ssh.exec(f"cd {trex_dir} && ./t-rex-64 -i --no-scapy-server -c {cores_per_socket} "
             f"--cfg /etc/trex_cfg.yaml > /tmp/trex_daemon.log 2>&1 &")
    import time as _t; _t.sleep(8)
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
                D.info(f"{jc['label']} dir{s['dir']}: rx={_g(s.get('rx_bps',0))}Gbps "
                       f"loss={s['loss_pct']}% -> {s['verdict']}")
        elif line.strip():
            D.stream_line(line)
    ssh.exec_stream(f"python3 {REMOTE_STL_SCRIPT}", on_line=on_line,
                    timeout=len(cells) * (duration + 40) + 120)

    D.step("还原（停 TRex；mlx5 无需 rebind）")
    restore_mod.restore_ports(ssh, ports)

    rows, overall = summarize(judged)
    return {"cells": len(cells), "dry_run": False, "rows": rows, "overall": overall}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py`
Expected: prints `RUN4DIR_DRY_OK`.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/stl_runner.py tests/verify_offline_stl.py
git commit -m "feat(stl): run_4dir orchestration (detect/config/run/restore)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: CLI subcommands `run-4dir` and `restore` (`cli.py`)

**Files:**
- Modify: `trex_sim/cli.py`
- Test: `tests/verify_offline_stl.py` (command registration) + manual `--help`.

- [ ] **Step 1: Write the failing test** — append:

```python
from trex_sim import cli as _cli
cmds = _cli.cli.commands.keys()
assert "run-4dir" in cmds and "restore" in cmds
print("CLI_OK", sorted(cmds))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/verify_offline_stl.py`
Expected: `AssertionError` (run-4dir not registered)

- [ ] **Step 3: Implement** — add to `trex_sim/cli.py` (after the existing `run` command). Reuse `_ssh_opts`, `add_opts`, `resolve_ssh_inputs`, `make_ssh`, `connect_and_escalate`, `D`, and import `from . import stl_runner, restore as restore_mod, topo, deploy as dep`:

```python
@cli.command(name="run-4dir")
@add_opts(_ssh_opts)
@click.option("--ifaces-names", default=",".join(__import__("trex_sim.topo", fromlist=["x"]).NIC_NAMES_DEFAULT),
              show_default=True, help="4 个内核网卡名（NUMA 对齐顺序，逗号分隔）")
@click.option("--sizes", default="64,128,512,1500,9000", show_default=True, help="包长扫描（逗号分隔）")
@click.option("--mode", type=click.Choice(["seq", "group"]), default="seq", show_default=True)
@click.option("--duration", "-d", default=20, show_default=True, help="每个测量单元时长(秒)")
@click.option("--rate-percent", default=100, show_default=True, help="发送速率占线速百分比")
@click.option("--loss-thresh", default=0.1, show_default=True, help="丢包率判定阈值(%)")
@click.option("--cores-per-socket", default=16, show_default=True, help="每 socket DP 核数(专用机可调大)")
@click.option("--skip-deploy", is_flag=True, default=False)
def run_4dir(host, user, password, key, port, sudo_password,
             ifaces_names, sizes, mode, duration, rate_percent, loss_thresh,
             cores_per_socket, skip_deploy):
    """STL 4 口线速测试（方向/分组 × 包长，测线速/丢包/时延）。"""
    host, user, password, key = resolve_ssh_inputs(host, user, password, key)
    names = [s.strip() for s in ifaces_names.split(",") if s.strip()]
    size_list = [int(s) for s in sizes.split(",") if s.strip()]
    D.header(f"TRex STL 4 口测试  →  {user}@{host}:{port}")
    ssh = make_ssh(host, user, password, key, port)
    try:
        connect_and_escalate(ssh, password, sudo_password)
        if not skip_deploy:
            from . import deploy as dep
            dep.install_deps(ssh)
            for pkg in dep.MLX_DEPS:
                ssh.exec(f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkg} || true")
            if not dep.check_installed(ssh):
                dep.download_trex(ssh)
        res = stl_runner.run_4dir(
            ssh, names=names, mode=mode, sizes=size_list, duration=duration,
            rate_percent=rate_percent, loss_thresh=loss_thresh,
            cores_per_socket=cores_per_socket, trex_dir=dep.TREX_INSTALL_DIR)
        from . import display as _D
        _D.header(f"STL 结果汇总  模式={mode}  阈值={loss_thresh}% 丢包")
        _D.stl_table(res.get("rows", []))
        _D.success(f"总体结果: {res.get('overall', 'N/A')}")
        sys.exit(0 if res.get("overall") == "PASS" else 2)
    except SystemExit:
        raise
    except Exception as e:
        D.error(str(e))
        if "--debug" in sys.argv:
            traceback.print_exc()
        sys.exit(1)
    finally:
        ssh.disconnect()


@cli.command()
@add_opts(_ssh_opts)
@click.option("--ifaces-names", default=",".join(__import__("trex_sim.topo", fromlist=["x"]).NIC_NAMES_DEFAULT))
def restore(host, user, password, key, port, sudo_password, ifaces_names):
    """停 TRex 并把 4 口还原回内核（Mellanox 为空操作）。"""
    host, user, password, key = resolve_ssh_inputs(host, user, password, key)
    names = [s.strip() for s in ifaces_names.split(",") if s.strip()]
    ssh = make_ssh(host, user, password, key, port)
    try:
        connect_and_escalate(ssh, password, sudo_password)
        ports = topo.detect_ports(ssh, names)
        restore_mod.restore_ports(ssh, ports)
        D.success("还原完成")
    except Exception as e:
        D.error(str(e)); sys.exit(1)
    finally:
        ssh.disconnect()
```

Also add near the top imports of `cli.py`: `from . import stl_runner, topo` and `from . import restore as restore_mod`.

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/verify_offline_stl.py` → prints `CLI_OK [...]`.
Also: `trex-sim run-4dir --help` and `trex-sim restore --help` print options without error.

- [ ] **Step 5: Commit**

```bash
git add trex_sim/cli.py tests/verify_offline_stl.py
git commit -m "feat(cli): add run-4dir (STL) and restore subcommands

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 12: Finalize offline suite marker

**Files:**
- Modify: `tests/verify_offline_stl.py`

- [ ] **Step 1: Add the final marker** — append the last line:

```python
print("ALL_OFFLINE_STL_CHECKS_PASSED")
```

- [ ] **Step 2: Run the full suite**

Run: `python tests/verify_offline_stl.py`
Expected: all `*_OK` lines then `ALL_OFFLINE_STL_CHECKS_PASSED`, exit 0.

- [ ] **Step 3: Also run the legacy suite (no regressions)**

Run: `python tests/verify_offline.py`
Expected: `ALL_OFFLINE_CHECKS_PASSED`.

- [ ] **Step 4: Commit**

```bash
git add tests/verify_offline_stl.py
git commit -m "test(stl): finalize offline verification suite

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 13: remote wrapper `--backend [iperf|trex]` selector

**Files:**
- Modify: `/c/Users/AdrianXu/Downloads/remote_bidir_4dir_iperf.sh`

- [ ] **Step 1: Add backend resolution in MAIN** — after the IP validation block (before `mkdir -p "$OUT_DIR"`), insert:

```bash
# backend 选择：iperf(默认) 或 trex(DPDK/STL，调本机 trex-sim)
BACKEND="${BACKEND:-}"
if [[ -z "$BACKEND" ]]; then
  echo -n "选择测试后端 [iperf/trex] (默认 iperf): " >&2
  IFS= read -r BACKEND || true
fi
BACKEND="$(echo "${BACKEND:-iperf}" | tr 'A-Z' 'a-z' | awk '{$1=$1;print}')"
[[ -z "$BACKEND" ]] && BACKEND="iperf"
if [[ "$BACKEND" != "iperf" && "$BACKEND" != "trex" ]]; then
  err "BACKEND 非法（仅 iperf|trex）：'$BACKEND'"; exit 1
fi
if [[ "$BACKEND" == "trex" ]] && ! have_cmd trex-sim; then
  err "未找到 trex-sim；请先安装：cd trex-simulator && pip install -e ."
  exit 1
fi
log "测试后端：$BACKEND"
```

- [ ] **Step 2: Branch in `run_on_host`** — wrap the existing scp+run block (steps 6–8 of `run_on_host`) so it only runs for iperf, and add a trex branch. Replace the section from `# 6) 下发脚本` through the run pipeline with:

```bash
  if [[ "$BACKEND" == "iperf" ]]; then
    # 6) 下发脚本
    log "$ip：下发 $LOCAL_TEST_SCRIPT -> $ip:$REMOTE_TEST_SCRIPT"
    if ! SSHPASS="$PASSWORD" sshpass -e scp "${SSH_OPTS[@]}" -- "$LOCAL_TEST_SCRIPT" "$USERNAME@$ip:$REMOTE_TEST_SCRIPT" >/dev/null 2>&1; then
      err "$ip：scp 下发失败。跳过。"; echo "[ERROR] scp 下发失败。" | tee -a "$outfile" >/dev/null; return 1
    fi
    log "$ip：开始测试（单口依次，约 4×70s）。每方向 ~60-70s 仅阶段日志属正常。"
    printf '%s\n' "$PASSWORD" | SSHPASS="$PASSWORD" sshpass -e ssh "${SSH_OPTS[@]}" -- "$USERNAME@$ip" \
      "sudo -S -p '' env IPERF_PARALLEL='$PARALLEL' bash $REMOTE_TEST_SCRIPT 2>&1" | tee -a "$outfile"
    local rc=${PIPESTATUS[1]}
    rsudo "$ip" "rm -f $REMOTE_TEST_SCRIPT" >/dev/null 2>&1 || true
  else
    # trex：调用本机 trex-sim run-4dir（自身 SSH 部署/打流/还原）
    local to="$OUT_DIR/${sn}_${ts}_trex.log"
    log "$ip：trex-sim run-4dir → $to"
    trex-sim run-4dir -H "$ip" -u "$USERNAME" -p "$PASSWORD" \
      --duration 20 --mode seq 2>&1 | tee "$to"
    local rc=${PIPESTATUS[0]}
    outfile="$to"
  fi
```

(The trex log file name uses the existing `$sn`/`$ts`; `outfile` is reassigned so the trailing summary log lines append to the trex log.)

- [ ] **Step 3: Update the file's filename comment** for the iperf path (the `$outfile` for iperf keeps `_iperf` is optional). Leave iperf filename as-is for back-compat.

- [ ] **Step 4: Verify bash syntax + LF**

Run:
```bash
f=/c/Users/AdrianXu/Downloads/remote_bidir_4dir_iperf.sh
sed -i 's/\r$//' "$f"; bash -n "$f" && echo OK
```
Expected: `OK`.

- [ ] **Step 5: Commit** (this file lives outside the repo; record it in the repo via a copy under `examples/` so it is version-controlled):

```bash
mkdir -p examples
cp /c/Users/AdrianXu/Downloads/remote_bidir_4dir_iperf.sh examples/remote_bidir_4dir_iperf.sh
git add examples/remote_bidir_4dir_iperf.sh
git commit -m "feat(wrapper): add iperf|trex backend selector to remote wrapper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 14: README + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document `run-4dir`** — add a section after the `run` docs:

```markdown
### 4. STL 4 口线速测试（run-4dir）

对固定 4 口 / 2 对拓扑做 Stateless 线速打流，按包长扫描测 线速/丢包/时延：

\`\`\`bash
trex-sim run-4dir -H <ip> -u <user> -p <pass> \
  --mode seq --sizes 64,128,512,1500,9000 --duration 20 \
  --cores-per-socket 16 --loss-thresh 0.1
\`\`\`

- `--mode seq|group`：逐方向 / 分组(1&3、2&4)并发。
- 退出码：全 PASS=0，有 FAIL=2。
- Mellanox(mlx5) 网卡留在内核(bifurcated)，跑完无需 rebind；`trex-sim restore` 可手动还原(Intel/vfio 场景)。
```

- [ ] **Step 2: Run both offline suites**

Run: `python tests/verify_offline.py && python tests/verify_offline_stl.py`
Expected: `ALL_OFFLINE_CHECKS_PASSED` and `ALL_OFFLINE_STL_CHECKS_PASSED`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document run-4dir STL test

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Push branch & update PR**

```bash
git push -u origin <implementation-branch>
```

---

## Real-Machine Verification (lab Mellanox box, after offline passes)

Not runnable here — perform on the DUT:
1. `trex-sim probe -H <ip> -u <user> -p <pass>` → hugepages + rdma-core + 4 NIC present.
2. `trex-sim run-4dir -H .. -u .. -p .. --sizes 1500,9000 --duration 15` → 1500/9000 应接近线速、丢包 ~0；与 iperf goodput 交叉对照。
3. `trex-sim run-4dir --sizes 64 --mode group` → 小包 pps 上限 + 分组并发(每卡 200G)是否撞 PCIe/核数。
4. Run `bidir_4dir_iperf.sh` afterward → 确认 mlx5 口仍可用(无需 rebind)。

---

## Self-Review

- **Spec coverage:** §4 拓扑/端口映射→Task1-2；§5.2 配置→Task3-4；§5.4 STL流量→Task5-6；§5.5 指标/判定→Task7；§5.6 输出→Task8;§2b 还原→Task9;§5.1 CLI→Task11;§2c wrapper→Task13;§10 测试→Task1-12,14;§12 文件清单→全覆盖。
- **Placeholders:** none — every step has runnable code/commands.
- **Type consistency:** `PortInfo(name,pci,numa,mac,driver)`, `Stream(dir_num,tx,rx,pg_id)`, `Cell(label,size,streams)`, `DIR_BY_NUM`, `GROUPS`, `peer_index`, `dest_macs`, `build_cells`, `render_stl_runner_script`, `parse_cell_line`, `judge_cell`, `summarize`, `run_4dir`, `restore_ports`, `stl_table` — names used consistently across Tasks 1–13.
- **Note:** `t-rex-64` STL daemon uses `-i` (interactive/stateless) not `--astf`; per-socket cores are passed via `-c` and the dual_if config.
