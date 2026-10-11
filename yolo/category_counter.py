'''粗略类别数判断（阶段 1，大托盘）。

在阶段 1 观察托盘时，颜色模型（color_large_pallet）与形状模型（shape_large_pallet）
分别对同一帧检测物体，本模块按「位置 + 大小」把形状框与颜色框匹配到同一物体，
粗略统计「颜色+形状」一共出现多少种组合。

配合 main.py 使用：当粗略类别数达到 CATEGORY_COUNT_THRESHOLD（种类足够多）时，
阶段 2 跳过贴纸识别，直接按颜色+形状分类。

默认不启用（ENABLE_CATEGORY_PRE_CHECK=False），需要时打开。
'''

import numpy as np


def _to_xyxy(points):
    """四点框 (N,4,2) -> (N,4) 的 [x1, y1, x2, y2]。"""
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 4, 2)
    return np.concatenate([pts.min(axis=1), pts.max(axis=1)], axis=1)


def _iou(a, b):
    """a、b 均为 [x1, y1, x2, y2]，返回 IoU。"""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def match_color_shape(color_points, color_ids, shape_points, shape_ids,
                      iou_thresh=0.3):
    """把每个形状框匹配到与之重叠最大的颜色框。

    返回 [(color_id, shape_id), ...]。颜色、形状模型针对同一物体时框应近似重合，
    IoU 同时兼顾了位置与大小。匹配不上（IoU < iou_thresh）的框会被丢弃。
    """
    matched = []
    if len(color_points) == 0 or len(shape_points) == 0:
        return matched

    color_xyxy = _to_xyxy(color_points)
    shape_xyxy = _to_xyxy(shape_points)

    for si, sbox in enumerate(shape_xyxy):
        best_iou, best_ci = 0.0, -1
        for ci, cbox in enumerate(color_xyxy):
            iou = _iou(sbox, cbox)
            if iou > best_iou:
                best_iou, best_ci = iou, ci
        if best_ci >= 0 and best_iou >= iou_thresh:
            matched.append((int(color_ids[best_ci]), int(shape_ids[si])))
    return matched


def rough_category_count(color_points, color_ids, shape_points, shape_ids,
                         iou_thresh=0.3):
    """粗略统计「颜色+形状」的类别数。

    返回 (类别数, 组合集合)。无检测或匹配不上时返回 (0, set())。
    """
    matched = match_color_shape(color_points, color_ids, shape_points,
                                shape_ids, iou_thresh)
    combos = set(matched)
    return len(combos), combos
