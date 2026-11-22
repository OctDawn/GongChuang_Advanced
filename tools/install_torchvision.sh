#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate rknn
pip install -q torchvision==0.19.0+cu121 --index-url https://download.pytorch.org/whl/cu121
echo "pip_exit=$?"
python3 -c "import torch,torchvision; print(torch.__version__, torchvision.__version__)"
