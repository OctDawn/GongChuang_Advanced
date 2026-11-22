#!/usr/bin/env bash
# 现场 30 分钟调试的「一键体检」：只读，不启停服务、不改任何配置。
#
# 用法（项目根目录）：
#   bash tools/venue_check.sh                 # 全部被动检查，约 10~20 秒
#   bash tools/venue_check.sh --wait-ready    # 先等主程序打出「等待 c/a」再体检（量真实冷启动）
#   bash tools/venue_check.sh --cameras       # 额外逐路取帧（会占用相机，先 systemctl stop）
#   bash tools/venue_check.sh --no-net        # 跳过网络/API 往返（没网时用）
#   bash tools/venue_check.sh --help
#
# 退出码 = 失败项数（0 = 全绿，可以封车）。WARN 不计入退出码。
#
# 它检查什么：.env 是否被污染 / 关键定案值 / systemd 服务与重启次数 / 上电到 ready 的秒数 /
#            相机别名 / USB 掉线计数 / LLM 端点真实往返 / 屏幕分辨率 / 串口节点 / 磁盘与温度。
# 它不检查什么：串口协议（要电控配合，见 README「现场调试」）、识别准确率（不能现场调）。

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

WITH_CAMS=0
WITH_NET=1
WAIT_READY=0
for arg in "$@"; do
    case "$arg" in
        --cameras) WITH_CAMS=1 ;;
        --wait-ready) WAIT_READY=1 ;;
        --no-net)  WITH_NET=0 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) printf '未知参数: %s（用 --help 看用法）\n' "$arg"; exit 2 ;;
    esac
done

