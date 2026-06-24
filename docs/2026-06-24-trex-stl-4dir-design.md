# 设计：TRex Stateless 4 口线速测试 + remote 选择器（run-4dir）

- 日期：2026-06-24
- 状态：设计待评审
- 关联：第一步（iperf jumbo+NUMA）已完成并真机验证（单口 ~99–100G）；本文为第二步。
- 涉及代码：
  - `trex-simulator/`（独立 git repo）：新增 STL 测试子命令与配套模块
  - `C:\Users\AdrianXu\Downloads\remote_bidir_4dir_iperf.sh`（bash wrapper，非 repo 内）：新增 backend 选择器

---

## 1. 背景与目标

iperf3（内核 TCP）单口顺序在 1500 MTU 下顶 ~94G goodput，是**帧开销天花板**而非硬件瓶颈；jumbo(9000)+NUMA 绑核后达 ~99–100G goodput，已证明 7H12/PCIe4.0 x16 不是限制。

iperf3 测的是 TCP goodput，无法直接给出：① **真实线速 bps（含包头）**、② **小包（64/128B）线速 / pps 能力**（最压 NIC/PCIe/CPU、最能暴露瓶颈）、③ **丢包率/时延**。

目标：用 TRex **Stateless（STL）** 在固定 4 口 2 对拓扑上做 L2 线速打流，按包长扫描测 **达成 Gbps / Mpps / 丢包率 / 时延**，并以"目标速率下丢包率 ≤ 阈值"判定，作为 iperf 的补充手段；通过 remote wrapper 的 `[iperf|trex]` 选择器统一入口。

被测网卡：**Mellanox CX5/6（mlx5，bifurcated 驱动）**，AMD EPYC 7H12 ×2、1024G 内存、每卡 PCIe4.0 x16、分布在两个 NUMA 节点。

## 2. 范围 / 非目标

**范围**
- trex-simulator 新增 `run-4dir` 子命令：STL 4 口线速测试（方向/分组、包长扫描、丢包/时延、判定、结果汇总）。
- 4 口 + 双 NUMA 的 `trex_cfg.yaml` 生成；mlx5 PMD 原地使用（不绑 vfio）。
- `restore` 子命令（Mellanox 下基本空操作；自动探测，兼容未来 Intel/vfio 路径）。
- remote wrapper 加 `--backend [iperf|trex]` 选择器，trex 分支调 `trex-sim run-4dir`，结果存 `iperf_4dir_100G/<SN>_<日期>_trex.log`。

**非目标（本期不做，留扩展）**
- RFC2544 二分查找无丢包最大速率（先用固定速率 + 丢包阈值）。
- ASTF（有状态 TCP）4 口模式（已有 2 口 ASTF，不在本期改造）。
- Intel/vfio 真机路径的完整实现与验证（仅保留自动探测分支与还原骨架）。
- IMIX 混合包长（先做单一包长扫描）。

## 3. 总体架构与数据流

```
remote_bidir_4dir_iperf.sh (本地 Ubuntu, bash)
  └─ 输入 IP/账号/密码 + 选择 backend
       ├─ iperf → scp bidir_4dir_iperf.sh + sudo 运行   (现有)
       └─ trex  → 取 SN 定文件名
                   └─ trex-sim run-4dir -H.. -u.. -p.. --sizes.. --duration..
                        │  (trex-simulator 自己 SSH 到靶机)
                        ├─ probe/deploy（装 TRex + rdma-core/ibverbs + hugepages）
                        ├─ 映射 4 口: 名字→PCI/NUMA/MAC/驱动（绑前读，mlx5 不绑）
                        ├─ 生成 4 口/双 socket trex_cfg.yaml
                        ├─ 启动 TRex STL 守护进程
                        ├─ 按 方向/分组 × 包长 跑 STL 流，采 tx/rx/loss/latency
                        ├─ restore（mlx5：停 TRex 即可）
                        └─ 实时回显 + 汇总表
                   └─ tee → iperf_4dir_100G/<SN>_<日期>_trex.log
```

iperf 与 trex **一次只跑一个 backend**；mlx5 网卡始终在内核，切换无需 rebind。

