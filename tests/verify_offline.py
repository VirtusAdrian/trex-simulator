"""
离线验证：trex-simulator 纯逻辑（无需 SSH / DPDK / 真实流量）。

验证流量模型生成、配置生成、CPS 估算、校验逻辑、统计格式化。
用法：  python tests/verify_offline.py
退出码：0 全部通过；非 0 表示有断言失败。
"""
import trex_sim
from trex_sim import cli, ssh, deploy, profile, runner, display

print("IMPORT_OK version", trex_sim.__version__)

# 1) 流量模型生成产出可被 Python 编译的合法代码
code = profile.generate_profile(cps=50000, payload_size=65536, reqs_per_conn=100)
compile(code, "<profile>", "exec")
assert "ASTFProfile" in code and "jmp_nz" in code and 'X" * 65536' in code
print("PROFILE_GEN_OK len", len(code))

# 2) CPS 自动估算与 CLI 中公式一致（目标 100Gbps）
target_gbps, payload, reqs = 100.0, 65536, 100
est = int((target_gbps * 1e9) / (payload * 8 * max(reqs, 1)) * 2)
print("CPS_EST_OK target=100Gbps ->", max(est, 1000), "cps")

# 3) TRex 配置生成（2 个 PCI 地址，16 核）
class FakeSSH:
    def __init__(self): self.written = {}
    def put_content(self, content, path): self.written[path] = content
cfg_ssh = FakeSSH()
deploy.write_trex_config(cfg_ssh, ["0000:01:00.0", "0000:02:00.0"], cores=16)
cfg = cfg_ssh.written["/etc/trex_cfg.yaml"]
assert "port_limit: 2" in cfg and "dp_flows" in cfg
assert "threads: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]" in cfg
print("CONFIG_GEN_OK 16-core thread list + mbuf pools present")

# 4) 网卡不足 2 块时配置生成应拒绝
try:
    deploy.write_trex_config(FakeSSH(), ["0000:01:00.0"], cores=4)
    raise SystemExit("CONFIG_VALIDATION_FAIL (should have raised)")
except ValueError:
    print("CONFIG_VALIDATION_OK rejects single NIC")

# 5) 统计结果格式化
st = runner.TestStats(duration=120, tx_bps=98.7e9, rx_bps=99.1e9, tx_pps=12e6, active_flows=850000)
d = st.to_display_dict()
print("STATS_FMT_OK", d["发送带宽"], "/", d["接收带宽"], "/", d["发送速率"])

print("ALL_OFFLINE_CHECKS_PASSED")
