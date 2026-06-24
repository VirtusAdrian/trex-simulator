# TRex 打流模拟器 (trex-simulator)

基于 **Cisco TRex** 的 TCP 多核流量打流模拟器。在服务器端运行命令行工具，通过 SSH 连接到 Ubuntu 客户端设备，**自动部署 TRex**，下发 **TCP ASTF（有状态）多核流量模型**，实时回显测试过程并汇总结果。默认面向 **100 Gbps 双向**吞吐场景。

---

## 架构

```
┌──────────────────┐         SSH          ┌──────────────────────┐
│  服务器端 (本工具) │ ───────────────────► │  客户端 (Ubuntu)        │
│  trex-sim CLI    │                      │  ├─ 自动安装 TRex       │
│  - 部署          │ ◄─────────────────── │  ├─ DPDK 绑定网卡       │
│  - 下发流量模型   │   实时统计 / 结果      │  ├─ ASTF 守护进程       │
│  - 收集结果      │                      │  └─ TCP 多核打流         │
└──────────────────┘                      └──────────────────────┘
```

## 安装（在服务器端）

```bash
cd trex-simulator
pip install -e .
# 或：pip install -r requirements.txt
```

## 用法

启动前会**交互式询问**：客户端 IP、SSH 账号、密码、测试时长（任一项也可用命令行参数预先指定，跳过询问）。

### 0. 环境探测（部署前建议先跑）

检查靶机是否满足 TRex 前置条件（OS、Python、核心、内存、hugepages、数据面网卡、下载通道）：

```bash
trex-sim probe -H 192.168.1.50 -u ubuntu -k ~/.ssh/id_ed25519
trex-sim probe -H 192.168.1.50 -u ubuntu -k ~/.ssh/id_ed25519 --json   # 机器可读
```

退出码：就绪/带警告=0，未就绪=2（便于脚本判断）。输出示例见文末「环境探测结果示例」。

### 1. 一键部署并测试（推荐）

```bash
trex-sim run
```

```
客户端 IP / 主机名: 192.168.1.50
SSH 用户名 [root]: ubuntu
SSH 密码: ********
测试时长（秒） [60]: 120
```

也可全部用参数指定，实现无人值守：

```bash
trex-sim run -H 192.168.1.50 -u ubuntu -p secret -d 120 \
             --target-gbps 100 --cores 16 --payload 65536
```

### 2. 仅部署 TRex

```bash
trex-sim deploy -H 192.168.1.50 -u ubuntu -p secret
```

### 3. 查询状态

```bash
trex-sim status -H 192.168.1.50 -u ubuntu -p secret
```

> `probe` / `deploy` / `status` 同样支持留空交互输入与 `-k` 私钥登录。

### 4. STL 4 口线速测试（run-4dir）

对固定 4 口 / 2 对拓扑（`enp65s0f0np0`↔`enp161s0f0np0`、`enp65s0f1np1`↔`enp161s0f1np1`）做 **Stateless 线速**打流，按包长扫描测 **线速 / 丢包 / 时延**，作为 iperf3（内核 TCP goodput）的补充：

```bash
trex-sim run-4dir -H <ip> -u <user> -p <pass> \
  --mode seq --sizes 64,128,512,1500,9000 --duration 20 \
  --cores-per-socket 16 --loss-thresh 0.1
```

- `--mode seq|group`：逐方向（方向1-4）/ 分组（组1=方向1+3、组2=方向2+4）并发。
- `--sizes`：包长扫描（小包 64/128B 最压 NIC/PCIe/CPU 的 pps 上限）。
- `--cores-per-socket`：每 socket DP 物理核数（被测机专用、无 CPU 预留时可调大，7H12 建议 16–32）。
- 判定：目标速率下**丢包率 ≤ `--loss-thresh`%** 为 PASS；退出码 全 PASS=0、有 FAIL=2。
- **Mellanox(mlx5)** 网卡用 bifurcated 驱动留在内核，跑完无需 rebind；`trex-sim restore` 可手动还原（Intel/vfio 场景）。
- 端口按 NUMA 对齐：`card1`(bus65)=node0、`card2`(bus161)=node1，组1 让 card1 全 TX、card2 全 RX。

## 关键参数（run）