## 4. 拓扑与端口映射

固定 4 口（与 iperf 脚本一致），物理对连：

| 子网/对 | A 口 | B 口 |
|---|---|---|
| 对 A | enp65s0f0np0 | enp161s0f0np0 |
| 对 B | enp65s0f1np1 | enp161s0f1np1 |

**TRex 端口顺序按 NUMA 对齐**（`interfaces` 列表顺序决定 idx 与 dual_if 配对）：

| TRex idx | 网卡 | 卡/NUMA | 物理对端 idx |
|---|---|---|---|
| 0 | enp65s0f0np0  | card1 / node0 | 2 |
| 1 | enp65s0f1np1  | card1 / node0 | 3 |
| 2 | enp161s0f0np0 | card2 / node1 | 0 |
| 3 | enp161s0f1np1 | card2 / node1 | 1 |

对连关系：(0↔2)、(1↔3)。`dual_if[0]`=端口 0/1（card1, socket0 本地核），`dual_if[1]`=端口 2/3（card2, socket1 本地核）→ NUMA 对齐。

**方向（对齐 iperf 的四方向）**：
- 方向1：TX0 → RX2；方向2：TX2 → RX0；方向3：TX1 → RX3；方向4：TX3 → RX1。

**分组**（`--mode group`）：组1=方向1+方向3 = TX{0,1}(card1) → RX{2,3}(card2)；组2=方向2+方向4 = TX{2,3} → RX{0,1}。组内两口同向，正好 card1 全 TX / card2 全 RX，NUMA 均衡，且每卡单向 200G ≈ PCIe4.0 x16 的 ~80%（不撞 PCIe）。

每口 dest MAC = 其物理对端口的 MAC（绑前从 `/sys/class/net/<name>/address` 读取并按上表配对）。

## 5. 组件 2a：`trex-sim run-4dir`（STL）

### 5.1 CLI 选项
复用共享 SSH 选项（`-H/-u/-p/-k/--port/--sudo-password`），新增：
- `--ifaces-names`：4 个内核网卡名（默认 `enp65s0f0np0,enp65s0f1np1,enp161s0f0np0,enp161s0f1np1`，**已按 NUMA 对齐顺序**）。
- `--sizes`：包长列表，默认 `64,128,512,1500,9000`。
- `--mode`：`seq`（逐方向，默认）| `group`（1&3、2&4 并发）。
- `--duration` / `-d`：每个"测量单元"（方向/分组 × 包长）时长，默认 `20`。
- `--rate-percent`：发送速率占线速百分比，默认 `100`。
- `--loss-thresh`：丢包率判定阈值（%），默认 `0.1`。
- `--cores-per-socket`：每 socket DP 核数，默认 `16`（被测机为专用测试机、CPU 无预留，可大胆调大）。7H12 每 socket 64 物理核，小包线速建议 `16–32`；取各 socket 本地物理核，避开 master(0)/latency(1) 与 SMT 兄弟核，确保测的是 NIC/PCIe 上限而非核数不足。
- `--skip-deploy`：跳过部署。

### 5.2 配置生成（`trex_cfg.yaml`，4 口 + 双 socket）
扩展 `deploy.write_trex_config` 或新增 `write_trex_config_4port`：
- `port_limit: 4`、`interfaces:` 按 NUMA 对齐顺序填 4 个 PCI。
- `platform.dual_if`：两条，`{socket:0, threads:[node0 本地核...]}` 与 `{socket:1, threads:[node1 本地核...]}`；每 socket 取 `--cores-per-socket` 个本地**物理核**（从各 socket 的 `/sys` cpulist 选，避开 master(0)/latency(1) 与 SMT 兄弟核）。被测机专用、无预留，可放心多分配。
- mlx5：**不写 vfio 绑定**；TRex 自动用 mlx5 PMD。Mellanox 需 hugepages + rdma-core/ibverbs。
- `memory` 段沿用现有大 mbuf；STL 不需要 ASTF 的 dp_flows，可精简。

