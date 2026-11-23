"""capture 鐓х墖鎵归噺绮楁墦鏍囷細棰滆壊妯″瀷 + 褰㈢姸妯″瀷鍚勫嚭涓€濂?YOLO 妫€娴嬫爣绛俱€?
杈撳叆锛欴:\\GongChuang_Advanced\\capture 涓嬬殑鐓х墖锛堟帓闄よ矾寰勫惈 pallet 鐨勶級
杈撳嚭锛欴:\\GongChuang_Advanced\\capture_labels
        labels_color\\  labels_shape\\   姣忓紶鍥句竴涓?txt锛堟棤鐩爣鍒欑┖鏂囦欢锛?        preview_color\\ preview_shape\\  鎶芥鍙鍖?        stats.csv
"""
import csv
import os
import random
import sys
import time

import cv2
import numpy as np
from ultralytics import YOLO

SRC = r"D:\GongChuang_Advanced\capture"
OUT = r"D:\GongChuang_Advanced\capture_labels"
COLOR_PT = r"D:\GongChuang_Advanced\runs\color\run\weights\best.pt"
SHAPE_PT = r"D:\GongChuang_Advanced\runs\shape\run\weights\best.pt"
IMG_EXT = {".jpg", ".jpeg", ".png"}
CONF = 0.25
IOU = 0.45
IMGSZ = 640
MAX_DET = 100
PREVIEW_EVERY = 25

C_PALETTE = [(60, 76, 231), (219, 152, 52), (113, 204, 46), (34, 126, 230), (192, 60, 240)]
S_PALETTE = [(255, 0, 128), (0, 214, 255), (128, 255, 0), (255, 128, 0), (200, 0, 255)]


def collect():
    files = []
    for root, _dirs, names in os.walk(SRC):
        for n in names:
            ext = os.path.splitext(n)[1].lower()
            if ext not in IMG_EXT:
                continue
            full = os.path.join(root, n)
            rel = os.path.relpath(full, SRC)
            if "pallet" in rel.lower():
                continue
            files.append(full)
    files.sort()
    return files


def read_img(path):
    try:
        if os.path.getsize(path) == 0:
            return None
        buf = np.fromfile(path, dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return None


def flat_name(full):
    rel = os.path.relpath(full, SRC)
    return rel.replace("\\", "_")


def yolo_lines(result, h, w):
    lines = []
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return lines, []
    xyxy = boxes.xyxy.cpu().numpy()
    cls = boxes.cls.cpu().numpy().astype(int)
    conf = boxes.conf.cpu().numpy()
    names = result.names
    for (x1, y1, x2, y2), c, p in zip(xyxy, cls, conf):
        x1 = max(0.0, min(x1, w)); x2 = max(0.0, min(x2, w))
        y1 = max(0.0, min(y1, h)); y2 = max(0.0, min(y2, h))
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        if bw <= 0.002 or bh <= 0.002:
            continue
        cx = (x1 + x2) / 2 / w
        cy = (y1 + y2) / 2 / h
        lines.append(f"{c} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return lines, [(int(c), float(p), (x1, y1, x2, y2)) for (x1, y1, x2, y2), c, p in zip(xyxy, cls, conf)]


def draw(img, dets, names, palette, tag):
    out = img.copy()
    for c, p, (x1, y1, x2, y2) in dets:
        col = palette[c % len(palette)]
        cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), col, 2)
        lab = f"{names[c]} {p:.2f}"
        (tw, th), _ = cv2.getTextSize(lab, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(out, (int(x1), int(y1) - th - 6), (int(x1) + tw + 4, int(y1)), col, -1)
        cv2.putText(out, lab, (int(x1) + 2, int(y1) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(out, tag, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
    return out


def imwrite_ascii(path, img):
    ext = os.path.splitext(path)[1]
    ok, buf = cv2.imencode(ext, img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        return False
    buf.tofile(path)
    return True


def main():
    t0 = time.time()
    files = collect()
    print(f"images: {len(files)}", flush=True)
    for d in ("labels_color", "labels_shape", "preview_color", "preview_shape"):
        os.makedirs(os.path.join(OUT, d), exist_ok=True)

    color = YOLO(COLOR_PT)
    shape = YOLO(SHAPE_PT)

    rows = []
    empty_c = empty_s = 0
    for i, f in enumerate(files, 1):
        name = flat_name(f)
        stem = os.path.splitext(name)[0]
        img = read_img(f)
        if img is None:
            print("  !! 璇诲浘澶辫触", f, flush=True)
            continue
        h, w = img.shape[:2]

        rc = color.predict(img, imgsz=IMGSZ, conf=CONF, iou=IOU, max_det=MAX_DET, verbose=False)[0]
        rs = shape.predict(img, imgsz=IMGSZ, conf=CONF, iou=IOU, max_det=MAX_DET, verbose=False)[0]
        lc, dc = yolo_lines(rc, h, w)
        ls, ds = yolo_lines(rs, h, w)

        with open(os.path.join(OUT, "labels_color", stem + ".txt"), "w", encoding="utf-8") as fp:
            fp.write("\n".join(lc) + ("\n" if lc else ""))
        with open(os.path.join(OUT, "labels_shape", stem + ".txt"), "w", encoding="utf-8") as fp:
            fp.write("\n".join(ls) + ("\n" if ls else ""))

        if not lc:
            empty_c += 1
        if not ls:
            empty_s += 1

        top_c = sorted({rc.names[c] for c, _p, _b in dc})
        top_s = sorted({rs.names[c] for c, _p, _b in ds})
        rows.append([name, w, h, len(lc), len(ls), "|".join(top_c), "|".join(top_s)])

        if i % PREVIEW_EVERY == 0 or i == len(files):
            imwrite_ascii(os.path.join(OUT, "preview_color", stem + ".jpg"), draw(img, dc, rc.names, C_PALETTE, "COLOR"))
            imwrite_ascii(os.path.join(OUT, "preview_shape", stem + ".jpg"), draw(img, ds, rs.names, S_PALETTE, "SHAPE"))

        if i % 25 == 0 or i == len(files):
            el = time.time() - t0
            print(f"  {i}/{len(files)}  {el:.0f}s  avg {el / i:.2f}s/img  绌烘爣 color={empty_c} shape={empty_s}", flush=True)

    with open(os.path.join(OUT, "stats.csv"), "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.writer(fp)
        wr.writerow(["image", "width", "height", "n_color", "n_shape", "colors", "shapes"])
        wr.writerows(rows)

    print(f"done in {time.time() - t0:.0f}s; images={len(rows)} empty_color={empty_c} empty_shape={empty_s}", flush=True)


if __name__ == "__main__":
    main()

