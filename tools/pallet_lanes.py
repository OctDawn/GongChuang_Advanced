"""核对托盘「5 栏」网格是否还对准物理隔板，并打印 YOLO 查栏的落栏结果。

为什么需要：栏网格是按画面比例写死的（COLUMN_X_MIN/MAX），托盘/相机一旦挪动，
等分线会和隔板错开几十像素，贴着栏边放的货就会被算到隔壁栏——这种错位不会报错，
只会让电控推错栏。本工具把「实测隔板位置」和「配置的网格」画在同一张图上对比。

用法（板上，项目根目录）：

    python tools/pallet_lanes.py                    # 用最新一张 pallet_original
    python tools/pallet_lanes.py --image 某图.jpg
    python tools/pallet_lanes.py --image 某图.jpg --out /tmp/x.jpg

注意：**校准用空盘图**。盘里有货时，隔板被挡住，边缘检峰会锁到货的轮廓上
（实测最后一道会偏 40px 以上），这时只看网格和检出框，不要看「偏差」那几行。

输出：
    - 托盘裁剪范围（红框）、栏网格（黄线+序号）、检测框（蓝框，带类别/置信度）
    - 每道隔板的实测位置、与网格边界的偏差
    - 每个检出框落在第几栏、5 栏占用

不需要相机，不依赖服务状态。
"""
import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()

import cv2      # noqa: E402
import numpy as np  # noqa: E402

from shared.common import column_bounds_px, column_occupancy, tray_bounds_px  # noqa: E402
from shared.const import (  # noqa: E402
    COLUMN_COUNT, COLUMN_X_MAX, COLUMN_X_MIN, LASER_SENSOR_ON_LEFT,
    PALLET_COLOR_CLASSES, PALLET_MODEL_PATH,
)


def _imread(path):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def find_dividers(img, x0, x1, y0, y1):
    """在托盘内部找竖向隔板：列方向 Sobel 响应的局部峰值。"""
    gray = cv2.cvtColor(img[y0:y1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    profile = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)).mean(axis=0)
    smooth = np.convolve(profile, np.ones(9) / 9.0, mode="same")
    lo, hi = x0 + 40, x1 - 40
    threshold = np.percentile(smooth[lo:hi], 90)
    peaks = []
    for x in range(lo, hi):
        if smooth[x] == smooth[max(0, x - 25):x + 26].max() and smooth[x] > threshold:
            if peaks and x - peaks[-1][0] < 60:
                if smooth[x] > peaks[-1][1]:
                    peaks[-1] = (x, float(smooth[x]))
                continue
            peaks.append((x, float(smooth[x])))
    return [x for x, _ in peaks]


def main():
    parser = argparse.ArgumentParser(description="核对托盘栏网格与物理隔板")
    parser.add_argument("--image", default="", help="托盘原图；默认取最新一张 pallet_original")
    parser.add_argument("--out", default="/tmp/pallet_lanes.jpg", help="标注图输出路径")
    parser.add_argument("--no-model", action="store_true", help="只画网格，不加载托盘模型")
    args = parser.parse_args()

    path = args.image
    if not path:
        found = sorted(glob.glob("capture/images/*/pallet_original/*.jpg"))
        if not found:
            raise SystemExit("没有 pallet_original 图片，用 --image 指定一张")
        path = found[-1]
    img = _imread(path)
    if img is None:
        raise SystemExit("读不出图片: %s" % path)

    height, width = img.shape[:2]
    tx0, tx1, ty0, ty1 = tray_bounds_px(img.shape)
    cx0, cx1, cy0, cy1 = column_bounds_px(img.shape)
    print("图片      : %s (%dx%d)" % (path, width, height))
    print("托盘裁剪框: x %.0f~%.0f  y %.0f~%.0f" % (tx0, tx1, ty0, ty1))
    print("栏网格    : x %.0f~%.0f  y %.0f~%.0f  （COLUMN_X_MIN=%.3f MAX=%.3f）"
          % (cx0, cx1, cy0, cy1, COLUMN_X_MIN, COLUMN_X_MAX))
    edges = [cx0 + (cx1 - cx0) * k / COLUMN_COUNT for k in range(COLUMN_COUNT + 1)]
    print("网格边界  : " + " / ".join("%.0f" % value for value in edges))

    dividers = find_dividers(img, int(cx0), int(cx1), int(cy0), int(cy1))
    if dividers:
        print("实测隔板  : " + " / ".join(str(x) for x in dividers))
        for index, x in enumerate(dividers):
            # dividers[0] 对应第 1-2 栏之间的边界，即 edges[1]
            if index + 1 < len(edges):
                print("  隔板 x=%4d ↔ 网格 %.0f  偏差 %+.0f px" % (x, edges[index + 1], x - edges[index + 1]))
        print("  （若盘里有货，隔板会被挡住而锁到货的边缘，校准请用空盘图）")
    else:
        print("实测隔板  : 未检出（空盘反光弱或隔板被货挡住）")

    out = img.copy()
    cv2.rectangle(out, (int(tx0), int(ty0)), (int(tx1), int(ty1)), (0, 0, 255), 2)
    for index, x in enumerate(edges):
        cv2.line(out, (int(x), int(cy0)), (int(x), int(cy1)), (0, 255, 255), 2)
        if index < COLUMN_COUNT:
            cv2.putText(out, str(index + 1), (int(x) + 10, int(cy0) + 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 255, 255), 4)

    if not args.no_model:
        from yolo.inference_utils import DetectRunner

        runner = DetectRunner(PALLET_MODEL_PATH, PALLET_COLOR_CLASSES)
        points, ids, scores = runner.infer(img, return_scores=True)
        print("\n检出 %d 个框：" % len(points))
        for box, sid, score in zip(points, ids, scores):
            center_x = float(np.mean(box[:, 0]))
            center_y = float(np.mean(box[:, 1]))
            column = int((center_x - cx0) // ((cx1 - cx0) / COLUMN_COUNT)) + 1
            if not (cx0 <= center_x <= cx1 and cy0 <= center_y <= cy1):
                column = 0
            print("  %-12s %5.3f  center=(%4.0f,%4.0f)  → 第 %s 栏"
                  % (PALLET_COLOR_CLASSES[int(sid)], float(score), center_x, center_y,
                     column if column else "（盘外）"))
            cv2.rectangle(out, (int(box[:, 0].min()), int(box[:, 1].min())),
                          (int(box[:, 0].max()), int(box[:, 1].max())), (255, 0, 0), 2)
        occupancy = column_occupancy(points, img.shape)
        print("列占用    : " + "".join("1" if value else "0" for value in occupancy)
              + "  " + str(occupancy))
        print("回给电控  : " + " ".join("1" if v else "0" for v in occupancy)
              + "（LASER_SENSOR_ON_LEFT=%s，第 1 栏在画面最%s）"
              % (LASER_SENSOR_ON_LEFT, "左" if LASER_SENSOR_ON_LEFT else "右"))

    cv2.imwrite(args.out, out)
    print("\n标注图    : %s" % args.out)


if __name__ == "__main__":
    main()
