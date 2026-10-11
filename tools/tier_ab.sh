#!/usr/bin/env bash
# service_tier A/B：对同一批多视角图反复请求，比较 OpenRouter 不同上游档位的延迟。
#
# 用法（项目根目录）：
#   bash tools/tier_ab.sh                 # 每档 3 发，用最新 run 的 stage2_r010 组
#   bash tools/tier_ab.sh 5               # 每档 5 发
#   bash tools/tier_ab.sh 3 "capture/images/20260929_163117/original/stage2_r014_0*.jpg"
#
# 前提：mihomo 在跑，且进程能拿到 HTTPS_PROXY（否则会直连大陆出口 → 全部 Connection error）。
# 脚本会先做代理预检，3 次 curl 全通过才开始烧钱；单次失败会自动重试一次并跳过。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

RUNS="${1:-3}"
DIR="${2:-}"
if [ -z "$DIR" ]; then
    DIR="$(ls -d capture/images/*/original 2>/dev/null | tail -1)"
fi
if [ -z "$DIR" ] || [ ! -d "$DIR" ]; then
    echo "找不到图片目录，请把 glob 作为第二个参数传进来"; exit 1
fi
IMGS=(${DIR}/stage2_r010_0*.jpg)
echo "目录: $DIR"
echo "图片: ${IMGS[*]}"
if [ "${#IMGS[@]}" -lt 1 ]; then
    echo "!! 没匹配到图片（默认找 stage2_r010_0*.jpg），请手工指定 glob"; exit 1
fi
echo "（视角数 = ${#IMGS[@]}）"

echo "== 代理预检（3 次）=="
echo "HTTPS_PROXY=${HTTPS_PROXY:-<空>}"
ok=0
for i in 1 2 3; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 https://openrouter.ai/api/v1/models 2>/dev/null)"
    echo "  curl#$i -> ${code:-失败}"
    [ "$code" = "200" ] && ok=$((ok + 1))
done
if [ "$ok" -lt 3 ]; then
    echo "!! 预检 $ok/3：现在测 tier 没意义，先修链路"; exit 1
fi

LOG="tier_ab_$(date +%m%d_%H%M).log"
echo "日志: $LOG"

for t in default flex priority ultrafast; do
    tier="$t"; [ "$t" = "default" ] && tier=""
    { echo; echo "=== tier='${t}' ==="; } | tee -a "$LOG"
    for i in $(seq 1 "$RUNS"); do
        LLM_SERVICE_TIER="$tier" python test_llm.py classify "${IMGS[@]}" --runs 1 >>"$LOG" 2>&1
        if [ $? -ne 0 ]; then
            echo "  run$i 失败（多为链路抖动），3 秒后重试一次…" | tee -a "$LOG"
            sleep 3
            LLM_SERVICE_TIER="$tier" python test_llm.py classify "${IMGS[@]}" --runs 1 >>"$LOG" 2>&1
            [ $? -ne 0 ] && echo "  run$i 重试仍失败，丢弃该样本" | tee -a "$LOG"
        fi
        sleep 2
    done
done

echo
echo "== 汇总（只统计成功样本；首字节 = LLM 请求 → HTTP 200 那一行）=="
python - "$LOG" <<'PY'
import re, statistics, sys
from datetime import datetime

data = {}
tier = None
t0 = t_hdr = None
for line in open(sys.argv[1], encoding="utf-8"):
    m = re.search(r"=== tier='(.*?)' ===", line)
    if m:
        tier = m.group(1) or "(默认)"; data.setdefault(tier, [[], [], []]); continue
    if tier is None:
        continue
    ts = None
    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})", line)
    if m:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f")
    if "LLM 请求：" in line:
        t0 = ts; t_hdr = None
    elif "HTTP Request: POST" in line:
        t_hdr = ts
    elif "LLM 请求结束，耗时" in line and ts and t0:
        total = (ts - t0).total_seconds()
        ttft = (t_hdr - t0).total_seconds() if t_hdr else float("nan")
        data[tier][0].append(total)
        data[tier][1].append(ttft)
        data[tier][2].append(total - ttft)

print(f"{'tier':<10}{'n':>3}{'总耗时中位':>12}{'首字节中位':>12}{'首字节后中位':>14}{'总耗时均值':>12}")
for t, (tot, ttft, rest) in data.items():
    if not tot:
        print(f"{t:<10}{0:>3}{'（全部失败）':>26}")
        continue
    print(f"{t:<10}{len(tot):>3}{statistics.median(tot):>12.2f}{statistics.median(ttft):>12.2f}"
          f"{statistics.median(rest):>14.2f}{statistics.mean(tot):>12.2f}")
print()
print("注意：若某档第 1 发的 cache_write_tokens 不为 0，说明该档没吃到前缀缓存，首字节会偏慢。")
PY
