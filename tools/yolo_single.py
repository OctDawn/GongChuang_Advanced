"""单独测某一个 YOLO 模型：喂一张图，打印检测到的类别/置信度/框。

用途：新模型刚训好时，先确认「能加载 + 类别名对 + 框大致正确」，再谈接入主流程。
不依赖相机、串口、电显，也不需要另外三个模型。

用法（板上，项目根目录）：

    python tools/yolo_single.py --model shape --image 某图.jpg
    python tools/yolo_single.py --model color --image 某图.jpg --topk 3
    python tools/yolo_single.py --model shape --image capture/views/xxx/view_1.jpg

--model 取值与映射：
    shape   识别区形状（SHAPE_CLASSES，16 类）
    color   识别区颜色（DETECT_AREA_COLOR_CLASSES）
    label   识别区贴纸（LABEL_CLASSES）
    pallet  大托盘颜色（PALLET_COLOR_CLASSES）

输出里 score 低于 ~0.3 的框通常可以忽略；同一个物体会出现多个候选框，看分数最高的那个。
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

from shared.const import (  # noqa: E402
    PALLET_MODEL_PATH, PALLET_COLOR_CLASSES,
    DETECT_AREA_COLOR_MODEL_PATH, DETECT_AREA_COLOR_CLASSES,
    DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES,
    DETECT_AREA_LABEL_MODEL_PATH, LABEL_CLASSES,
)
from yolo.inference_utils import DetectRunner  # noqa: E402

MODELS = {
    "shape": (DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES),
    "color": (DETECT_AREA_COLOR_MODEL_PATH, DETECT_AREA_COLOR_CLASSES),
    "label": (DETECT_AREA_LABEL_MODEL_PATH, LABEL_CLASSES),
    "pallet": (PALLET_MODEL_PATH, PALLET_COLOR_CLASSES),
}


def _imread(path):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--model-path", default="",
                        help="覆盖模型路径（用于对比 .bak 旧模型）")
    parser.add_argument("--topk", type=int, default=5, help="打印前几个候选")
    args = parser.parse_args()

    model_path, classes = MODELS[args.model]
    if args.model_path:
        model_path = args.model_path
    image = _imread(args.image)
    if image is None:
        raise SystemExit("读不出图片: %s" % args.image)

    print("模型: %s" % model_path)
    print("类别数: 模型列表 %d 类" % len(classes))
    print("图片: %s  %dx%d\n" % (args.image, image.shape[1], image.shape[0]))

    with ExitStack() as stack:
        runner = DetectRunner(model_path, classes)
        stack.callback(runner.release)
        points, ids, scores = runner.infer(image, return_scores=True)

    if not ids:
        print("没有检测到任何目标（这张图对该模型可能太小/太暗/类别不符）")
        return

    order = sorted(range(len(ids)), key=lambda i: -float(scores[i]))[: max(1, args.topk)]
    print("%-24s %8s  %s" % ("类别", "score", "框 (x1,y1,x2,y2)"))
    for index in order:
        class_id = int(ids[index])
        name = classes[class_id] if 0 <= class_id < len(classes) else "越界#%d" % class_id
        box = [int(round(float(value)))
               for value in np.asarray(points[index]).reshape(-1)[:4]]
        print("%-24s %8.3f  %s" % (name, float(scores[index]), box))
    print("\n共 %d 个候选；分数低于 0.3 的一般可忽略。" % len(ids))


if __name__ == "__main__":
    main()
