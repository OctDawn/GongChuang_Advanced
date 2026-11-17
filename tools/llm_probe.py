"""探测 LLM_CHAIN 里每一家能不能用（启动前自检，不发图、不花时间）。

用法（项目根目录）：
    python tools/llm_probe.py
    python tools/llm_probe.py --timeout 3
    python tools/llm_probe.py --image capture/images/xxx/original/stage2_r001_01.jpg

不带 --image 时只发一条 "ping" 纯文本请求，用于确认 key / 端点 / 代理是否通；
带 --image 时再额外发一次真实图片请求，能同时验证模型是否接受图像输入。

退出码 = 不可用的 provider 个数（0 = 全部可用）。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()      # 必须在 llm.qw 之前

import cv2      # noqa: E402
import numpy as np  # noqa: E402

from llm import providers  # noqa: E402
from llm import qw  # noqa: E402


def _imread(path):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=2.5, help="单家探测超时（秒）")
    parser.add_argument("--image", default="", help="可选：再发一次真实图片请求")
    args = parser.parse_args()

    if not providers.configured():
        print("LLM_CHAIN 未配置：当前是单 provider 模式（行为与改造前一致）。")
        print("要启用链，请在 .env 里加 LLM_CHAIN=openai,qwen 及各家的 LLM_<NAME>_* 配置。")
        print("单 provider 链路请用：bash tools/net_check.sh")
        return 0

    print("链顺序：", providers.describe())
    print()

    import llm.chain as chain

    results = chain.probe(timeout=args.timeout)
    bad = 0
    for provider, ok, seconds, message in results:
        mark = "OK  " if ok else "FAIL"
        print("[%s] %-28s %5.2fs  %s" % (mark, provider.label, seconds, message))
        if not ok:
            bad += 1

    if args.image:
        frame = _imread(args.image)
        if frame is None:
            print("\n读不出图片：%s" % args.image)
            return bad + 1
        print("\n真实图片请求（走当前可用链）：")
        try:
            result = qw.qwen_realise(
                frame, '只回复 JSON：{"ok": true}', total_timeout=max(10.0, args.timeout * 3))
            print("  返回:", result)
        except Exception as error:  # noqa: BLE001
            print("  失败:", error)
            bad += 1

    print()
    if bad == 0:
        print("全部可用：%d 家" % len(results))
    else:
        print("有 %d 家不可用（已进入冷却，运行时会自动跳过）" % bad)
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
