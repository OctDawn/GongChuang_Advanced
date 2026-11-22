"""用形状模型给出的框，做 HSV 颜色判定；顺便打印统计值便于标定。

用法（板上，项目根目录）：

    python tools/color_probe.py 某图.jpg
    python tools/color_probe.py 某图.jpg --box 600,150,1000,600

不给 --box 时用形状模型检测到的最高分框；检测不到就退回画面中心区域。
"""

import argparse
import os
import sys
from contextlib import ExitStack

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()

import cv2      # noqa: E402
import numpy as np  # noqa: E402

from shared.color_hsv import classify_color_center, classify_color_from_bbox  # noqa: E402
from shared.const import DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES  # noqa: E402
from yolo.inference_utils import DetectRunner  # noqa: E402


def _imread(path):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _bbox_from_poly(poly):
    poly = np.asarray(poly, np.float32).reshape(-1, 2)
    return (float(poly[:, 0].min()), float(poly[:, 1].min()),
            float(poly[:, 0].max()), float(poly[:, 1].max()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--box", default="", help="x1,y1,x2,y2；不给则用模型检测框")
    args = parser.parse_args()

    frame = _imread(args.image)
    if frame is None:
        raise SystemExit("读不出图片: %s" % args.image)

    box = None
    label = "-"
    if args.box:
        box = tuple(float(x) for x in args.box.split(","))
    else:
        with ExitStack() as stack:
            runner = DetectRunner(DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES)
            stack.callback(runner.release)
            points, ids, scores = runner.infer(frame, return_scores=True)
        if len(ids):
            best = int(np.argmax(scores))
            box = _bbox_from_poly(points[best])
            label = "%s %.3f" % (SHAPE_CLASSES[int(ids[best])], float(scores[best]))

    if box is None:
        color, stats = classify_color_center(frame)
        print("未检测到目标，按中心区域判色")
    else:
        color, stats = classify_color_from_bbox(frame, box)
        print("框: %s  形状: %s" % ([int(v) for v in box], label))

    print("HSV: h=%(h)s s=%(s)s v=%(v)s 像素=%(pixels)s" % stats if stats else "HSV: 统计失败")
    print("颜色: %s" % (color or "无法判断"))


if __name__ == "__main__":
    main()
