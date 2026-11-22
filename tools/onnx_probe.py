"""在 WSL / PC 上用 ultralytics 直接跑 best.pt 或 best.onnx，验证模型本身是否正常。

用途：把「模型问题」和「RKNN 转换 / 解码问题」分开。
在 PC/WSL 上跑对了、板上跑不对 → 问题在转换或解码；
在 PC/WSL 上也不对 → 模型/数据集的问题，别在板上耗。

用法（WSL 里，conda 环境要有 ultralytics）：

    python tools/onnx_probe.py runs/shape/run/weights/best.pt /mnt/c/Users/xxx/view_1.jpg
    python tools/onnx_probe.py runs/shape/run/weights/best.onnx /mnt/c/Users/xxx/view_1.jpg

可选参数：
    --imgsz 640        推理尺寸（默认 640，与训练一致）
    --conf 0.25        置信度阈值
    --topk 5           最多打印几条
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("image")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--topk", type=int, default=5)
    args = parser.parse_args()

    for path in (args.model, args.image):
        if not os.path.isfile(path):
            raise SystemExit(f"找不到文件: {path}")

    try:
        from ultralytics import YOLO
    except ImportError:
        raise SystemExit("缺 ultralytics：conda activate rknn（或 pip install ultralytics）")

    model = YOLO(args.model)
    results = model.predict(args.image, imgsz=args.imgsz, conf=args.conf, verbose=False)

    print(f"模型: {args.model}")
    print(f"图片: {args.image}\n")

    for result in results:
        names = result.names
        # OBB 模型用 result.obb，普通检测用 result.boxes
        source = None
        if getattr(result, "obb", None) is not None and len(result.obb):
            source = ("obb", result.obb)
        elif getattr(result, "boxes", None) is not None:
            source = ("box", result.boxes)
        if source is None or len(source[1]) == 0:
            print("无检测（conf=%.2f）" % args.conf)
            continue
        kind, items = source
        print("检测到 %d 个目标（%s）：" % (len(items), kind))
        order = sorted(range(len(items)), key=lambda i: -float(items.conf[i]))[: args.topk]
        for index in order:
            class_id = int(items.cls[index])
            print("  %-22s %.3f  %s" % (
                names.get(class_id, class_id), float(items.conf[index]),
                [round(float(v), 1) for v in items.xyxy[index].tolist()]
                if kind == "box" else
                [round(float(v), 1) for v in items.xywhr[index].tolist()],
            ))


if __name__ == "__main__":
    main()
