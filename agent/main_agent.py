"""主程序（agent 版）：YOLO 查栏 + 自主 agent 识别。

协议与 main_llm / main_push 完全一致（a/b/c/v → r/0~6/f），电控零改动：
- 阶段1 查栏：YOLO 定位（yolo/DetectRunner + shared.common.column_occupancy）。
- 阶段2：agent 自主决策（拍几个角度、解不解码、归哪组）。
- 阶段3：显示回执。

本入口可运行：`python agent/main_agent.py`（在项目根目录）。
"""
import os
import sys

# 允许从项目根运行 `python agent/main_agent.py` 时 import shared / yolo / agent
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import queue
import threading
import time

import cv2

from shared.const import (
    CAPTURE_DIR, CAMERA_PALLET_ID, CAMERA_DETECT_AREA_ID,
    PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS,
    DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
    MAX_TEMP_CATEGORIES, MAX_ITEMS_PER_CATEGORY, COLUMN_COUNT,
    PALLET_MODEL_PATH, PALLET_COLOR_CLASSES,
)
from shared.common import (
    CameraStopped, get_frame, wait_event, wait_stage_two_signal,
    initial_cap, read_camera, communicate_serial,
    tray_bounds_px, column_occupancy,
)
from shared.show import RecognitionRow, run_recognition_display
from yolo.inference_utils import DetectRunner
from agent.loop import StageTwoAgent
from agent.memory import GroupMemory
from agent.tools import decode_current
from agent.llm_client import AgentLLMError

logger = logging.getLogger("wave.vision.agent")


