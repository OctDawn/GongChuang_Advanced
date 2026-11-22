# -*- coding: utf-8 -*-
"""审计 YOLO 数据集：数量 / 标签匹配 / 类别分布 / 框尺寸异常。"""
import os
from collections import Counter

ROOT = r"D:\GongChuang_Advanced\yolo_full"
COLORS = ["black", "blue", "brown", "deep_blue", "deep_green", "green",
          "orange", "pink", "purple", "red", "white", "yellow"]
SHAPES = ["ball", "cone", "cube", "cuboid", "cylinder", "cylinder_thinWalled",
          "hemisphere", "parallelepiped", "prism3", "prism3_thinWalled",
          "prism4_thinWalled", "prism5", "prism5_thinWalled", "prism6",
          "pyramid3", "pyramid4"]


def imgs(split):
    d = os.path.join(ROOT, "images", split)
    return sorted(f for f in os.listdir(d) if f.lower().endswith((".png", ".jpg"))) if os.path.isdir(d) else []


def audit_labels(tag, names):
    print(f"\n===== {tag} =====")
    for split in ("train", "val"):
        d = os.path.join(ROOT, f"labels_{tag}", split)
        files = sorted(f for f in os.listdir(d) if f.endswith(".txt")) if os.path.isdir(d) else []
        ims = imgs(split)
        stems_img = {os.path.splitext(f)[0] for f in ims}
        stems_lbl = {os.path.splitext(f)[0] for f in files}
        cnt = Counter()
        bad = []
        tiny = 0
        big = 0
        for f in files:
            parts = open(os.path.join(d, f)).read().split()
            if len(parts) != 5:
                bad.append((f, "fmt"))
                continue
            cid = int(parts[0])
            cx, cy, bw, bh = map(float, parts[1:])
            cnt[cid] += 1
            if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
                bad.append((f, "range"))
            if bw * bh < 0.002:
                tiny += 1
            if bw * bh > 0.85:
                big += 1
        print(f"[{split}] images={len(ims)} labels={len(files)} 缺标签={len(stems_img - stems_lbl)} 多标签={len(stems_lbl - stems_img)}")
        print(f"        bad={len(bad)} tiny(<0.2%面积)={tiny} huge(>85%)={big}")
        dist = {names[k]: v for k, v in sorted(cnt.items())}
        missing = [names[k] for k in range(len(names)) if cnt[k] == 0]
        print(f"        类别分布: {dist}")
        if missing:
            print(f"        ⚠ 无样本类别: {missing}")
        if bad[:5]:
            print(f"        bad 示例: {bad[:5]}")


def audit_images():
    print("===== images =====")
    from PIL import Image
    for split in ("train", "val"):
        d = os.path.join(ROOT, "images", split)
        sizes = Counter()
        for f in imgs(split)[:2000]:
            try:
                im = Image.open(os.path.join(d, f))
                sizes[im.size] += 1
            except Exception as e:  # noqa: BLE001
                sizes["ERR"] += 1
        print(f"[{split}] 尺寸分布(前2000张): {dict(sizes)}")


if __name__ == "__main__":
    audit_images()
    audit_labels("color", COLORS)
    audit_labels("shape", SHAPES)
