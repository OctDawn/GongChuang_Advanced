"""线上版2 软件端自检：从识别区相机拍一张，判颜色 + 形状（3×3 词表）。

不接电控、不碰串口/显示。适合"摆一件 → 看它判成什么"。

用法（板上，项目根目录；先停主程序）：
    sudo systemctl stop wave-vision-llm
    conda activate llm
    python tools/grid_check.py
    python tools/grid_check.py --tag 红正方体 --warmup 2.5
    python tools/grid_check.py --no-save

输出目录 capture/grid_check/<时间戳>_<tag>/：
    frame.jpg   相机原图
    sent.jpg    实际送模型的那张（crop_detect_llm 之后）
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()      # 必须在 llm.qw 之前

import cv2  # noqa: E402

from shared.common import crop_detect_llm, initial_cap  # noqa: E402
from shared.const import (  # noqa: E402
    DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
)
from llm.grid_recognition import classify_grid  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="")
    parser.add_argument("--warmup", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    camera_id = DETECT_CAMERA_IDS[0]
    name = DETECT_CAMERA_NAMES[0]
    cap = initial_cap(camera_id, name, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)
    if cap is None:
        raise SystemExit(f"打不开识别区相机 {camera_id}：先 sudo systemctl stop wave-vision-llm")
    try:
        deadline = time.time() + args.warmup
        while time.time() < deadline:
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            raise SystemExit("读帧失败，重跑一次")
    finally:
        cap.release()

    view = crop_detect_llm(frame)
    print("相机 %s  原图 %dx%d -> 送模型 %dx%d"
          % (camera_id, frame.shape[1], frame.shape[0], view.shape[1], view.shape[0]))

    started = time.time()
    result = classify_grid(view, total_timeout=args.timeout)
    seconds = time.time() - started

    print("\n耗时 %.2f 秒" % seconds)
    print("  状态:", result["status"])
    if result["status"] == "ok":
        print("  颜色:", result["color"])
        print("  形状:", result["shape"])
    print("  说明:", result["reason"] or "-")

    if args.no_save:
        return
    stamp = time.strftime("%Y%m%d_%H%M%S") + ("_" + args.tag if args.tag else "")
    out_dir = os.path.join("capture", "grid_check", stamp)
    os.makedirs(out_dir, exist_ok=True)
    cv2.imwrite(os.path.join(out_dir, "frame.jpg"), frame)
    cv2.imwrite(os.path.join(out_dir, "sent.jpg"), view)
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as stream:
        json.dump({"seconds": round(seconds, 2), **result}, stream,
                  ensure_ascii=False, indent=2)
    print("\n已保存:", out_dir)


if __name__ == "__main__":
    main()