class PushController:
    """托盘推送主控制流程（agent 版）。"""

    def __init__(self, pallet_runner, memory,
                 pallet_frame_queue, detect_area_frame_queue, send_queue,
                 stage_two_requested, display_row_queue,
                 start_requested, column_check_requested, display_requested,
                 stop_event, video_dir, image_dir, run_name, pallet_fps, detect_area_fps):
        self.pallet_runner = pallet_runner
        self.memory = memory

        self.pallet_frame_queue = pallet_frame_queue
        self.detect_area_frame_queue = detect_area_frame_queue
        self.send_queue = send_queue
        self.stage_two_requested = stage_two_requested
        self.display_row_queue = display_row_queue
        self.start_requested = start_requested
        self.column_check_requested = column_check_requested
        self.display_requested = display_requested
        self.stop_event = stop_event

        self.video_dir = video_dir
        self.image_dir = image_dir
        self.run_name = run_name
        self.pallet_fps = pallet_fps
        self.detect_area_fps = detect_area_fps

        self.large_pallet_video_writer = None
        self.detect_area_video_writer = None
        self.recognition_counts = [0] * MAX_TEMP_CATEGORIES
        self.total_sorted = 0
        self.pending_display_row = None
        self.stage1_round = 0
        self.stage2_round = 0

    # ---- 录像 ----
    def record_streams(self, pallet_frame, detect_frame):
        if self.large_pallet_video_writer is None:
            h, w = pallet_frame.shape[:2]
            self.large_pallet_video_writer = cv2.VideoWriter(
                os.path.join(self.video_dir, f"{self.run_name}_large_pallet.mp4"),
                cv2.VideoWriter_fourcc(*"mp4v"), self.pallet_fps, (w, h),
            )
        if self.detect_area_video_writer is None:
            h, w = detect_frame.shape[:2]
            self.detect_area_video_writer = cv2.VideoWriter(
                os.path.join(self.video_dir, f"{self.run_name}_detect_area.mp4"),
                cv2.VideoWriter_fourcc(*"mp4v"), self.detect_area_fps, (w, h),
            )
        self.large_pallet_video_writer.write(pallet_frame)
        self.detect_area_video_writer.write(detect_frame)

    # ---- 阶段 1：等待 c（查栏）或 a（开始下一轮）----
    def wait_for_start_or_check(self):
        logger.info("等待 c（查栏）或 a（开始下一轮）")
        while not (self.column_check_requested.is_set() or self.start_requested.is_set()):
            if self.stop_event.is_set():
                raise CameraStopped()
            pallet_frame = get_frame(self.pallet_frame_queue, self.stop_event)
            detect_frame = get_frame(self.detect_area_frame_queue, self.stop_event)
            if pallet_frame is None or detect_frame is None:
                continue
            self.record_streams(pallet_frame, detect_frame)

        if self.column_check_requested.is_set():
            self.column_check_requested.clear()
            return "c"
        self.start_requested.clear()
        return "a"

    # ---- 阶段 1：查栏（YOLO 定位）----
    def check_columns(self):
        self.stage1_round += 1
        frame = get_frame(self.pallet_frame_queue, self.stop_event)
        if frame is None:
            raise CameraStopped()

        points, _, result = self.pallet_runner.infer(frame, draw=True)
        occupancy = column_occupancy(points, frame.shape)

        x_min, x_max, y_min, y_max = tray_bounds_px(frame.shape)
        column_width = (x_max - x_min) / COLUMN_COUNT
        for i in range(COLUMN_COUNT):
            cv2.rectangle(
                result,
                (round(x_min + i * column_width), round(y_min)),
                (round(x_min + (i + 1) * column_width), round(y_max)),
                (255, 0, 0), 2,
            )
        cv2.imwrite(os.path.join(self.image_dir, f"stage1_r{self.stage1_round:03d}.jpg"), result)
        cv2.imwrite(os.path.join(self.image_dir, "pallet_original",
                                 f"stage1_r{self.stage1_round:03d}.jpg"), frame)

        reply = "".join("1\n" if x else "0\n" for x in occupancy)
        self.send_queue.put(reply)
        logger.info("托盘栏占用: %s", reply.strip().replace("\n", ""))

    def drain_stage_two_requests(self):
        try:
            while True:
                self.stage_two_requested.get_nowait()
        except queue.Empty:
            pass

    # ---- 阶段 2：自主 agent 识别 ----
    def run_stage_two(self):
        self.stage2_round += 1

        first = get_frame(self.detect_area_frame_queue, self.stop_event)
        if first is None:
            raise CameraStopped()

        def _save_frame(frame, idx):
            path = os.path.join(self.image_dir, "original",
                                f"stage2_r{self.stage2_round:03d}_{idx:02d}.jpg")
            cv2.imwrite(path, frame)
            return path

        _save_frame(first, 1)
        saved_count = [1]

        def rotate_cb():
            self.send_queue.put("r\n")
            wait_stage_two_signal(self.stage_two_requested, self.stop_event)
            frame = get_frame(self.detect_area_frame_queue, self.stop_event)
            if frame is None:
                raise CameraStopped()
            saved_count[0] += 1
            _save_frame(frame, saved_count[0])
            return frame

        agent = StageTwoAgent(self.memory, rotate_cb=rotate_cb, decode_cb=decode_current)
        try:
            gid, name = agent.run(first)
        except (AgentLLMError, CameraStopped) as e:
            logger.error("阶段2 识别失败: %s", e)
            self.pending_display_row = None
            self.send_queue.put("0\n")
            return

        if gid is None:
            logger.info("阶段2 结果：无物体")
            self.pending_display_row = None
            self.send_queue.put("0\n")
            return
        if gid == 0:
            logger.error("阶段2：储物盒已满，无法新建分组，result=0")
            self.pending_display_row = None
            self.send_queue.put("0\n")
            return

        self.pending_display_row = (gid, name, first.copy())
        self.send_queue.put(f"{gid}\n")
        logger.info("阶段2 结果: 组%d %s", gid, name)

    # ---- 阶段 3：等 v 上屏，回 f ----
    def finish_display(self):
        logger.info("等待 v")
        wait_event(self.display_requested, self.stop_event, what="v")
        if self.pending_display_row is not None:
            category, object_name, display_image = self.pending_display_row
            self.recognition_counts[category - 1] += 1
            self.total_sorted += 1
            self.display_row_queue.put(RecognitionRow(
                index=self.total_sorted,
                name=object_name,
                image=display_image,
                total=self.total_sorted,
            ))
        self.send_queue.put("f\n")

    def release_writers(self):
        if self.large_pallet_video_writer is not None:
            self.large_pallet_video_writer.release()
        if self.detect_area_video_writer is not None:
            self.detect_area_video_writer.release()

    def run(self):
        while True:
            command = self.wait_for_start_or_check()
            if command == "c":
                try:
                    self.check_columns()
                except CameraStopped:
                    raise
                except Exception as e:  # noqa: BLE001 —— YOLO 查栏异常保守回全 1
                    logger.error("查栏失败，保守回全 1: %s", e)
                    self.send_queue.put("".join("1\n" for _ in range(COLUMN_COUNT)))
                continue

            self.drain_stage_two_requests()
            self.run_stage_two()
            self.finish_display()


