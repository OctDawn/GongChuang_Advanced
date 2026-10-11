#!/usr/bin/env bash
# 启动前自检：确认板子到「当前 LLM 服务商」的这条链路真的通（含地区门禁）。
# 用法（项目根目录）：bash tools/net_check.sh
# 服务商与模型都从 .env 读（DASHSCOPE_BASE_URL / DASHSCOPE_MODEL），
# 所以 OpenRouter 与 OpenAI 官方 API 都能用同一个脚本自检。
# 全部通过退出码 0；不合格项数 = 退出码。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

ok=0
bad=0
green()  { printf '\033[32m%s\033[0m\n' "$1"; }
red()    { printf '\033[31m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }

BASE=""
KEY=""
MODEL=""
if [ -f .env ]; then
    BASE="$(sed -n 's/^DASHSCOPE_BASE_URL=//p' .env | head -1)"
    KEY="$(sed -n 's/^DASHSCOPE_API_KEY=//p' .env | head -1)"
    MODEL="$(sed -n 's/^DASHSCOPE_MODEL=//p' .env | head -1)"
    PROXY="$(sed -n 's/^HTTPS_PROXY=//p' .env | head -1)"
    if [ -n "$PROXY" ]; then
        export HTTPS_PROXY="${HTTPS_PROXY:-$PROXY}"
        export HTTP_PROXY="${HTTP_PROXY:-$PROXY}"
    fi
fi
BASE="${BASE:-https://openrouter.ai/api/v1}"
MODEL="${MODEL:-openai/gpt-6-astra}"
# key 也不放进命令行：用 curl -K 配置文件，退出时自动删除
CURL_AUTH_FILE="$(mktemp /tmp/.wave_net_check_XXXXXX)"
chmod 600 "$CURL_AUTH_FILE"
printf 'header = "Authorization: Bearer %s"\n' "$KEY" > "$CURL_AUTH_FILE"
trap 'rm -f "$CURL_AUTH_FILE"' EXIT

BASE="${BASE%/}"

echo "== 0. 本次自检的目标 =="
echo "   base_url: $BASE"
echo "   model   : $MODEL"
echo "   代理     : ${HTTPS_PROXY:-未设置}"
case "$BASE" in
    *openrouter*) PROVIDER="openrouter" ;;
    *api.openai.com*) PROVIDER="openai" ;;
    *) PROVIDER="other" ;;
esac
echo "   识别为   : $PROVIDER"

echo "== 1. 出口 IP / 国家（多个备选源，ipinfo 会限流）=="
IP=""
for url in "https://ipinfo.io/ip" "https://api.ipify.org" "https://ifconfig.co/ip"; do
    IP="$(curl -s --max-time 8 "$url" | tr -d '\r\n')"
    case "$IP" in
        *[0-9].[0-9]*) break ;;
        *) IP="" ;;
    esac
done
COUNTRY=""
for url in "https://ipinfo.io/country" "https://ipapi.co/country" "https://ifconfig.co/country-iso"; do
    COUNTRY="$(curl -s --max-time 8 "$url" | tr -d '\r\n' | tr '[:lower:]' '[:upper:]')"
    if [ "${#COUNTRY}" -eq 2 ]; then break; fi
    COUNTRY=""
done
echo "   IP: ${IP:-未知}   国家: ${COUNTRY:-未知}"
if [ -z "$COUNTRY" ] && [ -z "$IP" ]; then
    red "   x 拿不到出口信息：板子当前没有外网（先查热点与代理）"
    bad=$((bad + 1))
elif [ "$COUNTRY" = "CN" ]; then
    red "   x 出口在中国大陆：OpenAI 系模型会被地区门禁拦（403）"
    bad=$((bad + 1))
elif [ "$COUNTRY" = "HK" ] || [ "$COUNTRY" = "MO" ]; then
    yellow "   ! 出口在 $COUNTRY：实测对 OpenAI 系模型会 403，建议 filter 收紧到美国节点"
elif [ -n "$COUNTRY" ]; then
    green "   v 出口国家: $COUNTRY"
fi

echo "== 2. 板内代理是否在监听 =="
if ss -lntp 2>/dev/null | grep -q '127.0.0.1:7890'; then
    green "   v 127.0.0.1:7890 在监听（mihomo 在跑）"
    ok=$((ok + 1))
else
    yellow "   ! 127.0.0.1:7890 没有监听（改了 .bashrc 但 mihomo 没起？）"
fi

echo "== 3. key 是否有效 =="
if [ -z "$KEY" ]; then
    red "   x .env 里读不到 DASHSCOPE_API_KEY"
    bad=$((bad + 1))
else
    if [ "$PROVIDER" = "openrouter" ]; then
        CODE="$(curl -s -o /tmp/net_key.json -w '%{http_code}' --max-time 15 \
            "$BASE/key" -K "$CURL_AUTH_FILE")"
    else
        # OpenAI 官方没有 /key，用 /models 校验（200 = key 有效）
        CODE="$(curl -s -o /tmp/net_key.json -w '%{http_code}' --max-time 15 \
            "$BASE/models" -K "$CURL_AUTH_FILE")"
    fi
    if [ "$CODE" = "200" ]; then
        green "   v key 有效"
        grep -o '"limit_remaining":[^,}]*' /tmp/net_key.json | head -1
        ok=$((ok + 1))
    else
        red "   x key 校验 HTTP=$CODE  $(head -c 160 /tmp/net_key.json)"
        bad=$((bad + 1))
    fi
fi

echo "== 4. 目标模型能否真正调用（决定性判据）=="
echo "   模型: $MODEL"
# 不带任何输出上限参数：官方 API 不接受 max_tokens（要 max_completion_tokens），
# 主流程本来也不设上限，这里保持一致最稳。
CODE="$(curl -s -o /tmp/net_model.json -w '%{http_code}' --max-time 60 \
    "$BASE/chat/completions" \
    -K "$CURL_AUTH_FILE" -H "Content-Type: application/json" \
    -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}")"
if [ "$CODE" = "200" ]; then
    green "   v 可调用"
    ok=$((ok + 1))
elif [ "$CODE" = "403" ]; then
    red "   x 403 地区限制：$(grep -o '"message":"[^"]*"' /tmp/net_model.json | head -1)"
    red "     -> 换美国节点 / 换端点 / 换国内模型"
    bad=$((bad + 1))
elif [ "$CODE" = "400" ]; then
    red "   x 400 参数或模型名问题：$(head -c 200 /tmp/net_model.json)"
    red "     -> 用 tools/probe_openai_api.py 确认 model id 与端点支持"
    bad=$((bad + 1))
else
    red "   x HTTP=$CODE  $(head -c 160 /tmp/net_model.json)"
    bad=$((bad + 1))
fi

echo
if [ "$bad" -eq 0 ]; then
    green "全部通过（$ok/4），可以启动 main_push_llm.py"
else
    red "$bad 项不合格，先修好再启动（否则会白白等超时再回落 / 回 0）"
fi
exit "$bad"
