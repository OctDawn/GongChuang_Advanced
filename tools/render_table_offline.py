"""离屏渲染分拣结果表格，用来在没有电控、没有相机的情况下检查显示排版。

为什么需要：正常验证表格要电控发 `v` 才有内容，赛前很难凑齐。
这里直接调用 `shared/show.py` 的绘制函数（同一个 `_draw_table`），
在内存里画到指定分辨率并存成 PNG，看图和真机一致。

用法（板上，项目根目录）：

    python tools/render_table_offline.py                       # 1920x1080
    python tools/render_table_offline.py --size 800x480        # 对比掉分辨率时的样子
    python tools/render_table_offline.py --size 1920x1080 --out /tmp/a.png --image 某图.jpg

不需要相机和串口；用 SDL 的 dummy 驱动，不会占用真实屏幕。
"""
import argparse
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()

import cv2      # noqa: E402
import numpy as np  # noqa: E402

from shared.const import MAX_TEMP_CATEGORIES  # noqa: E402


def _parse_size(text):
    try:
        width, height = (int(part) for part in text.lower().split("x", 1))
    except ValueError as error:
        raise SystemExit("--size 要写成 宽x高，例如 1920x1080") from error
    if width < 320 or height < 200:
        raise SystemExit("--size 太小（至少 320x200）")
    return width, height


def _load_image(path):
    """读一张真实货物图当「图片」列样本；读不到就留空。"""
    candidates = []
    if path:
        candidates.append(path)
    candidates += [
        "llm/example/orange_cylinder_thinwalled_side.jpg",
        "llm/example/orange_cylinder_thinwalled_mirror.jpg",
    ]
    for candidate in candidates:
        if not candidate or not os.path.exists(candidate):
            continue
        data = np.fromfile(candidate, dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is not None:
            print("图片样本:", candidate)
            return image
    print("图片样本: 无（图片列留白）")
    return None


def main():
    parser = argparse.ArgumentParser(description="离屏渲染分拣结果表格")
    parser.add_argument("--size", default="1920x1080", help="宽x高，默认 1920x1080")
    parser.add_argument("--out", default="", help="输出 PNG 路径，默认 /tmp/table_<size>.png")
    parser.add_argument("--image", default="", help="图片列用的样图（默认取 llm/example 里的通心筒）")
    args = parser.parse_args()

    width, height = _parse_size(args.size)
    out = args.out or "/tmp/table_%dx%d.png" % (width, height)

    import pygame  # noqa: E402 —— 必须在设置 SDL_VIDEODRIVER 之后导入

    from shared.show import (  # noqa: E402
        BASE_BODY_FONT, BASE_HEADER_FONT, BASE_TITLE_FONT,
        RecognitionRow, _draw_table, _load_font,
    )

    pygame.init()
    screen = pygame.display.set_mode((width, height))
    scale = max(1.0, height / 720.0)
    fonts = (_load_font(int(BASE_TITLE_FONT * scale)),
             _load_font(int(BASE_HEADER_FONT * scale)),
             _load_font(int(BASE_BODY_FONT * scale)))

    picture = _load_image(args.image)
    names = ["橙色通心筒", "蓝色正方体", "红色三棱柱（薄壁）", "绿色球", "黄色四棱锥", "黑色六棱柱"]
    totals = [4, 3, 2, 4, 1, 0]
    rows = [RecognitionRow(index=i + 1, name=names[i],
                           image=picture if i % 2 == 0 else None,
                           total=totals[i])
            for i in range(MAX_TEMP_CATEGORIES)]

    _draw_table(screen, rows, fonts, MAX_TEMP_CATEGORIES)
    pygame.image.save(screen, out)
    pygame.display.quit()
    print("已保存:", out, "%dx%d" % (width, height))
    if height < 720:
        print("提示: <720p 时标题会压到表头、行高明显变挤（2026-11-23 实测）")


if __name__ == "__main__":
    main()