### 5.3 端口/驱动探测（绑前，run-4dir 内）
对 4 个内核名各读：PCI（`ethtool -i`/`/sys/.../device`）、NUMA（`/sys/class/net/<n>/device/numa_node`）、MAC（`/sys/class/net/<n>/address`）、驱动（`ethtool -i`）。校验 4 口齐全、对连 MAC 成对。记录驱动用于 restore 判断（mlx5 → 无需还原）。

### 5.4 STL 流量与编排
在上传到靶机的自动化脚本里用 `trex.stl.api`：
- 每方向构造 `STLStream`：`STLPktBuilder(Ether(dst=peer_mac)/IP(src,dst)/UDP/padding 到目标包长)`，`mode=STLTXCont(percentage=rate_percent)`，`flow_stats=STLFlowLatencyStats(pg_id=方向号)`（同时给丢包与时延；如需更高速可拆一条 bulk + 一条 latency 流，本期先用单流 latency stats）。
- `seq` 模式：对每个包长，逐方向 `c.add_streams(stream, ports=[tx])` → `c.start(ports=[tx], duration=d)` → 等待 → `get_stats()` + `get_pgid_stats()` → `c.stop()`/`c.remove_all_streams()`。
- `group` 模式：对每个包长，同时在两 TX 口加流并 `c.start(ports=[tx1,tx2], duration=d)`，一并采集。
- 实时每 ~2s 回显当前 tx/rx Gbps、Mpps（复用 display 风格）。

### 5.5 指标采集与判定
每个测量单元（方向/分组 × 包长）输出：
- `目标速率`（该包长 100G 线速换算）、`发送 Gbps/Mpps`、`接收 Gbps/Mpps`（对端口）、`丢包率%`=（tx_pkts−rx_pkts)/tx_pkts、`时延 平均/最大/抖动`（来自 latency pg）。
- **判定**：`丢包率 ≤ --loss-thresh` → PASS，否则 FAIL。（可选附加：接收线速% ≥ 某值。）

### 5.6 输出
- 实时：每测量单元开始/结束 + 每 2s 速率行。
- 汇总表：行=（方向/分组 × 包长），列=`包长 / 方向 / 目标Gbps / 发送Gbps / 接收Gbps / Mpps / 丢包% / 时延us / 判定`；末尾总体 PASS/FAIL。
- 复用 `display` 的彩色输出；`run_4dir` 返回结构化结果供上层（含 wrapper 落盘）。

## 6. 组件 2b：绑定 / 还原

- **Mellanox(mlx5)**：检测到 mlx5 驱动 → **不绑 vfio、不解绑**，TRex 用 mlx5 PMD 原地收发；`enpXX` 始终存在。`restore` 仅需 `pkill t-rex-64`。部署增加 Mellanox 依赖：`rdma-core ibverbs-providers libibverbs1`（+ 现有 DEPS）+ 确认 hugepages。
- **Intel(ice/i40e，未来)**：检测到非 mlx5 → 走现有 vfio-pci 绑定；测前记录原内核驱动，`restore` 用 `dpdk-devbind.py --bind=<原驱动>` 还原 → 内核重建接口。本期仅保留分支与骨架，不做真机验证。
- 新增 `trex-sim restore` 子命令：停 TRex；按记录的驱动逐口判断是否需要 rebind。run-4dir 结束自动调用还原逻辑。

## 7. 组件 2c：remote wrapper 选择器

`remote_bidir_4dir_iperf.sh`：
- 新增 backend 选择：环境变量/参数 `--backend [iperf|trex]`，或交互提示（默认 `iperf`）。
- `iperf` 分支：现有流程不变。
- `trex` 分支：
  - 仍先 SSH 取 SN（dmidecode）定文件名 `iperf_4dir_100G/<SN>_<日期时间>_trex.log`。
  - 检测本机有无 `trex-sim`（`command -v trex-sim`）；缺失则报错提示 `cd trex-simulator && pip install -e .`。
  - 调用 `trex-sim run-4dir -H <ip> -u <user> -p <pass> --sizes <..> --duration <..> --mode <..> 2>&1 | tee -a <outfile>`，rc 取 `PIPESTATUS`。
  - 多台目标沿用现有 for 循环；逐台落盘。
