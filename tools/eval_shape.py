"""离线评测形状模型（.pt / .onnx），按「5 视角置信度累加投票」给出每件的结论。

为什么按投票而不是单图：板上 fallback 就是这么做的（多视角累计分数），
所以这里的结论才和现场一致。

用法（WSL，项目根目录）：
    python tools/eval_shape.py --model models_try/shape_best.pt
    python tools/eval_shape.py --model runs/shape_B/weights/best.pt --json out_B.json
"""
import argparse
import glob
import json
import os
import re

import numpy as np
from ultralytics import YOLO

# 离线评测集真值（2026-11-22 标定 + 11/23 四棱锥 + 11/19 通心筒）
TRUTH_BY_DIR = {
    '20261119_215812_五视角_通心筒': 'cylinder_thinWalled',
    '20261119_220112_五视角_通心筒': 'cylinder_thinWalled',
    '20261119_220300_五视角_通心筒': 'cylinder_thinWalled',
    '20261123_212223_橙四棱锥': 'pyramid4',
    '20261123_213123_橙四棱锥_遮光后': 'pyramid4',
}
TRUTH_BY_IMAGE = {
    'stage2_r001_01': 'cylinder_thinWalled',
    'stage2_r002_01': 'prism4_thinWalled',
    'stage2_r003_01': 'cylinder_thinWalled',
    'stage2_r004_01': 'prism6',
    'stage2_r005_01': 'prism6',
    'stage2_r006_01': 'prism6_thinWalled',
}


def norm(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def predict(model, path, conf):
    result = model.predict(path, imgsz=640, conf=conf, verbose=False)[0]
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return None, 0.0
    scores = boxes.conf.tolist()
    best = int(max(range(len(scores)), key=lambda i: scores[i]))
    return model.names[int(boxes.cls[best])], float(scores[best])


def predict_mirror(model, path, conf, crop_top):
    """镜面视角：只取上 crop_top（与 DETECT_MIRROR_CROP 一致）后再推。"""
    import cv2

    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None, 0.0
    height = image.shape[0]
    crop = image[:int(height * crop_top)]
    result = model.predict(crop, imgsz=640, conf=conf, verbose=False)[0]
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return None, 0.0
    scores = boxes.conf.tolist()
    best = int(max(range(len(scores)), key=lambda i: scores[i]))
    return model.names[int(boxes.cls[best])], float(scores[best])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--root', default='_eval2')
    parser.add_argument('--conf', type=float, default=0.25)
    parser.add_argument('--json', default='')
    parser.add_argument('--drop-views', default='', help='不参与投票的视角，如 3')
    parser.add_argument('--mirror-crop', type=float, default=0.0,
                        help='镜面视角只取上多少比例（0=不裁），如 0.60')
    args = parser.parse_args()

    drop_views = {int(v) for v in args.drop_views.split(',') if v.strip()}

    model = YOLO(args.model)
    entries = []

    # 1) 五视角目录：逐视角预测 → 累加分数投票
    for directory in sorted(glob.glob(os.path.join(args.root, 'views', '*'))):
        name = os.path.basename(directory)
        truth = TRUTH_BY_DIR.get(name)
        images = sorted(glob.glob(os.path.join(directory, 'view_*.jpg')))
        sums = {}
        per_view = []
        for image in images:
            match = re.search(r'view_(\d+)', os.path.basename(image))
            view_index = int(match.group(1)) if match else 0
            if view_index in drop_views:
                per_view.append((os.path.basename(image), 'skipped', 0.0))
                continue
            if args.mirror_crop > 0 and view_index == 5:
                cls, conf = predict_mirror(model, image, args.conf, args.mirror_crop)
            else:
                cls, conf = predict(model, image, args.conf)
            per_view.append((os.path.basename(image), cls, conf))
            if cls:
                sums[cls] = sums.get(cls, 0.0) + conf
        voted = max(sums.items(), key=lambda kv: kv[1])[0] if sums else '-'
        entries.append({'item': name, 'truth': truth, 'pred': voted,
                        'ok': bool(truth) and norm(truth) == norm(voted),
                        'per_view': per_view})

    # 2) 单图（6 件标定）
    for path in sorted(glob.glob(os.path.join(args.root, 'images', '*.jpg'))):
        stem = os.path.splitext(os.path.basename(path))[0]
        truth = TRUTH_BY_IMAGE.get(stem)
        cls, conf = predict(model, path, args.conf)
        entries.append({'item': stem, 'truth': truth, 'pred': cls or '-',
                        'ok': bool(truth) and norm(truth) == norm(cls or '-'),
                        'per_view': [(os.path.basename(path), cls, conf)]})

    ok = sum(1 for e in entries if e['ok'])
    print('模型: %s' % args.model)
    print('%-34s %-22s %-22s %s' % ('item', 'truth', 'pred', 'ok'))
    for entry in entries:
        print('%-34s %-22s %-22s %s'
              % (entry['item'][:34], entry['truth'], entry['pred'], 'OK' if entry['ok'] else 'XX'))
    print('\n准确: %d / %d' % (ok, len(entries)))
    for entry in entries:
        if not entry['ok']:
            detail = ', '.join('%s=%s(%.2f)' % (v, c or '-', s) for v, c, s in entry['per_view'])
            print('  失败: %s  真值=%s  预测=%s\n         逐视角: %s'
                  % (entry['item'], entry['truth'], entry['pred'], detail))

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump({'model': args.model, 'ok': ok, 'total': len(entries),
                       'entries': entries}, handle, ensure_ascii=False, indent=2)
        print('已写', args.json)


if __name__ == '__main__':
    main()