def main():
    run_name = time.strftime("%Y%m%d_%H%M%S")
    video_dir = os.path.join(CAPTURE_DIR, "videos")
    image_dir = os.path.join(CAPTURE_DIR, "images", run_name)

    large_pallet_frame_queue = queue.Queue(maxsize=1)
    detect_area_frame_queue = queue.Queue(maxsize=1)
    large_pallet_fps_queue = queue.Queue(maxsize=1)
    detect_area_fps_queue = queue.Queue(maxsize=1)
    send_queue = queue.Queue()
    stage_two_requested = queue.Queue()
    display_row_queue = queue.Queue()
    stop_event = threading.Event()
    start_requested = threading.Event()
    column_check_requested = threading.Event()
    display_requested = threading.Event()

    os.makedirs(video_dir, exist_ok=True)
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(os.path.join(image_dir, "pallet_original"), exist_ok=True)
    os.makedirs(os.path.join(image_dir, "original"), exist_ok=True)

    large_pallet_cap = initial_cap(CAMERA_PALLET_ID, "托盘上方广角摄像头",
                                   PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS)
    detect_area_cap = initial_cap(CAMERA_DETECT_AREA_ID, "识别区摄像头",
                                  DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)

    large_pallet_camera = threading.Thread(
        target=read_camera,
        args=(large_pallet_cap, "托盘上方广角摄像头", large_pallet_frame_queue,
              large_pallet_fps_queue, stop_event, False),
        daemon=True,
    )
    detect_area_camera = threading.Thread(
        target=read_camera,
        args=(detect_area_cap, "识别区摄像头", detect_area_frame_queue,
              detect_area_fps_queue, stop_event, True),
        daemon=True,
    )
    serial_worker = threading.Thread(
        target=communicate_serial,
        args=(send_queue, stop_event, start_requested, stage_two_requested,
              column_check_requested, display_requested),
        daemon=True,
    )
    display_worker = threading.Thread(
        target=run_recognition_display,
        args=(display_row_queue, stop_event),
        name="recognition-display",
        daemon=True,
    )
    large_pallet_camera.start()
    detect_area_camera.start()
    serial_worker.start()
    display_worker.start()

    pallet_runner = DetectRunner(PALLET_MODEL_PATH, PALLET_COLOR_CLASSES)
    memory = GroupMemory(
        max_groups=MAX_TEMP_CATEGORIES,
        max_items_per_group=MAX_ITEMS_PER_CATEGORY,
        save_path=os.path.join(image_dir, "agent_groups.json"),
    )

    controller = None
    try:
        try:
            pallet_fps = large_pallet_fps_queue.get(timeout=5.0)
        except queue.Empty:
            pallet_fps = 25.0
        if not pallet_fps or pallet_fps <= 0:
            pallet_fps = 25.0
        try:
            detect_area_fps = detect_area_fps_queue.get(timeout=5.0)
        except queue.Empty:
            detect_area_fps = 25.0
        if not detect_area_fps or detect_area_fps <= 0:
            detect_area_fps = 25.0

        controller = PushController(
            pallet_runner, memory,
            large_pallet_frame_queue, detect_area_frame_queue, send_queue,
            stage_two_requested, display_row_queue,
            start_requested, column_check_requested, display_requested,
            stop_event,
            video_dir, image_dir, run_name, pallet_fps, detect_area_fps,
        )
        controller.run()

    except KeyboardInterrupt:
        pass
    except CameraStopped as error:
        logger.error("流程因故障停止: %s", error)
    finally:
        stop_event.set()
        large_pallet_camera.join(timeout=1)
        detect_area_camera.join(timeout=1)
        serial_worker.join(timeout=1)
        display_worker.join(timeout=1)
        if controller is not None:
            controller.release_writers()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
