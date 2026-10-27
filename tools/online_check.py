"""线上批量识别的软件端联调：拍一张 → 整盘识别 → 打印取件顺序和类别。

不需要电控、不需要显示、不碰串口。适合"摆好货，看看它会怎么分"。

用法（板上，项目根目录；先停主程序释放相机）：
    sudo systemctl stop wave-vision-llm
    conda activate llm
    python tools/online_check.py
    python tools/online_check.py --tag 第一版 --warmup 2.5
    python tools/online_check.py --no-save          # 只看结果，不落盘

输出目录 capture/online_check/<时间戳>_<tag>/：
    frame.jpg       相机原图（1280x960）
    tray.jpg        实际送模型的托盘图
    annotated.jpg   叠加了栏线 + 每件的序号/类别，用来核对顺序
    item_01_lane5_y0.12_cls3.jpg ...   每件的缩略图
    result.json     原始识别结果（lane/y/cls）
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()      # 必须在 llm.qw 之前：key / base_url / 参数都是 import 期读的

import cv2  # noqa: E402

from shared.common import crop_pallet_llm, initial_cap  # noqa: E402
from shared.const import (  # noqa: E402
    CAMERA_PALLET_ID, PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS,
    COLUMN_COUNT, ONLINE_BATCH_TIMEOUT,
)
from llm.online_batch import classify_pallet, class_names, assign_box_numbers  # noqa: E402


def grab_pallet(warmup):
    cap = initial_cap(CAMERA_PALLET_ID, "托盘上方广角摄像头",
                      PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS)
    if cap is None:
        raise SystemExit("打不开托盘相机：先 sudo systemctl stop wave-vision-llm，并确认 "
                         "/dev/wave/cam_pallet 存在")
    try:
        deadline = time.time() + warmup
        while time.time() < deadline:      # 丢掉未收敛的帧
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            raise SystemExit("读帧失败：相机可能在重连，重跑一次")
    finally:
        cap.release()
    return frame


def item_thumb(tray, lane, y):
    height, width = tray.shape[:2]
    x0 = int(width * (lane - 1) / float(COLUMN_COUNT))
    x1 = int(width * lane / float(COLUMN_COUNT))
    center = int(y * height)
    half = max(10, int(height * 0.18))
    y0, y1 = max(0, center - half), min(height, center + half)
    if x1 <= x0 or y1 <= y0:
        return None
    return tray[y0:y1, x0:x1].copy()


def annotate(tray, objects, boxes):
    """画栏线 + 每件的圆心与"序号:盒号"。中文字体 cv2 画不了，所以只用 ASCII。"""
    marked = tray.copy()
    height, width = marked.shape[:2]
    for lane in range(1, COLUMN_COUNT):
        x = int(width * lane / float(COLUMN_COUNT))
        cv2.line(marked, (x, 0), (x, height), (0, 255, 255), 1)
    for index, (item, box) in enumerate(zip(objects, boxes), start=1):
        cx = int(width * (item["lane"] - 0.5) / float(COLUMN_COUNT))
        cy = int(item["y"] * height)
        cv2.circle(marked, (cx, cy), 16, (0, 0, 255), 2)
        cv2.putText(marked, "%d:b%d" % (index, box), (cx - 16, cy - 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
    return marked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="", help="输出目录备注")
    parser.add_argument("--warmup", type=float, default=2.0, help="空读秒数（曝光收敛）")
    parser.add_argument("--timeout", type=float, default=ONLINE_BATCH_TIMEOUT,
                        help="整盘识别时限（秒）")
    parser.add_argument("--no-save", action="store_true", help="只看结果，不写文件")
    args = parser.parse_args()

    frame = grab_pallet(args.warmup)
    tray = crop_pallet_llm(frame)
    print("相机原图 %dx%d -> 送模型 %dx%d" % (frame.shape[1], frame.shape[0],
                                              tray.shape[1], tray.shape[0]))

    started = time.time()
    objects = classify_pallet(tray, total_timeout=args.timeout)
    seconds = time.time() - started

    names = class_names()
    assignments, mapping = assign_box_numbers(objects, names)
    boxes = [box for box, _ in assignments]
    print("\n共 %d 件，耗时 %.1f 秒" % (len(objects), seconds))
    print("  #   lane   y      类别  盒号  名称")
    for index, (item, (box, name)) in enumerate(zip(objects, assignments), start=1):
        print("  %-3d %-5d  %-5.2f  %-4d  %-4d  %s" % (index, item["lane"], item["y"],
                                                      item["cls"], box, name))

    counts = [0] * COLUMN_COUNT
    for item in objects:
        counts[item["lane"] - 1] += 1
    occupancy = "".join("1" if count else "0" for count in counts)
    print("\n取件顺序（盒号）:", boxes)
    print("盒号 = 首次出现顺序:",
          {names[cls - 1]: box for cls, box in sorted(mapping.items(), key=lambda kv: kv[1])})
    print("各栏件数（1=最左）:", counts)
    print("查栏会回:", occupancy)

    if len(objects) != 24:
        print("⚠️ 预期 24 件，实际 %d 件：检查漏检/重复计数" % len(objects))
    missing = [i for i, box in enumerate(boxes, start=1) if box == 0]
    if missing:
        print("⚠️ 第 %s 件没匹配到类别（或类别已满），会回 0" % missing)

    if args.no_save:
        return
    stamp = time.strftime("%Y%m%d_%H%M%S") + ("_" + args.tag if args.tag else "")
    out_dir = os.path.join("capture", "online_check", stamp)
    os.makedirs(out_dir, exist_ok=True)
    cv2.imwrite(os.path.join(out_dir, "frame.jpg"), frame)
    cv2.imwrite(os.path.join(out_dir, "tray.jpg"), tray)
    cv2.imwrite(os.path.join(out_dir, "annotated.jpg"), annotate(tray, objects, boxes))
    for index, item in enumerate(objects, start=1):
        thumb = item_thumb(tray, item["lane"], item["y"])
        if thumb is not None:
            cv2.imwrite(os.path.join(out_dir, "item_%02d_lane%d_y%.2f_cls%d.jpg"
                                     % (index, item["lane"], item["y"], item["cls"])), thumb)
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as stream:
        json.dump({"seconds": round(seconds, 2), "counts": counts,
                   "objects": objects, "names": names}, stream, ensure_ascii=False, indent=2)
    print("\n已保存:", out_dir)
    print("核对用: annotated.jpg（栏线 + 序号:类别）")


if __name__ == "__main__":
    main()