| 参数 | 说明 | 默认 |
|------|------|------|
| `-H, --host` | 客户端 IP（留空交互输入） | — |
| `-u, --user` | SSH 账号（留空交互输入） | root |
| `-p, --password` | SSH 密码（留空交互输入） | — |
| `-k, --key` | SSH 私钥（替代密码） | — |
| `--sudo-password` | sudo 密码（非 root 账号用；密码登录默认复用 SSH 密码） | 自动 |
| `-d, --duration` | 测试时长（秒，留空交互输入） | 60 |
| `--target-gbps` | 目标双向吞吐，自动估算 CPS | 100 |
| `-c, --cores` | 分配核心数（100G 建议 ≥16） | 16 |
| `--payload` | HTTP 响应体大小（字节） | 65536 |
| `--reqs-per-conn` | 单连接内请求循环次数 | 100 |
| `--cps` | 每核每秒新建连接数（留空自动估算） | 自动 |
| `-i, --interfaces` | 数据面网卡 PCI（留空自动检测） | 自动 |
| `--skip-deploy` | 跳过部署（TRex 已装） | off |

## 100 Gbps 双向调优要点

工具已为高吞吐做了如下默认配置，可按硬件微调：

- **64 KB payload + 单连接多次请求循环** —— 降低握手开销，让带宽快速拉满。
- **16 核数据面线程** —— 多核并行收发；核心越多上限越高。
- **大 mbuf 内存池 + 百万级 dp_flows** —— 见自动生成的 `/etc/trex_cfg.yaml`。
- **DPDK 网卡绑定** —— 优先 `vfio-pci`，回退 `igb_uio`。

> 实际打满 100G 还依赖客户端网卡（建议 100G NIC，如 Intel E810 / Mellanox CX5/6）、CPU 核数与 NUMA 亲和、以及大页内存（hugepages）配置。

> **关于 `--target-gbps` / `--cps`**：`--target-gbps` 仅用于推算一个**起始** CPS 基数，是理论估算、未经真实流量标定。打满目标带宽的实际做法是：先跑起来，再用 `-m` 倍率参数边调边看每 2 秒的实时 bps 回显，逼近目标后固定。也可直接用 `--cps` 指定基数、用 `-m` 控总量。

## 客户端前置条件

`trex-sim probe` 会逐项检查以下条件：

- Ubuntu（已在 20.04 / 22.04 / 26.04 上验证代码运行）
- Python3（已验证 3.14）
- 至少 2 个数据面网卡（除管理口外），用于流量收发
- root 或具备 sudo 的账号
- 已配置 hugepages（TRex 启动需要）
- 可访问 `trex-tgn.cisco.com` 下载安装包（或预置离线包于 `/tmp`）

## 非 root 账号（sudo）

安装依赖、写入 `/etc/trex_cfg.yaml`、绑定 DPDK、启动 TRex 都需要 root 权限。连接后工具会**自动检测登录账号**：

- **root 登录** —— 直接执行，无需额外配置。
- **非 root 账号（如 `ubuntu`）** —— 自动用 `sudo` 执行特权命令：
  - **密码登录**：默认复用 SSH 密码作为 sudo 密码，无需额外参数。
  - **密钥登录**：若 sudo 需要密码，用 `--sudo-password` 提供；若已配置免密 sudo（NOPASSWD），则无需任何参数。
  - 写入 `/etc` 等 root 目录时，会先经 SFTP 上传到 `/tmp` 再用 `sudo mv` 落位（SFTP 自身不带 sudo）。

连接后会打印一行提示，如 `非 root 账号，特权命令将通过 sudo + 密码 执行`。若账号不具备 sudo 权限，`deploy` / `run` 会**提前明确报错**而非中途失败。

```bash
# 非 root + 密码登录（sudo 密码自动复用）
trex-sim run -H 10.0.0.5 -u ubuntu -p secret

# 非 root + 密钥登录 + 单独的 sudo 密码
trex-sim run -H 10.0.0.5 -u ubuntu -k ~/.ssh/id_ed25519 --sudo-password secret
```

## 故障排查

### 下载 TRex 失败（`wget exit=5` SSL 证书校验失败）

常见于有 SSL 中间代理或 CA 证书过期的环境。工具会**自动按阶梯重试**：校验下载 → 更新 CA 证书重试 → `--no-check-certificate` 跳过校验 → `curl` 兜底，并校验下载包是否为合法 gzip（拦截代理返回的 HTML 错误页）。多数情况无需干预即可通过。

