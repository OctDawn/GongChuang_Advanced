"""裁剪预览：按四边比例裁剪图片并保存。在 PC 上跑，只依赖 opencv-python + numpy。

四边比例都相对「当前图」的宽/高，0 表示该边不裁，四边独立可调。

用法（在项目根目录执行）：

    # 1) 什么都不改，只是看看原图（比例全 0）
    python tools/crop_preview.py capture/xxx/stage1_r001.jpg

    # 2) 阶段1 托盘：先按 TRAY_* 裁出托盘区域，再四边各内缩 1/8
    python tools/crop_preview.py 托盘图.jpg --stage pallet --left 0.125 --top 0.125 --right 0.125 --bottom 0.125

    # 3) 阶段2 识别区：先居中裁成正方形，再右、上各内缩 1/6
    python tools/crop_preview.py 识别区图.jpg --stage detect --right 0.1667 --top 0.1667

    # 4) 直接用项目当前 const.py 里的比例，并生成对比图
    python tools/crop_preview.py capture/xxx/original/stage2_r001_01.jpg --stage detect --use-const --compare

    # 5) 顺带模拟发给大模型前的缩放/JPEG 质量，看输出体积
    python tools/crop_preview.py 图.jpg --left 0.1 --scale 0.75 --quality 75

不指定 --left/--top/--right/--bottom 时默认 0（只做 --stage 的基础裁剪）。
"""

import argparse
import os
import sys

try:
    import cv2
    import numpy as np
except ImportError as error:  # noqa: BLE001
    raise SystemExit(f"缺少依赖 {error.name}，请先安装：pip install opencv-python numpy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.const import (  # noqa: E402  （只读常量，shared/const.py 不依赖第三方库）
    TRAY_X_MIN, TRAY_X_MAX, TRAY_Y_MIN, TRAY_Y_MAX,
    PALLET_LLM_INSET, DETECT_LLM_INSET,
)


