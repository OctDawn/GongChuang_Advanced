"""落料监测 + 固定机位 LLM 分拣。

c -> 四栏 0/1；a -> 监测变化 -> s -> 0~6；v -> 屏幕刷新 -> f。
发送字符沿用 main_push 的换行分隔，接收支持单字符及 CR/LF 分隔。
只有串口线程读写 UART；每台相机只有一个采集线程，各任务独立读最新帧。
"""
import logging
import os
import queue
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import serial

from shared import const as C
from shared.common import (initial_cap, crop_to_square, crop_detect_llm,
                           object_display_name, FrameStream, read_camera, reserve_group)
from shared.laser import ChangeDetector, laser_task, get_detection_frame, calculate_rgb_difference
from shared.show import RecognitionRow, DisplayUpdate, run_recognition_display
from llm.pallet_monitor import PalletMonitor
from llm.shape_recognition import FixedViewRecognizer
from llm.shape_memory import ShapeMemory
from yolo.fallback import YoloFallback

logger = logging.getLogger("wave.vision.laser")


@dataclass
class SerialMessage:
    text: str
    sent: object = field(default_factory=threading.Event)


def wait_signal(event, stop_event, timeout):
    deadline = time.perf_counter() + timeout
    while not stop_event.is_set():
        if event.wait(min(0.05, max(0, deadline-time.perf_counter()))):
            return
        if time.perf_counter() >= deadline:
            raise TimeoutError("等待任务完成超时")
    raise InterruptedError("程序已停止")


def serial_task(port, outgoing, controller, stop_event):
    while not stop_event.is_set():
        try:
            message = outgoing.get_nowait()
        except queue.Empty:
            message = None
        if message is not None:
            payload = (message.text + "\n").encode("ascii")
            if port.write(payload) != len(payload):
                raise OSError("串口未完整写入")
            port.flush()
            message.sent.set()
            logger.info("发送 %r", message.text)
        # 不用 readline：兼容无换行、连续到达的单字符命令。
        data = port.read(max(1, port.in_waiting))
        for value in data:
            command = chr(value)
            if command not in "\r\n ":
                controller.on_command(command)


