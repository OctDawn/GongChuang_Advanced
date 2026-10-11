'''后处理依赖文件'''

import logging
import math
import os

import cv2
import numpy as np

from shared.const import PROJECT_ROOT
from .py_utils.rknn_executor import RKNN_model_container

logger = logging.getLogger("wave.vision")


IMG_SIZE = (640, 640)
OBJ_THRESH = 0.2
NMS_THRESH = 0.5
BRANCH_INFO = {80: (8, 0), 40: (16, 6400), 20: (32, 8000)}


def letterbox_resize(image, size, bg_color):
    target_width, target_height = size
    image_height, image_width = image.shape[:2]
    ratio = min(target_width / image_width, target_height / image_height)
    new_size = (int(image_width * ratio), int(image_height * ratio))
    resized = cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)

    result = np.full((target_height, target_width, 3), bg_color, dtype=np.uint8)
    offset_x = (target_width - new_size[0]) // 2
    offset_y = (target_height - new_size[1]) // 2
    result[offset_y:offset_y + new_size[1], offset_x:offset_x + new_size[0]] = resized
    return result, ratio, offset_x, offset_y


class DetectBox:
    def __init__(self, class_id, score, xmin, ymin, xmax, ymax, angle):
        self.classId = class_id
        self.score = score
        self.xmin = xmin
        self.ymin = ymin
        self.xmax = xmax
        self.ymax = ymax
        self.angle = angle


def rectangle_points(x1, y1, x2, y2):
    return np.array([(x1, y1), (x1, y2), (x2, y2), (x2, y1)], np.float32)


def rotate_rectangle(x1, y1, x2, y2, angle):
    center = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
    points = rectangle_points(x1, y1, x2, y2)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    rotation = np.array([[cos_a, -sin_a], [sin_a, cos_a]])
    return ((points - center) @ rotation.T + center).astype(np.float32)


def intersection(first, second):
    """计算两个旋转矩形的 IoU。"""
    first = np.ascontiguousarray(first, np.float32)
    second = np.ascontiguousarray(second, np.float32)
    inter, _ = cv2.intersectConvexConvex(first, second)
    union = cv2.contourArea(first) + cv2.contourArea(second) - inter
    return inter / union if union else 0


def nms(detections):
    detections = sorted(detections, key=lambda box: box.score, reverse=True)
    kept = []
    for i, box in enumerate(detections):
        if box.classId == -1:
            continue
        kept.append(box)
        box_points = rotate_rectangle(
            box.xmin, box.ymin, box.xmax, box.ymax, box.angle
        )
        for other in detections[i + 1:]:
            if box.classId != other.classId:
                continue
            other_points = rotate_rectangle(
                other.xmin, other.ymin, other.xmax, other.ymax, other.angle
            )
            if intersection(box_points, other_points) > NMS_THRESH:
                other.classId = -1
    return kept


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def softmax(x, axis=-1):
    exp_x = np.exp(x - np.max(x, axis=axis, keepdims=True))
    return exp_x / np.sum(exp_x, axis=axis, keepdims=True)


def process(out, model_w, model_h, stride, angle_feature, angle_index):
    angle_feature = angle_feature.reshape(-1)
    box_feature = out[:, :64, :]
    confidence = sigmoid(out[:, 64:, :]).reshape(-1)
    detections = []

    for candidate in np.flatnonzero(confidence > OBJ_THRESH):
        w = candidate % model_w
        h = candidate % (model_w * model_h) // model_w
        class_id = candidate // (model_w * model_h)

        box = box_feature[0, :, h * model_w + w].reshape(1, 4, 16, 1)
        box = softmax(box, 2) * np.arange(16).reshape(1, 1, 16, 1)
        box = np.sum(box, axis=2).reshape(-1)
        box_size = box[:2] + box[2:]
        center_offset = (box[2:] - box[:2]) / 2

        angle = (angle_feature[angle_index + h * model_w + w] - 0.25) * math.pi
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        offset_x = center_offset[0] * cos_a - center_offset[1] * sin_a
        offset_y = center_offset[0] * sin_a + center_offset[1] * cos_a
        decoded = np.array([
            offset_x + w + 0.5, offset_y + h + 0.5,
            box_size[0], box_size[1],
        ]) * stride

        detections.append(DetectBox(
            class_id, confidence[candidate],
            decoded[0] - decoded[2] / 2, decoded[1] - decoded[3] / 2,
            decoded[0] + decoded[2] / 2, decoded[1] + decoded[3] / 2,
            angle,
        ))
    return detections


