#!/usr/bin/env bash
# 开机自启包装（由 deploy/wave-vision-llm.service 调用，也可手动执行来验证）。
#
# 为什么需要它：冷启动时 USB 摄像头往往还没枚举完，主程序按 /dev/v4l/by-id 名字
# 打开会直接失败退出（日志形态：VIDEOIO(V4L2) ... can't be used to capture by name）。
# 本脚本先等两个摄像头节点与板内代理端口就绪，再把主程序交给 systemd 监督。
#
# 退出码：主程序的退出码原样透传；systemd 端 Restart=always 会继续重启。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

CONDA_BIN="${CONDA_BIN:-/home/orangepi/miniconda3/bin/activate}"
CONDA_ENV="${CONDA_ENV:-llm}"
ENTRY="${ENTRY:-main_push_llm.py}"
WAIT_DEV="${WAIT_DEV:-60}"      # 等摄像头最长秒数
WAIT_PROXY="${WAIT_PROXY:-30}"  # 等板内代理端口最长秒数

PALLET_CAM="/dev/v4l/by-id/usb-Generic_USB_Camera_200901010001-video-index0"
DETECT_CAM="/dev/v4l/by-id/usb-RYS_USB_Camera_200901010001-video-index0"

log() { printf '[start_vision %s] %s\n' "$(date +%H:%M:%S)" "$1"; }

wait_for() {  # wait_for <描述> <秒数> <命令...>
    local desc="$1" limit="$2"
    shift 2
    local waited=0
    while [ "$waited" -lt "$limit" ]; do
        if "$@" >/dev/null 2>&1; then
            log "就绪: $desc（等了 ${waited}s）"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    log "!! 超时: $desc（等了 ${limit}s）—— 仍然继续启动，交给主程序/systemd 重试"
    return 1
}

log "项目目录: $ROOT"
wait_for "两个摄像头设备节点" "$WAIT_DEV" \
    bash -c "[ -e '$PALLET_CAM' ] && [ -e '$DETECT_CAM' ]"
wait_for "板内代理 127.0.0.1:7890" "$WAIT_PROXY" \
    bash -c "ss -lnt 2>/dev/null | grep -q 127.0.0.1:7890"

[ -e "$PALLET_CAM" ] || log "!! 托盘摄像头节点不存在: $PALLET_CAM"
[ -e "$DETECT_CAM" ] || log "!! 识别区摄像头节点不存在: $DETECT_CAM"

# 代理是最大的静默坑：systemd 不读 .bashrc，缺了代理会表现为「LLM 一直超时 → 回落/回 0」
if [ -n "${HTTPS_PROXY:-}" ]; then
    log "代理环境: HTTPS_PROXY=$HTTPS_PROXY"
else
    log "!! 没读到 HTTPS_PROXY：请把下面三行写进 .env（systemd 不读 .bashrc）"
    log "!!   HTTPS_PROXY=http://127.0.0.1:7890"
    log "!!   HTTP_PROXY=http://127.0.0.1:7890"
    log "!!   NO_PROXY=localhost,127.0.0.1,::1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12"
fi

# —— 网络就绪：不只是「端口在监听」，而是「带着 key 真能打通 LLM 端点」——
# mihomo 在监听 ≠ 节点能通：节点掉线时端口照样 LISTEN，CONNECT 会失败，主程序表现为
# 每一轮都 'LLM 请求最终失败: Connection error.' → 直接丢件（YOLO_FALLBACK=0 时）。
# 所以这里做一次真实往返（curl 走代理打 $BASE/models），通了才启动主程序。
BASE_URL="$(sed -n 's/^DASHSCOPE_BASE_URL=//p' .env 2>/dev/null | head -1)"
API_KEY="$(sed -n 's/^DASHSCOPE_API_KEY=//p' .env 2>/dev/null | head -1)"
BASE_URL="${BASE_URL%/}"
WAIT_NET="${WAIT_NET:-300}"       # 等网络真正可用的最长秒数（冷启动后代理可能要几分钟才通）
# 单调时钟（秒）。**不要用 $SECONDS**：它是墙钟，开机后 NTP 校正会让它跳变
# （实测开机 26 秒时跳了 3h48m，导致等待判定失真、门 26 秒就误判超时放行）。
mono_seconds() { cut -d. -f1 /proc/uptime 2>/dev/null || date +%s; }
PROXY_FOR_CURL="${HTTPS_PROXY:-${HTTP_PROXY:-}}"