if [ -t 1 ]; then
    G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[36m'; N=$'\033[0m'
else
    G=; R=; Y=; B=; N=
fi

PASS=0
WARN=0
FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  %s[OK]%s   %s\n' "$G" "$N" "$1"; }
warn() { WARN=$((WARN + 1)); printf '  %s[WARN]%s %s\n' "$Y" "$N" "$1"; shift; [ $# -gt 0 ] && printf '         -> %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  %s[FAIL]%s %s\n' "$R" "$N" "$1"; shift; [ $# -gt 0 ] && printf '         -> %s\n' "$1"; }
info() { printf '  %s[..]%s   %s\n' "$B" "$N" "$1"; }
section() { printf '\n%s── %s%s\n' "$B" "$1" "$N"; }

# 从 .env 取一个值（去 CR，忽略注释行）。不用 source：.env 里有空格和括号。
env_get() {
    [ -f .env ] || return 0
    sed -n "s/^$1=//p" .env | head -1 | tr -d '\r'
}

printf '%s\n' "=============================================="
printf ' 现场体检  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf ' 项目根    %s\n' "$ROOT"
printf ' 上电时长  %s 秒\n' "$(cut -d. -f1 /proc/uptime 2>/dev/null || echo '?')"
printf '%s\n' "=============================================="

# 就绪标记：主程序进入主循环时打的那行日志「等待 c（查栏）或 a（开始下一轮）」，
# 它在相机打开 / LLM 预热 / 显示线程之后才出现，是真正的就绪点。
app_ready() {
    journalctl -u wave-vision-llm -b -o short-unix --no-pager 2>/dev/null \
        | grep -q '等待 c'
}

if [ "$WAIT_READY" = "1" ]; then
    info '等主程序就绪（最多 180s，等日志「等待 c/a」）…'
    T0="$(cut -d. -f1 /proc/uptime)"
    while :; do
        if app_ready; then
            NOW_S="$(cut -d. -f1 /proc/uptime)"
            ok "上电后 ${NOW_S}s 主程序就绪（本次等了 $((NOW_S - T0))s）"
            break
        fi
        NOW_S="$(cut -d. -f1 /proc/uptime)"
        if [ $((NOW_S - T0)) -ge 180 ]; then
            bad '等了 180s 仍未就绪' 'journalctl -u wave-vision-llm -b --no-pager | tail -40，看卡在哪一道门'
            break
        fi
        sleep 2
    done
fi

# ────────────────────────────────────────────────────────────
section '1. 配置（.env）'

if [ -f .env ]; then
    ok ".env 存在（$(wc -l < .env) 行）"
else
    bad ".env 不存在" "cp .env.example .env 后按「运行前配置」填 key / 代理 / 定案值"
fi

# 行内注释：dotenv 不剥注释，数值型会被 int()/float() 抛错后静默吞掉
BAD_INLINE="$(grep -nE '^[A-Za-z_][A-Za-z0-9_]*=.+[[:space:]]#' .env 2>/dev/null || true)"
if [ -n "$BAD_INLINE" ]; then
    bad ".env 里有行内注释（会被当成值的一部分）" "删掉「#」及其后内容，注释单独占一行"
    printf '%s\n' "$BAD_INLINE" | sed 's/^/           /' | head -8
else
    ok '.env 无行内注释'
fi

DUP="$(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' .env 2>/dev/null | sort | uniq -d)"
if [ -n "$DUP" ]; then
    bad "存在重复键：$(printf '%s' "$DUP" | tr '\n' ' ')" "两个加载器都是先到先得，留一行"
else
    ok '.env 无重复键'
fi

COUNT_ENV="$(env_get STAGE_TWO_DETECTION_COUNT)"
COUNT="${COUNT_ENV:-4}"
case "$COUNT" in
    ''|*[!0-9]*) COUNT=4; warn "STAGE_TWO_DETECTION_COUNT 不是数字：${COUNT_ENV}" '写 5（线下五路）' ;;
esac
[ -z "$COUNT_ENV" ] && warn "STAGE_TWO_DETECTION_COUNT 未写，代码默认 4（线下应为 5）" "在 .env 写 STAGE_TWO_DETECTION_COUNT=5"
[ "$COUNT" = "5" ] && ok "STAGE_TWO_DETECTION_COUNT=5"

MIRROR_ENV="$(env_get DETECT_MIRROR_VIEW_INDEX)"
if [ "${MIRROR_ENV:-0}" = "$COUNT" ]; then
    ok "DETECT_MIRROR_VIEW_INDEX=$MIRROR_ENV（与机位数一致）"
else
    warn "DETECT_MIRROR_VIEW_INDEX=${MIRROR_ENV:-0}，机位数=$COUNT（⑤ 不会走镜面裁剪）" \
         "线下应为 DETECT_MIRROR_VIEW_INDEX=$COUNT"
fi

CROP_ENV="$(env_get DETECT_MIRROR_CROP)"
if [ "${CROP_ENV:-0,0,1,0.60}" = "0,0,1,0.60" ]; then
    ok "DETECT_MIRROR_CROP=0,0,1,0.60"
else
    warn "DETECT_MIRROR_CROP=${CROP_ENV:-（默认）}，不是定案值 0,0,1,0.60" "只裁镜面上 60%，整图直送会把虚焦桶体当主体"
fi

TEMP_ENV="$(env_get LLM_TEMPERATURE)"
if [ "${TEMP_ENV:-}" = "0" ]; then
    ok 'LLM_TEMPERATURE=0（同一组图输出一致）'
else
    warn "LLM_TEMPERATURE=${TEMP_ENV:-（未写=服务端默认）}" "线下定案为 0，否则同一件货可能每次不同答案"
fi

VIEWS_ENV="$(env_get YOLO_VIEW_INDICES)"
if [ -z "$VIEWS_ENV" ]; then
    ok 'YOLO_VIEW_INDICES 为空（全部视角参与，符合定案）'
elif printf '%s' "$VIEWS_ENV" | grep -qE '^[0-9]+(,[0-9]+)*$'; then
    warn "YOLO_VIEW_INDICES=$VIEWS_ENV（形状投票会被砍路）" "实测砍到 2 路会掉 prism5 并漏检，建议留空"
else
    bad "YOLO_VIEW_INDICES 值非法：$VIEWS_ENV" "只允许逗号分隔的数字，或整行留空"
fi

FALLBACK_ENV="$(env_get YOLO_FALLBACK)"
info "YOLO_FALLBACK=${FALLBACK_ENV:-（未写=0，不回落）}"

BASE_URL="$(env_get DASHSCOPE_BASE_URL)"
API_KEY="$(env_get DASHSCOPE_API_KEY)"
MODEL="$(env_get DASHSCOPE_MODEL)"
if [ -n "$API_KEY" ]; then
    ok "DASHSCOPE_API_KEY 已填（${#API_KEY} 字符）"
else
    bad "DASHSCOPE_API_KEY 为空" "填 key；否则每轮都超时回 0"
fi
info "模型=${MODEL:-（未写）}  端点=${BASE_URL:-（未写）}"

# ────────────────────────────────────────────────────────────
section '2. 服务（systemd）'

if command -v systemctl >/dev/null 2>&1; then
    ENABLED="$(systemctl is-enabled wave-vision-llm 2>/dev/null || true)"
    if [ "$ENABLED" = "enabled" ]; then
        ok 'wave-vision-llm 已 enable（第二天上电自动起）'
    else
        bad "wave-vision-llm is-enabled=${ENABLED:-未知}" "sudo systemctl enable wave-vision-llm"
    fi

    ACTIVE="$(systemctl is-active wave-vision-llm 2>/dev/null || true)"
    if [ "$ACTIVE" = "active" ]; then
        ok 'wave-vision-llm 正在运行'
    elif [ "$WITH_CAMS" = "1" ]; then
        info '服务未运行（--cameras 取帧需要它停着，属预期）'
    else
        bad "wave-vision-llm is-active=${ACTIVE:-未知}" "journalctl -u wave-vision-llm -b --no-pager | tail -40"
    fi

    NR="$(systemctl show -p NRestarts --value wave-vision-llm 2>/dev/null || echo '?')"
    if [ "$NR" = "0" ]; then
        ok 'NRestarts=0（没有反复崩溃重启）'
    elif [ "$NR" = "?" ]; then
        warn '读不到 NRestarts' 'systemctl status wave-vision-llm --no-pager'
    else
        bad "NRestarts=$NR（服务在反复重启）" "看 journal：多半是相机别名缺失或 X 没起来"
    fi

    # 真实就绪：主程序打出「等待 c（查栏）或 a（开始下一轮）」那一刻。
    # 不能用 ActiveEnterTimestamp：Type=simple 下它在 fork 瞬间就置位（实测 13s），
    # 那时相机还没开、预热也没做，会让人误判「冷启动很快」。
    READY_TS="$(journalctl -u wave-vision-llm -b -o short-unix --no-pager 2>/dev/null \
        | grep -m1 '等待 c' | awk '{printf "%d", $1}')"
    BOOT_EPOCH=$(( $(date +%s) - $(cut -d. -f1 /proc/uptime) ))
    if [ -n "${READY_TS:-}" ] && [ "${READY_TS:-0}" -gt "$BOOT_EPOCH" ]; then
        READY_S=$(( READY_TS - BOOT_EPOCH ))
        if [ "$READY_S" -gt 300 ]; then
            warn "上电后 ${READY_S}s 主程序才就绪（>300s：多半是后来手动重启，不代表冷启动）" \
                 'reboot 后加 --wait-ready 重跑，拿真实数字'
        else
            ok "上电后 ${READY_S}s 主程序就绪（日志「等待 c/a」；正常 60~120s）"
        fi
    else
        UPTIME_S="$(cut -d. -f1 /proc/uptime)"
        if [ "${UPTIME_S:-0}" -lt 150 ]; then
            info "刚上电 ${UPTIME_S}s，主程序还没打出「等待 c/a」（预热中）；加 --wait-ready 可等到它"
        else
            warn '日志里找不到「等待 c（查栏）或 a（开始下一轮）」' \
                 'journalctl -u wave-vision-llm -b --no-pager | tail -40 看卡在哪'
        fi
    fi
else
    warn '没有 systemctl（不是板子？）' '这条检查只在板端有意义'
fi

# ────────────────────────────────────────────────────────────
section '3. 相机别名'

EXPECT=$((COUNT + 1))
MISSING=0
for name in pallet $(seq -f 'detect_%g' 1 "$COUNT"); do
    if [ -e "/dev/wave/cam_$name" ]; then
        :
    else
        MISSING=$((MISSING + 1))
        printf '  缺失: /dev/wave/cam_%s\n' "$name"
    fi
done
if [ "$MISSING" -eq 0 ]; then
    ok "别名齐全：cam_pallet + cam_detect_1..$COUNT（共 $EXPECT 路）"
else
    bad "缺 $MISSING 个相机别名" "查 70-wave-cameras.rules 与接线：lsusb -t; udevadm info -q property -n /dev/videoX"
fi

if [ "$WITH_CAMS" = "1" ]; then
    if [ "$(systemctl is-active wave-vision-llm 2>/dev/null || true)" = "active" ]; then
        bad '--cameras 需要先停服务，否则相机被占用' 'sudo systemctl stop wave-vision-llm（测完记得 start）'
    elif command -v python >/dev/null 2>&1; then
        info "逐路取帧（每路 20 帧，约 10 秒）…"
        python tools/probe_cameras.py --rounds 20 2>&1 | tail -20
        CAM_RC=${PIPESTATUS[0]}
        if [ "$CAM_RC" -eq 0 ]; then
            ok 'probe_cameras 退出码 0'
        else
            bad "probe_cameras 退出码 $CAM_RC（有相机开不出或掉帧）" '看上面的逐路输出：某个别名打不开 → 换口/换线，别在软件里改'
        fi
    else
        warn '找不到 python，跳过取帧' 'conda activate llm 后再跑 --cameras'
    fi
fi

# ────────────────────────────────────────────────────────────
section '4. 供电 / USB 稳定性'

if command -v dmesg >/dev/null 2>&1; then
    DMESG_LINES="$(dmesg 2>/dev/null | wc -l)"
    if [ "${DMESG_LINES:-0}" -eq 0 ]; then
        warn '读不到 dmesg（kernel.dmesg_restrict=1 或容器环境）' '用 sudo dmesg | grep -c "USB disconnect" 自查；建议封车前记录两次对比'
    else
        DISC="$(dmesg 2>/dev/null | grep -c 'USB disconnect' || true)"
        if [ "${DISC:-0}" = "0" ]; then
            ok 'dmesg 中无 USB disconnect（5V 没被拉塌）'
        else
            warn "dmesg 里有 $DISC 次 USB disconnect" '4G 棒别插板子 USB；相机换到带供电的 Hub 或另一条总线'
        fi
        OVER="$(dmesg 2>/dev/null | grep -ciE 'over-current|Cannot enable' || true)"
        [ "${OVER:-0}" -gt 0 ] && warn "dmesg 有 $OVER 条 over-current / Cannot enable" '供电不足：换带电源 Hub，或减少同口设备'
    fi
else
    warn '没有 dmesg 命令' '跳过供电检查'
fi

# ────────────────────────────────────────────────────────────
section '5. 网络 / LLM 端点'

if [ "$WITH_NET" = "0" ]; then
    info '（--no-net，跳过）'
elif [ -z "$BASE_URL" ] || [ -z "$API_KEY" ]; then
    bad '缺 BASE_URL 或 key，无法探测' '先补 .env'
else
    AUTH_FILE="$(mktemp /tmp/.venue_curl_XXXXXX)"
    chmod 600 "$AUTH_FILE"
    printf 'header = "Authorization: Bearer %s"\n' "$API_KEY" > "$AUTH_FILE"
    PROXY="${HTTPS_PROXY:-$(env_get HTTPS_PROXY)}"
    CURL_PROXY=()
    [ -n "$PROXY" ] && CURL_PROXY=(-x "$PROXY")
    CODE="$(curl -s -o /tmp/.venue_probe.json -w '%{http_code}' -m 8 \
        "${CURL_PROXY[@]}" -K "$AUTH_FILE" "${BASE_URL%/}/models" 2>/dev/null || echo 000)"
    rm -f "$AUTH_FILE"
    case "$CODE" in
        200) ok "LLM 端点往返 HTTP 200（${BASE_URL%/}/models）" ;;
        000) bad 'LLM 端点不通（curl 000：DNS/TCP/TLS 失败）' \
                 '换热点：nmcli con up "我是谁的Mate 60"；确认 4G 先上电再开板子' ;;
        401|403) bad "HTTP $CODE：key 无效或地区门禁" '换 key；或临时启用 HTTPS_PROXY 走代理' ;;
        404) bad 'HTTP 404：端点或模型名不对' '核对 DASHSCOPE_BASE_URL / DASHSCOPE_MODEL（应为 qwen3-vl-plus）' ;;
        *)   warn "HTTP $CODE" '看 /tmp/.venue_probe.json 里的 error.message' ;;
    esac
