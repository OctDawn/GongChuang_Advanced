"""可复用的 BGR 画面变化检测，不打开摄像头、不操作串口。

空闲时学习稳定背景；arm 后冻结背景，触发一次后自动解除监测。
变化只代表 ROI 内出现运动/遮挡，主程序还需等待落稳后识别。
"""
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np
from shared.const import LASER_AREA, RGB_DIFF_THRESHOLD, STABLE_THRESHOLD, REQUIRED_STABLE_FRAMES


def get_detection_frame(frame, area=LASER_AREA):
    """按实际帧尺寸裁剪 area=(x_min,x_max,y_min,y_max)。"""
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
        raise ValueError("laser 需要非空三通道 BGR 图像")
    x0, x1, y0, y1 = area
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("LASER_AREA 必须是 0~1 内的有效矩形")
    h, w = frame.shape[:2]
    roi = frame[int(h*y0):int(h*y1), int(w*x0):int(w*x1)]
    if not roi.size:
        raise ValueError("laser ROI 为空")
    return roi


def calculate_rgb_difference(current, reference, threshold=RGB_DIFF_THRESHOLD):
    """任一通道差值超过阈值的像素比例，范围 0~1。"""
    if current.shape != reference.shape or not current.size:
        raise ValueError("比较图像必须尺寸一致且非空")
    return float(np.mean(np.max(cv2.absdiff(current, reference), axis=2) > threshold))


@dataclass(frozen=True)
class ChangeResult:
    difference: float
    triggered: bool


class ChangeDetector:
    def __init__(self, area=LASER_AREA, pixel_threshold=RGB_DIFF_THRESHOLD,
                 change_threshold=STABLE_THRESHOLD, stable_frames=REQUIRED_STABLE_FRAMES,
                 trigger_frames=1):
        if not (0 <= pixel_threshold <= 255 and 0 < change_threshold <= 1):
            raise ValueError("变化阈值越界")
        if stable_frames < 1 or trigger_frames < 1:
            raise ValueError("连续帧数至少为 1")
        self.area, self.pixel_threshold = area, pixel_threshold
        self.change_threshold = change_threshold
        self.stable_frames, self.trigger_frames = stable_frames, trigger_frames
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self._reference = None
        self._stable = self._hits = 0
        self._armed = False
        self._armed_at = self._last_at = 0.0

    def arm(self, now=None, max_age=0.5):
        """冻结背景；参考画面尚未稳定或过期时拒绝启动。"""
        now = time.perf_counter() if now is None else now
        with self._lock:
            if not self.ready.is_set() or now - self._last_at > max_age:
                raise RuntimeError("变化检测参考图未稳定或已经过期")
            self._armed, self._armed_at, self._hits = True, now, 0

    def disarm(self):
        with self._lock:
            self._armed, self._hits = False, 0

    def update(self, frame, captured_at=None):
        current = get_detection_frame(frame, self.area)
        captured_at = time.perf_counter() if captured_at is None else captured_at
        with self._lock:
            if captured_at <= self._last_at:
                return ChangeResult(0.0, False)
            self._last_at = captured_at
            if self._reference is None:
                self._reference = current.copy()
                return ChangeResult(0.0, False)
            if current.shape != self._reference.shape:
                raise ValueError("摄像头尺寸在运行中发生变化")
            difference = calculate_rgb_difference(current, self._reference, self.pixel_threshold)
            if self._armed:
                if captured_at <= self._armed_at:
                    return ChangeResult(difference, False)
                self._hits = self._hits + 1 if difference >= self.change_threshold else 0
                if self._hits >= self.trigger_frames:
                    self._armed = False
                    self.ready.clear()
                    self._stable = self._hits = 0
                    self._reference = current.copy()
                    return ChangeResult(difference, True)
            elif difference >= self.change_threshold:
                self._reference = current.copy()
                self._stable = 0
                self.ready.clear()
            else:
                self._stable += 1
                if self._stable >= self.stable_frames:
                    self._reference = current.copy()
                    self._stable = 0
                    self.ready.set()
            return ChangeResult(difference, False)


def laser_task(stream, detector, on_change, stop_event):
    """可选线程入口，stream 为独立游标读取的 FrameStream。

    on_change(packet, result) 由主程序实现；异常由线程启动方处理。
    """
    sequence = 0
    while not stop_event.is_set():
        packet = stream.wait_after(sequence, stop_event)
        sequence = packet.sequence
        result = detector.update(packet.image, packet.captured_at)
        if result.triggered:
            on_change(packet, result)
