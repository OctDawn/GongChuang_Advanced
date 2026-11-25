"""把「真实域数据」和「yolo_full 主干数据」混成训练集，并做 16→18 的类别重映射。

为什么要重映射：yolo_full 是 16 类（0..13 同 18 类，14=pyramid3、15=pyramid4），
而 18 类里 14=prism6_thinWalled、15=pyramid3、16=pyramid4。照搬索引会让棱锥标签整体错位。

用法（WSL，项目根目录）：
    # B 线：真实数据 3 倍重复 + 主干 1,100 张（val 只用真实图，反映现场）
    python tools/mix_backbone.py --real datasets/shape18_final --out datasets/shape18_B \
        --repeat-real 3 --backbone 1100 --augment-real 0

    # A 线：真实数据离线增强（翻转+亮度）、主干 1,700 张
    python tools/mix_backbone.py --real datasets/shape18_final --out datasets/shape18_A \
        --repeat-real 1 --backbone 1700 --augment-real 2
"""
import argparse
import glob
import os
import random
import shutil

import cv2
import numpy as np

CLASSES = [
    'ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere',
    'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5',
    'prism5_thinWalled', 'prism6', 'prism6_thinWalled', 'pyramid3', 'pyramid4', 'pyramid5',
]
OLD_TO_NEW = {i: i for i in range(14)}
OLD_TO_NEW[14] = 15   # pyramid3
OLD_TO_NEW[15] = 16   # pyramid4


def copy_pair(image, label, dst, stem):
    shutil.copyfile(image, os.path.join(dst, 'images', 'train', stem + '.jpg'))
    shutil.copyfile(label, os.path.join(dst, 'labels', 'train', stem + '.txt'))


def transforms(image):
    """翻转 + 两种亮度/伽马；框不变（翻转时 x 镜像，由调用方处理标签）。"""
    yield 'flip', cv2.flip(image, 1), True
    yield 'dim', np.clip(image.astype(np.float32) * 0.78, 0, 255).astype(np.uint8), False
    yield 'bright', np.clip(255.0 * (image.astype(np.float32) / 255.0) ** 0.85, 0, 255).astype(np.uint8), False


def flip_label(text):
    out = []
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        cls, cx, cy, bw, bh = int(parts[0]), *map(float, parts[1:])
        out.append('%d %.6f %.6f %.6f %.6f' % (cls, 1.0 - cx, cy, bw, bh))
    return '\n'.join(out) + ('\n' if out else '')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real', default='datasets/shape18_final')
    parser.add_argument('--backbone', default='yolo_full')
    parser.add_argument('--out', default='datasets/shape18_B')
    parser.add_argument('--repeat-real', type=int, default=3)
    parser.add_argument('--augment-real', type=int, default=0, help='每张真实图额外生成几个变体')
    parser.add_argument('--backbone-count', type=int, default=1100)
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()

    random.seed(args.seed)
    for split in ('train', 'val'):
        for sub in ('images', 'labels'):
            os.makedirs(os.path.join(args.out, sub, split), exist_ok=True)

    # 1) 真实数据：train 复制 repeat 次（+可选离线增强），val 原样
    added = 0
    for split in ('train', 'val'):
        images = sorted(glob.glob(os.path.join(args.real, 'images', split, '*.jpg')))
        for image_path in images:
            stem = os.path.splitext(os.path.basename(image_path))[0]
            label_path = os.path.join(args.real, 'labels', split, stem + '.txt')
            if split == 'val':
                copy_pair(image_path, label_path, args.out, stem)
                added += 1
                continue
            for repeat in range(max(1, args.repeat_real)):
                copy_pair(image_path, label_path, args.out, '%s_r%d' % (stem, repeat))
                added += 1
            image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
            text = open(label_path, encoding='utf-8').read()
            for index, (name, variant, mirrored) in enumerate(transforms(image)):
                if index >= args.augment_real:
                    break
                target = '%s_a%s' % (stem, name)
                cv2.imencode('.jpg', variant)[1].tofile(
                    os.path.join(args.out, 'images', 'train', target + '.jpg'))
                with open(os.path.join(args.out, 'labels', 'train', target + '.txt'), 'w',
                          encoding='utf-8') as handle:
                    handle.write(flip_label(text) if mirrored else text)
                added += 1

    # 2) 主干数据（只进 train），类别重映射
    backbone_images = sorted(glob.glob(os.path.join(args.backbone, 'images', 'train', '*')))
    random.shuffle(backbone_images)
    taken = 0
    for image_path in backbone_images:
        if taken >= args.backbone_count:
            break
        stem = os.path.splitext(os.path.basename(image_path))[0]
        label_path = os.path.join(args.backbone, 'labels_shape', 'train', stem + '.txt')
        if not os.path.exists(label_path):
            continue
        lines = []
        for line in open(label_path, encoding='utf-8'):
            parts = line.split()
            if len(parts) != 5:
                continue
            old = int(parts[0])
            if old not in OLD_TO_NEW:
                continue
            lines.append('%d %s' % (OLD_TO_NEW[old], ' '.join(parts[1:])))
        if not lines:
            continue
        target = 'bb_' + stem
        shutil.copyfile(image_path, os.path.join(args.out, 'images', 'train', target + '.jpg'))
        with open(os.path.join(args.out, 'labels', 'train', target + '.txt'), 'w',
                  encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
        taken += 1

    names = '\n'.join('  %d: %s' % (i, n) for i, n in enumerate(CLASSES))
    with open(os.path.join(args.out, 'data.yaml'), 'w', encoding='utf-8') as handle:
        handle.write('path: %s\ntrain: images/train\nval: images/val\nnames:\n%s\n'
                     % (os.path.abspath(args.out), names))

    train_count = len(glob.glob(os.path.join(args.out, 'images', 'train', '*.jpg')))
    val_count = len(glob.glob(os.path.join(args.out, 'images', 'val', '*.jpg')))
    print('真实+增强: %d   主干: %d' % (added, taken))
    print('train=%d  val=%d  → %s' % (train_count, val_count, os.path.abspath(args.out)))


if __name__ == '__main__':
    main()