fi

# ────────────────────────────────────────────────────────────
section '6. 显示屏'

if command -v xrandr >/dev/null 2>&1; then
    SCREEN="$(DISPLAY="${DISPLAY:-:0}" xrandr --current 2>/dev/null | grep -m1 ' connected')"
    if [ -n "$SCREEN" ]; then
        RES="$(printf '%s' "$SCREEN" | grep -oE '[0-9]+x[0-9]+' | head -1)"
        W="${RES%x*}"
        if [ "${W:-0}" -ge 1024 ] 2>/dev/null; then
            ok "显示已连接，分辨率 $RES"
        else
            warn "分辨率 $RES 偏低（EDID 没读到会退到 800x480，表格会串/截断）" \
                 '确认看门狗：systemctl is-enabled wave-hdmi-watch；没开就 sudo systemctl enable --now wave-hdmi-watch'
        fi
    else
        bad "DISPLAY=${DISPLAY:-:0} 上读不到显示" '确认已登录图形会话；或 xrandr 用对 DISPLAY'
    fi
else
    warn '没装 xrandr，跳过显示检查' 'sudo apt install x11-xserver-utils'
fi

# ────────────────────────────────────────────────────────────
section '7. 串口'

SERIAL_PORT="/dev/ttyS0"
if [ -e "$SERIAL_PORT" ]; then
    if [ -r "$SERIAL_PORT" ] && [ -w "$SERIAL_PORT" ]; then
        ok "$SERIAL_PORT 可读写（115200，与 const.py 一致）"
    else
        bad "$SERIAL_PORT 权限不足" 'sudo usermod -aG dialout $USER 后重新登录'
    fi
