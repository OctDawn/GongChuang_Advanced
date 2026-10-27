"""开拍前自检：拿一张托盘图跑一次线上批量识别，打印类别与取件顺序。

用法（板上，项目根目录）：
    python tools/online_preview.py capture/images/<run>/pallet_original/stage1_r001.jpg
    python tools/online_preview.py --latest          # 用 capture/ 下最新的托盘原图

只读图片、不碰相机/串口/显示，主程序在跑也能用。
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()   # 必须在 llm.qw 之前：key/base_url/参数都是 import 期读的

import cv2  # noqa: E402

from shared.common import crop_pallet_llm  # noqa: E402
from shared.const import ONLINE_BATCH_TIMEOUT  # noqa: E402
from llm.online_batch import classify_pallet, class_names  # noqa: E402


def latest_pallet_image():
    pattern = os.path.join("capture", "images", "*", "pallet_original", "*.jpg")
    candidates = sorted(glob.glob(pattern))
    return candidates[-1] if candidates else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?", help="托盘俯视原图（1280×960）")
    parser.add_argument("--latest", action="store_true", help="自动取 capture/ 下最新一张")
    args = parser.parse_args()

    path = latest_pallet_image() if args.latest else args.image
    if not path or not os.path.isfile(path):
        raise SystemExit("没找到托盘图：给一个路径，或加 --latest")

    frame = cv2.imread(path)
    if frame is None or frame.size == 0:
        raise SystemExit(f"读不了图片: {path}")
    tray = crop_pallet_llm(frame)
    print(f"图片: {path}  {frame.shape[1]}x{frame.shape[0]} -> 送模型 {tray.shape[1]}x{tray.shape[0]}")

    objects = classify_pallet(tray, total_timeout=ONLINE_BATCH_TIMEOUT)
    names = class_names()
    print(f"\n共 {len(objects)} 件，按取件顺序（列从右到左，同列上→下）：")
    for index, item in enumerate(objects, start=1):
        cls = item["cls"]
        name = names[cls - 1] if 1 <= cls <= len(names) else "未匹配(回 0)"
        print(f"  {index:02d}. lane={item['lane']}  y={item['y']:.2f}  ->  类别 {cls} {name}")

    counts = [0] * 5
    for item in objects:
        counts[item["lane"] - 1] += 1
    print("\n各栏件数（1=最左）:", counts)
    if len(objects) != 24:
        print(f"⚠️ 预期 24 件，实际 {len(objects)} 件：检查是否漏检/重复计数")


if __name__ == "__main__":
    main()
