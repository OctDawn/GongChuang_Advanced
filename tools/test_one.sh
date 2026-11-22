#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate rknn
cd /mnt/d/rk_ultralytics11
export PYTHONPATH=/mnt/d/rk_ultralytics11
python3 - <<'PY'
from ultralytics import YOLO
m = YOLO('/mnt/d/GongChuang_Advanced/weights/yolo11s.pt')
m.train(data='/mnt/d/GongChuang_Advanced/yolo_full/data_color_wsl.yaml',
        epochs=1, batch=4, fraction=0.05, workers=2,
        project='/mnt/d/GongChuang_Advanced/runs/test', name='one', exist_ok=True)
print('TRAIN_OK')
PY
