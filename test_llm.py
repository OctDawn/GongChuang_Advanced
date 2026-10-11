"""离线测试 LLM 识别：不依赖摄像头 / 串口 / RKNN，直接喂本地图片。

复用线上同一套代码（llm.qw / llm.prompt / llm.shape_recognition /
llm.shape_memory / llm.empty_examples / shared.common），结果与 main_llm 一致。

前置条件（PC 上）：
    pip install openai opencv-python numpy
    项目根目录已有 .env（key + model + base_url），LLM_CACHE_MODE 默认 portable。

用法（在项目根目录执行）：
    python test_llm.py classify <图1> <图2> <图3>          # 识别区多视角分类（个数 = STAGE_TWO_DETECTION_COUNT）
    python test_llm.py pallet <托盘俯视图>                  # 查栏（空托盘参考自动读 llm/example）

示例（分类，现在识别区是 3 个视角）：
    python test_llm.py classify ^
        capture/images/20260101_120000/original/stage2_r001_01.jpg ^
        capture/images/20260101_120000/original/stage2_r001_02.jpg ^
        capture/images/20260101_120000/original/stage2_r001_03.jpg

切换模型（不落盘，仅本次生效，PowerShell）：
    $env:DASHSCOPE_MODEL="openai/gpt-4o-mini"; python test_llm.py classify a.jpg b.jpg c.jpg

对比上游服务档位（OpenRouter 的 service_tier，配 --runs 重复几次看统计）：
    $env:LLM_SERVICE_TIER="flex";      python test_llm.py classify a.jpg b.jpg c.jpg --runs 5
    $env:LLM_SERVICE_TIER="priority";  python test_llm.py classify a.jpg b.jpg c.jpg --runs 5
    $env:LLM_SERVICE_TIER="ultrafast"; python test_llm.py classify a.jpg b.jpg c.jpg --runs 5
    不设 = 用 OpenRouter 默认路由；每次请求实际落的档位看日志里的 service_tier=。
"""

import argparse
import logging
import os
import sys
import time

import cv2
import numpy as np

from llm.qw import qwen_realise, LLMError
from llm.prompt import PROMPT_PALLET_COLUMNS
from llm.shape_memory import ShapeMemory
from llm.shape_recognition import ShapeRecognizer
from llm.empty_examples import load_empty_examples, load_shape_examples
from shared.common import crop_pallet_llm
from shared.const import COLUMN_COUNT, STAGE_TWO_DETECTION_COUNT

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("wave.vision.test")


def _parse_pallet(result):
    """查栏结果校验：长度 COLUMN_COUNT 的布尔数组（与 main_llm 同逻辑）。"""
    if not isinstance(result, list) or len(result) != COLUMN_COUNT:
        raise LLMError(f"托盘结果应为长度 {COLUMN_COUNT} 的数组: {result!r}")
    out = []
    for v in result:
        if isinstance(v, bool):
            out.append(v)
        elif isinstance(v, (int, float)) and v in (0, 1):
            out.append(bool(v))
        elif isinstance(v, str) and v.strip().lower() in ("0", "1", "true", "false", "yes", "no"):
            out.append(v.strip().lower() in ("1", "true", "yes"))
        else:
            raise LLMError(f"托盘结果值非法: {result!r}")
    return out


def _imread(path):
    """读取图片，兼容 Windows 下中文/非 ASCII 路径（cv2.imread 会乱码失败）。"""
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _read(paths):
    frames = []
    for p in paths:
        img = _imread(p)
        if img is None:
            sys.exit(f"无法读取图片: {p}")
        frames.append(img)
    return frames


