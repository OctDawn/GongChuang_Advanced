# -*- coding: utf-8 -*-
"""把 empty_out 的渲染合成到 empty 背景上并画框。"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SANDBOX = r"C:\Users\Lenovo\AppData\Local\Temp\chatbox-sandbox\b05b5ba8-c6b2-4995-9429-0a8e52e57be0"
OUT = SANDBOX + r"\empty_out"
BG = r"D:\GongChuang_Advanced\llm\example\empty_top.jpg"

bg = Image.open(BG).convert("RGBA")
fg = Image.open(os.path.join(OUT, "rgba.png")).convert("RGBA")
comp = Image.alpha_composite(bg, fg)
draw = ImageDraw.Draw(comp)
try:
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)
except Exception:
    font = ImageFont.load_default()

items = [("prism3", (255, 40, 40)), ("prism4_thinWalled", (0, 190, 0))]
for i, (label, color) in enumerate(items):
    mask = np.asarray(Image.open(os.path.join(OUT, f"mask_{i}.png")).convert("L"))
    ys, xs = np.where(mask > 100)
    if not len(xs):
        print("empty mask", i)
        continue
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    print(i, label, (x1, y1, x2, y2))
    draw.rectangle([x1, y1, x2 - 1, y2 - 1], outline=color, width=3)
    ty = max(0, y1 - 20)
    tw = draw.textlength(label, font=font)
    draw.rectangle([x1, ty, x1 + tw + 6, ty + 20], fill=color)
    draw.text((x1 + 3, ty + 1), label, fill=(255, 255, 255), font=font)

p = os.path.join(OUT, "final.png")
comp.convert("RGB").save(p)
print("saved", p)
