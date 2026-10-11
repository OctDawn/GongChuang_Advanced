"""单次运行的形状图库。图片留档，下一次运行必须创建新的会话目录。"""

import json
import os
import re
import uuid
from pathlib import Path

import cv2
import numpy as np


class ShapeMemoryError(Exception):
    """图库不可用时禁止静默以空图库继续识别。"""


class ShapeMemory:
    def __init__(self, parent, limit=12, view_count=4):
        if not 1 <= limit <= 32:
            raise ValueError("LLM_SHAPE_MEMORY_LIMIT 必须在 1~32 之间")
        self.limit = limit
        if view_count < 1:
            raise ValueError("view_count 必须大于 0")
        self.view_count = view_count
        # 不载入旧会话，甚至同一秒启动也不会复用同一个目录。
        self.root = Path(parent) / ("shape_memory_" + uuid.uuid4().hex)
        self._records = []

    @property
    def records(self):
        return [dict(record) for record in self._records]

    def _write_json(self, path, value):
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _save_frames(self, directory, frames):
        directory.mkdir(parents=True, exist_ok=False)
        for i, frame in enumerate(frames):
            if not cv2.imwrite(str(directory / f"view_{i + 1}.jpg"), frame):
                raise ShapeMemoryError("参考图片写入失败")

    def add(self, description, frames):
        if len(self._records) >= self.limit:
            raise ShapeMemoryError(f"本次运行形状图库已满（{self.limit}），不删除历史、不强行合并")
        if not isinstance(description, str) or not description.strip() or len(description) > 400:
            raise ShapeMemoryError("新形状缺少有效几何描述")
        if len(frames) != self.view_count:
            raise ShapeMemoryError(f"登记形状需要 {self.view_count} 张图片")
        shape_id = f"unknown_{len(self._records) + 1:03d}"
        # 每次尝试使用不同目录；失败留下的诊断图片不妨碍后续重试。
        sample = shape_id + "_" + uuid.uuid4().hex
        record = {"id": shape_id, "description": description.strip(), "sample": sample}
        try:
            self._save_frames(self.root / sample, frames)
            candidate = self._records + [record]
            self._write_json(self.root / "memory.json", {"version": 1, "shapes": candidate})
        except (OSError, cv2.error) as error:
            raise ShapeMemoryError(f"形状记忆保存失败: {error}") from error
        # 图片及索引全部成功才使 ID 生效；不会覆盖已有原型导致记忆漂移。
        self._records = candidate
        return shape_id

    def reference_images(self):
        images = []
        for record in self._records:
            if not re.fullmatch(r"unknown_\d{3}_[0-9a-f]{32}", record["sample"]):
                raise ShapeMemoryError("参考图路径不合法")
            cells = []
            for i in range(self.view_count):
                path = self.root / record["sample"] / f"view_{i + 1}.jpg"
                frame = cv2.imread(str(path))
                if frame is None:
                    raise ShapeMemoryError(f"历史参考图片缺失或损坏: {path}")
                # 等比缩放留黑边；每格 384×384，按 2 列拼图（视角数为奇数时补一格黑）。
                h, w = frame.shape[:2]
                scale = min(384 / w, 384 / h)
                small = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))))
                cell = np.zeros((384, 384, 3), dtype=np.uint8)
                sh, sw = small.shape[:2]
                y, x = (384 - sh) // 2, (384 - sw) // 2
                cell[y:y + sh, x:x + sw] = small
                cells.append(cell)
            if len(cells) == 1:
                images.append(cells[0])
            else:
                if len(cells) % 2:
                    cells.append(np.zeros_like(cells[0]))
                images.append(np.vstack([np.hstack(cells[i:i+2]) for i in range(0, len(cells), 2)]))
        return images

    def save_review(self, frames, reason, decisions):
        """待复核图片独立留档，不加入参考图库。"""
        directory = self.root / "pending" / uuid.uuid4().hex
        try:
            self._save_frames(directory, frames)
            self._write_json(directory / "review.json", {"reason": reason, "decisions": decisions})
        except (OSError, cv2.error) as error:
            raise ShapeMemoryError(f"待复核现场保存失败: {error}") from error
        return str(directory)
