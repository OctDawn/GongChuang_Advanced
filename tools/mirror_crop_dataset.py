"""按新的训练口径重建数据集：丢掉指定视角 + 对镜面视角做与 LLM 相同的裁剪。

规则（2026-11-25 用户要求）：
  * 去掉「逆光的仰视图」= view_3（③ 正下方仰视）
  * ⑤ 镜面只取上半部分（与 DETECT_MIRROR_CROP=0,0,1,0.60 一致）：裁 y 方向前 60%，
    标注框同步换算；完全落在裁剪区外的框丢弃

用法（WSL，项目根目录）：
    python tools/mirror_crop_dataset.py --src datasets/shape18_mix \
        --out datasets/shape18_mix2 --drop-view 3 --mirror-crop 0.60
"""
import argparse
import glob
import os
import re
import shutil

import cv2
import numpy as np


def transform_label(text, crop_top):
    out = []
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        cls, cx, cy, bw, bh = int(parts[0]), *map(float, parts[1:])
        y1 = max(0.0, (cy - bh / 2) / crop_top)
        y2 = min(1.0, (cy + bh / 2) / crop_top)
        if y2 - y1 <= 0.002:
            continue
        out.append('%d %.6f %.6f %.6f %.6f' % (cls, cx, (y1 + y2) / 2, bw, y2 - y1))
    return '\n'.join(out) + ('\n' if out else '')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--src', default='datasets/shape18_mix')
    parser.add_argument('--out', default='datasets/shape18_mix2')
    parser.add_argument('--drop-view', type=int, default=3)
    parser.add_argument('--mirror-view', type=int, default=5)
    parser.add_argument('--mirror-crop', type=float, default=0.60)
    args = parser.parse_args()

    dropped = cropped = kept = 0
    for split in ('train', 'val'):
        for sub in ('images', 'labels'):
            os.makedirs(os.path.join(args.out, sub, split), exist_ok=True)
        for image_path in sorted(glob.glob(os.path.join(args.src, 'images', split, '*.jpg'))
                                 + glob.glob(os.path.join(args.src, 'images', split, '*.png'))):
            stem = os.path.splitext(os.path.basename(image_path))[0]
            label_path = os.path.join(args.src, 'labels', split, stem + '.txt')
            if not os.path.exists(label_path):
                continue

            if re.search(r'_view_%d(?!\d)' % args.drop_view, stem):
                dropped += 1
                continue

            if re.search(r'_view_%d(?!\d)' % args.mirror_view, stem):
                image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    continue
                height = image.shape[0]
                crop = image[:int(height * args.mirror_crop)]
                target = os.path.join(args.out, 'images', split, stem + '.jpg')
                cv2.imencode('.jpg', crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])[1].tofile(target)
                text = transform_label(open(label_path, encoding='utf-8').read(), args.mirror_crop)
                with open(os.path.join(args.out, 'labels', split, stem + '.txt'), 'w',
                          encoding='utf-8') as handle:
                    handle.write(text)
                cropped += 1
                continue

            shutil.copyfile(image_path, os.path.join(args.out, 'images', split,
                                                     os.path.basename(image_path)))
            shutil.copyfile(label_path, os.path.join(args.out, 'labels', split, stem + '.txt'))
            kept += 1

    shutil.copyfile(os.path.join(args.src, 'data.yaml'), os.path.join(args.out, 'data.yaml'))
    print('原样保留 %d  镜面裁剪 %d  丢弃(视角 %d) %d' % (kept, cropped, args.drop_view, dropped))
    for split in ('train', 'val'):
        count = len(glob.glob(os.path.join(args.out, 'images', split, '*')))
        print('  %s: %d' % (split, count))


if __name__ == '__main__':
    main()
