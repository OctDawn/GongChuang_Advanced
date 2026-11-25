"""标注完成后的自检：类别越界、框尺寸异常、贴角假框、缺标签。

用法（Windows 或 WSL 都可以）：
    python tools/check_labels.py --dir datasets/to_label
"""
import argparse
import glob
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', default='datasets/to_label')
    parser.add_argument('--classes', default='', help='classes.txt 路径，默认 <dir>/classes.txt')
    parser.add_argument('--min-area', type=float, default=0.005, help='框最小面积占比')
    parser.add_argument('--max-area', type=float, default=0.90, help='框最大面积占比')
    args = parser.parse_args()

    classes_path = args.classes or os.path.join(args.dir, 'classes.txt')
    classes = [line.strip() for line in open(classes_path, encoding='utf-8') if line.strip()]
    print('类别数:', len(classes))

    images = sorted(glob.glob(os.path.join(args.dir, 'images', '*')))
    problems = []
    empty = 0

    for image in images:
        stem = os.path.splitext(os.path.basename(image))[0]
        label = os.path.join(args.dir, 'labels', stem + '.txt')
        if not os.path.exists(label):
            problems.append((stem, '缺标签文件（labelImg 里可能没保存）'))
            continue
        lines = [l.strip() for l in open(label, encoding='utf-8') if l.strip()]
        if not lines:
            empty += 1
            continue
        for line in lines:
            parts = line.split()
            if len(parts) != 5:
                problems.append((stem, '格式错误: %r' % line[:40]))
                continue
            cls = int(parts[0])
            cx, cy, w, h = map(float, parts[1:])
            area = w * h
            if cls < 0 or cls >= len(classes):
                problems.append((stem, '类别越界: %d' % cls))
            if area < args.min_area:
                problems.append((stem, '框太小: %.4f' % area))
            if area > args.max_area:
                problems.append((stem, '框太大: %.4f' % area))
            x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
            if (x1 < 0.01 and y1 < 0.01) or (x1 < 0.01 and y2 > 0.99) \
                    or (x2 > 0.99 and y1 < 0.01) or (x2 > 0.99 and y2 > 0.99):
                problems.append((stem, '框贴住两条边（典型假框：挡板/墙）'))

    print('图片 %d 张，空标签（无目标）%d 张' % (len(images), empty))

    # 同时写一份 UTF-8 报告，避免 Windows 控制台代码页把中文显示成乱码
    report_path = os.path.join(args.dir, 'label_check.txt')
    with open(report_path, 'w', encoding='utf-8') as handle:
        handle.write('图片 %d 张，空标签 %d 张，问题 %d 条\n\n' % (len(images), empty, len(problems)))
        for stem, message in problems:
            handle.write('%-60s %s\n' % (stem, message))

    if not problems:
        print('没有发现问题 ✅')
        print('报告:', report_path)
        return
    print('发现 %d 个问题（完整列表见 %s）：' % (len(problems), report_path))
    for stem, message in problems[:50]:
        print('  %-60s %s' % (stem[:60], message))
    if len(problems) > 50:
        print('  … 其余 %d 条省略' % (len(problems) - 50))


if __name__ == '__main__':
    main()
