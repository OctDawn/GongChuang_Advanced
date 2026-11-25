#!/usr/bin/env bash
# 拍 YOLO 训练数据：每类 N 个姿态，每个姿态一次 snap_views（5 视角）。
#
# 用法（板上，先停主服务）：
#   sudo systemctl stop wave-vision-llm
#   bash tools/shot_sessions.sh                      # 18 类 × 6 姿态
#   bash tools/shot_sessions.sh --count 10           # 每类 10 个姿态
#   bash tools/shot_sessions.sh --classes "pyramid4 prism6_thinWalled"
#   bash tools/shot_sessions.sh --resume              # 跳过已经拍过的姿态
#   bash tools/shot_sessions.sh --empty 20            # 额外拍 20 组空台（负样本）
#
# 每个姿态会停下来等你摆好货再回车，所以不会连拍错位。
# tag 形如 pyramid4_p03 —— 训练脚本按 _pNN 前的部分当类别、整个 tag 当一个姿态。
#
# 姿态顺序：01 直立 / 02 侧躺 / 03 倒放 / 04 绕竖轴 45° / 05 绕竖轴 90° / 06 偏左 5cm，
# 超过 6 个的部分按“自由姿态”编号，自己换角度就行。

set -u
cd "$(dirname "$0")/.." || exit 1

PY="${PY:-python}"
WARMUP="${WARMUP:-2}"
COUNT=6
CLASSES_ARG=""
RESUME=0
EMPTY=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --count)    COUNT="$2"; shift 2 ;;
        --classes)  CLASSES_ARG="$2"; shift 2 ;;
        --resume)   RESUME=1; shift ;;
        --empty)    EMPTY="$2"; shift 2 ;;
        -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "未知参数: $1"; exit 2 ;;
    esac
done

ALL_CLASSES=(
    ball cone cube cuboid cylinder cylinder_thinWalled hemisphere parallelepiped
    prism3 prism3_thinWalled prism4_thinWalled
    prism5 prism5_thinWalled prism6 prism6_thinWalled
    pyramid3 pyramid4 pyramid5
)
POSES=("直立" "侧躺" "倒放" "绕竖轴 45°" "绕竖轴 90°" "偏左 5cm")

if systemctl is-active --quiet wave-vision-llm 2>/dev/null; then
    echo "!! wave-vision-llm 正在运行，相机被占用："
    echo "   sudo systemctl stop wave-vision-llm"
    exit 1
fi

if [ -n "$CLASSES_ARG" ]; then
    read -r -a CLASSES <<< "$CLASSES_ARG"
else
    CLASSES=("${ALL_CLASSES[@]}")
fi

for name in "${CLASSES[@]}"; do
    found=0
    for known in "${ALL_CLASSES[@]}"; do
        [ "$name" = "$known" ] && found=1 && break
    done
    if [ "$found" -eq 0 ]; then
        echo "!! 未知类名: $name"
        echo "   可用: ${ALL_CLASSES[*]}"
        exit 2
    fi
done

shot_one() {  # shot_one <tag> <提示文字>
    local tag=$1 hint=$2
    if [ "$RESUME" = "1" ] && compgen -G "capture/views/*_${tag}" > /dev/null; then
        echo "   跳过（已存在）$tag"
        return 0
    fi
    echo "   摆好后回车拍摄（s=跳过，q=退出）：$hint"
    read -r answer
    case "$answer" in
        s|S) echo "   跳过 $tag"; return 0 ;;
        q|Q) echo "退出"; exit 0 ;;
    esac
    "$PY" tools/snap_views.py --tag "$tag" --warmup "$WARMUP" 2>&1 | tail -1
}

declare -A DONE 2>/dev/null || true

echo "== 目标：${#CLASSES[@]} 类 × $COUNT 姿态 =="
for name in "${CLASSES[@]}"; do
    echo
    echo "======== $name ========"
    for index in $(seq 1 "$COUNT"); do
        pose_index=$(( (index - 1) % ${#POSES[@]} ))
        if [ "$index" -le "${#POSES[@]}" ]; then
            hint="${POSES[$pose_index]}"
        else
            hint="自由姿态（第 $index 个）"
        fi
        tag=$(printf "%s_p%02d" "$name" "$index")
        echo "  [$index/$COUNT] $hint"
        shot_one "$tag" "$hint"
    done
done

if [ "$EMPTY" -gt 0 ]; then
    echo
    echo "======== 空台负样本 × $EMPTY ========"
    for index in $(seq 1 "$EMPTY"); do
        tag=$(printf "empty_p%02d" "$index")
        echo "  [$index/$EMPTY] 清空识别区"
        shot_one "$tag" "空台"
    done
fi

echo
echo "===== 汇总 ====="
total=0
for name in "${CLASSES[@]}"; do
    count=$(ls -1d capture/views/*_${name}_p[0-9][0-9] 2>/dev/null | wc -l)
    total=$(( total + count ))
    printf "  %-22s %2d / %d\n" "$name" "$count" "$COUNT"
done
if [ "$EMPTY" -gt 0 ]; then
    count=$(ls -1d capture/views/*_empty_p[0-9][0-9] 2>/dev/null | wc -l)
    printf "  %-22s %2d / %d\n" "empty（负样本）" "$count" "$EMPTY"
fi
echo "  合计 $total 组（每组 5 张）"
