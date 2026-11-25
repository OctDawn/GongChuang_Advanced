"""合并「自动框（400）」与「人工标注（170）」，得到全部可信的真实数据。

做法：直接对两个数据集的 (目录, 视角) 取并集，人工优先；不再回源目录匹配。
按姿态切分：最后一个姿态 p06 进 val，其余进 train（与之前口径一致）。

用法：
    python tools/merge_human_labels.py
"""
import glob
import os
import re
import shutil

ROOT = '/mnt/d/GongChuang_Advanced'
AUTO = os.path.join(ROOT, 'datasets/shape18_real')
HUMAN = os.path.join(ROOT, 'datasets/to_label')
OUT = os.path.join(ROOT, 'datasets/shape18_all')

AUTO_RE = re.compile(r'^(?P<dir>.+_p(?P<pose>\d{2}))_(?P<view>view_\d+)_(?P<cls>[A-Za-z0-9_]+)$')
CLASSES = ['ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere',
           'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5',
           'prism5_thinWalled', 'prism6', 'prism6_thinWalled', 'pyramid3', 'pyramid4', 'pyramid5']


def main():
    for split in ('train', 'val'):
        for sub in ('images', 'labels'):
            os.makedirs(os.path.join(OUT, sub, split), exist_ok=True)

    entries = {}

    # 1) 自动框（400 张）
    for split in ('train', 'val'):
        for label in glob.glob(os.path.join(AUTO, 'labels', split, '*.txt')):
            stem = os.path.splitext(os.path.basename(label))[0]
            match = AUTO_RE.match(stem)
            if not match:
                continue
            directory = match.group('dir')
            view = match.group('view')
            image = os.path.join(AUTO, 'images', split, stem + '.jpg')
            if not os.path.exists(image):
                continue
            entries[(directory, view)] = (image,
                                          open(label, encoding='utf-8').read().strip(),
                                          match.group('pose'))

    # 2) 人工标注（170 张）覆盖同名键
    human_count = 0
    for label in glob.glob(os.path.join(HUMAN, 'labels', '*.txt')):
        name = os.path.basename(label)
        if name == 'classes.txt':
            continue
        stem = os.path.splitext(name)[0]
        parts = stem.split('__')
        if len(parts) != 3:
            continue
        _cls, directory, view = parts
        image = os.path.join(HUMAN, 'images', stem + '.jpg')
        if not os.path.exists(image):
            continue
        pose_match = re.search(r'_p(\d{2})$', directory)
        pose = pose_match.group(1) if pose_match else '01'
        entries[(directory, view)] = (image, open(label, encoding='utf-8').read().strip(), pose)
        human_count += 1

    empty = 0
    for (directory, view), (image, text, pose) in entries.items():
        split = 'val' if pose == '06' else 'train'
        stem = '%s_%s' % (directory, view)
        shutil.copyfile(image, os.path.join(OUT, 'images', split, stem + '.jpg'))
        with open(os.path.join(OUT, 'labels', split, stem + '.txt'), 'w', encoding='utf-8') as handle:
            if text:
                handle.write(text + '\n')
            else:
                empty += 1

    print('人工覆盖 %d 张，合计 %d 张，空标签 %d' % (human_count, len(entries), empty))
    for split in ('train', 'val'):
        print('  %s: %d' % (split, len(glob.glob(os.path.join(OUT, 'images', split, '*.jpg')))))

    with open(os.path.join(OUT, 'data.yaml'), 'w', encoding='utf-8') as handle:
        handle.write('path: %s\ntrain: images/train\nval: images/val\nnames:\n' % OUT)
        for index, name in enumerate(CLASSES):
            handle.write('  %d: %s\n' % (index, name))


if __name__ == '__main__':
    main()
