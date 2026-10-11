#!/usr/bin/env python3
"""LLM 接口兼容性探针：确认 base_url / model / key 可用，并逐个验证
service_tier、reasoning、缓存字段是否被服务端接受。

为什么要这个：从 OpenRouter 换成 OpenAI 官方（或其他服务）时，字段名和支持范围不一样：
  * Responses API 的显式缓存字段（prompt_cache_options / prompt_cache_breakpoint）是转发服务扩展，
    官方 API 不认识，会 400；
  * Chat Completions 的推理字段是平铺的 reasoning_effort，而不是 Responses 的 reasoning={effort}；
  * service_tier 各家的取值不同（官方为 auto/default/flex/priority，ultrafast 可能是转发服务特有）。
本脚本每个变体只发一个极小的纯文本请求，成本可忽略，几秒出结果。

用法（在板子或 PC 上，项目根目录）：
    python tools/probe_openai_api.py                     # 读环境变量 / .env
    python tools/probe_openai_api.py --tier ultrafast
    python tools/probe_openai_api.py --model gpt-6-astra --base-url https://api.openai.com/v1
"""

from __future__ import annotations

import argparse
import base64
import os
import struct
import sys
import time
import zlib
from pathlib import Path

PROMPT = "只回复两个字：收到"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def make_tiny_png(size: int = 16, rgb: tuple = (220, 30, 30)) -> str:
    """用标准库现场生成一张合法 PNG 的 data URL。

    不依赖 cv2/PIL，避免手写 base64 出错（手写的那串会被 API 判为“不是有效图片”）。
    """
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    png = (b"\x89PNG\r\n\x1a\n"
           + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + _png_chunk(b"IDAT", zlib.compress(raw, 9))
           + _png_chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def mask(value: str) -> str:
    if not value:
        return "<空>"
    return value[:7] + "…" + value[-4:] if len(value) > 14 else value[:4] + "…"


def run_case(title: str, fn):
    start = time.time()
    try:
        response = fn()
        tier = getattr(response, "service_tier", None)
        usage = getattr(response, "usage", None)
        detail = ""
        if usage is not None:
            tokens_in = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
            tokens_out = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
            detail = f"，tokens {tokens_in}/{tokens_out}"
        print(f"  [OK ] {title}（{time.time() - start:.2f}s，service_tier={tier}{detail}）")
        return True
    except Exception as error:  # noqa: BLE001 - 探针就是要看各种异常
        message = str(error).replace("\n", " ")[:220]
        status = getattr(error, "status_code", None)
        print(f"  [!! ] {title}（{time.time() - start:.2f}s，status={status}）")
        print(f"        {type(error).__name__}: {message}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 接口兼容性探针")
    parser.add_argument("--base-url", default=os.environ.get("DASHSCOPE_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("DASHSCOPE_MODEL", ""))
    parser.add_argument("--tier", default=os.environ.get("LLM_SERVICE_TIER", "ultrafast"))
    parser.add_argument("--effort", default=os.environ.get("LLM_REASONING_EFFORT", "low"))
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))
    args.base_url = args.base_url or os.environ.get("DASHSCOPE_BASE_URL", "") or "https://api.openai.com/v1"
    args.model = args.model or os.environ.get("DASHSCOPE_MODEL", "") or "gpt-6-astra"
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")

    print("=== 配置 ===")
    print(f"  base_url = {args.base_url}")
    print(f"  model    = {args.model}")
    print(f"  key      = {mask(api_key)}（来自 DASHSCOPE_API_KEY）")
    print(f"  代理     = HTTPS_PROXY={os.environ.get('HTTPS_PROXY', '<未设>')}")
    if not api_key:
        sys.exit("缺少 DASHSCOPE_API_KEY（.env 或环境变量）")

    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("当前 Python 环境里没有 openai SDK（板上应在 conda 环境 llm 里运行）")

    client = OpenAI(base_url=args.base_url, api_key=api_key, timeout=30, max_retries=0)
    messages = [{"role": "user", "content": PROMPT}]

    print("\n=== 1) Chat Completions /chat/completions ===")
    print("  （注意：这个模型不接受 max_tokens，必须用 max_completion_tokens）")
    run_case("基础调用", lambda: client.chat.completions.create(
        model=args.model, messages=messages, max_completion_tokens=16))
    run_case(f"service_tier={args.tier}", lambda: client.chat.completions.create(
        model=args.model, messages=messages, max_completion_tokens=16,
        extra_body={"service_tier": args.tier}))
    run_case(f"reasoning_effort={args.effort}", lambda: client.chat.completions.create(
        model=args.model, messages=messages, max_completion_tokens=16,
        extra_body={"reasoning_effort": args.effort}))
    run_case(f"service_tier + reasoning_effort（本仓库 portable 模式的组合）",
             lambda: client.chat.completions.create(
                 model=args.model, messages=messages, max_completion_tokens=16,
                 extra_body={"service_tier": args.tier, "reasoning_effort": args.effort}))

    print("\n=== 2) Responses /responses ===")
    run_case("基础调用", lambda: client.responses.create(
        model=args.model, input=PROMPT, max_output_tokens=16))
    run_case(f"service_tier + reasoning={{{args.effort}}}（本仓库 openai_explicit 模式的组合）",
             lambda: client.responses.create(
                 model=args.model, input=PROMPT, max_output_tokens=16,
                 extra_body={"service_tier": args.tier,
                             "reasoning": {"effort": args.effort}}))
    run_case("prompt_cache_options（转发服务扩展，官方预期会 400）",
             lambda: client.responses.create(
                 model=args.model, input=PROMPT, max_output_tokens=16,
                 extra_body={"prompt_cache_options": {"mode": "explicit", "ttl": "30m"}}))

    print("\n=== 3) 真实请求形状（本项目真正发出去的两种块）===")
    tiny_png = make_tiny_png()
    run_case("content block 里带 prompt_cache_breakpoint（openai_explicit 模式会加）",
             lambda: client.responses.create(
                 model=args.model, max_output_tokens=16,
                 input=[{"role": "user", "content": [
                     {"type": "input_text", "text": PROMPT,
                      "prompt_cache_breakpoint": {"mode": "explicit"}}]}]))
    run_case("input_image 块（现场生成的合法 PNG，验证图片块形状）",
             lambda: client.responses.create(
                 model=args.model, max_output_tokens=16,
                 input=[{"role": "user", "content": [
                     {"type": "input_text", "text": "这张图什么颜色？两个字"},
                     {"type": "input_image", "image_url": tiny_png, "detail": "auto"}]}]))
    run_case("input_image + prompt_cache_breakpoint（最接近真实请求）",
             lambda: client.responses.create(
                 model=args.model, max_output_tokens=16,
                 extra_body={"service_tier": args.tier, "reasoning": {"effort": args.effort}},
                 input=[{"role": "user", "content": [
                     {"type": "input_text", "text": PROMPT,
                      "prompt_cache_breakpoint": {"mode": "explicit"}},
                     {"type": "input_image", "image_url": tiny_png, "detail": "auto"}]}]))

    print("\n=== 结论怎么读 ===")
    print("  * Responses 组全绿 → LLM_CACHE_MODE 保持 openai_explicit（本仓库默认）；")
    print("    Chat Completions 组若报 'Invalid service_tier argument'，说明该端点不接受这个档位，")
    print("    此时 portable 模式即使可用也拿不到 ultrafast。")
    print("  * 第 3 组是真实请求的形状（content block 级缓存断点 + input_image），")
    print("    这三条必须全绿，主流程才不会在真机上 400。")
    print("  * 看 service_tier= 是否等于你请求的档位；若变成 default/priority，说明被服务端降级了")
    print("    （运行时也会打在同一处日志：'LLM 响应: … service_tier='）。")


if __name__ == "__main__":
    main()
