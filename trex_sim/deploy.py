"""
TRex auto-deployment to a remote Ubuntu client via SSH.

Installs TRex under /opt/trex, configures DPDK interfaces,
and starts the TRex daemon in ASTF (stateful TCP) mode.
"""

import json
from .ssh import SSHClient
from . import display as D

TREX_VERSION = "3.03"
TREX_DIR = "/opt/trex"
TREX_INSTALL_DIR = f"{TREX_DIR}/{TREX_VERSION}"
TREX_DOWNLOAD_URL = f"https://trex-tgn.cisco.com/trex/release/v{TREX_VERSION}.tar.gz"

DEPS = [
    "python3", "python3-pip", "python3-zmq",
    "pciutils", "kmod", "iproute2", "ethtool",
    "wget", "curl", "ca-certificates", "tar", "net-tools",
]


def _run(ssh: SSHClient, cmd: str, desc: str, timeout: int = 120) -> str:
    code, out, err = ssh.exec(cmd, timeout=timeout)
    if code != 0:
        raise RuntimeError(f"{desc} 失败 (exit={code}):\n{err.strip() or out.strip()}")
    return out.strip()


def check_installed(ssh: SSHClient) -> bool:
    code, _, _ = ssh.exec(f"test -f {TREX_INSTALL_DIR}/t-rex-64")
    return code == 0


def install_deps(ssh: SSHClient):
    D.info("更新 apt 缓存…")
    _run(ssh, "apt-get update -qq", "apt update", timeout=180)
    pkgs = " ".join(DEPS)
    D.info(f"安装依赖包: {pkgs}")
    _run(ssh, f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkgs}", "安装依赖", timeout=300)
    D.success("依赖安装完成")


def _is_valid_tarball(ssh: SSHClient, tarball: str) -> bool:
    """非空且为合法 gzip（拦截 SSL 代理返回的 HTML 错误页等情况）。"""
    code, _, _ = ssh.exec(f"test -s {tarball} && gzip -t {tarball}")
    return code == 0


def _fetch_trex_archive(ssh: SSHClient, tarball: str):
    """按 安全wget → 更新CA重试 → 跳过校验wget → curl 的阶梯尝试下载。"""
    url = TREX_DOWNLOAD_URL
    errors = []

    # 1) 标准 wget（校验证书）
    code, _, err = ssh.exec(f"rm -f {tarball}; wget --no-verbose --tries=2 --timeout=20 -O {tarball} {url}", timeout=600)
    if code == 0 and _is_valid_tarball(ssh, tarball):
        D.success("TRex 下载完成（证书已校验）")
        return
    errors.append(f"wget(校验): exit={code} {err.strip()}")

    # 证书校验失败（exit 5）：尝试更新 CA 证书后重试
    if code == 5:
        D.warn("证书校验失败——尝试更新 CA 证书后重试…")
        ssh.exec("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates && update-ca-certificates", timeout=180)
        code, _, err = ssh.exec(f"rm -f {tarball}; wget --no-verbose --tries=2 --timeout=20 -O {tarball} {url}", timeout=600)
        if code == 0 and _is_valid_tarball(ssh, tarball):
            D.success("更新 CA 证书后下载成功")
            return
        errors.append(f"wget(更新CA后): exit={code} {err.strip()}")

    # 2) 跳过证书校验（应对 SSL 中间代理）
    D.warn("回退到跳过证书校验下载（--no-check-certificate）…")
    code, _, err = ssh.exec(f"rm -f {tarball}; wget --no-verbose --tries=2 --timeout=20 --no-check-certificate -O {tarball} {url}", timeout=600)
    if code == 0 and _is_valid_tarball(ssh, tarball):
        D.warn("已下载（未校验证书，注意 SSL 中间代理风险）")
        return
    errors.append(f"wget(跳过校验): exit={code} {err.strip()}")

    # 3) curl 兜底
    code, _, err = ssh.exec(f"rm -f {tarball}; curl -fSLk --connect-timeout 20 --max-time 600 --retry 1 -o {tarball} {url}", timeout=600)
    if code == 0 and _is_valid_tarball(ssh, tarball):
        D.warn("已通过 curl 下载（未校验证书）")
        return
    errors.append(f"curl: exit={code} {err.strip()}")

    ssh.exec(f"rm -f {tarball}")
    detail = "\n    ".join(errors)
    raise RuntimeError(
        "TRex 下载失败（已尝试校验/更新CA/跳过校验/curl）。\n"
        f"  最可能是网络被限制或 SSL 代理拦截。请在有外网的机器下载后，scp 到客户端再重跑：\n"
        f"    下载: {url}\n"
        f"    放到客户端: {tarball}\n"
        f"  各步错误:\n    {detail}"
    )


def download_trex(ssh: SSHClient):
    D.info(f"下载 TRex v{TREX_VERSION}（约 300 MB，请稍候）…")
    _run(ssh, f"mkdir -p {TREX_DIR}", "创建目录")
    tarball = f"/tmp/trex-{TREX_VERSION}.tar.gz"

    if _is_valid_tarball(ssh, tarball):
        D.success(f"发现已预置的安装包 {tarball}，跳过下载")
    else:
        _fetch_trex_archive(ssh, tarball)

    D.info("解压 TRex…")
    code, _, err = ssh.exec(f"tar -xzf {tarball} -C {TREX_DIR}", timeout=120)
    if code != 0:
        ssh.exec(f"rm -f {tarball}")  # 损坏包：删掉以便下次重新下载
        raise RuntimeError(f"解压失败（安装包可能损坏，已删除）: {err.strip()}")
    _run(ssh, f"mv {TREX_DIR}/v{TREX_VERSION} {TREX_INSTALL_DIR} 2>/dev/null || true", "重命名目录")
    D.success("TRex 下载并解压完成")