else
    warn "$SERIAL_PORT 不存在" '按板上实际串口改 shared/const.py 的 SERIAL_PORT'
fi
info '协议本身要电控配合验：c→5 位 1/0、a→1~6、v→先上屏再回 f'

# ────────────────────────────────────────────────────────────
section '8. 资源'

if command -v df >/dev/null 2>&1; then
    AVAIL_MB="$(df -Pk / | awk 'NR==2{print int($4/1024)}')"
    if [ "${AVAIL_MB:-0}" -ge 1024 ]; then
        ok "根分区剩余 ${AVAIL_MB} MB"
    elif [ "${AVAIL_MB:-0}" -ge 300 ]; then
        warn "根分区剩余 ${AVAIL_MB} MB" '录像/抓图会写满；清 capture/ 里的旧目录'
    else
        bad "根分区只剩 ${AVAIL_MB} MB" '先删 capture/ 旧目录，否则写图失败会让整轮识别中断'
    fi
fi

if [ -r /sys/class/thermal/thermal_zone0/temp ]; then
    T_RAW="$(cat /sys/class/thermal/thermal_zone0/temp)"
    T_C=$((T_RAW / 1000))
    if [ "$T_C" -ge 80 ]; then
        warn "SoC 温度 ${T_C}°C" '检查风扇/散热片；高温会降频，a→v 变慢'
    else
        ok "SoC 温度 ${T_C}°C"
    fi
fi

CAP_MB="$(du -sm capture 2>/dev/null | awk '{print $1}')"
[ -n "${CAP_MB:-}" ] && info "capture/ 占用 ${CAP_MB} MB（录像多了会拖慢 IO）"

# ────────────────────────────────────────────────────────────
printf '\n%s\n' "=============================================="
printf ' 结果：%s通过 %d%s  %s警告 %d%s  %s失败 %d%s\n' \
    "$G" "$PASS" "$N" "$Y" "$WARN" "$N" "$R" "$FAIL" "$N"
if [ "$FAIL" -eq 0 ]; then
    printf ' %s可以封车%s（FAIL=0；WARN 逐条确认即可）\n' "$G" "$N"
else
    printf ' %s有 %d 项 FAIL%s：按上面的「->」逐条处理，处理完重跑\n' "$R" "$FAIL" "$N"
fi
printf '%s\n\n' "=============================================="

exit "$FAIL"
