#!/usr/bin/env python3
"""比对「模型真实类别数」与「代码里的类别列表」并报警。

背景：YOLO 的类别 ID 来自模型输出（np.argmax 取通道），而类别名来自 shared/const.py
的列表。只要列表比模型少一类或顺序不同，从缺的那类开始名称会整体错位，而且**不会报错**
（静默错判）。data.yaml 也可能过期，所以本脚本以 .rknn 自身为准。

板上用法（项目根目录）：
    python3 tools/probe_model_classes.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from shared.const import (  # noqa: E402
    PALLET_MODEL_PATH, PALLET_COLOR_CLASSES,
    DETECT_AREA_COLOR_MODEL_PATH, DETECT_AREA_COLOR_CLASSES,
    DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES,
    DETECT_AREA_LABEL_MODEL_PATH, LABEL_CLASSES,
    PROJECT_ROOT,
)
from yolo.inference_utils import IMG_SIZE, RKNN_model_container, infer_class_count  # noqa: E402


def yaml_names(path):
    """从 data.yaml 里取 names 列表（不依赖 pyyaml）。"""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return None, None
    nc_match = re.search(r"^nc:\s*(\d+)", text, re.M)
    names_match = re.search(r"^names:\s*(\[.*\])\s*$", text, re.M)
    nc = int(nc_match.group(1)) if nc_match else None
    names = None
    if names_match:
        try:
            names = eval(names_match.group(1))  # noqa: S307 —— 本地仓库文件，仅解析字面量
        except Exception:
            names = None
    return nc, names


def main():
    cases = (
        ("大托盘颜色", PALLET_MODEL_PATH, PALLET_COLOR_CLASSES, "color_large_pallet_data.yaml"),
        ("识别区颜色", DETECT_AREA_COLOR_MODEL_PATH, DETECT_AREA_COLOR_CLASSES, "color_detect_area_data.yaml"),
        ("识别区形状", DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES, "shape_detect_area_data.yaml"),
        ("识别区贴纸", DETECT_AREA_LABEL_MODEL_PATH, LABEL_CLASSES, "label_detect_area_data.yaml"),
    )
    dummy = np.zeros((1, IMG_SIZE[1], IMG_SIZE[0], 3), dtype=np.uint8)
    failed = 0

    for title, model_path, classes, yaml_file in cases:
        print("=" * 76)
        print(f"【{title}】{model_path}")
        full = model_path if os.path.isabs(model_path) else os.path.join(PROJECT_ROOT, model_path)
        if not os.path.isfile(full):
            print(f"  !! 找不到模型文件：{full}")
            failed += 1
            continue

        yaml_nc, names = yaml_names(os.path.join(PROJECT_ROOT, "models", yaml_file))
        print(f"  data.yaml : nc={yaml_nc}  names={len(names) if names else '读取失败'}")
        if names and yaml_nc is not None and yaml_nc != len(names):
            print(f"  !! yaml 自身就不一致：nc={yaml_nc} 但只列了 {len(names)} 个名字")

        container = RKNN_model_container(full)
        try:
            outputs = container.run([dummy])
        finally:
            container.release()

        shapes = [tuple(o.shape) for o in outputs]
        print(f"  模型输出 {len(outputs)} 个张量：")
        for index, shape in enumerate(shapes):
            print(f"    out[{index}] {shape}")
        model_nc = infer_class_count(outputs)
        print(f"  → 模型真实类别数 = {model_nc}")
        print(f"  → 代码列表 len = {len(classes)}")
        if model_nc is None:
            print("  ?? 无法从输出形状推断类别数，请人工看上面的形状")
            failed += 1
        elif model_nc == len(classes):
            print("  OK 一致")
        else:
            failed += 1
            print(f"  !! 不一致：模型 {model_nc} 类 vs 代码 {len(classes)} 个名字")
            print("     后果：类别 ID 与名称错位（从缺的那类开始之后全错），且不会报错。")
            print(f"     处理：按训练用的 data.yaml 补齐并保持顺序，改 shared/const.py 里对应的列表。")

    print("=" * 76)
    print(f"共 {failed} 处需要处理" if failed else "全部一致")


if __name__ == "__main__":
    main()
