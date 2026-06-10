"""
实机验证：SSH 层 + 网卡检测，对着一台真实可达的主机运行。

验证 连接 / 命令执行 / 流式输出 / SFTP 上传 / 安装检测 / 网卡枚举。
不部署 TRex、不绑定 DPDK、不打流量，因此对任意可 SSH 的 Linux 主机都安全。

用法：
  python tests/verify_ssh.py -H <host> -u <user> -k <key_file> [--port 22]
  python tests/verify_ssh.py -H <host> -u <user> -p <password>
退出码：0 全部通过；非 0 表示失败。
"""
import argparse
import sys

from trex_sim.ssh import SSHClient
from trex_sim import deploy


def main():
    ap = argparse.ArgumentParser(description="trex-simulator SSH 层实机验证")
    ap.add_argument("-H", "--host", required=True)
    ap.add_argument("-u", "--user", default="root")
    ap.add_argument("-k", "--key", default=None, help="SSH 私钥文件路径")
    ap.add_argument("-p", "--password", default=None)
    ap.add_argument("--port", type=int, default=22)
    args = ap.parse_args()

    if not args.key and not args.password:
        ap.error("请提供 --key 或 --password")

    print("=== connect ===")
    ssh = SSHClient(host=args.host, user=args.user, password=args.password,
                    key_file=args.key, port=args.port)
    ssh.connect()
    print("CONNECT_OK")

    print("=== exec ===")
    code, out, err = ssh.exec("echo hello && nproc")
    assert code == 0 and "hello" in out, (code, out, err)
    print("EXEC_OK code=%d cores=%s" % (code, out.strip().splitlines()[-1]))

    print("=== exec_stream ===")
    lines = []
    rc = ssh.exec_stream("for i in 1 2 3; do echo line$i; done", on_line=lines.append)
    assert rc == 0 and lines == ["line1", "line2", "line3"], (rc, lines)
    print("STREAM_OK got", lines)

    print("=== put_content (SFTP) ===")
    ssh.put_content("hello-from-trex-sim\n", "/tmp/_trex_sim_probe.txt")
    code, out, _ = ssh.exec("cat /tmp/_trex_sim_probe.txt")
    assert out.strip() == "hello-from-trex-sim", out
    print("PUT_CONTENT_OK")

    print("=== privilege escalation ===")
    info = ssh.enable_sudo_if_needed(args.password)
    print("PRIV:", info)
    assert info["sudo_ok"], "sudo 不可用：" + info["detail"]
    # 写入 root-only 路径并读回，验证 put_privileged（root 直接写 / 非 root 走 tmp+sudo mv）
    ssh.put_privileged("trexsim-priv-test\n", "/etc/_trexsim_priv_test.conf")
    code, out, _ = ssh.exec("cat /etc/_trexsim_priv_test.conf")
    assert out.strip() == "trexsim-priv-test", out
    ssh.exec("rm -f /etc/_trexsim_priv_test.conf")
    print("PUT_PRIVILEGED_OK wrote+read+removed /etc/_trexsim_priv_test.conf as",
          "root" if info["is_root"] else "sudo")

    print("=== check_installed ===")
    print("CHECK_INSTALLED:", deploy.check_installed(ssh))

    print("=== detect_interfaces ===")
    ifaces = deploy.detect_interfaces(ssh)
    print("DETECT_IFACES_OK count=%d ->" % len(ifaces), ifaces)

    ssh.exec("rm -f /tmp/_trex_sim_probe.txt")
    ssh.disconnect()
    print("ALL_SSH_CHECKS_PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("ASSERTION_FAILED:", e, file=sys.stderr)
        sys.exit(1)
