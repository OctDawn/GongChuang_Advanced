"""从 capture/train_YYYYMMDD 的「<类名>_pNN」目录构建 18 类形状数据集。

关键约定（2026-11-24 实拍）：
  * tag = paralelepiped 的目录其实是**空样本** → 写空标签（负样本）
  * tag = empty 的目录其实是**平行六面体** → 按 paralelepiped 类标
  * 类别一律取目录名，不取模型输出（模型的类错不能教给新模型）
  * 框由当前 shape_best.pt 给出（只取框，不取类）；没有检出的**丢弃并计数**

用法（WSL，项目根目录）：
    python tools/build_shape_dataset.py --src capture/train_20261124 \
        --out datasets/shape18_real --val-pose 06
"""
import argparse
import csv
import glob
import os
import re
import shutil
import sys

import cv2
import numpy as np
from ultralytics import YOLO

CLASSES = [
    'ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere',
    'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5',
    'prism5_thinWalled', 'prism6', 'prism6_thinWalled', 'pyramid3', 'pyramid4', 'pyramid5',
]
INDEX = {name: i for i, name in enumerate(CLASSES)}

# tag → 真实类名；None 表示空样本（负样本）
TAG_FIX = {
    'parallelepiped': None,      # 用户实拍：这个名字下面是空台
    'empty': 'parallelepiped',   # 用户实拍：这个名字下面是平行六面体
}

NAME_RE = re.compile(r'^\d{8}_\d{6}_(.+)_p(\d{2})$')


def imread(path):
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--src', default='capture/train_20261124')
    parser.add_argument('--out', default='datasets/shape18_real')
    parser.add_argument('--model', default='models_try/shape_best.pt')
    parser.add_argument('--val-pose', default='06', help='哪个姿态进 val（默认 06）')
    parser.add_argument('--conf', type=float, default=0.25)
    parser.add_argument('--imgsz', type=int, default=640)
    args = parser.parse_args()

    dirs = sorted(glob.glob(os.path.join(args.src, '*')))
    if not dirs:
        raise SystemExit('没有目录: %s' % args.src)

    for split in ('train', 'val'):
        for sub in ('images', 'labels'):
            os.makedirs(os.path.join(args.out, sub, split), exist_ok=True)

    model = YOLO(args.model)
    stats = {}
    rows = []
    missing = 0
    dropped = 0

    for directory in dirs:
        name = os.path.basename(directory)
        match = NAME_RE.match(name)
        if not match:
            print('跳过（不符合 <tag>_pNN 命名）:', name)
            continue
        tag, pose = match.group(1), match.group(2)
        if tag in TAG_FIX:
            cls = TAG_FIX[tag]
        elif tag in INDEX:
            cls = tag
        else:
            print('跳过（未知 tag）:', name)
            continue

        split = 'val' if pose == args.val_pose else 'train'
        images = sorted(glob.glob(os.path.join(directory, 'view_*.jpg')))
        for image_path in images:
            frame = imread(image_path)
            if frame is None:
                missing += 1
                continue
            height, width = frame.shape[:2]
            label = ''
            if cls is not None:
                result = model.predict(frame, imgsz=args.imgsz, conf=args.conf, verbose=False)[0]
                boxes = result.boxes
                if boxes is None or len(boxes) == 0:
                    dropped += 1
                    continue
                scores = boxes.conf.tolist()
                best = max(range(len(scores)), key=lambda i: scores[i])
                x1, y1, x2, y2 = boxes.xyxy[best].tolist()
                cx = ((x1 + x2) / 2) / width
                cy = ((y1 + y2) / 2) / height
                bw = (x2 - x1) / width
                bh = (y2 - y1) / height
                cx, cy = max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy))
                bw, bh = max(0.0, min(1.0, bw)), max(0.0, min(1.0, bh))
                label = '%d %.6f %.6f %.6f %.6f\n' % (INDEX[cls], cx, cy, bw, bh)
                stats.setdefault(cls, {'train': 0, 'val': 0})[split] += 1
            else:
                stats.setdefault('__empty__', {'train': 0, 'val': 0})[split] += 1

            stem = '%s_%s_%s' % (name, os.path.basename(image_path)[:-4], cls or 'empty')
            target_image = os.path.join(args.out, 'images', split, stem + '.jpg')
            target_label = os.path.join(args.out, 'labels', split, stem + '.txt')
            shutil.copyfile(image_path, target_image)
            with open(target_label, 'w', encoding='utf-8') as handle:
                handle.write(label)
            rows.append([stem, tag, cls or 'EMPTY', pose, split])

    names = '\n'.join('  %d: %s' % (i, n) for i, n in enumerate(CLASSES))
    with open(os.path.join(args.out, 'data.yaml'), 'w', encoding='utf-8') as handle:
        handle.write('path: %s\ntrain: images/train\nval: images/val\nnames:\n%s\n'
                     % (os.path.abspath(args.out), names))
    with open(os.path.join(args.out, 'manifest.csv'), 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['stem', 'tag', 'class', 'pose', 'split'])
        writer.writerows(rows)

    print('\n===== 结果 =====')
    print('有效图片: %d   读不出: %d   漏检丢弃: %d' % (len(rows), missing, dropped))
    print('%-22s %6s %6s' % ('类', 'train', 'val'))
    for name in CLASSES + ['__empty__']:
        if name in stats:
            print('%-22s %6d %6d' % (name, stats[name]['train'], stats[name]['val']))
    total_train = sum(v['train'] for v in stats.values())
    total_val = sum(v['val'] for v in stats.values())
    print('合计 train=%d  val=%d' % (total_train, total_val))
    print('输出: %s' % os.path.abspath(args.out))


if __name__ == '__main__':
    main()