# 认证头走 curl 的 -K 配置文件，不进命令行：否则 ps / systemctl status / journal
# 都会把 key 原样照下来（比赛后请把 key 轮换一次）。
CURL_AUTH_FILE="/tmp/.wave_net_probe_curlrc"
printf 'header = "Authorization: Bearer %s"\n' "$API_KEY" > "$CURL_AUTH_FILE"
chmod 600 "$CURL_AUTH_FILE"

probe_llm_endpoint() {  # 打印 HTTP 状态码；000 = 隧道没通/超时
    local code
    if [ -n "$PROXY_FOR_CURL" ]; then
        code="$(curl -s -o /tmp/net_probe.json -w '%{http_code}' -m 10 -x "$PROXY_FOR_CURL" \
            -K "$CURL_AUTH_FILE" "$BASE_URL/models" 2>/dev/null)"
    else
        code="$(curl -s -o /tmp/net_probe.json -w '%{http_code}' -m 10 \
            -K "$CURL_AUTH_FILE" "$BASE_URL/models" 2>/dev/null)"
    fi
    printf '%s' "$code"
}

if [ -z "$BASE_URL" ] || [ -z "$API_KEY" ]; then
    log "!! .env 里读不到 DASHSCOPE_BASE_URL / DASHSCOPE_API_KEY，跳过网络就绪检查"
else
    log "网络就绪检查: $BASE_URL/models（带 key 真实往返，最长 ${WAIT_NET}s）"
    NET_START=$(mono_seconds)   # 单调计时：curl 自身的耗时也要算进去
    rounds=0
    while :; do
        rounds=$((rounds + 1))
        code="$(probe_llm_endpoint)"
        elapsed=$(( $(mono_seconds) - NET_START ))
        case "$code" in
            200)
                log "网络就绪: 代理链路 + key 均可用（第 ${rounds} 次探测，等了 ${elapsed}s）"
                break
                ;;
            ""|000|"0")
                if [ "$elapsed" -ge "$WAIT_NET" ]; then
                    log "!! 等了 ${elapsed}s 仍连不通（curl 状态码 000）：继续启动，但 LLM 很可能一直失败"
                    log "!!   先查 mihomo 节点/订阅，再查 .env 里那三行 PROXY"
                    break
                fi
                log "网络未就绪（第 ${rounds} 次，HTTP=${code:-000}，已等 ${elapsed}s/${WAIT_NET}s）"
                sleep 2
                ;;
            *)
                log "!! 网络能通但 LLM 端点返回 HTTP=$code：多半是 key 失效或地区门禁，不再等待"
                break
                ;;
        esac
    done
fi

# 顺手记一条网络自检：放后台跑。它要访问几个第三方探针（ipinfo/ipify…），最慢能花 1 分钟，
# 同步跑会把「上电 → 主程序就绪」拖长，冷启动演练时最不该等这个。结果照样留在
# /tmp/net_check_last.log 里，日志证据一点不少。
if [ -f tools/net_check.sh ]; then
    ( bash tools/net_check.sh >/tmp/net_check_last.log 2>&1 || true ) &
    log "net_check 已转后台，结果见 /tmp/net_check_last.log"
fi

# 显示会话就绪：systemd 常先于 lightdm 的用户会话把本服务拉起，此时 :0 还打不开，
# 建窗口会失败并让显示线程当场结束（主程序不会重启它 → 整场无画面）。
if command -v xrandr >/dev/null 2>&1; then
    XAUTH="${XAUTHORITY:-$HOME/.Xauthority}"
    wait_for "X 显示 ${DISPLAY:-:0}" "${WAIT_X:-60}" \
        bash -c "DISPLAY=${DISPLAY:-:0} XAUTHORITY='$XAUTH' xrandr >/dev/null 2>&1"
else
    log "!! 没装 xrandr（x11-xserver-utils），跳过 X 就绪检查"
fi

# 认证头文件用完即删，别把 key 留在 /tmp 里
rm -f "$CURL_AUTH_FILE"

log "启动主程序: conda env=$CONDA_ENV  python -u $ENTRY"
# shellcheck disable=SC1090
source "$CONDA_BIN"
conda activate "$CONDA_ENV" || { log "!! conda activate $CONDA_ENV 失败"; exit 3; }
exec python -u "$ENTRY"
