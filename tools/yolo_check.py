"""离线验证 YOLO 兜底：不开相机、不碰串口，直接拿保存的四视角图片跑 RKNN。

用途：确认「千问挂了之后 YOLO 能不能接手」这件事。
和 lines push 主流程走的是同一个 YoloFallback.classify()，所以结论可迁移。

用法（板上，项目根目录）：

    conda activate llm
    python tools/yolo_check.py capture/views/20261118_191124_正常光_橙色通心筒横放
    python tools/yolo_check.py capture/views/*正常光*/          # 批量
    python tools/yolo_check.py capture/views/xxx --per-model    # 额外打印每个模型的耗时

输出（每个目录一行）：
    color / shape / label / 总耗时 / 每路平均耗时

比对建议：和同目录下 test_llm.py 的千问结论并列看，形状不一致的条目最值得关注。
"""

import argparse
import glob
import os
import sys
import time
from contextlib import ExitStack

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()      # 必须在 shared.const / yolo.* 之前

import cv2      # noqa: E402
import numpy as np  # noqa: E402

from shared.const import STAGE_TWO_DETECTION_COUNT  # noqa: E402
from yolo.fallback import YoloFallback  # noqa: E402


def _imread(path):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _load_views(directory):
    """优先 view_*.jpg（相机原图）；没有就退回 sent_*.jpg。"""
    for pattern in ("view_*.jpg", "sent_*.jpg", "*.jpg"):
        paths = sorted(glob.glob(os.path.join(directory, pattern)))
        if len(paths) >= 1:
            return paths
    return []


def _per_model(fallback, frames):
    """分别计时三个识别模型（贴纸模型不参与形状判定，单独看）。"""
    marks = []
    for name, runner in (("color", fallback.color_runner),
                         ("shape", fallback.shape_runner),
                         ("label", fallback.label_runner)):
        if runner is None:
            continue
        started = time.monotonic()
        for frame in frames:
            try:
                runner.infer(frame)
            except Exception as error:  # noqa: BLE001
                print("    %s 推理失败: %s" % (name, error))
                break
        marks.append((name, (time.monotonic() - started) * 1000.0 / max(1, len(frames))))
    return marks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dirs", nargs="+", help="包含 view_1..N 的目录（支持通配符）")
    parser.add_argument("--per-model", action="store_true", help="打印每个模型的单帧耗时")
    args = parser.parse_args()

    targets = []
    for item in args.dirs:
        if os.path.isdir(item):
            targets.append(item)
        else:
            targets.extend(sorted(p for p in glob.glob(item) if os.path.isdir(p)))
    if not targets:
        raise SystemExit("没找到目录：%r" % (args.dirs,))

    with ExitStack() as stack:
        started = time.monotonic()
        fallback = YoloFallback.load(stack)
        print("YOLO 模型加载完成，耗时 %.2f 秒；需要 %d 路视角\n"
              % (time.monotonic() - started, STAGE_TWO_DETECTION_COUNT))

        for directory in targets:
            paths = _load_views(directory)
            frames = [frame for frame in (_imread(p) for p in paths) if frame is not None]
            tag = os.path.basename(directory.rstrip("/\\"))
            if len(frames) != STAGE_TWO_DETECTION_COUNT:
                print("%-40s 跳过：需要 %d 张，实际 %d 张"
                      % (tag[:40], STAGE_TWO_DETECTION_COUNT, len(frames)))
                continue
            started = time.monotonic()
            color, shape, label = fallback.classify(frames)
            elapsed = time.monotonic() - started
            print("%-40s %-8s %-22s %-10s %5.2fs"
                  % (tag[:40], color or "-", shape or "-", label or "-", elapsed))
            if args.per_model:
                for name, ms in _per_model(fallback, frames):
                    print("    %-6s 单帧 %.1f ms" % (name, ms))


if __name__ == "__main__":
    main()
