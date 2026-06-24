"""
trex-sim CLI entry point.

Commands:
  probe    -- Check whether a remote client meets TRex prerequisites
  deploy   -- Install TRex on a remote Ubuntu client
  run      -- Deploy (if needed) and run a TCP ASTF test
  run-4dir -- Stateless 4-port line-rate test (per-size loss/latency)
  restore  -- Stop TRex and restore NICs to kernel (mlx5 no-op)
  status   -- Check TRex daemon status on a remote client
"""

import sys
import traceback
import click

from .ssh import SSHClient
from . import deploy as dep
from . import runner as run_mod
from . import probe as probe_mod
from . import display as D
from . import stl_runner, topo
from . import restore as restore_mod


# ──────────────────────────────────────────────
# Shared SSH options
# ──────────────────────────────────────────────
_ssh_opts = [
    click.option("--host", "-H", default=None, help="客户端 IP 地址或主机名（留空则启动前交互输入）"),
    click.option("--user", "-u", default=None, help="SSH 用户名（留空则启动前交互输入）"),
    click.option("--password", "-p", default=None, envvar="TREXSIM_PASSWORD",
                 help="SSH 密码（留空且未用 --key 时交互输入；也可经 TREXSIM_PASSWORD 环境变量传入）"),
    click.option("--key", "-k", default=None, help="SSH 私钥文件路径"),
    click.option("--port", default=22, show_default=True, help="SSH 端口"),
    click.option("--sudo-password", default=None,
                 help="sudo 密码（非 root 账号用；密码登录时默认复用 SSH 密码，免密 sudo 可不填）"),
]


def add_opts(opts):
    def decorator(f):
        for opt in reversed(opts):
            f = opt(f)
        return f
    return decorator


def resolve_ssh_inputs(host, user, password, key):
    """Interactively prompt for any SSH inputs not supplied on the command line."""
    if not host:
        host = click.prompt("客户端 IP / 主机名", type=str)
    if not user:
        user = click.prompt("SSH 用户名", default="root", type=str)
    if not key and not password:
        password = click.prompt("SSH 密码", hide_input=True, type=str)
    return host, user, password, key


def make_ssh(host, user, password, key, port) -> SSHClient:
    return SSHClient(host=host, user=user, password=password, key_file=key, port=port)


def connect_and_escalate(ssh: SSHClient, password, sudo_password):
    """Connect, then enable sudo wrapping if the login user is not root."""
    ssh.connect()
    D.success(f"已连接至 {ssh.host}")
    # 密码登录时，sudo 密码默认复用 SSH 密码
    result = ssh.enable_sudo_if_needed(sudo_password or password)
    if result["is_root"]:
        D.info("登录账号: root")
    elif result["sudo_ok"]:
        D.info(result["detail"])
    else:
        D.error(f"sudo 不可用：{result['detail']}")
        D.warn("该账号需要 sudo 权限来安装依赖 / 写入 /etc / 绑定 DPDK。")
        D.warn("请改用 root 登录、配置免密 sudo，或用 --sudo-password 提供 sudo 密码。")
        raise RuntimeError("特权检查失败：当前账号无法执行 sudo")


# ──────────────────────────────────────────────
# CLI Group
# ──────────────────────────────────────────────
@click.group()
@click.version_option(package_name="trex-simulator")
def cli():
    """TRex 打流模拟器 — 自动部署并运行 TCP 多核流量测试"""


# ──────────────────────────────────────────────
# probe
# ──────────────────────────────────────────────
@cli.command()
@add_opts(_ssh_opts)
@click.option("--json", "as_json", is_flag=True, default=False, help="以 JSON 输出探测结果")
def probe(host, user, password, key, port, sudo_password, as_json):
    """探测远程客户端是否满足 TRex 部署的前置条件。"""
    host, user, password, key = resolve_ssh_inputs(host, user, password, key)
    if not as_json:
        D.header(f"环境探测  →  {user}@{host}:{port}")
    ssh = make_ssh(host, user, password, key, port)
    try:
        ssh.connect()
        # 探测为只读诊断：尽力启用 sudo 以提升检测准确度，但失败也不中断
        ssh.enable_sudo_if_needed(sudo_password or password)
        result = probe_mod.probe(ssh)
        if as_json:
            import json as _json
            click.echo(_json.dumps(result, ensure_ascii=False, indent=2))
        # 退出码：READY=0，带警告=0，未就绪=2，便于脚本判断
        sys.exit(2 if result["verdict"] == "NOT_READY" else 0)
    except SystemExit:
        raise
    except Exception as e:
        D.error(str(e))
        if "--debug" in sys.argv:
            traceback.print_exc()
        sys.exit(1)
    finally:
        ssh.disconnect()


