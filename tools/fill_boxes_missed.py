"""用千问给「当前模型漏检」的真实图补框，并并入已建好的数据集。

为什么需要：570 张实拍里有 74 张当前形状模型检不出（大多是 ③ 仰视被过曝洗白、
或黑球这类低对比件）。这些是真实存在的物体，直接丢掉会让训练集偏向"容易的视角"。

约定：框来自千问，**类别仍取自目录名**（不收模型的类），空样本（tag=parallelepiped）不在此列。

用法（WSL，项目根目录）：
    python tools/fill_boxes_missed.py --list datasets/missed.txt \
        --dataset datasets/shape18_final --workers 4
"""
import argparse
import csv
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import autolabel_llm as A  # noqa: E402  —— 复用它的 request/parse（含 .env 读取）

CLASSES = [
    'ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere',
    'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5',
    'prism5_thinWalled', 'prism6', 'prism6_thinWalled', 'pyramid3', 'pyramid4', 'pyramid5',
]
INDEX = {name: i for i, name in enumerate(CLASSES)}
TAG_FIX = {'parallelepiped': None, 'empty': 'parallelepiped'}
NAME_RE = re.compile(r'^\d{8}_\d{6}_(.+)_p(\d{2})$')


def process_one(item, dataset, val_pose):
    path, tag, pose = item
    cls = TAG_FIX.get(tag, tag)
    if cls is None or cls not in INDEX:
        return (path, 'skip-unknown-tag', 0, '', 0.0)
    try:
        text, size, _usage = A.request(path)
        objects = A.parse(text, size)
    except Exception as error:  # noqa: BLE001
        return (path, 'error:%s' % type(error).__name__, 0, '', 0.0)
    if not objects:
        return (path, 'llm-empty', 0, '', 0.0)
    best = max(objects, key=lambda o: (o['bbox'][2] - o['bbox'][0]) * (o['bbox'][3] - o['bbox'][1]))
    x1, y1, x2, y2 = [max(0.0, min(1.0, float(v))) for v in best['bbox'][:4]]
    if x2 - x1 <= 0.01 or y2 - y1 <= 0.01:
        return (path, 'degenerate-box', len(objects), '', 0.0)
    split = 'val' if pose == val_pose else 'train'
    stem = '%s_%s_%s' % (os.path.basename(os.path.dirname(path)),
                         os.path.splitext(os.path.basename(path))[0], cls)
    shutil.copyfile(path, os.path.join(dataset, 'images', split, stem + '.jpg'))
    with open(os.path.join(dataset, 'labels', split, stem + '.txt'), 'w', encoding='utf-8') as handle_file:
        handle_file.write('%d %.6f %.6f %.6f %.6f\n'
                          % (INDEX[cls], (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1))
    area = (x2 - x1) * (y2 - y1)
    return (path, 'ok', len(objects), cls, area)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--list', default='datasets/missed.txt')
    parser.add_argument('--dataset', default='datasets/shape18_final')
    parser.add_argument('--val-pose', default='06')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()

    items = []
    for line in open(args.list, encoding='utf-8'):
        path = line.strip()
        if not path:
            continue
        match = NAME_RE.match(os.path.basename(os.path.dirname(path)))
        if not match:
            print('跳过（命名不符）:', path)
            continue
        items.append((path, match.group(1), match.group(2)))
    print('待补框:', len(items))

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(lambda it: process_one(it, args.dataset, args.val_pose), items):
            results.append(result)
            print('  %-12s %s' % (result[1], os.path.basename(result[0])))

    ok = sum(1 for r in results if r[1] == 'ok')
    with open(os.path.join(os.path.dirname(args.dataset), 'fill_report.csv'), 'w',
              newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['path', 'status', 'objects', 'class', 'area'])
        writer.writerows(results)
    print('\n成功 %d / %d' % (ok, len(results)))
    for status in sorted({r[1] for r in results}):
        print('  %-24s %d' % (status, sum(1 for r in results if r[1] == status)))


if __name__ == '__main__':
    main()
