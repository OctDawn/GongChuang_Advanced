"""按 HSV 判颜色：在 YOLO 给出的框内统计主色，弥补颜色模型把橙色判成棕色的问题。

为什么需要它：YOLO 颜色头在真实图上会输出 brown/deep_blue 这类近色类，且浅色件直接检不出；
而形状框是可靠的，框内像素的 HSV 统计比整帧分类稳定得多。

约定：输入是 OpenCV 的 BGR 图（与项目其它部分一致），H 用 OpenCV 的 0~179 范围。
返回值为 12 色词表里的名字；拿不准时返回 None（调用方决定回退策略）。
"""

import cv2
import numpy as np

# OpenCV: H 0~179, S/V 0~255
_COLOR_NAMES = (
    "black", "blue", "brown", "deep_blue", "deep_green", "green",
    "orange", "pink", "purple", "red", "white", "yellow",
)


def _name_from_hsv(h, s, v):
    """把中位 HSV 映射到颜色名。阈值按实物（3D 打印件 + 顶光）标定。"""
    if v < 45:
        return "black"
    if s < 35:
        return "white" if v >= 140 else "gray"
    # 色相分段
    if h < 8 or h >= 168:
        # 红 / 粉：低饱和偏粉
        return "pink" if s < 120 else "red"
    if h < 20:
        # 橙 / 棕：只差明度
        return "brown" if v < 110 else "orange"
    if h < 33:
        return "yellow"
    if h < 85:
        return "green"
    if h < 130:
        return "blue"
    return "purple"


def classify_color_from_bbox(frame, box, shrink=0.18):
    """frame: BGR; box: (x1, y1, x2, y2)。返回 (颜色名或None, 统计字典)。

    先向内收缩 shrink，避开框边缘的背景/阴影；再剔除高光（V 很高且 S 很低）。
    """
    if frame is None or box is None:
        return None, {}
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [float(value) for value in box[:4]]
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    w, h = x2 - x1, y2 - y1
    if w < 8 or h < 8:
        return None, {}
    x1 += w * shrink
    x2 -= w * shrink
    y1 += h * shrink
    y2 -= h * shrink
    x1 = max(0, min(width - 1, int(round(x1))))
    x2 = max(1, min(width, int(round(x2))))
    y1 = max(0, min(height - 1, int(round(y1))))
    y2 = max(1, min(height, int(round(y2))))
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None, {}

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hh = hsv[..., 0].astype(np.float32)
    ss = hsv[..., 1].astype(np.float32)
    vv = hsv[..., 2].astype(np.float32)

    # 去掉镜面高光（几乎纯白）与过暗的阴影
    keep = ~(((vv > 246) & (ss < 30)) | (vv < 12))
    if keep.sum() < 40:
        keep = np.ones_like(vv, dtype=bool)
    h = float(np.median(hh[keep]))
    s = float(np.median(ss[keep]))
    v = float(np.median(vv[keep]))
    stats = {"h": round(h, 1), "s": round(s, 1), "v": round(v, 1),
             "pixels": int(keep.sum())}
    return _name_from_hsv(h, s, v), stats


def classify_color_center(frame, ratio=0.45):
    """没有框时的兜底：取画面中心区域。"""
    if frame is None:
        return None, {}
    height, width = frame.shape[:2]
    cx, cy = width // 2, height // 2
    half_w, half_h = int(width * ratio / 2), int(height * ratio / 2)
    box = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
    return classify_color_from_bbox(frame, box, shrink=0.0)
