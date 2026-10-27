"""单拍一张托盘俯视图（1280×960），不需要显示器。

典型用途：给线上赛段的六类参考图拍"俯视单件图"，或补托盘空场景图。

用法（板上，项目根目录；先停主程序，否则相机被占用）：
    sudo systemctl stop wave-vision-llm
    conda activate llm
    python tools/snap_pallet.py                       # 存到 capture/snapshot/
    python tools/snap_pallet.py --out /tmp/pallet.jpg
    python tools/snap_pallet.py --warmup 2.5 --tag 黑正方体

出图是**未裁剪**的整幅原图（1280×960）。要裁成送模型的样子再看，
用 tools/crop_preview.py：python tools/crop_preview.py 图.jpg --stage pallet
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from shared.common import initial_cap  # noqa: E402
from shared.const import CAMERA_PALLET_ID, PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="输出文件路径（默认 capture/snapshot/pallet_<时间戳>.jpg）")
    parser.add_argument("--tag", default="", help="文件名里的备注，例如 黑正方体")
    parser.add_argument("--warmup", type=float, default=2.0,
                        help="先空读多少秒让曝光/白平衡收敛（默认 2.0）")
    args = parser.parse_args()

    cap = initial_cap(CAMERA_PALLET_ID, "托盘上方广角摄像头",
                      PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS)
    if cap is None:
        raise SystemExit(
            "打不开托盘相机。先确认：\n"
            "  1) pgrep -af main_push_llm   应为空（否则先 sudo systemctl stop wave-vision-llm）\n"
            "  2) ls -l /dev/wave/cam_pallet"
        )

    try:
        deadline = time.time() + args.warmup
        while time.time() < deadline:      # 空读，丢掉未收敛的帧
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            raise SystemExit("读帧失败：相机可能在重连，重跑一次")
    finally:
        cap.release()

    if args.out:
        path = args.out
    else:
        name = time.strftime("%Y%m%d_%H%M%S")
        if args.tag:
            name += "_" + args.tag
        path = os.path.join("capture", "snapshot", f"pallet_{name}.jpg")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if not cv2.imwrite(path, frame):
        raise SystemExit(f"写文件失败: {path}")
    height, width = frame.shape[:2]
    mean = float(frame.mean())
    print(f"已保存: {path}  ({width}x{height}, 亮度均值 {mean:.0f})")
    if mean < 30:
        print("⚠️ 画面很暗：灯没开或镜头被挡")
    if mean > 240:
        print("⚠️ 画面很亮：检查是否过曝（对准了光源）")


if __name__ == "__main__":
    main()
