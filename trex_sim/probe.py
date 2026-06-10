"""
远程客户端环境探测：在部署 TRex 前检查靶机是否满足前置条件。

检查项对应 README "客户端前置条件"，输出逐项 通过/警告/不满足 与总体结论。
"""

import json
from .ssh import SSHClient
from .deploy import detect_interfaces, check_installed, TREX_INSTALL_DIR, TREX_VERSION
from . import display as D

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _os_info(ssh: SSHClient) -> tuple[str, str]:
    code, out, _ = ssh.exec("lsb_release -d 2>/dev/null | cut -f2-")
    desc = out.strip() if code == 0 and out.strip() else ""
    if not desc:
        _, out2, _ = ssh.exec("grep PRETTY_NAME /etc/os-release | cut -d'\"' -f2")
        desc = out2.strip()
    is_ubuntu = "ubuntu" in desc.lower()
    return (PASS if is_ubuntu else WARN, desc or "未知")


def _python_info(ssh: SSHClient) -> tuple[str, str]:
    code, out, err = ssh.exec("python3 --version")
    ver = (out or err).strip()
    return (PASS if code == 0 else FAIL, ver or "未安装 python3")


def _cpu_info(ssh: SSHClient) -> tuple[str, str]:
    _, out, _ = ssh.exec("nproc")
    try:
        cores = int(out.strip())
    except ValueError:
        return WARN, "无法获取核心数"
    return (PASS if cores >= 4 else WARN, f"{cores} 核（100G 建议 ≥16）")


def _mem_info(ssh: SSHClient) -> tuple[str, str]:
    _, out, _ = ssh.exec("free -m | awk '/^Mem:/{print $2}'")
    try:
        mb = int(out.strip())
    except ValueError:
        return WARN, "无法获取内存"
    gb = mb / 1024
    return (PASS if gb >= 8 else WARN, f"{gb:.1f} GiB")


def _hugepages_info(ssh: SSHClient) -> tuple[str, str]:
    _, out, _ = ssh.exec("grep HugePages_Total /proc/meminfo | awk '{print $2}'")
    try:
        total = int(out.strip())
    except ValueError:
        return WARN, "无法读取 hugepages"
    if total > 0:
        return PASS, f"HugePages_Total={total}（已配置）"
    return FAIL, "HugePages_Total=0（TRex 启动需要，请配置大页内存）"


def _nic_info(ssh: SSHClient) -> tuple[str, str, list]:
    ifaces = detect_interfaces(ssh)
    if len(ifaces) >= 2:
        desc = "可用数据面网卡 " + ", ".join(f"{i['name']}({i['pci']},{i['driver']})" for i in ifaces)
        return PASS, desc, ifaces
    if len(ifaces) == 1:
        i = ifaces[0]
        return FAIL, f"仅检测到 1 块数据面网卡 {i['name']}（需 ≥2）", ifaces
    return FAIL, "未检测到数据面网卡（除管理口外需 ≥2 块，用于流量收发）", ifaces


def _trex_info(ssh: SSHClient) -> tuple[str, str]:
    if check_installed(ssh):
        return PASS, f"TRex v{TREX_VERSION} 已安装于 {TREX_INSTALL_DIR}"
    return WARN, f"TRex 未安装（运行 deploy 将自动安装到 {TREX_INSTALL_DIR}）"


def _network_info(ssh: SSHClient) -> tuple[str, str]:
    # 先按校验证书探测；失败再按跳过校验探测，以区分“不可达”与“证书校验失败”
    code, _, _ = ssh.exec("wget -q --tries=1 --timeout=8 --spider https://trex-tgn.cisco.com 2>/dev/null")
    if code == 0:
        return PASS, "可访问 trex-tgn.cisco.com（证书校验通过，支持在线下载）"
    code2, _, _ = ssh.exec("wget -q --tries=1 --timeout=8 --no-check-certificate --spider https://trex-tgn.cisco.com 2>/dev/null")
    if code2 == 0:
        return WARN, "可达但证书校验失败（部署时将自动跳过校验下载，或预置离线包）"
    return WARN, "无法访问 trex-tgn.cisco.com（需预置离线包于 /tmp/trex-3.03.tar.gz）"


_SYMBOL = {PASS: ("✔", D.GREEN), WARN: ("⚠", D.YELLOW), FAIL: ("✘", D.RED)}


def probe(ssh: SSHClient) -> dict:
    """运行全部检查，打印报告，返回结构化结果。"""
    checks = []

    status, detail = _os_info(ssh);        checks.append(("操作系统", status, detail))
    status, detail = _python_info(ssh);    checks.append(("Python3", status, detail))
    status, detail = _cpu_info(ssh);       checks.append(("CPU 核心", status, detail))
    status, detail = _mem_info(ssh);       checks.append(("内存", status, detail))
    status, detail = _hugepages_info(ssh); checks.append(("Hugepages", status, detail))
    status, detail, ifaces = _nic_info(ssh); checks.append(("数据面网卡", status, detail))
    status, detail = _trex_info(ssh);      checks.append(("TRex", status, detail))
    status, detail = _network_info(ssh);   checks.append(("下载通道", status, detail))

    D.step("环境探测报告")
    for name, status, detail in checks:
        sym, color = _SYMBOL[status]
        print(f"  {color}{sym}{D.RESET} {D.BOLD}{name:<10}{D.RESET} {detail}")

    fails = [c for c in checks if c[1] == FAIL]
    warns = [c for c in checks if c[1] == WARN]

    if fails:
        D.error(f"未就绪：{len(fails)} 项不满足，{len(warns)} 项警告。请先处理 FAIL 项再部署。")
        verdict = "NOT_READY"
    elif warns:
        D.warn(f"基本就绪：{len(warns)} 项警告，可继续但建议确认。")
        verdict = "READY_WITH_WARNINGS"
    else:
        D.success("环境完全就绪，可执行 deploy / run。")
        verdict = "READY"

    return {
        "verdict": verdict,
        "checks": [{"name": n, "status": s, "detail": d} for n, s, d in checks],
        "interfaces": ifaces,
    }
