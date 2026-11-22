#!/bin/bash
nohup bash /mnt/d/GongChuang_Advanced/tools/run_yolo_wsl.sh 1 4 0.15 > /mnt/d/GongChuang_Advanced/tools/test_train.log 2>&1 &
echo "started pid=$!"