def cmd_classify(args):
    if len(args.images) != STAGE_TWO_DETECTION_COUNT:
        sys.exit(f"classify 需要恰好 {STAGE_TWO_DETECTION_COUNT} 张视角图片"
                 "（a 触发第 1 张 + 每次转向各 1 张）")
    frames = _read(args.images)

    empty = load_empty_examples()
    shape_examples = load_shape_examples()
    memory = ShapeMemory(args.image_dir,
                         limit=int(os.environ.get("LLM_SHAPE_MEMORY_LIMIT", "12")),
                         view_count=STAGE_TWO_DETECTION_COUNT)
    recognizer = ShapeRecognizer(memory, empty["detect_area"], shape_examples=shape_examples)

    t0 = time.monotonic()
    color, shape, label_kind, label_desc = recognizer.classify(frames, total_timeout=args.timeout)
    dt = time.monotonic() - t0
    durations = [dt]
    result = (color, shape, label_kind, label_desc)
    runs = max(1, args.runs)
    for run in range(2, runs + 1):
        t0 = time.monotonic()
        result = recognizer.classify(frames, total_timeout=args.timeout)
        dt = time.monotonic() - t0
        durations.append(dt)
        print(f"[第 {run}/{runs} 次] {dt:.2f} 秒  color={result[0]} shape={result[1]} label_kind={result[2]}")

    color, shape, label_kind, label_desc = result
    print("\n===== 分类结果（最后一次）=====")
    print(f"color       = {color}")
    print(f"shape       = {shape}")
    print(f"label_kind  = {label_kind}")
    print(f"label_desc  = {label_desc}")
    print(f"耗时        = {durations[-1]:.2f} 秒")
    print("====================")

    if len(durations) > 1:
        ordered = sorted(durations)
        print("\n===== 汇总 =====")
        print(f"service_tier = {os.environ.get('LLM_SERVICE_TIER', '') or '默认'}")
        print(f"runs         = {len(durations)}")
        print(f"中位         = {ordered[len(ordered) // 2]:.2f} 秒")
        print(f"均值         = {sum(durations) / len(durations):.2f} 秒")
        print(f"最小 / 最大  = {ordered[0]:.2f} / {ordered[-1]:.2f} 秒")
        print("====================")


def cmd_pallet(args):
    img = _imread(args.image)
    if img is None:
        sys.exit(f"无法读取图片: {args.image}")
    empty = load_empty_examples()
    tray = crop_pallet_llm(img)

    t0 = time.monotonic()
    # 图序必须与 PROMPT_PALLET_COLUMNS 的约定一致：第 1 张空托盘参考图，第 2 张当前托盘。
    occupancy = qwen_realise(
        [empty["pallet"], tray], PROMPT_PALLET_COLUMNS,
        validator=_parse_pallet, total_timeout=args.timeout,
    )
    dt = time.monotonic() - t0

    # 注意：这里按画面从左到右输出；线上还会按 LASER_SENSOR_ON_LEFT 反转。
    print("\n===== 查栏结果 =====")
    print(f"五栏占用(左→右) = {''.join('1' if x else '0' for x in occupancy)}")
    print(f"耗时            = {dt:.2f} 秒")
    print("====================")


def main():
    parser = argparse.ArgumentParser(description="离线测试 LLM 识别")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cls = sub.add_parser("classify", help="识别区多视角分类")
    p_cls.add_argument("images", nargs="+", help="本轮各视角图片路径（个数需等于 STAGE_TWO_DETECTION_COUNT）")
    p_cls.add_argument("--timeout", type=float, default=40.0, help="单轮总时限（秒）")
    p_cls.add_argument("--image-dir", default=os.path.join("capture", "test_llm"),
                       help="形状记忆目录（新形状会登记在此）")
    p_cls.add_argument("--runs", type=int, default=1,
                       help="重复次数（做 service_tier 对比时用，如 5）")

    p_pal = sub.add_parser("pallet", help="托盘查栏")
    p_pal.add_argument("image", help="托盘俯视图路径")
    p_pal.add_argument("--timeout", type=float, default=35.0, help="单轮总时限（秒）")

    args = parser.parse_args()
    if args.cmd == "classify":
        cmd_classify(args)
    elif args.cmd == "pallet":
        cmd_pallet(args)


if __name__ == "__main__":
    main()
