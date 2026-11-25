#!/usr/bin/env bash
# 按类拍 YOLO 训练数据：每类 6 个姿态，每个姿态一次 snap_views（5 视角）。
#
# 用法（板上，先停主服务）：
#   sudo systemctl stop wave-vision-llm
#   bash tools/shot_sessions.sh                 # 全 18 类
#   bash tools/shot_sessions.sh pyramid4        # 只拍指定类
#   bash tools/shot_sessions.sh pyramid4 "prism6_thinWalled"
#
# 每个姿态会停下来等你摆好货再回车，所以不会连拍错位。
# tag 形如 pyramid4_p03 —— 训练脚本按 _pNN 前的部分当类别、整个 tag 当一个姿态。

set -u
cd "$(dirname "$0")/.." || exit 1

PY="${PY:-python}"
WARMUP="${WARMUP:-2}"

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

if [ "$#" -gt 0 ]; then
    CLASSES=("$@")
else
    CLASSES=("${ALL_CLASSES[@]}")
fi

# 校验类名，避免拼错后白拍
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

total=$(( ${#CLASSES[@]} * ${#POSES[@]} ))
index=0
for name in "${CLASSES[@]}"; do
    for pose_index in "${!POSES[@]}"; do
        pose=$(( pose_index + 1 ))
        tag=$(printf "%s_p%02d" "$name" "$pose")
        index=$(( index + 1 ))
        echo
        echo "=============================================="
        echo "[$index/$total] $name  —— 姿态 $pose/6：${POSES[$pose_index]}"
        echo "摆好后回车开始拍（Ctrl-C 退出）"
        echo "=============================================="
        read -r _
        "$PY" tools/snap_views.py --tag "$tag" --warmup "$WARMUP" 2>&1 | tail -2
    done
done

echo
echo "全部完成：共 $total 组（每组 5 张）。"
echo "检查：ls -1d capture/views/*_p0* | wc -l"
