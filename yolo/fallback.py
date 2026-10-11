"""LLM 失败后使用同轮照片执行 YOLO，不发送串口或修改业务记忆。"""

import logging
from contextlib import ExitStack

import numpy as np

from shared.common import column_occupancy, sticker_decode_enabled
from shared.const import (
    PALLET_MODEL_PATH, PALLET_COLOR_CLASSES,
    DETECT_AREA_COLOR_MODEL_PATH, DETECT_AREA_COLOR_CLASSES,
    DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES,
    DETECT_AREA_LABEL_MODEL_PATH, LABEL_CLASSES, LABEL_DECODE_TYPES,
    PRIVILEGED_SHAPES, PRIVILEGED_SHAPE_CONFIDENCE,
    STAGE_TWO_DETECTION_COUNT,
)
from yolo.decoder import crop_box, decode_sticker

logger = logging.getLogger("wave.vision")


class YoloFallback:
    def __init__(self, pallet_runner, color_runner, shape_runner, label_runner):
        self.pallet_runner = pallet_runner
        self.color_runner = color_runner
        self.shape_runner = shape_runner
        self.label_runner = label_runner

    @classmethod
    def load(cls, stack, runner_factory=None):
        """在硬件线程启动前加载；失败或退出时由调用方 ExitStack 释放模型。"""
        if runner_factory is None:
            from yolo.inference_utils import DetectRunner
            runner_factory = DetectRunner
        # 局部 stack 保证第 N 个模型失败时，前 N-1 个也释放。
        with ExitStack() as loading:
            runners = []
            for path, classes in (
                (PALLET_MODEL_PATH, PALLET_COLOR_CLASSES),
                (DETECT_AREA_COLOR_MODEL_PATH, DETECT_AREA_COLOR_CLASSES),
                (DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES),
                (DETECT_AREA_LABEL_MODEL_PATH, LABEL_CLASSES),
            ):
                logger.info("预加载 YOLO fallback 模型: %s", path)
                runner = runner_factory(path, classes)
                loading.callback(runner.release)
                runners.append(runner)
            stack.enter_context(loading.pop_all())
        return cls(*runners)

    def check_columns(self, pallet_frame, column_count=None):
        """输入托盘原图；栏数默认共用配置，也可由新协议单独指定。"""
        points, _ = self.pallet_runner.infer(pallet_frame)
        if column_count is None:
            return column_occupancy(points, pallet_frame.shape)
        return column_occupancy(points, pallet_frame.shape, column_count)

    def classify(self, frames, expected_count=STAGE_TWO_DETECTION_COUNT, include_labels=True):
        """累计置信度、薄壁优先及贴纸解码；None 允许固定机位的实际视角数。"""
        if not frames or (expected_count is not None and len(frames) != expected_count):
            raise ValueError(f"YOLO fallback 需要 {expected_count} 个视角的照片（实际 {len(frames)} 张）")
        color_sums = np.zeros(len(DETECT_AREA_COLOR_CLASSES), dtype=np.float32)
        shape_sums = np.zeros(len(SHAPE_CLASSES), dtype=np.float32)
        label_sums = np.zeros(len(LABEL_CLASSES), dtype=np.float32)
        label_best = np.full(len(LABEL_CLASSES), -1.0, dtype=np.float32)
        label_crops = [None] * len(LABEL_CLASSES)
        privileged_id, privileged_score = None, -1.0

        for frame in frames:
            _, color_ids, color_scores = self.color_runner.infer(frame, return_scores=True)
            _, shape_ids, shape_scores = self.shape_runner.infer(frame, return_scores=True)
            if include_labels:
                points, label_ids, label_scores = self.label_runner.infer(frame, return_scores=True)
            else:
                points, label_ids, label_scores = [], [], []
            for cid, score in zip(color_ids, color_scores):
                cid = int(cid)
                if 0 <= cid < len(color_sums):
                    color_sums[cid] += float(score)
            for sid, score in zip(shape_ids, shape_scores):
                sid, score = int(sid), float(score)
                if 0 <= sid < len(shape_sums):
                    shape_sums[sid] += score
                    if (SHAPE_CLASSES[sid] in PRIVILEGED_SHAPES
                            and score >= PRIVILEGED_SHAPE_CONFIDENCE
                            and score > privileged_score):
                        privileged_id, privileged_score = sid, score
            for pts, lid, score in zip(points, label_ids, label_scores):
                lid, score = int(lid), float(score)
                if 0 <= lid < len(label_sums):
                    label_sums[lid] += score
                    if score > label_best[lid]:
                        label_best[lid] = score
                        label_crops[lid] = crop_box(frame, pts)

        if not np.any(color_sums > 0) or not np.any(shape_sums > 0):
            return None, None, None
        color = DETECT_AREA_COLOR_CLASSES[int(np.argmax(color_sums))]
        shape = SHAPE_CLASSES[privileged_id if privileged_id is not None
                              else int(np.argmax(shape_sums))]
        label = None
        if np.any(label_sums > 0):
            lid = int(np.argmax(label_sums))
            label = LABEL_CLASSES[lid]
            if label in LABEL_DECODE_TYPES and sticker_decode_enabled():
                label = decode_sticker(label_crops[lid], label) or label
        return color, shape, label
