"""对「低阈值检出」的图做框复核：模型框与千问粗框做 IoU 交叉验证。

问题背景：conf=0.01 能框住 53/74，但会出假框（实测把白墙框成 cylinder）。
策略：
  * 有千问粗框：取与它 IoU 最大的模型框；IoU >= 0.15 才采信（精确框），否则保留粗框；
  * 没有千问粗框（千问也判空）：无法交叉验证 → 整张丢弃；
  * 两者都没有：丢弃。

用法（WSL，项目根目录）：
    python tools/reselect_boxes.py --dataset datasets/shape18_lc \
        --coarse datasets/shape18_final --list datasets/missed.txt
"""
import argparse
import os
import re
import sys

import numpy as np
from ultralytics import YOLO

CLASSES = [
    'ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere',
    'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5',
    'prism5_thinWalled', 'prism6', 'prism6_thinWalled', 'pyramid3', 'pyramid4', 'pyramid5',
]
INDEX = {name: i for i, name in enumerate(CLASSES)}
TAG_FIX = {'parallelepiped': None, 'empty': 'parallelepiped'}
NAME_RE = re.compile(r'^\d{8}_\d{6}_(.+)_p(\d{2})$')


def touches_corner(box):
    """框同时贴住相邻两条边 → 典型「把挡板/墙当物体」的假框。"""
    x1, y1, x2, y2 = box
    return ((x1 < 0.01 and y1 < 0.01) or (x1 < 0.01 and y2 > 0.99)
            or (x2 > 0.99 and y1 < 0.01) or (x2 > 0.99 and y2 > 0.99))


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter + 1e-9)


def read_label(path):
    """读 label 的归一化 cx cy w h → xyxy。"""
    line = open(path, encoding='utf-8').read().strip().split()
    if len(line) != 5:
        return None, None
    cx, cy, bw, bh = map(float, line[1:5])
    return int(line[0]), (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', default='datasets/shape18_lc')
    parser.add_argument('--coarse', default='datasets/shape18_final')
    parser.add_argument('--list', default='datasets/missed.txt')
    parser.add_argument('--model', default='models_try/shape_best.pt')
    parser.add_argument('--conf', type=float, default=0.01)
    parser.add_argument('--iou-min', type=float, default=0.15)
    args = parser.parse_args()

    model = YOLO(args.model)
    stats = {'refined': 0, 'kept-coarse': 0, 'dropped': 0, 'skip': 0}

    for raw in open(args.list, encoding='utf-8'):
        path = raw.strip()
        if not path:
            continue
        directory = os.path.dirname(path)
        match = NAME_RE.match(os.path.basename(directory))
        if not match:
            stats['skip'] += 1
            continue
        tag, pose = match.group(1), match.group(2)
        cls = TAG_FIX.get(tag, tag)
        stem = '%s_%s_%s' % (os.path.basename(directory), os.path.splitext(os.path.basename(path))[0], cls or 'empty')
        dataset_label = os.path.join(args.dataset, 'labels', 'train', stem + '.txt')
        if not os.path.exists(dataset_label):
            dataset_label = os.path.join(args.dataset, 'labels', 'val', stem + '.txt')
        if not os.path.exists(dataset_label):
            stats['skip'] += 1
            continue

        coarse = None
        for split in ('train', 'val'):
            candidate = os.path.join(args.coarse, 'labels', split, stem + '.txt')
            if os.path.exists(candidate):
                _cid, coarse = read_label(candidate)
                break

        result = model.predict(path, imgsz=640, conf=args.conf, verbose=False)[0]
        boxes = result.boxes
        candidates = []
        if boxes is not None and len(boxes):
            height, width = 720, 1280
            for xyxy in boxes.xyxy.tolist():
                candidates.append((xyxy[0] / width, xyxy[1] / height,
                                   xyxy[2] / width, xyxy[3] / height))

        if not candidates:
            stats['dropped'] += 1
            os.remove(dataset_label)
            image_path = dataset_label.replace('labels', 'images').replace('.txt', '.jpg')
            if os.path.exists(image_path):
                os.remove(image_path)
            continue

        if coarse is None:
            stats['dropped'] += 1
            os.remove(dataset_label)
            image_path = dataset_label.replace('labels', 'images').replace('.txt', '.jpg')
            if os.path.exists(image_path):
                os.remove(image_path)
            continue

        # 优先：与千问框 IoU 足够、且不贴对角
        ranked = sorted(candidates, key=lambda box: iou(box, coarse), reverse=True)
        chosen = None
        for box in ranked:
            if iou(box, coarse) >= args.iou_min and not touches_corner(box):
                chosen = box
                stats['refined'] += 1
                break
        if chosen is None:
            # 退而求其次：自身不贴对角、面积合理的候选框
            sane = [box for box in candidates
                    if not touches_corner(box) and 0.01 <= (box[2] - box[0]) * (box[3] - box[1]) <= 0.45]
            if sane:
                chosen = max(sane, key=lambda box: iou(box, coarse))
                stats['refined'] += 1
            elif not touches_corner(coarse):
                chosen = coarse
                stats['kept-coarse'] += 1

        if chosen is None:
            stats['dropped'] += 1
            os.remove(dataset_label)
            image_path = dataset_label.replace('labels', 'images').replace('.txt', '.jpg')
            if os.path.exists(image_path):
                os.remove(image_path)
            continue
        box = chosen

        x1, y1, x2, y2 = box
        with open(dataset_label, 'w', encoding='utf-8') as handle:
            handle.write('%d %.6f %.6f %.6f %.6f\n'
                         % (INDEX[cls], (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1))

    print('复核结果:', stats)


if __name__ == '__main__':
    main()