def imread(path):
    """读图，兼容 Windows 下的中文路径。"""
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite(path, image, quality):
    """写图，兼容中文路径，按指定 JPEG 质量编码；返回文件大小 KB。"""
    ok, buf = cv2.imencode(os.path.splitext(path)[1] or ".jpg", image,
                           [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise OSError(f"编码失败: {path}")
    buf.tofile(path)
    return len(buf) / 1024


def crop_tray(image):
    """按 TRAY_* 裁出托盘区域（与阶段1 线上逻辑一致）。"""
    h, w = image.shape[:2]
    return image[int(TRAY_Y_MIN * h):int(TRAY_Y_MAX * h),
                 int(TRAY_X_MIN * w):int(TRAY_X_MAX * w)]


def crop_square(image):
    """居中裁成正方形（与识别区采集端的 crop_square=True 一致）。"""
    h, w = image.shape[:2]
    s = min(h, w)
    top, left = (h - s) // 2, (w - s) // 2
    return image[top:top + s, left:left + s]


def crop_by_ratio(image, left, top, right, bottom):
    """按四边比例裁剪；裁没了就原样返回并提示。"""
    h, w = image.shape[:2]
    x0, x1 = int(round(w * left)), w - int(round(w * right))
    y0, y1 = int(round(h * top)), h - int(round(h * bottom))
    if x1 - x0 < 16 or y1 - y0 < 16:
        print(f"  ! 比例过大（结果 {x1 - x0}x{y1 - y0}），本次不裁剪")
        return image
    return image[y0:y1, x0:x1]


def scale_to(image, scale, max_side):
    """等比缩放（模拟 LLM_IMAGE_SCALE / LLM_MAX_SIDE），不放大。"""
    h, w = image.shape[:2]
    factor = min(1.0, scale)
    if max_side > 0 and max(h, w) * factor > max_side:
        factor = max_side / max(h, w)
    if factor >= 1.0:
        return image
    return cv2.resize(image, (max(1, int(w * factor)), max(1, int(h * factor))),
                      interpolation=cv2.INTER_AREA)


def build_compare(original, cropped, left, top, right, bottom):
    """左：原图 + 红框标出保留区域；右：裁剪结果。两边等高。"""
    h, w = original.shape[:2]
    x0, x1 = int(round(w * left)), w - int(round(w * right))
    y0, y1 = int(round(h * top)), h - int(round(h * bottom))
    marked = original.copy()
    cv2.rectangle(marked, (x0, y0), (x1 - 1, y1 - 1), (0, 0, 255), max(2, h // 200))
    ch, cw = cropped.shape[:2]
    right_side = cv2.resize(cropped, (max(1, int(cw * h / ch)), h)) if ch else cropped
    return np.hstack([marked, np.full((h, 12, 3), 255, np.uint8), right_side])


def main():
    parser = argparse.ArgumentParser(description="按四边比例裁剪图片并保存")
    parser.add_argument("images", nargs="+", help="输入图片（可多张）")
    parser.add_argument("--stage", choices=("raw", "pallet", "detect"), default="raw",
                        help="raw=直接按比例裁；pallet=先按 TRAY_* 裁托盘；detect=先居中裁正方形")
    parser.add_argument("--left", type=float, default=None, help="左边裁掉的比例，0~1")
    parser.add_argument("--top", type=float, default=None, help="上边裁掉的比例，0~1")
    parser.add_argument("--right", type=float, default=None, help="右边裁掉的比例，0~1")
    parser.add_argument("--bottom", type=float, default=None, help="下边裁掉的比例，0~1")
    parser.add_argument("--use-const", action="store_true",
                        help="比例改用 const.py 里的 PALLET_LLM_INSET / DETECT_LLM_INSET")
    parser.add_argument("--scale", type=float, default=1.0, help="裁剪后再等比缩放（默认 1.0）")
    parser.add_argument("--max-side", type=int, default=0, help="最长边封顶像素（0=不限）")
    parser.add_argument("--quality", type=int, default=85, help="输出 JPEG 质量（默认 85）")
    parser.add_argument("--compare", action="store_true", help="额外保存一张对比图")
    parser.add_argument("-o", "--out", default=None, help="输出文件（单张图时）或输出目录")
    args = parser.parse_args()

    insets = {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
    if args.use_const:
        insets = dict(PALLET_LLM_INSET if args.stage == "pallet" else DETECT_LLM_INSET)
        print(f"使用 const.py 里 {args.stage} 的比例: {insets}")
    for side in ("left", "top", "right", "bottom"):
        value = getattr(args, side)
        if value is not None:
            insets[side] = value
    print(f"本次裁剪比例: {insets}  stage={args.stage}  scale={args.scale}  "
          f"max_side={args.max_side}  quality={args.quality}\n")

    out_dir = None
    if args.out and len(args.images) > 1:
        os.makedirs(args.out, exist_ok=True)
        out_dir = args.out

    for path in args.images:
        image = imread(path)
        if image is None:
            print(f"无法读取: {path}")
            continue

        staged = image
        if args.stage == "pallet":
            staged = crop_tray(image)
        elif args.stage == "detect":
            staged = crop_square(image)
        cropped = crop_by_ratio(staged, insets["left"], insets["top"],
                                insets["right"], insets["bottom"])
        output = scale_to(cropped, args.scale, args.max_side)

        if args.out and out_dir is None and len(args.images) == 1:
            save_path = args.out
        else:
            stem, ext = os.path.splitext(os.path.basename(path))
            save_path = os.path.join(out_dir or os.path.dirname(path) or ".",
                                     f"{stem}_crop{ext or '.jpg'}")

        kb = imwrite(save_path, output, args.quality)
        shape = lambda img: f"{img.shape[1]}x{img.shape[0]}"
        print(os.path.basename(path))
        print(f"  原图 {shape(image)}"
              + (f" -> {args.stage} 后 {shape(staged)}" if args.stage != "raw" else "")
              + f" -> 裁剪后 {shape(cropped)} -> 输出 {shape(output)}   {kb:.0f} KB")
        print(f"  已保存: {save_path}")

        if args.compare:
            base = image if args.stage == "raw" else staged
            preview = build_compare(base, cropped, insets["left"], insets["top"],
                                    insets["right"], insets["bottom"])
            compare_path = os.path.splitext(save_path)[0] + "_compare.jpg"
            imwrite(compare_path, preview, 90)
            print(f"  对比图(左=原图+红框, 右=裁剪结果): {compare_path}")


if __name__ == "__main__":
    main()
