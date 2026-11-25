"""把 18 类数据集重映射为 16 类，用于微调现部署的 16 类模型。

18 类索引:  0..13 相同 | 14=prism6_thinWalled | 15=pyramid3 | 16=pyramid4 | 17=pyramid5
16 类索引:  0..13 相同 | 14=pyramid3 | 15=pyramid4

映射：15→14、16→15；类 14（prism6_thinWalled）与 17（pyramid5）在 16 类里不存在 →
含这些框的图整张丢弃（不能当负样本，因为物体确实存在）。

用法：
    python tools/remap_to16.py --src datasets/shape18_train --out datasets/shape16_train
"""
import argparse
import glob
import os
import shutil

CLASSES16 = ['ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere',
             'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5',
             'prism5_thinWalled', 'prism6', 'pyramid3', 'pyramid4']
DROP = {14, 17}
REMAP = {15: 14, 16: 15}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--src', default='datasets/shape18_train')
    parser.add_argument('--out', default='datasets/shape16_train')
    args = parser.parse_args()

    dropped_images = kept = 0
    for split in ('train', 'val'):
        for sub in ('images', 'labels'):
            os.makedirs(os.path.join(args.out, sub, split), exist_ok=True)
        for label in sorted(glob.glob(os.path.join(args.src, 'labels', split, '*.txt'))):
            stem = os.path.splitext(os.path.basename(label))[0]
            image = os.path.join(args.src, 'images', split, stem + '.jpg')
            if not os.path.exists(image):
                continue
            lines = []
            skip = False
            for line in open(label, encoding='utf-8'):
                parts = line.split()
                if len(parts) != 5:
                    continue
                cls = int(parts[0])
                if cls in DROP:
                    skip = True
                    break
                cls = REMAP.get(cls, cls)
                lines.append('%d %s' % (cls, ' '.join(parts[1:])))
            if skip:
                dropped_images += 1
                continue
            shutil.copyfile(image, os.path.join(args.out, 'images', split, stem + '.jpg'))
            with open(os.path.join(args.out, 'labels', split, stem + '.txt'), 'w', encoding='utf-8') as handle:
                if lines:
                    handle.write('\n'.join(lines) + '\n')
            kept += 1

    with open(os.path.join(args.out, 'data.yaml'), 'w', encoding='utf-8') as handle:
        handle.write('path: %s\ntrain: images/train\nval: images/val\nnames:\n' % os.path.abspath(args.out))
        for index, name in enumerate(CLASSES16):
            handle.write('  %d: %s\n' % (index, name))

    print('保留 %d 张，丢弃(含 prism6_thinWalled/pyramid5) %d 张' % (kept, dropped_images))
    for split in ('train', 'val'):
        print('  %s: %d' % (split, len(glob.glob(os.path.join(args.out, 'images', split, '*.jpg')))))


if __name__ == '__main__':
    main()
