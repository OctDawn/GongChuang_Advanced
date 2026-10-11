"""用抓下来的真实请求体，测"图片张数 → 延迟"的关系。

前置：先抓一份真实 body（在板子上、项目根目录）：
    LLM_DEBUG_TIMING=1 LLM_DEBUG_DUMP=/tmp/real_req.json \
      python test_llm.py classify "$DIR"/stage2_r010_0*.jpg --runs 1

用法：
    python3 tools/probe_images.py /tmp/real_req.json [每档重复次数=2]

做法：只裁掉"参考图"（前缀里的示例图），本轮各张视角图保持不动，
逐档测总耗时 + prompt_tokens + cached_tokens，看张数对延迟的影响。
注意：这纯粹是**计时探针**——删了参考图模型可能答错，我们只看时间不看答案。
纯标准库（urllib），自动读 .env 里的 key 与 HTTPS_PROXY。
"""

import copy
import json
import os
import statistics
import sys
import time
import urllib.request

URL = "https://openrouter.ai/api/v1/responses"


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def output_text(response):
    text = response.get("output_text") or ""
    if text:
        return text
    for item in response.get("output", []) or []:
        for chunk in item.get("content", []) or []:
            text += chunk.get("text", "") or ""
    return text


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    load_env()
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        sys.exit("读不到 DASHSCOPE_API_KEY（.env 里没有？）")
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    path = sys.argv[1]
    body = json.load(open(path, encoding="utf-8"))
    messages = body["input"]
    positions = [
        (mi, bi)
        for mi, message in enumerate(messages)
        if isinstance(message.get("content"), list)
        for bi, block in enumerate(message["content"])
        if block.get("type") == "input_image"
    ]
    last = len(messages) - 1
    refs = [p for p in positions if p[0] != last]
    views = [p for p in positions if p[0] == last]
    print(f"请求体 {os.path.getsize(path)/1024:.0f} KB，每档重复 {runs} 次")
    print(f"图片总数={len(positions)}  参考图={len(refs)}  本轮视角图={len(views)}\n")

    def post(payload):
        request = urllib.request.Request(
            URL, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        started = time.time()
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
        return time.time() - started, json.loads(raw)

    header = f"{'保留参考图':>10}{'总图数':>8}{'中位耗时':>11}{'最快':>9}{'prompt':>9}{'cached':>9}  答案"
    print(header)
    print("-" * len(header))

    for keep in sorted({len(refs), 3, 2, 1, 0}, reverse=True):
        if keep > len(refs):
            continue
        payload = copy.deepcopy(body)
        for mi, bi in sorted(refs[: len(refs) - keep], reverse=True):
            del payload["input"][mi]["content"][bi]

        times, usage, answer = [], {}, ""
        for attempt in range(runs):
            try:
                duration, response = post(payload)
            except Exception as error:  # noqa: BLE001 —— 探针脚本，出错就报出来
                print(f"{keep:>10}{'-':>8}  第 {attempt + 1} 次失败: {error}")
                break
            times.append(duration)
            usage = response.get("usage") or {}
            answer = output_text(response).strip().replace("\n", " ")[:48]
        if times:
            cached = ((usage.get("input_tokens_details") or {}).get("cached_tokens", "?"))
            print(f"{keep:>10}{keep + len(views):>8}{statistics.median(times):>10.2f}s"
                  f"{min(times):>8.2f}s{usage.get('input_tokens', '?'):>9}{cached:>9}  {answer}")

    print("\n提示：参考图一变，前缀缓存就失效（cached_tokens 会变 0），所以主要看第 2 次及以后。")


if __name__ == "__main__":
    main()
