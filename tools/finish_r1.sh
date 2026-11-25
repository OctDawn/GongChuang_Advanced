#!/bin/bash
# 等 R1 训练结束，然后导出 ONNX → RKNN → 用新口径评测 → 写报告
set -u
source ~/miniconda3/etc/profile.d/conda.sh
conda activate rknn
export PYTHONPATH=/mnt/d/rk_ultralytics11
cd /mnt/d/GongChuang_Advanced || exit 1

REPORT=datasets/r1_report.md
: > "$REPORT"

echo "等待训练结束…" | tee -a "$REPORT"
for i in $(seq 1 400); do
    if ! pgrep -f train_shape18.py > /dev/null; then
        break
    fi
    sleep 10
done

BEST=runs/night3/R1/weights/best.pt
if [ ! -f "$BEST" ]; then
    echo "没有 best.pt，训练可能失败" | tee -a "$REPORT"
    exit 1
fi

echo "训练结束：$(date '+%H:%M:%S')" | tee -a "$REPORT"
tail -3 runs/night3/R1/results.csv | cut -c1-80 | tee -a "$REPORT"

echo "导出 ONNX…" | tee -a "$REPORT"
python - "$BEST" <<'PY'
import sys
from ultralytics import YOLO
print(YOLO(sys.argv[1]).export(format='onnx', imgsz=640, opset=12, simplify=True))
PY

echo "转 RKNN…" | tee -a "$REPORT"
python tools/convert_rknn.py runs/night3/R1/weights/best.onnx datasets/night/R1.rknn 2>&1 | tail -3 | tee -a "$REPORT"

echo "" | tee -a "$REPORT"
echo "=== 评测（去 ③、⑤ 裁上 60%）===" | tee -a "$REPORT"
python tools/eval_shape.py --model "$BEST" --drop-views 3 --mirror-crop 0.60 \
    --json datasets/night/eval_R1.json 2>&1 | tee -a "$REPORT"

echo "完成：$(date '+%H:%M:%S')" | tee -a "$REPORT"