def infer_class_count(outputs):
    """从模型输出形状推断类别数，兼容两种常见导出。

    (1, 64+nc, N)：单张量输出，返回 shape[1] - 64。
    (1, nc, h, w)：分类分支单独输出（本仓库当前用的 OBB 导出），取非 64 通道的那个。
    推断不出时返回 None（仅用于校验提示，不影响推理）。
    """
    for output in outputs:
        shape = output.shape
        if len(shape) == 3 and shape[1] > 64:
            return int(shape[1] - 64)
    candidates = [int(o.shape[1]) for o in outputs
                  if len(o.shape) == 4 and int(o.shape[1]) != 64]
    if candidates:
        return max(set(candidates), key=candidates.count)
    return None


def detect_process(outputs):
    detections = []
    pair_per_branch = len(outputs) // 3

    for branch in range(3):
        position = outputs[pair_per_branch * branch]
        class_scores = outputs[pair_per_branch * branch + 1]
        grid_h, grid_w = position.shape[2:]

        box = position.reshape(1, 4, 16, grid_h, grid_w)
        box = softmax(box, 2) * np.arange(16).reshape(1, 1, 16, 1, 1)
        box = np.sum(box, axis=2)

        col, row = np.meshgrid(np.arange(grid_w), np.arange(grid_h))
        grid = np.stack((col, row)).reshape(1, 2, grid_h, grid_w) + 0.5
        stride = np.array([
            IMG_SIZE[0] / grid_w, IMG_SIZE[1] / grid_h,
        ]).reshape(1, 2, 1, 1)
        boxes = np.concatenate((
            (grid - box[:, :2]) * stride,
            (grid + box[:, 2:]) * stride,
        ), axis=1).transpose(0, 2, 3, 1).reshape(-1, 4)

        scores = class_scores.transpose(0, 2, 3, 1).reshape(-1, class_scores.shape[1])
        class_ids = np.argmax(scores, axis=1)
        confidence = np.max(scores, axis=1)

        for index in np.flatnonzero(confidence > OBJ_THRESH):
            detections.append(DetectBox(
                int(class_ids[index]), confidence[index], *boxes[index], 0,
            ))

    return nms(detections)


class OBBRunner:
    def __init__(self, model_path, classes):
        # 相对模型路径以项目根目录为基准
        if not os.path.isabs(model_path):
            model_path = os.path.join(PROJECT_ROOT, model_path)
        self.model = RKNN_model_container(model_path)
        # 类别顺序必须与训练模型使用的 data.yaml 一致
        self.classes = classes

    def infer(self, image, draw=False):
        """输入 BGR uint8 图片 (H,W,3)，输出角度为 rad、类别为 ID。"""
        input_image, ratio, offset_x, offset_y = letterbox_resize(
            image, IMG_SIZE, 114
        )
        # RK3588 板端输入需要 batch 维，并保证内存连续
        input_data = np.expand_dims(input_image[..., ::-1], axis=0)
        input_data = np.ascontiguousarray(input_data, dtype=np.uint8)
        outputs = self.model.run([input_data])
        detections = []

        for output in outputs[:-1]:
            grid_size = output.shape[2]
            stride, angle_index = BRANCH_INFO[grid_size]
            detections.extend(process(
                output.reshape(1, output.shape[1], -1),
                output.shape[3], grid_size, stride, outputs[-1], angle_index,
            ))

        rotated_points, original_points, angles, class_ids, boxes = [], [], [], [], []
        for box in nms(detections):
            xmin = (box.xmin - offset_x) / ratio
            ymin = (box.ymin - offset_y) / ratio
            xmax = (box.xmax - offset_x) / ratio
            ymax = (box.ymax - offset_y) / ratio
            original_points.append(rectangle_points(xmin, ymin, xmax, ymax))
            rotated_points.append(rotate_rectangle(
                xmin, ymin, xmax, ymax, box.angle
            ))
            angles.append(box.angle)
            class_ids.append(box.classId)
            boxes.append(box)

        rotated_points = np.asarray(rotated_points, np.float32).reshape(-1, 4, 2)
        original_points = np.asarray(original_points, np.float32).reshape(-1, 4, 2)
        angles = np.asarray(angles, np.float32)
        class_ids = np.asarray(class_ids, np.int32)
        if not draw:
            return rotated_points, original_points, angles, class_ids

        result = image.copy()
        for box, points in zip(boxes, rotated_points):
            points_int = np.rint(points).astype(np.int32)
            cv2.polylines(result, [points_int], True, (0, 255, 0), 2)
            class_name = self.classes[box.classId]
            label = "%s %.2f %.1fdeg" % (
                class_name, box.score, math.degrees(box.angle)
            )
            cv2.putText(
                result, label, tuple(points_int[0]), cv2.FONT_HERSHEY_DUPLEX,
                0.5, (0, 0, 255), 1,
            )
        return rotated_points, original_points, angles, class_ids, result

    def release(self):
        self.model.release()


