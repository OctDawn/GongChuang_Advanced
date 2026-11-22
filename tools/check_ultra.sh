#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate rknn
cd /mnt/d/rk_ultralytics11
PYTHONPATH=. python3 -c "import ultralytics; print('ultralytics', ultralytics.__version__)"
