#!/usr/bin/env bash
# 夜间无人值守流水线：B 线（微调）→ 转换 → 评测 → A 线（COCO 初始化）→ 转换 → 评测 → 报告
#
# 设计约束：
#   * 任何一步失败都不中断后续（只为了一份能用的产物），失败原因写进日志；
#   * 显存不足（OOM）自动降 batch 4 重跑一次；
#   * **不改部署**：只产出 rknn + 对比报告，是否替换 models/ 等早上人工决定。
#
# 启动：nohup bash tools/night_pipeline.sh > datasets/night.log 2>&1 &
# 进度：tail -f datasets/night.log    或看 datasets/night_status.txt
set -u

ROOT=/mnt/d/GongChuang_Advanced
cd "$ROOT" || exit 1

DATA=datasets/shape18_mix/data.yaml
OUT=datasets/night
STATUS=$OUT/night_status.txt
mkdir -p "$OUT"

source ~/miniconda3/etc/profile.d/conda.sh
conda activate rknn
export PYTHONPATH=/mnt/d/rk_ultralytics11

# data.yaml 里的 path 必须能在 WSL 里解析；每次开跑前重写一遍，避免继承 Windows 路径。
python - > /dev/null <<'PY'
import os

classes = ['ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere',
           'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5',
           'prism5_thinWalled', 'prism6', 'prism6_thinWalled', 'pyramid3', 'pyramid4', 'pyramid5']
root = '/mnt/d/GongChuang_Advanced/datasets/shape18_mix'
lines = ['path: ' + root, 'train: images/train', 'val: images/val', 'names:']
lines += ['  %d: %s' % (i, n) for i, n in enumerate(classes)]
open(os.path.join(root, 'data.yaml'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
PY

log() { echo "[$(date '+%m-%d %H:%M:%S')] $*"; }
stage() { log "===== $* ====="; echo "$*  $(date '+%H:%M:%S')" >> "$STATUS"; }

train_track() {  # train_track <名字> <init> <epochs> [freeze]
    local name=$1 init=$2 epochs=$3 freeze=${4:-0}
    local batch=8
    for attempt in 1 2; do
        stage "训练 $name (init=$init, epochs=$epochs, batch=$batch, freeze=$freeze)"
        python - "$init" "$epochs" "$batch" "$freeze" "$name" <<'PY'
import sys
from ultralytics import YOLO

init, epochs, batch, freeze, name = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
model = YOLO(init)
model.train(
    data='datasets/shape18_mix/data.yaml',
    epochs=epochs, imgsz=640, batch=batch, freeze=freeze,
    lr0=0.003 if freeze else 0.01, patience=30,
    project='runs/night', name=name, exist_ok=True,
    device=0, workers=4, plots=False, verbose=True,
)
PY
        if [ -f "runs/night/$name/weights/best.pt" ]; then
            log "训练 $name 完成"
            return 0
        fi
        log "训练 $name 失败（attempt=$attempt batch=$batch）"
        if [ "$batch" -gt 4 ]; then
            batch=4
            log "降到 batch=$batch 重试"
        else
            return 1
        fi
    done
    return 1
}

export_and_eval() {  # export_and_eval <名字>
    local name=$1
    local best="runs/night/$name/weights/best.pt"
    [ -f "$best" ] || { log "没有 $best，跳过"; return 1; }

    stage "导出 ONNX $name"
    python - "$best" <<'PY'
import sys
from ultralytics import YOLO
YOLO(sys.argv[1]).export(format='onnx', imgsz=640, opset=12, simplify=True)
PY

    local onnx="runs/night/$name/weights/best.onnx"
    if [ -f "$onnx" ]; then
        stage "转换 RKNN $name"
        python tools/convert_rknn.py "$onnx" "$OUT/$name.rknn"
    else
        log "ONNX 导出失败，跳过 RKNN"
    fi

    stage "评测 $name"
    python tools/eval_shape.py --model "$best" --json "$OUT/eval_$name.json"
}

# ---------- B 线 ----------
if train_track B models_try/shape_best_3.pt 70 10; then
    export_and_eval B
else
    log "B 线训练失败，继续 A 线"
fi

# ---------- A 线 ----------
INIT_A=yolo11n.pt
if [ ! -f "$INIT_A" ]; then
    log "本地没有 yolo11n.pt，尝试下载（无网则退回 shape_best_3.pt）"
    python - <<'PY' || true
from ultralytics import YOLO
YOLO('yolo11n.pt')
PY
    [ -f "$INIT_A" ] || INIT_A=models_try/shape_best_3.pt
fi
log "A 线初始化: $INIT_A"

if train_track A "$INIT_A" 100 0; then
    export_and_eval A
else
    log "A 线训练失败"
fi

# ---------- 报告 ----------
stage "汇总"
python - <<'PY'
import json
import os

OUT = 'datasets/night'
base = 6  # 当前模型在 _eval2 上的基线（11 项中 6 项正确）
lines = ['# 夜间训练报告', '', f'基线（当前部署模型）：{base}/11', '']
for name in ('B', 'A'):
    path = os.path.join(OUT, f'eval_{name}.json')
    if not os.path.exists(path):
        lines.append(f'## {name} 线：未产出（训练或评测失败，见 night.log）')
        continue
    data = json.load(open(path, encoding='utf-8'))
    lines.append(f"## {name} 线：{data['ok']}/{data['total']}"
                 f"（{'优于' if data['ok'] > base else '持平' if data['ok'] == base else '劣于'}基线）")
    for entry in data['entries']:
        lines.append(f"- {'OK' if entry['ok'] else 'XX'} {entry['item']}  真值={entry['truth']}  预测={entry['pred']}")
    lines.append('')
with open(os.path.join(OUT, 'report.md'), 'w', encoding='utf-8') as handle:
    handle.write('\n'.join(lines) + '\n')
print('报告已写', os.path.join(OUT, 'report.md'))
PY

log "全部完成；报告：datasets/night/report.md"
echo "DONE $(date '+%H:%M:%S')" >> "$STATUS"
