#!/usr/bin/env bash
#
# remote_bidir_4dir_iperf.sh
# -----------------------------------------------------------------------------
# bidir_4dir_iperf.sh 的远端版：
#   - 手动输入 目标IP(可空格分隔多台) / 统一用户名 / 统一密码 / 并发(-P)
#   - 运行前预检查目标系统环境（网口/依赖/vrf 支持等）
#   - 用 scp 把 bidir_4dir_iperf.sh 下发到目标 /tmp，sudo 以 root 运行
#   - 实时回显测试输出与结果（与本地运行一致），同时保存到本目录
#     iperf_4dir_100G/<设备SN>_<日期时间>.log
#   - SN 用 dmidecode -s baseboard-serial-number 获取（参考 get_ast_bmcipsn.sh）
#
# 运行环境：本地 Ubuntu，需 sshpass + openssh-client(ssh/scp)
# 用法：    ./remote_bidir_4dir_iperf.sh   （下发并运行单口依次测试的 bidir_4dir_iperf.sh）
#
# 说明：密码通过 SSHPASS 环境变量(给 ssh/scp)与 ssh stdin(给 sudo -S)传递，
#       不出现在远端进程命令行参数里；密码可含特殊字符（不可含换行）。
# -----------------------------------------------------------------------------
set -uo pipefail

# ---------------- 路径与常量 ----------------
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
LOCAL_TEST_SCRIPT="$SCRIPT_DIR/bidir_4dir_iperf.sh"
OUT_DIR="$SCRIPT_DIR/iperf_4dir_100G"
REMOTE_TEST_SCRIPT="/tmp/bidir_4dir_iperf.sh"
NICS_DEFAULT="enp65s0f0np0 enp161s0f0np0 enp65s0f1np1 enp161s0f1np1"

# ---- SSH 主机密钥校验策略 ----
# 默认 no：实验室批量/被测机常重装(指纹易变)，不校验主机指纹，免首连人工确认。
# ⚠️ 取舍：关闭校验=无法防范目标 IP 被冒充的中间人(MITM)，而本工具会把登录/sudo
#    密码发往目标，故仅限可信内网/直连受控环境使用。
# 加固：运行前 export SSH_STRICT=accept-new（首连记录指纹、之后变更即拒绝，
#       指纹存于 $SCRIPT_DIR/known_hosts），或改用 SSH 密钥认证。
SSH_STRICT="${SSH_STRICT:-no}"
if [[ "$SSH_STRICT" == "no" ]]; then
  SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10)
else
  SSH_OPTS=(-o StrictHostKeyChecking="$SSH_STRICT" -o UserKnownHostsFile="$SCRIPT_DIR/known_hosts" -o LogLevel=ERROR -o ConnectTimeout=10)
fi

log()  { echo -e "[INFO] $*" >&2; }
warn() { echo -e "[WARN] $*" >&2; }
err()  { echo -e "[ERROR] $*" >&2; }
have_cmd() { command -v "$1" >/dev/null 2>&1; }