# ──────────────────────────────────────────────
# deploy
# ──────────────────────────────────────────────
@cli.command()
@add_opts(_ssh_opts)
@click.option(
    "--interfaces", "-i", default=None,
    help="TRex 数据面网卡 PCI 地址（逗号分隔，如 0000:01:00.0,0000:02:00.0）"
         "；不指定则自动检测"
)
@click.option("--cores", "-c", default=4, show_default=True, help="分配给 TRex 的 CPU 核心数")
def deploy(host, user, password, key, port, sudo_password, interfaces, cores):
    """在远程客户端上安装并配置 TRex。"""
    host, user, password, key = resolve_ssh_inputs(host, user, password, key)
    D.header(f"TRex 部署  →  {user}@{host}:{port}")

    iface_list = [i.strip() for i in interfaces.split(",")] if interfaces else []

    ssh = make_ssh(host, user, password, key, port)
    try:
        D.step("建立 SSH 连接")
        connect_and_escalate(ssh, password, sudo_password)
        dep.deploy(ssh, iface_list, cores)
        D.header("部署完成 ✔")
    except Exception as e:
        D.error(str(e))
        if "--debug" in sys.argv:
            traceback.print_exc()
        sys.exit(1)
    finally:
        ssh.disconnect()


# ──────────────────────────────────────────────
# run
# ──────────────────────────────────────────────
@cli.command()
@add_opts(_ssh_opts)
@click.option("--interfaces", "-i", default=None, help="数据面网卡 PCI 地址（逗号分隔）")
@click.option("--cores",     "-c", default=16,   show_default=True, help="CPU 核心数（100G 建议 ≥16）")
@click.option("--duration",  "-d", default=None, type=int, help="测试时长（秒，留空则启动前交互输入）")
@click.option("--multiplier","-m", default=1.0,  show_default=True, help="流量倍率（1.0 = 基础速率）")
@click.option("--target-gbps", default=100.0, show_default=True, help="目标双向吞吐（Gbps），自动估算 CPS")
@click.option("--cps",       default=None, type=int, help="每核每秒新建 TCP 连接数（留空则按目标吞吐自动估算）")
@click.option("--payload",   default=65536, show_default=True, help="HTTP 响应 payload 大小（字节，100G 建议 64K）")
@click.option("--reqs-per-conn", default=100, show_default=True, help="单条 TCP 连接内的请求/响应循环次数")
@click.option("--client-ip-start", default="16.0.0.1",     show_default=True, help="客户端源 IP 起始")
@click.option("--client-ip-end",   default="16.0.255.254", show_default=True, help="客户端源 IP 结束")
@click.option("--server-ip-start", default="48.0.0.1",     show_default=True, help="服务端目的 IP 起始")
@click.option("--server-ip-end",   default="48.0.255.254", show_default=True, help="服务端目的 IP 结束")
@click.option("--skip-deploy", is_flag=True, default=False, help="跳过部署步骤（TRex 已安装）")
def run(host, user, password, key, port, sudo_password,
        interfaces, cores, duration, multiplier, target_gbps, cps, payload, reqs_per_conn,
        client_ip_start, client_ip_end, server_ip_start, server_ip_end,
        skip_deploy):
    """部署 TRex 并执行 TCP 多核流量测试，打印实时统计与最终结果。"""
    # ── 启动前交互式输入：客户端 IP / 账号 / 密码 / 测试时长 ──
    host, user, password, key = resolve_ssh_inputs(host, user, password, key)
    if duration is None:
        duration = click.prompt("测试时长（秒）", default=60, type=int)

    # 按目标双向吞吐估算每核 CPS：
    #   单连接吞吐 ≈ payload * reqs_per_conn / duration（粗略），此处用稳态近似：
    #   总带宽(bps) = 并发连接数 * payload(bytes) * 8 / 单次往返时间
    # 实际中以 multiplier 微调；这里给出可跑满 100G 的连接基数。
    if cps is None:
        # 经验估算：64KB payload、16 核下，约 50万 CPS 可逼近 100Gbps 双向
        est_cps = int((target_gbps * 1e9) / (payload * 8 * max(reqs_per_conn, 1)) * 2)
        cps = max(est_cps, 1000)

    D.header(f"TRex TCP 流量测试  →  {user}@{host}:{port}")
    D.info(f"时长={duration}s  倍率={multiplier}x  核心={cores}  目标={target_gbps}Gbps 双向")
    D.info(f"CPS={cps:,}  payload={payload}B  单连接请求数={reqs_per_conn}")
    D.info(f"客户端 IP: {client_ip_start}–{client_ip_end}")
    D.info(f"服务端 IP: {server_ip_start}–{server_ip_end}")

    iface_list = [i.strip() for i in interfaces.split(",")] if interfaces else []

    ssh = make_ssh(host, user, password, key, port)
    try:
        D.step("建立 SSH 连接")
        connect_and_escalate(ssh, password, sudo_password)

        if not skip_deploy:
            dep.deploy(ssh, iface_list, cores)

        stats = run_mod.run_test(
            ssh=ssh,
            duration=duration,
            multiplier=multiplier,
            cores=cores,
            client_ip_start=client_ip_start,
            client_ip_end=client_ip_end,
            server_ip_start=server_ip_start,
            server_ip_end=server_ip_end,
            cps=cps,
            payload_size=payload,
            reqs_per_conn=reqs_per_conn,
        )

        D.result_table(stats.to_display_dict())

    except Exception as e:
        D.error(str(e))
        if "--debug" in sys.argv:
            traceback.print_exc()
        sys.exit(1)
    finally:
        ssh.disconnect()


