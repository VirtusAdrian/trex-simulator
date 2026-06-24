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
