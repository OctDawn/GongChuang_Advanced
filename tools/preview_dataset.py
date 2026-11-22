# -*- coding: utf-8 -*-
"""把合成训练集拼成带框的 contact sheet，便于目视检查标注。"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = r"D:\GongChuang_Advanced\yolo_offline_dataset"
IMG_DIR = os.path.join(OUT, "images")
LC = os.path.join(OUT, "labels_color")
LS = os.path.join(OUT, "labels_shape")


def load_names(path):
    with open(path, encoding="utf-8") as f:
        return [x.strip() for x in f if x.strip()]


colors = load_names(os.path.join(OUT, "classes_color.txt"))
shapes = load_names(os.path.join(OUT, "classes_shape.txt"))

files = sorted(f for f in os.listdir(IMG_DIR) if f.endswith(".png"))
cols, cell_w, cell_h = 4, 320, 180
rows = (len(files) + cols - 1) // cols
sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (30, 30, 30))
draw = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 13)
except Exception:
    font = ImageFont.load_default()

for i, name in enumerate(files):
    stem = os.path.splitext(name)[0]
    im = Image.open(os.path.join(IMG_DIR, name)).convert("RGB")
    W, H = im.size
    im = im.resize((cell_w, cell_h))
    ox, oy = (i % cols) * cell_w, (i // cols) * cell_h

    with open(os.path.join(LC, stem + ".txt")) as f:
        cid, cx, cy, bw, bh = f.read().split()[:5]
    with open(os.path.join(LS, stem + ".txt")) as f:
        sid = f.read().split()[0]
    cid, sid = int(cid), int(sid)

    x1 = (float(cx) - float(bw) / 2) * cell_w
    y1 = (float(cy) - float(bh) / 2) * cell_h
    x2 = (float(cx) + float(bw) / 2) * cell_w
    y2 = (float(cy) + float(bh) / 2) * cell_h
    draw.rectangle([ox + x1, oy + y1, ox + x2, oy + y2], outline=(255, 60, 60), width=2)
    draw.text((ox + x1 + 2, oy + y1 + 1), f"{colors[cid]}/{shapes[sid]}", fill=(255, 255, 0), font=font)
    sheet.paste(im, (ox, oy))

path = os.path.join(OUT, "preview.png")
sheet.save(path)
print("saved", path, "cells", len(files))
