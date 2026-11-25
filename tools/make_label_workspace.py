"""把「自动标注不可信」的 170 张整理成可直接人工标注的工作区。

输出结构（labelImg / Label Studio 都能直接用）：
    datasets/to_label/
      images/<类名>__<原目录>__<视角>.jpg
      labels/<同名>.txt            预标注框（YOLO 格式，0~1）
      classes.txt                  18 类，顺序与 data.yaml 一致
      review.csv                  每张的来源与可信度，按优先级排序
      README.md                    怎么标、标什么

预标注来源（可信度递减）：
    model@0.05  模型低阈值框（位置通常准，类不可信 → 类已按目录名写好）
    llm         千问补框（约 20% 偏松/带背景，需要重点看）
    none        无框，需从零画
"""
import csv
import glob
import os
import re
import shutil

ROOT = '/mnt/d/GongChuang_Advanced'
SRC_LIST = os.path.join(ROOT, 'datasets/need_label.txt')
OUT = os.path.join(ROOT, 'datasets/to_label')
C005 = os.path.join(ROOT, 'datasets/shape18_c005')      # model@0.05
LLM = os.path.join(ROOT, 'datasets/shape18_final')      # 千问补框

CLASSES = ['ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere',
           'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5',
           'prism5_thinWalled', 'prism6', 'prism6_thinWalled', 'pyramid3', 'pyramid4', 'pyramid5']
INDEX = {n: i for i, n in enumerate(CLASSES)}
TAG_FIX = {'parallelepiped': None, 'empty': 'parallelepiped'}
NAME_RE = re.compile(r'^\d{8}_\d{6}_(.+)_p(\d{2})$')


def to_wsl(path):
    """need_label.txt 里存的是 Windows 路径（可能是 D:/ 或 D:\\），转成 WSL 可读。"""
    path = path.strip().lstrip('\ufeff')
    match = re.match(r'^([A-Za-z]):[\\/](.*)$', path)
    if match:
        return '/mnt/%s/%s' % (match.group(1).lower(), match.group(2).replace('\\', '/'))
    return path.replace('\\', '/')


def main():
    for sub in ('images', 'labels'):
        os.makedirs(os.path.join(OUT, sub), exist_ok=True)

    rows = []
    for raw in open(SRC_LIST, encoding='utf-8-sig'):
        path = to_wsl(raw)
        if not path:
            continue
        directory = os.path.dirname(path)
        match = NAME_RE.match(os.path.basename(directory))
        if not match:
            continue
        tag = match.group(1)
        cls = TAG_FIX.get(tag, tag)
        if cls not in INDEX:
            print('未知类别，跳过:', tag)
            continue

        view = os.path.splitext(os.path.basename(path))[0]
        stem = '%s__%s__%s' % (cls, os.path.basename(directory), view)
        target_image = os.path.join(OUT, 'images', stem + '.jpg')
        shutil.copyfile(path, target_image)

        # 找预标注：先 model@0.05，再千问
        source, box = 'none', None
        for base, label in ((C005, 'model@0.05'), (LLM, 'llm')):
            for split in ('train', 'val'):
                candidate = os.path.join(base, 'labels', split, '%s_%s_%s.txt'
                                         % (os.path.basename(directory), view, cls or 'empty'))
                if os.path.exists(candidate):
                    text = open(candidate, encoding='utf-8').read().strip()
                    if text:
                        box = text.split()
                        source = label
                    break
            if box:
                break

        label_path = os.path.join(OUT, 'labels', stem + '.txt')
        if box:
            with open(label_path, 'w', encoding='utf-8') as handle:
                handle.write('%d %s\n' % (INDEX[cls], ' '.join(box[1:5])))
        else:
            open(label_path, 'w', encoding='utf-8').close()

        rows.append({'image': stem + '.jpg', 'class': cls, 'source': source,
                     'has_box': 'yes' if box else 'no'})

    # 千问框优先复核、无框的最后处理
    order = {'llm': 0, 'model@0.05': 1, 'none': 2}
    rows.sort(key=lambda r: (order[r['source']], r['class']))

    with open(os.path.join(OUT, 'review.csv'), 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['image', 'class', 'source', 'has_box'])
        writer.writeheader()
        writer.writerows(rows)

    with open(os.path.join(OUT, 'classes.txt'), 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(CLASSES) + '\n')

    stats = {}
    for row in rows:
        stats[row['source']] = stats.get(row['source'], 0) + 1
    print('总计 %d 张' % len(rows))
    for key in ('llm', 'model@0.05', 'none'):
        print('  %-11s %d' % (key, stats.get(key, 0)))
    print('输出:', OUT)


if __name__ == '__main__':
    main()
