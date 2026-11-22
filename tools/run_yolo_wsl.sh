#!/bin/bash
# 训练 YOLO（颜色 + 形状）→ 导出 ONNX（RKOPT 分支格式）→ 转 RK3588 RKNN
# 用法: wsl -d Ubuntu -- bash /mnt/d/GongChuang_Advanced/tools/run_yolo_wsl.sh
# 可选环境变量: EPOCHS(默认60) BATCH(默认4) FRACTION(默认1.0)
set -e
source ~/miniconda3/etc/profile.d/conda.sh
conda activate rknn

PROJECT=/mnt/d/GongChuang_Advanced
DATA=$PROJECT/yolo_full
RKOPT=/mnt/d/rk_ultralytics11
WEIGHTS=$PROJECT/weights/yolo11s.pt
OUT=$PROJECT/models_new
EPOCHS=${1:-${EPOCHS:-60}}
BATCH=${2:-${BATCH:-4}}
FRACTION=${3:-${FRACTION:-1.0}}
mkdir -p "$OUT"

# seaborn 是 ultralytics 画图依赖，缺了会 warning
python3 -c "import seaborn" 2>/dev/null || pip install -q seaborn

gen_yaml() {
  local tag=$1; shift
  {
    echo "path: $DATA"
    echo "train: images/train"
    echo "val: images/val"
    echo "names:"
    local i=0
    for n in "$@"; do echo "  $i: $n"; i=$((i+1)); done
  } > "$DATA/data_${tag}_wsl.yaml"
}
gen_yaml color black blue brown deep_blue deep_green green orange pink purple red white yellow
gen_yaml shape ball cone cube cuboid cylinder cylinder_thinWalled hemisphere parallelepiped prism3 prism3_thinWalled prism4_thinWalled prism5 prism5_thinWalled prism6 pyramid3 pyramid4

cd "$RKOPT"
export PYTHONPATH="$RKOPT"

for tag in color shape; do
  echo "================ PREPARE labels: $tag ================"
  # ultralytics 默认按 images -> labels 找标签，这里把对应任务的标签复制成 labels/
  rm -rf "$DATA/labels"
  cp -r "$DATA/labels_${tag}" "$DATA/labels"

  echo "================ TRAIN $tag ================"
  python3 - "$WEIGHTS" "$DATA/data_${tag}_wsl.yaml" "$PROJECT/runs/$tag" "$EPOCHS" "$BATCH" "$FRACTION" <<'PY'
import sys
from ultralytics import YOLO
w, data, proj, epochs, batch, frac = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), float(sys.argv[6])
m = YOLO(w)
m.train(data=data, imgsz=640, epochs=epochs, batch=batch, fraction=frac,
        project=proj, name='run', exist_ok=True, workers=4, verbose=True)
PY

  echo "================ EXPORT $tag ================"
  python3 - "$PROJECT/runs/$tag/run/weights/best.pt" <<'PY'
import sys
from ultralytics import YOLO
m = YOLO(sys.argv[1])
p = m.export(format='rknn', imgsz=640)
print('EXPORTED:', p)
PY

  echo "================ CONVERT $tag ================"
  ONNX="$PROJECT/runs/$tag/run/weights/best.onnx"
  python3 "$PROJECT/tools/convert_rknn.py" "$ONNX" "$OUT/${tag}_detect_area.rknn"
done

echo "ALL DONE. rknn -> $OUT"
ls -lh "$OUT"