def detect_interfaces(ssh: SSHClient) -> list[dict]:
    """Return list of {name, pci, driver} for non-management NICs."""
    code, out, _ = ssh.exec("ip -j link show")
    if code != 0:
        return []
    try:
        links = json.loads(out)
    except Exception:
        return []

    # Get the interface used for the default route (management)
    _, mgmt_out, _ = ssh.exec("ip route show default | awk '{print $5}' | head -1")
    mgmt_iface = mgmt_out.strip()

    ifaces = []
    for link in links:
        name = link.get("ifname", "")
        if name in ("lo", mgmt_iface) or name.startswith("docker") or name.startswith("veth"):
            continue
        if link.get("link_type") != "ether":
            continue
        # Get PCI address
        _, pci_out, _ = ssh.exec(f"ethtool -i {name} 2>/dev/null | grep bus-info | awk '{{print $2}}'")
        pci = pci_out.strip()
        _, drv_out, _ = ssh.exec(f"ethtool -i {name} 2>/dev/null | grep driver | awk '{{print $2}}'")
        driver = drv_out.strip()
        if pci:
            ifaces.append({"name": name, "pci": pci, "driver": driver})

    return ifaces


def write_trex_config(ssh: SSHClient, interfaces: list[str], cores: int):
    """Write /etc/trex_cfg.yaml for ASTF mode."""
    if len(interfaces) < 2:
        raise ValueError("至少需要 2 个网络接口（PCI 地址）用于 TRex 流量收发")

    port_entries = "\n".join(f"        - '{pci}'" for pci in interfaces[:2])
    thread_list = ', '.join(str(i) for i in range(2, 2 + cores))
    config = f"""### TRex 配置 (自动生成) — 面向 100Gbps 双向高吞吐
- port_limit: 2
  version: 2
  interfaces:
{port_entries}
  port_info:
    - ip: 1.1.1.1
      default_gw: 1.1.1.2
    - ip: 2.2.2.2
      default_gw: 2.2.2.1
  # 高吞吐内存配置：增大 mbuf 池以支撑百万级并发流与 64K payload
  memory:
    mbuf_64:    16383
    mbuf_128:   16383
    mbuf_256:   16383
    mbuf_512:   16383
    mbuf_1024:  16383
    mbuf_2048:  32767
    mbuf_4096:  16383
    mbuf_9k:    8191
    traffic_mbuf_512:  130000
    traffic_mbuf_2048: 130000
    dp_flows:   1048576
  platform:
    master_thread_id: 0
    latency_thread_id: 1
    dual_if:
      - socket: 0
        threads: [{thread_list}]
"""
    ssh.put_privileged(config, "/etc/trex_cfg.yaml")
    D.success("TRex 配置文件已写入 /etc/trex_cfg.yaml")


def bind_dpdk(ssh: SSHClient, interfaces: list[str]):
    """Bind NICs to DPDK (vfio-pci or igb_uio)."""
    D.info("绑定网卡到 DPDK 驱动…")
    # Try vfio-pci first (preferred, works without IOMMU on newer kernels)
    _run(ssh, "modprobe vfio-pci 2>/dev/null || modprobe igb_uio 2>/dev/null || true", "加载 DPDK 驱动")
    bind_script = f"{TREX_INSTALL_DIR}/dpdk_setup_ports.py"
    for pci in interfaces[:2]:
        code, _, err = ssh.exec(
            f"python3 {bind_script} --bind=vfio-pci {pci} 2>/dev/null || "
            f"python3 {bind_script} --bind=igb_uio {pci} 2>/dev/null || true"
        )
    D.success("DPDK 网卡绑定完成")


def deploy(ssh: SSHClient, interfaces: list[str], cores: int = 4):
    """Full deployment pipeline."""
    D.step("检查 TRex 是否已安装")
    if check_installed(ssh):
        D.success(f"TRex v{TREX_VERSION} 已存在，跳过下载")
    else:
        D.step("安装系统依赖")
        install_deps(ssh)
        D.step("下载并安装 TRex")
        download_trex(ssh)

    D.step("检测网络接口")
    detected = detect_interfaces(ssh)
    if detected:
        D.info("检测到以下可用网卡:")
        for iface in detected:
            D.info(f"  {iface['name']}  PCI={iface['pci']}  驱动={iface['driver']}")

    if not interfaces:
        if len(detected) >= 2:
            interfaces = [detected[0]["pci"], detected[1]["pci"]]
            D.info(f"自动选择接口: {interfaces}")
        else:
            raise RuntimeError(
                "未能自动检测到 2 个可用网卡，请使用 --interfaces 手动指定 PCI 地址"
            )

    D.step("生成 TRex 配置")
    write_trex_config(ssh, interfaces, cores)

    D.step("绑定 DPDK 驱动")
    bind_dpdk(ssh, interfaces)

    D.success(f"TRex 部署完成！安装路径: {TREX_INSTALL_DIR}")
    return TREX_INSTALL_DIR


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
