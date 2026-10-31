"""线上版2 离线自检：不打开相机，直接对本地图片跑 classify_grid（3 色 × 3 形）。

用法（项目根目录）：
    python tools/grid_offline.py 图片.jpg
    python tools/grid_offline.py capture/grid_check/20261031_2110/          # 目录下所有图
    python tools/grid_offline.py "capture/images/20261031_*/original/*.jpg" # 通配符
    python tools/grid_offline.py 图片.jpg --no-crop                        # 整幅直送，不裁剪
    python tools/grid_offline.py 图片.jpg --save-dir out/                  # 另存实际送模型的那张

说明：
    - 默认走 crop_detect_llm（与线上送图路径完全一致）；--no-crop 则把原图直接送模型；
    - 只调用 LLM，不碰摄像头 / 串口 / 显示 / RKNN，可随时中断；
    - 每张图打印 状态 / 颜色 / 形状 / 耗时，多张时末尾给汇总。
"""

import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()      # 必须在 llm.qw 之前

import cv2      # noqa: E402
import numpy as np  # noqa: E402

from shared.common import crop_detect_llm  # noqa: E402
from llm.grid_recognition import classify_grid  # noqa: E402

_EXTS = ("*.jpg", "*.jpeg", "*.png", "*.bmp")


def collect(paths):
    """展开目录 / 通配符，返回按名字排序的文件列表。"""
    files = []
    for p in paths:
        if os.path.isdir(p):
            for ext in _EXTS:
                files += glob.glob(os.path.join(p, ext))
        elif os.path.isfile(p):
            files.append(p)
        else:
            for ext in _EXTS:
                files += glob.glob(p) if glob.has_magic(p) else []
    # 去重 + 排序，便于多次运行结果对齐
    return sorted(set(files))


def imread(path):
    """兼容中文/非 ASCII 路径：cv2.imread 在 Windows 下会失败。"""
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="图片路径 / 目录 / 通配符")
    parser.add_argument("--no-crop", action="store_true", help="不裁剪，整幅送模型")
    parser.add_argument("--timeout", type=float, default=15.0, help="单张总时限（秒）")
    parser.add_argument("--save-dir", default="", help="把实际送模型的图另存到此目录")
    args = parser.parse_args()

    files = collect(args.paths)
    if not files:
        raise SystemExit("没找到图片：%r" % (args.paths,))
    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)

    print("共 %d 张，裁剪=%s，模型=%s\n" % (
        len(files), "否" if args.no_crop else "是",
        os.environ.get("DASHSCOPE_MODEL", "?")))

    rows = []
    for path in files:
        frame = imread(path)
        if frame is None:
            print("[跳过] 读不出图片: %s" % path)
            continue
        view = frame if args.no_crop else crop_detect_llm(frame)
        name = os.path.basename(path)
        started = time.time()
        try:
            result = classify_grid(view, total_timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001 —— 单张失败不影响后续
            seconds = time.time() - started
            print("%-34s %5.2fs  异常: %s" % (name, seconds, exc))
            rows.append((name, "-", "-", "error", seconds))
            continue
        seconds = time.time() - started
        status = result.get("status") or "?"
        color = result.get("color") or "-"
        shape = result.get("shape") or "-"
        print("%-34s %5.2fs  %-6s %-8s %-22s %s" % (
            name, seconds, status, color, shape, (result.get("reason") or "")[:44]))
        rows.append((name, color, shape, status, seconds))
        if args.save_dir:
            cv2.imwrite(os.path.join(args.save_dir, name), view)

    if len(rows) > 1:
        ok = sum(1 for r in rows if r[3] == "ok")
        total = sum(r[4] for r in rows)
        print("\n共 %d 张：ok %d / 其余 %d，总耗时 %.2fs，平均 %.2fs/张" % (
            len(rows), ok, len(rows) - ok, total, total / len(rows)))
        # 按 颜色+形状 聚合，方便核对"9 类是否各判对"
        buckets = {}
        for name, color, shape, status, _ in rows:
            buckets.setdefault((color, shape), []).append(name)
        print("\n分布：")
        for (color, shape), names in sorted(buckets.items()):
            print("  %-8s %-22s ×%d" % (color, shape, len(names)))


if __name__ == "__main__":
    main()
