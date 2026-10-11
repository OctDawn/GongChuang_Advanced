import logging
import os
import queue
import sys
import threading
import time

import cv2
import numpy as np

from shared.const import *
from yolo.inference_utils import DetectRunner
from yolo.decoder import crop_box, decode_sticker
from yolo.category_counter import rough_category_count
from shared.show import RecognitionRow, run_recognition_display
from shared.common import (
    CameraStopped, get_frame, wait_event, wait_stage_two_signal,
    initial_cap, read_camera, communicate_serial,
    tray_bounds_px, column_occupancy, record_pallet_streams, categorize,
    category_is_full, object_display_name,
)

logger = logging.getLogger("wave.vision")

# 阶段 2 详细诊断输出开关：排查识别问题时置 True，平时 False 保持控制台简洁
DEBUG_STAGE2 = False


'''def choose_target(boxes):
    """从检测框中选出当前需要处理的目标。

    目前是选择面积最大的目标，其所在列即为本次要推的列。
    """
    areas = [cv2.contourArea(box) for box in boxes]

    return int(np.argmax(areas))


def tray_bounds_px(frame_shape):
    """把托盘范围常量（相对画面比例）换算成画面像素边界。

    返回 (x_min, x_max, y_min, y_max)，其中 y_min 为出口端（bottom，画面顶端），
    y_max 为推铲端（top，画面底端）。推送方向为向出口端（y 减小）推进。
    """
    height, width = frame_shape[:2]
    x_min = TRAY_X_MIN * width
    x_max = TRAY_X_MAX * width
    y_min = TRAY_Y_MIN * height
    y_max = TRAY_Y_MAX * height
    return x_min, x_max, y_min, y_max


def column_of(center_x, x_min, x_max):
    """根据物体中心 x 判断所属列，返回 1~COLUMN_COUNT。"""
    column_width = (x_max - x_min) / COLUMN_COUNT
    if column_width <= 0:
        return 1
    column = int((center_x - x_min) // column_width) + 1
    return int(np.clip(column, 1, COLUMN_COUNT))


def push_distance_ratio(end_box, y_min, y_max):
    """计算推铲需要前进的距离（相对托盘长度的比例 [0,1]）。

    end_box 为该列最靠近出口端（y 最小）的末端物体的框。
    推送距离 = 末端物体出口侧边缘到出口端的间隙 + 该物体沿推送方向的高度，
    即把末端物体完全推出出口端所需的距离。假定同列物体前后紧贴、无空隙。
    """
    top = float(np.min(end_box[:, 1]))
    bottom = float(np.max(end_box[:, 1]))
    gap = max(0.0, top - y_min)
    object_height = max(0.0, bottom - top)
    tray_length_px = max(1.0, y_max - y_min)
    ratio = (gap + object_height) / tray_length_px
    return float(np.clip(ratio + PUSH_MARGIN, 0.0, 1.0))'''


def draw_debug_frame(frame, color_points, color_ids, color_scores,
                     shape_points, shape_ids, shape_scores,
                     label_points, label_ids, label_scores):
    """把颜色/形状/贴纸三个模型的检测框叠加到同一张图上，用于诊断。

    颜色=红色，形状=绿色，贴纸=蓝色；越界类别 ID 显示为 ?id。
    """
    result = frame.copy()

    def _draw(points, ids, scores, names, color):
        if points is None or len(points) == 0:
            return
        for pts, cid, sc in zip(points, ids, scores):
            pts_int = np.rint(pts).astype(np.int32)
            cv2.polylines(result, [pts_int], True, color, 2)
            cid = int(cid)
            name = names[cid] if 0 <= cid < len(names) else f"?{cid}"
            cv2.putText(result, f"{name} {float(sc):.2f}",
                        tuple(pts_int[0]), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, color, 1, cv2.LINE_AA)

    _draw(color_points, color_ids, color_scores, DETECT_AREA_COLOR_CLASSES, (0, 0, 255))
    _draw(shape_points, shape_ids, shape_scores, SHAPE_CLASSES, (0, 255, 0))
    _draw(label_points, label_ids, label_scores, LABEL_CLASSES, (255, 0, 0))
    return result