@cli.command(name="run-4dir")
@add_opts(_ssh_opts)
@click.option("--ifaces-names", default=",".join(topo.NIC_NAMES_DEFAULT), show_default=True,
              help="4 个内核网卡名（NUMA 对齐顺序，逗号分隔）")
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
            dep.install_deps(ssh)
            for pkg in dep.MLX_DEPS:
                ssh.exec(f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkg} || true")
            if not dep.check_installed(ssh):
                dep.download_trex(ssh)
        res = stl_runner.run_4dir(
            ssh, names=names, mode=mode, sizes=size_list, duration=duration,
            rate_percent=rate_percent, loss_thresh=loss_thresh,
            cores_per_socket=cores_per_socket, trex_dir=dep.TREX_INSTALL_DIR)
        D.header(f"STL 结果汇总  模式={mode}  阈值={loss_thresh}% 丢包")
        D.stl_table(res.get("rows", []))
        D.success(f"总体结果: {res.get('overall', 'N/A')}")
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
@click.option("--ifaces-names", default=",".join(topo.NIC_NAMES_DEFAULT))
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


# ──────────────────────────────────────────────
# status
# ──────────────────────────────────────────────
@cli.command()
@add_opts(_ssh_opts)
def status(host, user, password, key, port, sudo_password):
    """查询远程客户端上 TRex 守护进程的运行状态。"""
    host, user, password, key = resolve_ssh_inputs(host, user, password, key)
    D.header(f"TRex 状态查询  →  {user}@{host}:{port}")
    ssh = make_ssh(host, user, password, key, port)
    try:
        ssh.connect()
        D.success(f"SSH 已连接至 {host}")
        ssh.enable_sudo_if_needed(sudo_password or password)

        # TRex installed?
        if dep.check_installed(ssh):
            D.success(f"TRex v{dep.TREX_VERSION} 已安装于 {dep.TREX_INSTALL_DIR}")
        else:
            D.warn(f"TRex 未安装（路径 {dep.TREX_INSTALL_DIR} 不存在）")

        # Daemon running?
        code, pids, _ = ssh.exec("pgrep -f 't-rex-64'")
        if code == 0:
            D.success(f"TRex 守护进程正在运行 (PID: {pids.strip()})")
        else:
            D.warn("TRex 守护进程未运行")

        # Config file
        code2, cfg, _ = ssh.exec("cat /etc/trex_cfg.yaml 2>/dev/null")
        if code2 == 0:
            D.info("当前配置文件 /etc/trex_cfg.yaml:")
            for line in cfg.splitlines():
                D.stream_line(line)
        else:
            D.warn("/etc/trex_cfg.yaml 不存在")

    except Exception as e:
        D.error(str(e))
        sys.exit(1)
    finally:
        ssh.disconnect()


def main():
    cli()


if __name__ == "__main__":
    main()