class LaserController:
    def __init__(self, pallet, cameras, detector, outgoing, display_queue,
                 stop_event, fallback, recognizer, image_dir, monitor=None):
        self.pallet, self.cameras, self.detector = pallet, cameras, detector
        self.outgoing, self.display_queue = outgoing, display_queue
        self.stop_event, self.fallback, self.recognizer = stop_event, fallback, recognizer
        self.image_dir = Path(image_dir)
        self.jobs = queue.Queue()
        self.lock = threading.RLock()
        self.state = "IDLE"
        self.dropped = threading.Event()
        self.stop_message = None
        self.drop_detected = False
        self.pending = None
        self.categories = {}
        self.groups = {}
        self.monitor = monitor
        self.counts = [0] * C.MAX_TEMP_CATEGORIES
        self.round = self.check_round = 0

    def send(self, text):
        message = SerialMessage(text)
        self.outgoing.put(message)
        return message

    def on_command(self, command):
        """由串口线程调用；a 到达时立即冻结背景，不等识别线程轮询。"""
        with self.lock:
            if command == "c" and self.state == "IDLE":
                self.state = "CHECK"
                self.jobs.put("c")
            elif command == "a" and self.state == "IDLE":
                self.pending = None
                self.drop_detected = False
                self.stop_message = None
                self.dropped.clear()
                self.state = "WATCH"
                if self.monitor:
                    self.monitor.invalidate()
                try:
                    self.detector.arm()
                except RuntimeError as error:
                    logger.error("无法开始监测，停推并回 0: %s", error)
                    self.request_stop(False)
                self.jobs.put("a")
            elif command == "v" and self.state == "WAIT_V":
                self.state = "DISPLAY"
                self.jobs.put("v")
            else:
                logger.warning("忽略当前状态 %s 下的命令 %r", self.state, command)

    def on_change(self, packet, result):
        logger.info("ROI 变化 %.1f%%", result.difference * 100)
        self.request_stop(True)

    def request_stop(self, detected):
        with self.lock:
            if self.state != "WATCH":
                return
            self.detector.disarm()
            self.state = "STOPPING"
            self.drop_detected = detected
            self.stop_message = self.send("s")
            self.dropped.set()

    def halt_on_failure(self):
        """推送中发生视觉故障时，尽力先发 s；串口自身故障时只能退出。"""
        self.request_stop(False)
        if self.stop_message is not None and not self.stop_event.is_set():
            self.stop_message.sent.wait(0.5)

    def _save(self, name, frame):
        path = self.image_dir / name
        if not cv2.imwrite(str(path), frame):
            logger.warning("图片保存失败: %s", path)

    def check_columns(self):
        self.check_round += 1
        frame = self.pallet.wait_after(0, self.stop_event, after_time=time.perf_counter()).image
        self._save(f"pallet_{self.check_round:03d}.jpg", frame)
        count = C.LASER_COLUMN_COUNT

        snapshot = self.monitor.latest(C.LASER_PALLET_MAX_AGE) if self.monitor else None
        if snapshot is not None:
            occupancy = snapshot["occupancy"]
        else:
            # 持续盘点可能较慢，c 直接使用新帧 YOLO，不等待后台请求。
            try:
                occupancy = self.fallback.check_columns(frame, column_count=count)
            except Exception:
                logger.exception("YOLO 查栏失败，保守回全 1")
                occupancy = [True] * count
        if self.stop_event.is_set():
            raise InterruptedError()
        # 锁内先开放接收状态、再入发送队列，快速返回的下一条命令不会丢失。
        with self.lock:
            self.state = "IDLE"
            self.send("\n".join("1" if value else "0" for value in occupancy))

    def stable_frames(self, after_time):
        """所有识别相机均须提供 s 之后的新帧，并在检测 ROI 内连续稳定。"""
        deadline = time.perf_counter() + C.LASER_SETTLE_TIMEOUT
        sources = dict(self.cameras, top=self.pallet)
        sequences = {name: 0 for name in sources}
        references, frames = {}, {}
        counts = {name: 0 for name in sources}
        while time.perf_counter() < deadline:
            for name, stream in sources.items():
                packet = stream.wait_after(sequences[name], self.stop_event,
                                           timeout=max(0.01, deadline-time.perf_counter()),
                                           after_time=after_time)
                sequences[name] = packet.sequence
                view = (get_detection_frame(packet.image, C.LASER_TOP_DETECT_AREA).copy()
                        if name == "top" else crop_detect_llm(crop_to_square(packet.image)))
                roi = view
                previous = references.get(name)
                if previous is not None and calculate_rgb_difference(roi, previous) < C.STABLE_THRESHOLD:
                    counts[name] += 1
                else:
                    references[name] = roi.copy()
                    counts[name] = 0
                frames[name] = view
            if all(value >= C.REQUIRED_STABLE_FRAMES for value in counts.values()):
                return [frames[name] for name in sources]
        raise TimeoutError("落料后画面未稳定")

    def process_object(self):
        self.round += 1
        try:
            wait_signal(self.dropped, self.stop_event, C.LASER_DROP_TIMEOUT)
        except TimeoutError:
            logger.warning("等待落料超时，发送 s 停推后回 0")
            self.request_stop(False)
        # 必须确认 s 实际写出后才能开始稳定等待及识别。
        wait_signal(self.stop_message.sent, self.stop_event, 2.0)
        sent_category = False
        category = 0
        if self.drop_detected:
            try:
                frames = self.stable_frames(time.perf_counter() + C.LASER_SETTLE_SECONDS)
                for name, frame in zip((*self.cameras, "top"), frames):
                    self._save(f"object_{self.round:03d}_{name}.jpg", frame)
                try:
                    status, gid = self.recognizer.quick_match(frames, self.groups)
                except Exception as error:
                    logger.warning("快速匹配失败，退回完整识别: %s", error)
                    status, gid = "uncertain", None
                if status == "existing":
                    category = gid if self.counts[gid-1] < C.MAX_ITEMS_PER_CATEGORY else 0
                    if category:
                        group = self.groups[gid]
                        self.pending = (gid, group["name"], frames[0].copy())
                elif status != "empty":
                    if status == "new" and len(self.groups) < C.MAX_TEMP_CATEGORIES:
                        category = self.reserve_group(frames)
                        self.pending = (category, self.groups[category]["name"], frames[0].copy())
                        self.publish_category(category)
                        sent_category = True
                        # 电控投放与详细识别并行；串口线程接收 v 后排队等待。
                        # 本方法补全名称后再处理显示，不能提前发送 f。
                    color, shape = self.identify(frames)
                    if color is not None and shape is not None:
                        key = (color, shape)
                        if not sent_category:
                            category = self.categories.get(key, 0)
                            if not category and len(self.groups) < C.MAX_TEMP_CATEGORIES:
                                category = self.reserve_group(frames)
                            if category and self.counts[category-1] >= C.MAX_ITEMS_PER_CATEGORY:
                                category = 0
                        if category:
                            name = object_display_name(color, shape)
                            self.groups[category].update(color=color, shape=shape, name=name)
                            previous = self.categories.get(key)
                            if previous is not None and previous != category:
                                logger.error("先行分组冲突：箱 %d 与箱 %d 同类，保留已发送箱号", category, previous)
                            else:
                                self.categories[key] = category
                            self.pending = (category, name, frames[0].copy())
                    elif sent_category:
                        logger.error("已发送箱号 %d，但详细识别无结果，保留待确认名称及参考图", category)
            except InterruptedError:
                raise
            except Exception:
                logger.exception("本轮识别异常，已发送箱号不会撤回或重发")
                if not sent_category:
                    category = 0
                    self.pending = None
        if self.stop_event.is_set():
            raise InterruptedError()
        if not sent_category:
            self.publish_category(category)

    def reserve_group(self, frames):
        return reserve_group(self.groups, frames)

    def identify(self, frames):
        try:
            response_path = (str(self.image_dir / f"response_{self.round:03d}.txt")
                             if C.SAVE_LLM_RESPONSES else None)
            color, shape, _ = self.recognizer.classify(frames, response_path)
        except Exception as error:
            logger.warning("LLM 分类失败，使用同轮 YOLO: %s", error)
            color, shape, _ = self.fallback.classify(frames, expected_count=None, include_labels=False)
        return color, shape

    def publish_category(self, category):
        with self.lock:
            if self.stop_event.is_set():
                raise InterruptedError("程序已停止，不发送迟到分类结果")
            self.state = "WAIT_V"
            self.send(str(category))

    def finish_display(self):
        row = None
        if self.pending:
            category, name, frame = self.pending
            row = RecognitionRow(category, name, frame, self.counts[category-1] + 1)
        done = threading.Event()
        self.display_queue.put(DisplayUpdate(row, done))
        wait_signal(done, self.stop_event, C.LASER_DISPLAY_TIMEOUT)
        if row:
            self.counts[row.index-1] += 1
        self.pending = None
        if self.monitor:
            self.monitor.invalidate()
        with self.lock:
            self.state = "IDLE"
            self.send("f")

    def run(self):
        while not self.stop_event.is_set():
            try:
                command = self.jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            if command == "c":
                self.check_columns()
            elif command == "a":
                self.process_object()
            else:
                self.finish_display()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cameras = dict(C.LASER_CAMERAS)
    devices = [C.CAMERA_PALLET_ID, *cameras.values()]
    if (list(cameras) != ["left", "right"] or len(cameras) != len(C.LASER_CAMERAS)
            or len(set(devices)) != len(devices) or C.LASER_TRIGGER_CAMERA not in cameras):
        raise ValueError("相机名称/路径必须唯一，且 LASER_TRIGGER_CAMERA 必须在相机列表内")
    stop_event = threading.Event()
    workers = []
    image_dir = Path(C.CAPTURE_DIR) / "images" / (time.strftime("%Y%m%d_%H%M%S") + "_laser")
    image_dir.mkdir(parents=True, exist_ok=True)

    def start(name, target, *args):
        def guarded():
            try:
                target(*args)
            except InterruptedError:
                pass
            except Exception:
                logger.exception("%s 线程故障", name)
                if name != "serial":
                    controller.halt_on_failure()
                stop_event.set()
        worker = threading.Thread(target=guarded, name=name, daemon=True)
        workers.append(worker)
        worker.start()

    with ExitStack() as resources:
        fallback = YoloFallback.load(resources)
        memory = ShapeMemory(image_dir, int(os.environ.get("LLM_SHAPE_MEMORY_LIMIT", "12")), view_count=3)
        recognizer = FixedViewRecognizer(memory)
        pallet = FrameStream()
        streams = {name: FrameStream() for name in cameras}
        outgoing, display_queue = queue.Queue(), queue.Queue()
        detector = ChangeDetector(trigger_frames=C.LASER_TRIGGER_FRAMES)
        monitor = PalletMonitor(pallet, stop_event)
        controller = LaserController(pallet, streams, detector, outgoing, display_queue,
                                     stop_event, fallback, recognizer, image_dir, monitor)
        try:
            specs = [("pallet", C.CAMERA_PALLET_ID, C.PALLET_WIDTH, C.PALLET_HEIGHT, C.PALLET_FPS, pallet)]
            specs += [(name, device, C.LASER_WIDTH, C.LASER_HEIGHT, C.LASER_FPS, streams[name])
                      for name, device in cameras.items()]
            for name, device, width, height, fps, stream in specs:
                cap = initial_cap(device, name, width, height, fps)
                if cap is None:
                    raise RuntimeError(f"相机 {name} 无法打开")
                start("camera-" + name, read_camera, cap, name, stream, None, stop_event)
            for stream in (pallet, *streams.values()):
                stream.wait_after(0, stop_event)
            start("laser", laser_task, streams[C.LASER_TRIGGER_CAMERA], detector,
                  controller.on_change, stop_event)
            start("display", run_recognition_display, display_queue, stop_event, True)
            wait_signal(detector.ready, stop_event, 10.0)
            # 先确认显示线程可用，再允许电控发起运动。
            ready = threading.Event()
            display_queue.put(DisplayUpdate(None, ready))
            wait_signal(ready, stop_event, C.LASER_DISPLAY_TIMEOUT)
            port = resources.enter_context(serial.Serial(C.SERIAL_PORT, C.BAUDRATE,
                                                         timeout=0.01, write_timeout=1.0))
            start("serial", serial_task, port, outgoing, controller, stop_event)
            start("pallet-monitor", monitor.run)
            logger.info("已就绪，等待 c 或 a；识别相机: %s", ", ".join(cameras))
            controller.run()
        except KeyboardInterrupt:
            logger.info("用户停止")
        finally:
            controller.halt_on_failure()
            stop_event.set()
            detector.disarm()
            for worker in workers:
                worker.join(timeout=2.0)


if __name__ == "__main__":
    main()