class DetectRunner:
    def __init__(self, model_path, classes):
        # 相对模型路径以项目根目录为基准
        if not os.path.isabs(model_path):
            model_path = os.path.join(PROJECT_ROOT, model_path)
        self.model_path = model_path
        self.model = RKNN_model_container(model_path)
        # 类别顺序必须与训练模型使用的 data.yaml 一致
        self.classes = classes
        self._class_count_checked = False

    def infer(self, image, draw=False, return_scores=False):
        """输入 BGR 图片；按需返回矩形四点、类别 ID、置信度和绘制结果。"""
        input_image, ratio, offset_x, offset_y = letterbox_resize(
            image, IMG_SIZE, 114
        )
        # RK3588 板端输入需要 batch 维，并保证内存连续
        input_data = np.expand_dims(input_image[..., ::-1], axis=0)
        input_data = np.ascontiguousarray(input_data, dtype=np.uint8)
        raw_outputs = self.model.run([input_data])
        if not self._class_count_checked:
            self._class_count_checked = True
            count = infer_class_count(raw_outputs)
            if count is not None and count != len(self.classes):
                logger.error(
                    "%s 的类别数与代码列表不一致：模型 %d 类，代码列表 %d 个 —— "
                    "类别名会整体错位且不报错。请按该模型训练用的 data.yaml "
                    "更新 shared/const.py 里对应的列表",
                    os.path.basename(self.model_path), count, len(self.classes),
                )
        boxes = detect_process(raw_outputs)

        points, class_ids, scores = [], [], []
        for box in boxes:
            xmin = (box.xmin - offset_x) / ratio
            ymin = (box.ymin - offset_y) / ratio
            xmax = (box.xmax - offset_x) / ratio
            ymax = (box.ymax - offset_y) / ratio
            points.append(rectangle_points(xmin, ymin, xmax, ymax))
            class_ids.append(box.classId)
            scores.append(box.score)

        points = np.asarray(points, np.float32).reshape(-1, 4, 2)
        class_ids = np.asarray(class_ids, np.int32)
        scores = np.asarray(scores, np.float32)
        if not draw:
            if return_scores:
                return points, class_ids, scores
            return points, class_ids

        result = image.copy()
        for box, box_points in zip(boxes, points):
            points_int = np.rint(box_points).astype(np.int32)
            cv2.polylines(result, [points_int], True, (0, 255, 0), 2)
            label = "%s %.2f" % (self.classes[box.classId], box.score)
            cv2.putText(
                result, label, tuple(points_int[0]), cv2.FONT_HERSHEY_DUPLEX,
                0.5, (0, 0, 255), 1,
            )
        if return_scores:
            return points, class_ids, scores, result
        return points, class_ids, result

    def release(self):
        self.model.release()
