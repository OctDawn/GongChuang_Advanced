#!/bin/bash
set -e
source ~/miniconda3/etc/profile.d/conda.sh
conda activate rknn
cd /mnt/d/rk_ultralytics11
export PYTHONPATH=/mnt/d/rk_ultralytics11

echo "=== export onnx ==="
python3 - <<'PY'
from ultralytics import YOLO
m = YOLO('/mnt/d/GongChuang_Advanced/weights/yolo11s.pt')
p = m.export(format='rknn', imgsz=640)
print('EXPORTED PATH:', p)
PY

ONNX=/mnt/d/GongChuang_Advanced/weights/yolo11s.onnx
echo "=== convert rknn ==="
python3 /mnt/d/GongChuang_Advanced/tools/convert_rknn.py "$ONNX" /mnt/d/GongChuang_Advanced/models_new/test_yolo11s.rknn
echo "=== done ==="
ls -lh /mnt/d/GongChuang_Advanced/models_new/ 2>/dev/null || true