# ---------------- 本地依赖检查 ----------------
check_local_deps() {
  local miss=()
  have_cmd sshpass || miss+=("sshpass")
  have_cmd ssh     || miss+=("openssh-client(ssh)")
  have_cmd scp     || miss+=("openssh-client(scp)")
  if [[ ${#miss[@]} -gt 0 ]]; then
    err "本地缺少依赖：${miss[*]}"
    err "Ubuntu 安装：sudo apt-get update && sudo apt-get install -y sshpass openssh-client"
    exit 1
  fi
  if [[ ! -f "$LOCAL_TEST_SCRIPT" ]]; then
    err "未找到本地测试脚本：$LOCAL_TEST_SCRIPT"
    err "请把 bidir_4dir_iperf.sh 与本脚本放在同一目录。"
    exit 1
  fi
}

# ---------------- 远端执行封装 ----------------
# 普通远端命令（非 sudo）。用法：rexec <ip> "<cmd>"
rexec() {
  local ip="$1"; shift
  SSHPASS="$PASSWORD" sshpass -e ssh "${SSH_OPTS[@]}" -- "$USERNAME@$ip" "$*"
}
# 远端 sudo 命令（密码经 ssh stdin 喂给 sudo -S，不进命令行）。用法：rsudo <ip> "<cmd>"
rsudo() {
  local ip="$1"; shift
  printf '%s\n' "$PASSWORD" | SSHPASS="$PASSWORD" sshpass -e ssh "${SSH_OPTS[@]}" -- "$USERNAME@$ip" "sudo -S -p '' $*"
}

# ---------------- 输入并发 ----------------
read_parallel() {
  local v
  while true; do
    echo -n "请输入 并发(-P，四个方向共用): " >&2
    IFS= read -r v || true
    v="$(echo "${v:-}" | awk '{$1=$1;print}')"
    if [[ -z "$v" || ! "$v" =~ ^[0-9]+$ || "$v" -le 0 ]]; then
      warn "请输入正整数。"
      continue
    fi
    echo "$v"
    return 0
  done
}

# ---------------- 获取 SN ----------------
get_sn() {
  local ip="$1" sn
  sn="$(rsudo "$ip" "dmidecode -s baseboard-serial-number" 2>/dev/null \
        | tr -d '\r' | grep -vi 'try again\|^\[sudo' | tail -n1 | tr -cd 'A-Za-z0-9_.-')"
  [[ -z "$sn" ]] && sn="UNKNOWNSN"
  echo "$sn"
}

# ---------------- 预检查（输出到 stdout，便于 tee 落盘）----------------
precheck() {
  local ip="$1"
  SSHPASS="$PASSWORD" sshpass -e ssh "${SSH_OPTS[@]}" -- "$USERNAME@$ip" 'bash -s' <<'PRECHK'
set +e
NICS="enp65s0f0np0 enp161s0f0np0 enp65s0f1np1 enp161s0f1np1"
echo "----- 预检查 @ $(hostname) -----"
. /etc/os-release 2>/dev/null; echo "OS: ${PRETTY_NAME:-unknown}    Kernel: $(uname -r)"
for n in $NICS; do
  if [ -d "/sys/class/net/$n" ]; then
    echo "NIC $n: state=$(cat /sys/class/net/$n/operstate 2>/dev/null) carrier=$(cat /sys/class/net/$n/carrier 2>/dev/null) speed=$(cat /sys/class/net/$n/speed 2>/dev/null)Mb/s mtu=$(cat /sys/class/net/$n/mtu 2>/dev/null) numa=$(cat /sys/class/net/$n/device/numa_node 2>/dev/null)"
  else
    echo "NIC $n: !!! 缺失 !!!"
  fi
done
for c in iperf3 ip python3 ethtool numactl; do
  if command -v "$c" >/dev/null 2>&1; then echo "CMD $c: ok"; else echo "CMD $c: 缺失(测试脚本会尝试自动安装)"; fi
done
if ip vrf help >/dev/null 2>&1; then echo "ip vrf: 支持"; else echo "ip vrf: 不支持(iproute2 可能过旧)"; fi
if modinfo vrf >/dev/null 2>&1 || [ -d /sys/module/vrf ]; then echo "kernel vrf: 可用"; else echo "kernel vrf: 未找到(可能需 modprobe vrf)"; fi
echo "--------------------------------"
PRECHK
}

# ---------------- 在单台目标上执行 ----------------
run_on_host() {
  local ip="$1"

  log "================ 目标 $ip ================"

  # 1) SSH 登录/认证
  if ! rexec "$ip" "true" >/dev/null 2>&1; then
    err "$ip：SSH 登录失败（检查 IP/用户名/密码/网络/对端 sshd）。跳过。"
    return 1
  fi
  log "$ip：SSH 登录成功。"

  # 2) sudo 验证
  if ! rsudo "$ip" "true" >/dev/null 2>&1; then
    warn "$ip：sudo 验证失败（密码错误或该用户无 sudo 权限），后续 root 操作可能失败。"
  else
    log "$ip：sudo 验证成功。"
  fi

  # 3) 取 SN，定输出文件
  local sn ts outfile
  sn="$(get_sn "$ip")"
  ts="$(date +%Y%m%d_%H%M%S)"
  outfile="$OUT_DIR/${sn}_${ts}.log"
  log "$ip：设备SN=${sn}；输出文件 -> ${outfile}"

  # 4) 写抬头 + 预检查（同时落盘）
  {
    echo "############################################################"
    echo "# 远端四方向打流测试"
    echo "# 目标=$ip  SN=$sn  用户=$USERNAME  并发(-P)=$PARALLEL"
    echo "# 开始时间=$(date '+%F %T')"
    echo "############################################################"
  } | tee "$outfile"

  log "$ip：运行环境预检查中..."
  precheck "$ip" 2>&1 | tee -a "$outfile"

  # 5) 确认继续
  local yn=""
  echo -n "是否在 $ip 上开始四方向打流测试? [y/N]: " >&2
  IFS= read -r yn || true
  if [[ ! "$yn" =~ ^[Yy]$ ]]; then
    warn "$ip：已跳过（用户未确认）。"
    echo "[SKIPPED] 用户未确认，未执行测试。" | tee -a "$outfile" >/dev/null
    return 0
  fi

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

  {
    echo "############################################################"
    echo "# 结束时间=$(date '+%F %T')  远端返回码=$rc"
    echo "############################################################"
  } | tee -a "$outfile"

  if [[ "$rc" -eq 0 ]]; then
    log "$ip：测试完成，结果已保存：$outfile"
  else
    warn "$ip：远端返回码 $rc（结果已尽量保存：$outfile）"
  fi
  return 0
}

# ====================== MAIN ======================
check_local_deps

read -r -p "请输入目标服务器IP地址（空格分隔，可多台）: " IP_LIST
read -r -p "请输入统一的登录用户名: " USERNAME
read -r -s -p "请输入统一的登录密码: " PASSWORD; echo ""
PARALLEL="$(read_parallel)"

if [[ -z "${IP_LIST// /}" || -z "$USERNAME" || -z "$PASSWORD" ]]; then
  err "IP/用户名/密码均不能为空。"
  exit 1
fi
# 校验用户名/目标地址，避免以 '-' 开头的取值被 ssh/scp 误当作选项（option injection）
if [[ ! "$USERNAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  err "用户名含非法字符（仅允许 字母/数字/._-）：'$USERNAME'"
  exit 1
fi
read -ra IP_ARR <<< "$IP_LIST"
for ip in "${IP_ARR[@]}"; do
  if [[ "$ip" == -* || ! "$ip" =~ ^[A-Za-z0-9._:-]+$ ]]; then
    err "目标地址非法：'$ip'"
    exit 1
  fi
done

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

mkdir -p "$OUT_DIR"
log "结果目录：$OUT_DIR"
log "并发(-P)=$PARALLEL，目标数：${#IP_ARR[@]}（$IP_LIST）"

for ip in "${IP_ARR[@]}"; do
  run_on_host "$ip" || true
done

echo >&2
log "全部目标处理完成。结果保存在：$OUT_DIR"
unset PASSWORD SSHPASS 2>/dev/null || true
exit 0