- 密码传递沿用现有安全方式（`trex-sim` 接受 `-p`；注意密码出现在子进程命令行——可改用 `--password-stdin` 或环境变量，作为加固项记录，见 §9）。

## 8. 配置与默认值汇总

| 项 | 默认 | 说明 |
|---|---|---|
| sizes | 64,128,512,1500,9000 | 包长扫描 |
| mode | seq | seq 逐方向 / group 分组并发 |
| duration | 20s | 每测量单元 |
| rate-percent | 100 | 发送占线速比例 |
| loss-thresh | 0.1% | 判定阈值 |
| cores-per-socket | 16 | DP 核/每 socket（专用机可调至 32+，绑本地物理核） |
| 输出 | iperf_4dir_100G/<SN>_<日期>_trex.log | 与 iperf 同目录 |

## 9. 错误处理

- 4 口探测不全 / 对连 MAC 不成对 → 明确报错并退出（不打流）。
- mlx5 依赖缺失（rdma-core/hugepages）→ probe 报 FAIL；deploy 自动补装；仍失败则明确报错。
- TRex 守护进程起不来 → 回显 `/tmp/trex_daemon.log` 末尾并退出。
- 某测量单元异常 → 记该单元 FAIL（loss=100% 或 error），继续其余单元；总体 FAIL。
- run-4dir 退出务必经过还原逻辑（trap/finally）→ 不把靶机留在异常态。
- 加固项（记录、非阻塞）：wrapper→trex-sim 的密码尽量走 stdin/env 而非命令行参数（`ps` 可见）。

## 10. 测试与验证

- **离线逻辑**（本机，仿 `tests/verify_offline.py`）：端口名→PCI/NUMA/MAC 映射与对连配对、4 口 trex_cfg.yaml 生成、STL 自动化脚本字符串生成、方向/分组编排顺序、统计/丢包/判定格式化、汇总表。新增 `tests/verify_offline_stl.py`。
- **SSH 层**（仿 `verify_ssh.py`，对任意可达主机安全、不打流）：4 口探测、驱动/ NUMA 读取。
- **真机**（lab Mellanox+TRex 机）：完整 run-4dir 一轮；与 iperf 结果交叉对照（iperf goodput vs STL 1500/9000 线速、64B 小包 pps）。本机无法替代。

## 11. 假设与待定

- 被测机为专用测试机、CPU 无预留限制 → DP 核可大胆分配（默认每 socket 16，可调至 32+），仅受物理核数与 NUMA 本地性约束。
- 假设 4 口内核名固定且 NUMA 映射为 card1=node0、card2=node1（运行时实测确认；不符则按实测 PCI/NUMA 重排 `interfaces`）。
- 假设 TRex 3.03 的 mlx5 PMD 在靶机 OFED/rdma-core 下可用（probe 验证；如需 MLNX_OFED 另行处理）。
- 时延采集先用单流 `STLFlowLatencyStats`；若高速下 latency 流影响吞吐，再拆 bulk+latency 双流（待定）。
- 判定先用固定速率+丢包阈值；RFC2544 二分留扩展。

## 12. 实现文件清单（预估）

- `trex_sim/cli.py`：新增 `run-4dir`、`restore` 子命令与选项。
- `trex_sim/topo.py`（新）：4 口名→PCI/NUMA/MAC/驱动 探测与对连配对。
- `trex_sim/deploy.py`：4 口/双 socket 配置生成；Mellanox 依赖；mlx5 跳过绑定；驱动记录。
- `trex_sim/stl_profile.py`（新）：STL 流/自动化脚本生成（按方向/分组/包长）。
- `trex_sim/stl_runner.py`（新）：编排 方向/分组 × 包长、采集、判定、汇总。
- `trex_sim/restore.py`（新）：停 TRex + 按驱动还原（mlx5 空操作）。
- `trex_sim/display.py`：STL 汇总表格式（小幅扩展）。
- `tests/verify_offline_stl.py`（新）：离线逻辑验证。
- `Downloads/remote_bidir_4dir_iperf.sh`：`--backend [iperf|trex]` 选择器 + trex 分支。
- `trex-simulator/README.md`：补充 run-4dir 用法。
