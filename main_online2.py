"""线上版2 入口：物体推到识别区，单目 + 3 色 × 3 形固定词表识别。

和另外两个入口的区别：
    main_push_llm.py  线下：4 路识别区 + 形状记忆，最稳
    main_online.py    线上版1：托盘俯视整盘一次预判，最快
    main_online2.py   线上版2：只开 1 路识别区相机，逐件单目识别（本文件）

本入口会强制：
    ONLINE_MODE=0              关掉整盘预判
    SINGLE_SHAPE_MODE=1        走单目 3×3 词表
    STAGE_TWO_DETECTION_COUNT=1  只开 cam_detect_1，其余三路不占带宽

用法（板上，项目根目录）：
    sudo systemctl stop wave-vision-llm
    conda activate llm
    DISPLAY=:0 python main_online2.py

注意：如果用 systemd 起，start_vision.sh 会按 .env 里的 STAGE_TWO_DETECTION_COUNT
等别名；届时需要把该值也写成 1，或直接手动跑本入口。
"""

import os

# 必须在 import main_push_llm 之前：const / qw 都在 import 期读环境变量
os.environ["ONLINE_MODE"] = "0"
os.environ["SINGLE_SHAPE_MODE"] = "1"
os.environ["STAGE_TWO_DETECTION_COUNT"] = "1"

import main_push_llm  # noqa: E402

if __name__ == "__main__":
    main_push_llm.main()
