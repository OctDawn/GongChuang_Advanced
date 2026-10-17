"""A/B 测试：同一份多视角请求，对比不同「接口 + reasoning effort」的耗时与 reasoning 开销。

目的：定位阶段2 单次识别的时间瓶颈到底在"上传/排队"还是"服务端生成"。
复用线上同一套图片流水线（llm.prompt / llm.empty_examples / shared.common.crop_detect_llm /
llm.qw._encode_image），所以载荷与线上完全一致，只是换了接口参数。

建议在**板子上**跑（网络路径和线上一致），别在 PC 上跑。

用法（项目根目录）：
    # 用上一轮存下的视角图（个数 = 本轮实际拍几张），每个变体跑 3 次
    python tools/ab_reasoning.py \
        capture/images/20260925_172939/original/stage2_r001_01.jpg \
        capture/images/20260925_172939/original/stage2_r001_02.jpg \
        capture/images/20260925_172939/original/stage2_r001_03.jpg --repeat 3

    # 只测其中两个变体
    python tools/ab_reasoning.py 视角*.jpg --variants responses_default,responses_low

变体：
    responses_default   /responses + 显式缓存，reasoning 交给服务端（= 线上现状）
    responses_minimal   /responses + reasoning effort=minimal
    responses_low       /responses + reasoning effort=low
    chat_default        /chat/completions，不带 reasoning（= LLM_CACHE_MODE=portable）
    chat_low            /chat/completions + reasoning effort=low

注意：每次调用都会真的花 API 钱（一次约 9 张图），--repeat 别开太大。
"""

import argparse
import os
import statistics
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.empty_examples import load_empty_examples, load_shape_examples  # noqa: E402
from llm.prompt import build_classify_prefix, build_classify_prompt  # noqa: E402
from llm.qw import (  # noqa: E402
    DASHSCOPE_MODEL, LLM_REF_MAX_SIDE, _encode_image, _get_client, _build_messages,
    _build_responses_input,
)
from shared.common import crop_detect_llm, detect_frame_preprocessed  # noqa: E402

VARIANTS = {
    "responses_default": ("responses", None),
    "responses_minimal": ("responses", "minimal"),
    "responses_low": ("responses", "low"),
    "responses_high": ("responses", "high"),
    "chat_default": ("chat", None),
    "chat_low": ("chat", "low"),
    "chat_minimal": ("chat", "minimal"),
}


def imread(path):
    """读图，兼容中文路径。"""
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def build_payload(view_paths):
    """按线上 ShapeRecognizer.classify 的结构组装前缀段与当前图。"""
    frames = []
    for path in view_paths:
        image = imread(path)
        if image is None:
            sys.exit(f"无法读取图片: {path}")
        frames.append(image)

    empty = load_empty_examples()
    examples = load_shape_examples()
    stats = []
    prefix_contents = [
        ([{"type": "text", "text": build_classify_prefix(examples)}]
         + [_encode_image(crop_detect_llm(image), stats, LLM_REF_MAX_SIDE or None)
            for image in empty["detect_area"]]
         + [_encode_image(example["image"], stats, LLM_REF_MAX_SIDE or None) for example in examples]),
        # 本次是全新记忆，没有历史参考图，与线上第一件货的情形一致
        ([{"type": "text", "text": build_classify_prompt([], examples)}]),
    ]
    image_contents = [_encode_image(crop_detect_llm(detect_frame_preprocessed(frame)), stats)
                      for frame in frames]
    total_kb = sum(kb for _, _, kb in stats)
    print(f"载荷：{len(stats)} 张，共 {total_kb:.0f} KB"
          f"（LLM_REF_MAX_SIDE={LLM_REF_MAX_SIDE or '不限'}）")
    for index, (w, h, kb) in enumerate(stats, start=1):
        print(f"  #{index}: {w}x{h}  {kb:6.1f} KB")
    return prefix_contents, image_contents


def plain_messages(prefix_contents, image_contents, prompt):
    """chat/completions 用：不带 prompt_cache_breakpoint，其他与线上一致。"""
    messages = [{"role": "user", "content": blocks} for blocks in prefix_contents]
    messages.append({"role": "user", "content": [{"type": "text", "text": prompt}, *image_contents]})
    return messages