若全部失败（网络被完全限制），**预置离线包**即可绕过下载：在有外网的机器下载，scp 到客户端的固定路径，再重跑（工具检测到合法安装包会自动跳过下载）：

```bash
# 任意有外网的机器
wget --no-check-certificate -O trex-3.03.tar.gz https://trex-tgn.cisco.com/trex/release/v3.03.tar.gz
scp trex-3.03.tar.gz ubuntu@<客户端>:/tmp/trex-3.03.tar.gz
# 然后重跑 trex-sim run / deploy
```

> 若上一次失败残留了空的/损坏的 `/tmp/trex-3.03.tar.gz`，新版会自动识别并重下；旧版可先 `rm -f /tmp/trex-3.03.tar.gz` 再重跑。

## 项目结构

```
trex_sim/
├── cli.py       # 命令行入口（交互输入 + 子命令 probe/deploy/run/status）
├── ssh.py       # SSH 连接 / 命令流式执行 / 文件上传
├── probe.py     # 环境探测：部署前的前置条件检查
├── deploy.py    # TRex 自动下载、依赖安装、网卡检测、DPDK 绑定、配置生成
├── profile.py   # TCP ASTF 多核流量模型生成
├── runner.py    # 启动守护进程、下发测试、实时统计采集、结果汇总
└── display.py   # 终端彩色输出
tests/
├── verify_offline.py  # 离线纯逻辑验证（无需 SSH/DPDK）
└── verify_ssh.py      # SSH 层 + 网卡检测实机验证（对任意可 SSH 主机安全）
```

## 验证

封装了两个验证脚本，**不部署 TRex、不绑定 DPDK、不打流量**，因此对任意主机都安全，可在部署前确认代码与连通性。

```bash
# 1) 离线纯逻辑（流量模型生成、配置生成、CPS 估算、统计格式化）——本地即可跑
python tests/verify_offline.py

# 2) SSH 层 + 网卡检测——对着一台真实可达主机跑
python tests/verify_ssh.py -H <host> -u <user> -k <key_file>
python tests/verify_ssh.py -H <host> -u <user> -p <password>
```

全部通过时分别打印 `ALL_OFFLINE_CHECKS_PASSED` / `ALL_SSH_CHECKS_PASSED`。

### 已验证结果

已在一台 Ubuntu 26.04 / Python 3.14 / 16 核的机器上验证（该机仅 1 块虚拟网卡、无 hugepages，故只做到代码与 SSH 层验证，未做真实 DPDK 打流）：

- `verify_offline.py` —— 全部通过：7 模块编译、流量模型可编译、16 核配置生成、网卡校验、统计格式化。
- `verify_ssh.py` —— 全部通过：密钥认证连接、命令执行、逐行流式输出、SFTP 上传、安装检测、网卡枚举（正确排除管理口）。
- **非 root sudo 路径** —— 用一个临时 sudo 账号验证通过：自动检测非 root、`sudo -S` 密码注入、`put_privileged` 经 `/tmp` + `sudo mv` 写入 `/etc` 并读回。
- `trex-sim --help` / `run --help` / `probe` —— 四个子命令与全部交互参数就绪。

### 环境探测结果示例

`trex-sim probe` 在上述机器上的实际输出（正确判定为「未就绪」）：

```
[环境探测报告]
  ✔ 操作系统      Ubuntu 26.04 LTS
  ✔ Python3       Python 3.14.4
  ✔ CPU 核心      16 核（100G 建议 ≥16）
  ✔ 内存          30.4 GiB
  ✘ Hugepages     HugePages_Total=0（TRex 启动需要，请配置大页内存）
  ✘ 数据面网卡     未检测到数据面网卡（除管理口外需 ≥2 块，用于流量收发）
  ⚠ TRex          TRex 未安装（运行 deploy 将自动安装到 /opt/trex/3.03）
  ⚠ 下载通道       无法访问 trex-tgn.cisco.com（需预置离线包于 /tmp）
  ✘ 未就绪：2 项不满足，2 项警告。请先处理 FAIL 项再部署。
```

在**标准测试环境**（双 100G 网卡 + hugepages）上，前两项 ✘ 应变为 ✔，即可继续 `deploy` / `run`。