class PushController:
    """托盘推送主控制流程。

    把原来 main 里的大 while 拆成几个阶段方法，共享状态（录像 writer、
    识别计数、待显示行等）统一收在实例里，避免一堆参数传来传去。
    """

    def __init__(self, pallet_runner, color_runner, shape_runner, label_runner,
                 pallet_frame_queue, detect_area_frame_queue, send_queue,
                 stage_two_requested, display_row_queue,
                 start_requested, column_check_requested, display_requested,
                 stop_event,
                 video_dir, image_dir, run_name, pallet_fps, detect_area_fps):
        self.pallet_runner = pallet_runner
        self.color_runner = color_runner
        self.shape_runner = shape_runner
        self.label_runner = label_runner

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

    def wait_for_start_or_check(self):
        """等待 c（查栏）或 a（直接开始下一轮），期间保持双路录像。

        电控修改后的协议：上一轮分类、上屏完成后，不一定每次都发 c 查栏，
        也可能跳过查栏、直接发 a 开始下一轮，因此这里同时监听两种指令。
        返回 "c" 或 "a"，表示本次先收到的指令。
        """
        print("[阶段1] 等待 c(查栏) 或 a(开始)")
        while not (self.column_check_requested.is_set() or
                   self.start_requested.is_set()):
            if self.stop_event.is_set():
                raise CameraStopped()
            large_pallet_frame = get_frame(self.pallet_frame_queue, self.stop_event)
            detect_area_frame = get_frame(self.detect_area_frame_queue, self.stop_event)
            if large_pallet_frame is None or detect_area_frame is None:
                continue
            _, _, result = self.pallet_runner.infer(large_pallet_frame, draw=True)
            self.large_pallet_video_writer, self.detect_area_video_writer = record_pallet_streams(
                large_pallet_frame, detect_area_frame, result,
                self.large_pallet_video_writer, self.detect_area_video_writer,
                self.video_dir, self.run_name, self.pallet_fps, self.detect_area_fps,
            )

        if self.column_check_requested.is_set():
            self.column_check_requested.clear()
            return "c"
        self.start_requested.clear()
        return "a"

    def check_columns(self):
        """识别托盘各栏是否有物体，回复四位布尔数组：1 有 / 0 无。"""
        self.stage1_round += 1
        large_pallet_frame = get_frame(self.pallet_frame_queue, self.stop_event)
        if large_pallet_frame is None:
            raise CameraStopped()
        points, _, result = self.pallet_runner.infer(large_pallet_frame, draw=True)
        occupancy = column_occupancy(points, large_pallet_frame.shape)

        x_min, x_max, y_min, y_max = tray_bounds_px(large_pallet_frame.shape)
        column_width = (x_max - x_min) / COLUMN_COUNT
        for i in range(COLUMN_COUNT):
            cv2.rectangle(
                result,
                (round(x_min + i * column_width), round(y_min)),
                (round(x_min + (i + 1) * column_width), round(y_max)),
                (255, 0, 0), 2,
            )
        image_path = os.path.join(
            self.image_dir, f"stage1_r{self.stage1_round:03d}.jpg"
        )
        cv2.imwrite(image_path, result)
        original_path = os.path.join(
            self.image_dir, "pallet_original", f"stage1_r{self.stage1_round:03d}.jpg"
        )
        cv2.imwrite(original_path, large_pallet_frame)

        reply = "".join("1\n" if column_has_object else "0\n" for column_has_object in occupancy)
        self.send_queue.put(reply)
        print(f"[阶段1] 查栏: {reply.strip().replace(chr(10), ' ')}")

    def drain_stage_two_requests(self):
        """清空遗留的 b 请求，避免上一轮残留影响本轮。"""
        try:
            while True:
                self.stage_two_requested.get_nowait()
        except queue.Empty:
            pass

    # 阶段 2：多角度累计识别 + 汇总
    def run_stage_two(self):
        """多角度累计识别：a 触发第一次，随后每个 b 触发下一次。"""
        self.stage2_round += 1
        color_confidence_sums = np.zeros(len(DETECT_AREA_COLOR_CLASSES), dtype=np.float32)
        color_best_scores = np.full(len(DETECT_AREA_COLOR_CLASSES), -1.0, dtype=np.float32)
        color_crops = [None] * len(DETECT_AREA_COLOR_CLASSES)
        shape_confidence_sums = np.zeros(len(SHAPE_CLASSES), dtype=np.float32)
        label_confidence_sums = np.zeros(len(LABEL_CLASSES), dtype=np.float32)
        label_best_scores = np.full(len(LABEL_CLASSES), -1.0, dtype=np.float32)
        label_crops = [None] * len(LABEL_CLASSES)
        privileged_shape_id = None
        privileged_shape_score = -1.0
        skip_label = False

        for i in range(STAGE_TWO_DETECTION_COUNT):
            if i > 0:
                print(f"[阶段2] 等待转向 b ({i}/{STAGE_TWO_DETECTION_COUNT - 1})")
                wait_stage_two_signal(self.stage_two_requested, self.stop_event)

            detect_area_frame = get_frame(self.detect_area_frame_queue, self.stop_event)
            if detect_area_frame is None:
                raise CameraStopped()
            label_points = label_ids = label_scores = None
            color_points, color_ids, color_scores = (
                self.color_runner.infer(detect_area_frame, return_scores=True)
            )
            shape_points, shape_ids, shape_scores = self.shape_runner.infer(
                detect_area_frame, return_scores=True
            )

            # ===== 详细诊断（DEBUG_STAGE2=True 时输出）=====
            if DEBUG_STAGE2:
                print(f"[Stage2 诊断] 第 {i + 1}/{STAGE_TWO_DETECTION_COUNT} 次检测")
                print(f"  颜色模型检测框数: {len(color_ids)}")
                for cid, cs in zip(color_ids, color_scores):
                    cid = int(cid)
                    name = DETECT_AREA_COLOR_CLASSES[cid] if 0 <= cid < len(DETECT_AREA_COLOR_CLASSES) else f"越界ID{cid}"
                    print(f"    - 颜色 {name}  置信度={float(cs):.3f}")
                print(f"  形状模型检测框数: {len(shape_ids)}")
                for sid, ss in zip(shape_ids, shape_scores):
                    sid = int(sid)
                    name = SHAPE_CLASSES[sid] if 0 <= sid < len(SHAPE_CLASSES) else f"越界ID{sid}"
                    print(f"    - 形状 {name}  置信度={float(ss):.3f}")

            # 累加各次检测中每个颜色类别的全部置信度
            for idx, (detected_id, score) in enumerate(zip(color_ids, color_scores)):
                detected_id = int(detected_id)
                if 0 <= detected_id < len(DETECT_AREA_COLOR_CLASSES):
                    score = float(score)
                    color_confidence_sums[detected_id] += score
                    if score > color_best_scores[detected_id]:
                        color_best_scores[detected_id] = score
                        color_crops[detected_id] = crop_box(
                            detect_area_frame, color_points[idx]
                        )

            # 累加形状置信度，并记录特权形状（置信度最高的一次）
            for detected_id, score in zip(shape_ids, shape_scores):
                detected_id = int(detected_id)
                if not 0 <= detected_id < len(SHAPE_CLASSES):
                    continue
                score = float(score)
                shape_confidence_sums[detected_id] += score
                shape_name = SHAPE_CLASSES[detected_id]
                if (shape_name in PRIVILEGED_SHAPES and
                        score >= PRIVILEGED_SHAPE_CONFIDENCE and score > privileged_shape_score):
                    privileged_shape_id = detected_id
                    privileged_shape_score = score

            # 贴纸检测：图案类直接标记，码/文字类需要解码
            if not skip_label:
                label_points, label_ids, label_scores = self.label_runner.infer(
                    detect_area_frame, return_scores=True
                )
                # ===== 详细诊断（DEBUG_STAGE2=True 时输出）=====
                if DEBUG_STAGE2:
                    print(f"  贴纸模型检测框数: {len(label_ids)}")
                    for lid, ls in zip(label_ids, label_scores):
                        lid = int(lid)
                        name = LABEL_CLASSES[lid] if 0 <= lid < len(LABEL_CLASSES) else f"越界ID{lid}"
                        print(f"    - 贴纸 {name}  置信度={float(ls):.3f}")
                for idx, detected_id in enumerate(label_ids):
                    detected_id = int(detected_id)
                    if not 0 <= detected_id < len(LABEL_CLASSES):
                        continue
                    score = float(label_scores[idx])
                    label_confidence_sums[detected_id] += score
                    # 保留每种类型里置信度最高的一次裁剪，供最终解码使用
                    if score > label_best_scores[detected_id]:
                        label_best_scores[detected_id] = score
                        label_crops[detected_id] = crop_box(
                            detect_area_frame, label_points[idx]
                        )

            # 保存画框诊断图（三个模型叠加，红=颜色/绿=形状/蓝=贴纸）
            debug_frame = draw_debug_frame(
                detect_area_frame,
                color_points, color_ids, color_scores,
                shape_points, shape_ids, shape_scores,
                label_points, label_ids, label_scores,
            )
            image_name = f"stage2_r{self.stage2_round:03d}_{i + 1:02d}.jpg"
            debug_path = os.path.join(self.image_dir, image_name)
            original_path = os.path.join(self.image_dir, "original", image_name)
            cv2.imwrite(debug_path, debug_frame)
            cv2.imwrite(original_path, detect_area_frame)

            # 前几个角度识别完成后要求电控转向；最后一次直接发送最终类别
            if i < STAGE_TWO_DETECTION_COUNT - 1:
                self.send_queue.put("r\n")

        self._finalize_stage_two(
            detect_area_frame,
            color_confidence_sums, color_crops,
            shape_confidence_sums, label_confidence_sums, label_crops,
            privileged_shape_id, skip_label,
        )

    def _finalize_stage_two(self, detect_area_frame, color_confidence_sums, color_crops,
                            shape_confidence_sums, label_confidence_sums, label_crops,
                            privileged_shape_id, skip_label):
        """汇总多次检测结果，确定颜色/形状/贴纸类别并发送，否则发送 0。"""
        # ===== 详细诊断（DEBUG_STAGE2=True 时输出）=====
        if DEBUG_STAGE2:
            print("[Stage2 诊断] 最终累加置信度汇总")
            color_dbg = ", ".join(
                f"{DETECT_AREA_COLOR_CLASSES[k]}={float(v):.3f}" for k, v in enumerate(color_confidence_sums) if v > 0
            ) or "(全部为 0)"
            shape_dbg = ", ".join(
                f"{SHAPE_CLASSES[k]}={float(v):.3f}" for k, v in enumerate(shape_confidence_sums) if v > 0
            ) or "(全部为 0)"
            label_dbg = ", ".join(
                f"{LABEL_CLASSES[k]}={float(v):.3f}" for k, v in enumerate(label_confidence_sums) if v > 0
            ) or "(全部为 0)"
            print(f"  颜色: {color_dbg}")
            print(f"  形状: {shape_dbg}")
            print(f"  贴纸: {label_dbg}")
            print(f"  特权形状ID={privileged_shape_id}, "
                  f"最高颜色={DETECT_AREA_COLOR_CLASSES[int(np.argmax(color_confidence_sums))]}(和={float(color_confidence_sums.max()):.3f}), "
                  f"最高形状={SHAPE_CLASSES[int(np.argmax(shape_confidence_sums))]}(和={float(shape_confidence_sums.max()):.3f})")

        has_color = bool(np.any(color_confidence_sums > 0))
        has_shape = privileged_shape_id is not None or bool(np.any(shape_confidence_sums > 0))
        self.pending_display_row = None

        if has_color and has_shape:
            color_id = int(np.argmax(color_confidence_sums))
            shape_id = privileged_shape_id
            if shape_id is None:
                shape_id = int(np.argmax(shape_confidence_sums))
            color_name = DETECT_AREA_COLOR_CLASSES[color_id]
            shape_name = SHAPE_CLASSES[shape_id]
            # 贴纸：图案类直接标记，码/文字类解码；无贴纸则为 None
            label_value = None
            if not skip_label and np.any(label_confidence_sums > 0):
                label_id = int(np.argmax(label_confidence_sums))
                label_name = LABEL_CLASSES[label_id]
                if label_name in LABEL_DECODE_TYPES:
                    label_value = decode_sticker(label_crops[label_id], label_name)
                    if label_value is None:
                        label_value = label_name  # 解码失败退化为类型名
                else:
                    label_value = label_name  # 图案类

            category = categorize(color_name, shape_name, label_value)
            if category == 0:
                self.send_queue.put("0\n")
                print("[阶段2] 类别已满，result=0")
                return
            if category_is_full(self.recognition_counts, category):
                self.send_queue.put("0\n")
                print(f"[阶段2] 储物盒 {category} 已满(>= {MAX_ITEMS_PER_CATEGORY} 件)，result=0")
                return
            object_name = object_display_name(color_name, shape_name, label_value)
            display_image = color_crops[color_id]
            if display_image is None:
                display_image = detect_area_frame

            self.send_queue.put(f"{category}\n")
            self.pending_display_row = (category, object_name, display_image.copy())
            label_text = label_value if label_value is not None else "无"
            print(f"[阶段2] 结果: {color_name} / {shape_name} / {label_text} → 类别 {category}")
        else:
            self.send_queue.put("0\n")
            print("[阶段2] 未识别到有效颜色或形状，result=0")

    # 阶段 3：显示 + 回执 
    def finish_display(self):
        """电控完成本次操作后发 v，视觉先上屏显示，再回复 f。"""
        print("[阶段3] 等待 v(显示)")
        wait_event(self.display_requested, self.stop_event, what="v")

        if self.pending_display_row is not None:
            category, object_name, display_image = self.pending_display_row
            self.recognition_counts[category - 1] += 1
            self.total_sorted += 1
            self.display_row_queue.put(RecognitionRow(
                index=category,
                name=object_name,
                image=display_image,
                total=self.recognition_counts[category - 1],
            ))
        self.send_queue.put("f\n")

    def release_writers(self):
        """释放两路录像 writer。"""
        if self.large_pallet_video_writer is not None:
            self.large_pallet_video_writer.release()
        if self.detect_area_video_writer is not None:
            self.detect_area_video_writer.release()

    def run(self):
        """主循环：只保留阶段编排，具体逻辑下沉到各方法。"""
        while True:
            # 阶段 1：等 c 查栏或 a 直接开始下一轮，期间保持录像
            command = self.wait_for_start_or_check()
            if command == "c":
                self.check_columns()
                continue
            self.drain_stage_two_requests()

            # 阶段 2：多角度累计识别
            self.run_stage_two()

            # 阶段 3：等 v 上屏，回 f
            self.finish_display()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # 视频图片存储
    run_name = time.strftime("%Y%m%d_%H%M%S")
    video_dir = os.path.join(CAPTURE_DIR, "videos")
    image_dir = os.path.join(CAPTURE_DIR, "images", run_name)
    # 创建线程通信队列
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
    # 同一次启动的所有托盘原图共用一个目录，与画框图按文件名对应。
    os.makedirs(os.path.join(image_dir, "pallet_original"), exist_ok=True)
    os.makedirs(os.path.join(image_dir, "original"), exist_ok=True)

    # 摄像头初始化
    large_pallet_cap = initial_cap(CAMERA_PALLET_ID, "托盘上方广角摄像头", PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS)
    detect_area_cap = initial_cap(CAMERA_DETECT_AREA_ID, "识别区摄像头", DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)
    if large_pallet_cap is None or detect_area_cap is None:
        logger.error("摄像头初始化失败，程序退出")
        sys.exit(1)

    # 开线程，两路摄像头始终独立读取，避免阶段切换时拿到积压的旧画面
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

    pallet_runner = None
    detect_area_color_runner = None
    detect_area_shape_runner = None
    detect_area_label_runner = None
    pallet_shape_runner = None
    controller = None

    try:
        # 托盘摄像机保留原录像推理，但不再参与推送判断和通信
        pallet_runner = DetectRunner(PALLET_MODEL_PATH, PALLET_COLOR_CLASSES)
        detect_area_shape_runner = DetectRunner(DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES)
        detect_area_color_runner = DetectRunner(DETECT_AREA_COLOR_MODEL_PATH, DETECT_AREA_COLOR_CLASSES)
        detect_area_label_runner = DetectRunner(DETECT_AREA_LABEL_MODEL_PATH, LABEL_CLASSES)
        if ENABLE_CATEGORY_PRE_CHECK:
            pallet_shape_runner = DetectRunner(PALLET_SHAPE_MODEL_PATH, SHAPE_CLASSES)

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
            pallet_runner, detect_area_color_runner, detect_area_shape_runner,
            detect_area_label_runner,
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
        print(f"流程因故障停止: {error}")
    finally:
        stop_event.set()
        large_pallet_camera.join(timeout=1)
        detect_area_camera.join(timeout=1)
        serial_worker.join(timeout=1)
        display_worker.join(timeout=1)
        for runner in (pallet_runner, detect_area_color_runner,
                       detect_area_shape_runner, detect_area_label_runner,
                       pallet_shape_runner):
            if runner is not None:
                runner.release()
        if controller is not None:
            controller.release_writers()
        #cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