def call_once(client, api, effort, prefix_contents, image_contents, prompt):
    """发一次请求，返回 (耗时秒, reasoning tokens, 回复正文, 服务端 reasoning 字段)。"""
    options = {}
    if effort:
        # responses 与 chat/completions 都用统一的 reasoning 字段
        options["extra_body"] = {"reasoning": {"effort": effort}}
    if api == "responses":
        if effort:
            options["extra_body"] = {
                "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
                "reasoning": {"effort": effort},
            }
        else:
            options["extra_body"] = {"prompt_cache_options": {"mode": "explicit", "ttl": "30m"}}
        messages = _build_messages(prefix_contents, image_contents, prompt)
        started = time.monotonic()
        response = client.responses.create(
            model=DASHSCOPE_MODEL, input=_build_responses_input(messages), **options)
        elapsed = time.monotonic() - started
        usage = response.usage
        details = getattr(usage, "output_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None)
        text = response.output_text
        server_reasoning = getattr(response, "reasoning", None)
    else:
        messages = plain_messages(prefix_contents, image_contents, prompt)
        started = time.monotonic()
        response = client.chat.completions.create(
            model=DASHSCOPE_MODEL, messages=messages, **options)
        elapsed = time.monotonic() - started
        usage = response.usage
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None)
        text = response.choices[0].message.content
        server_reasoning = getattr(response.choices[0].message, "reasoning", None)
    return elapsed, reasoning, text, server_reasoning, usage


def main():
    parser = argparse.ArgumentParser(description="阶段2 识别接口/reasoning 的 A/B 测试")
    parser.add_argument("views", nargs="+", help="本轮各视角图片路径（个数据实际拍摄的视角数）")
    parser.add_argument("--variants", default="responses_default,responses_low,responses_minimal,chat_low",
                        help="逗号分隔，可选：" + "/".join(VARIANTS))
    parser.add_argument("--repeat", type=int, default=2, help="每个变体重复次数（默认 2）")
    args = parser.parse_args()

    names = [name.strip() for name in args.variants.split(",") if name.strip()]
    for name in names:
        if name not in VARIANTS:
            sys.exit(f"未知变体 {name}，可选：{'/'.join(VARIANTS)}")

    prefix_contents, image_contents = build_payload(args.views)
    prompt = "以下各张是本轮目标同一物体的不同视角。按前述规则只返回分类 JSON。"
    client = _get_client()

    results = {}
    for name in names:
        api, effort = VARIANTS[name]
        print(f"\n=== {name}（{api}, effort={effort or '服务端默认'}）===")
        times, reasonings = [], []
        for i in range(args.repeat):
            try:
                elapsed, reasoning, text, server_reasoning, usage = call_once(
                    client, api, effort, prefix_contents, image_contents, prompt)
            except Exception as error:  # noqa: BLE001 —— 测速工具，失败就报出来
                print(f"  第 {i + 1} 次失败: {type(error).__name__}: {error}")
                continue
            times.append(elapsed)
            reasonings.append(reasoning or 0)
            answer = (text or "").strip().replace("\n", " ")[:120]
            print(f"  第 {i + 1} 次: {elapsed:5.2f}s  reasoning={reasoning}  "
                  f"prompt={getattr(usage, 'input_tokens', getattr(usage, 'prompt_tokens', '?'))}  "
                  f"回复={answer}")
            if server_reasoning is not None:
                print(f"           服务端 reasoning 字段: {server_reasoning}")
        if times:
            results[name] = (statistics.median(times), sum(times) / len(times),
                             (sum(reasonings) / len(reasonings)) if reasonings else 0,
                             min(times), max(times), len(times))

    if not results:
        sys.exit("全部变体都失败了，检查网络/代理与 .env")

    print("\n================ 汇总 ================")
    print(f"{'变体':<20}{'中位耗时':>10}{'均值':>8}{'reasoning均值':>14}{'最快':>8}{'最慢':>8}{'样本':>6}")
    for name, (median, mean, reasoning, fastest, slowest, count) in results.items():
        print(f"{name:<20}{median:>9.2f}s{mean:>7.2f}s{reasoning:>13.0f}{fastest:>7.2f}s"
              f"{slowest:>7.2f}s{count:>6}")
    print("\n判读：先看 reasoning 均值掉了多少（对应生成阶段），再看中位耗时。")
    print("      中位耗时差 <1s 说明上游抖动仍占主导，需要更多样本才能区分。")


if __name__ == "__main__":
    main()
