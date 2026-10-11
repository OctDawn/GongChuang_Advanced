import logging
import os
import queue
import sys
import threading
import time

import cv2
import serial
import numpy as np

from shared.const import *
from yolo.inference_utils import DetectRunner
from yolo.decoder import crop_box, decode_sticker
from yolo.category_counter import rough_category_count
from shared.show import RecognitionRow, run_recognition_display
from shared.common import (
    CameraStopped, get_frame, wait_event, wait_stage_two_signal,
    initial_cap, read_camera, categorize, category_is_full, object_display_name,
)

logger = logging.getLogger("wave.vision")


def choose_target(boxes):
    """从检测框中选出当前需要处理的目标。

    目前是选择面积最大的目标。
    """
    areas = [cv2.contourArea(box) for box in boxes]

    return int(np.argmax(areas))


def communicate_serial(send_queue, stop_event, start_requested, stage_two_requested, finish_requested):
    """独立处理串口收发，避免串口 I/O 阻塞视觉主流程。

    抓取版协议：``a`` 用于启动阶段，``b`` 表示转向完成，``f`` 表示电控操作完成。
    """

    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=0.01)
    except serial.SerialException as error:
        print(f"Failed to open serial port: {error}")
        stop_event.set()
        return

    while not stop_event.is_set():
        try:
            sen = send_queue.get(timeout=0.01)
            ser.write(sen.encode())
            print(sen)
        except queue.Empty:
            pass
        except (serial.SerialException, OSError) as error:
            print(f"串口写入失败: {error}")
            stop_event.set()
            break

        try:
            if ser.in_waiting:
                reply = ser.readline().decode(errors="replace").strip()
                print("Serial received:", reply)
                if reply == "a":
                    start_requested.set()
                elif reply == "b":
                    stage_two_requested.put_nowait(True)
                elif reply == "f":
                    finish_requested.set()
        except (serial.SerialException, OSError) as error:
            print(f"串口读取失败: {error}")
            stop_event.set()
            break
        time.sleep(0.005)

    ser.close()


if __name__ == "__main__":
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
    recognition_counts = [0] * MAX_TEMP_CATEGORIES
    total_sorted = 0
    # 第一个 ``a`` 启动阶段 1，阶段 1 完成后的第二个 ``a`` 启动阶段 2
    start_requested = threading.Event()
    finish_requested = threading.Event()
    os.makedirs(video_dir, exist_ok=True)
    os.makedirs(image_dir, exist_ok=True)

    # 摄像头初始化
    large_pallet_cap = initial_cap(CAMERA_PALLET_ID, "large_pallet", PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS)
    detect_area_cap = initial_cap(CAMERA_DETECT_AREA_ID, "detect_area", DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)
    if large_pallet_cap is None or detect_area_cap is None:
        print("摄像头初始化失败，程序退出")
        sys.exit(1)

    # 开线程，两路摄像头始终独立读取，避免阶段切换时拿到积压的旧画面
    large_pallet_camera = threading.Thread(
        target=read_camera,
        args=(large_pallet_cap, "large_pallet", large_pallet_frame_queue,
              large_pallet_fps_queue, stop_event, False),
        daemon=True,
    )
    detect_area_camera = threading.Thread(
        target=read_camera,
        args=(detect_area_cap, "detect_area", detect_area_frame_queue,
              detect_area_fps_queue, stop_event, True),
        daemon=True,
    )
    serial_worker = threading.Thread(
        target=communicate_serial,
        args=(send_queue, stop_event, start_requested, stage_two_requested, finish_requested),
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
    large_pallet_video_writer = None
    detect_area_video_writer = None
    image_count = 0
    last_center = None
    last_class = None
    stable_since = None

    try:
        pallet_runner = DetectRunner(PALLET_MODEL_PATH, PALLET_COLOR_CLASSES)
        detect_area_shape_runner = DetectRunner(DETECT_AREA_SHAPE_MODEL_PATH, SHAPE_CLASSES)
        detect_area_color_runner = DetectRunner(DETECT_AREA_COLOR_MODEL_PATH, DETECT_AREA_COLOR_CLASSES)
        detect_area_label_runner = DetectRunner(DETECT_AREA_LABEL_MODEL_PATH, LABEL_CLASSES)
        if ENABLE_CATEGORY_PRE_CHECK:
            pallet_shape_runner = DetectRunner(PALLET_SHAPE_MODEL_PATH, SHAPE_CLASSES)

        try:
            large_pallet_fps = large_pallet_fps_queue.get(timeout=5.0)
        except queue.Empty:
            large_pallet_fps = 25.0
        if not large_pallet_fps or large_pallet_fps <= 0:
            large_pallet_fps = 25.0
        try:
            detect_area_fps = detect_area_fps_queue.get(timeout=5.0)
        except queue.Empty:
            detect_area_fps = 25.0
        if not detect_area_fps or detect_area_fps <= 0:
            detect_area_fps = 25.0

        # 程序启动后先等待首个 a；后续轮次由阶段 2 收到的 a 直接触发。
        print("Waiting for first serial command: a")
        wait_event(start_requested, stop_event, what="a")
        while True:
            print("Stage 1 started")
            category_count = None

            while True:
                # 阶段 1 只使用 large_pallet 画面推理，detect_area 画面目前仅参与录像
                large_pallet_frame = get_frame(large_pallet_frame_queue, stop_event)
                detect_area_frame = get_frame(detect_area_frame_queue, stop_event)
                if large_pallet_frame is None or detect_area_frame is None:
                    continue
                boxes, class_ids, result = pallet_runner.infer(large_pallet_frame, draw=True)
                #录像
                if large_pallet_video_writer is None:
                    height, width = result.shape[:2]
                    large_pallet_video_writer = cv2.VideoWriter(
                        os.path.join(video_dir, f"{run_name}_large_pallet.mp4"),
                        cv2.VideoWriter_fourcc(*"mp4v"), large_pallet_fps,
                        (width, height),
                    )
                if detect_area_video_writer is None:
                    height, width = detect_area_frame.shape[:2]
                    detect_area_video_writer = cv2.VideoWriter(
                        os.path.join(video_dir, f"{run_name}_detect_area.mp4"),
                        cv2.VideoWriter_fourcc(*"mp4v"), detect_area_fps,
                        (width, height),
                    )
                large_pallet_video_writer.write(result)
                detect_area_video_writer.write(detect_area_frame)

                if boxes.size:
                    target = choose_target(boxes)
                    points = boxes[target]
                    center = points.mean(axis=0)
                    class_id = int(class_ids[target])
                    still = (last_center is not None and last_class == class_id and
                             np.linalg.norm(center - last_center) <= MOVE_THRESHOLD)
                    if still and time.monotonic() - stable_since >= STABLE_SECONDS:
                        frame_height, frame_width = large_pallet_frame.shape[:2]

                        # 目标中心和框尺寸均映射到 0.0~1.0，但是这是相对于图而言，所以还需要标定
                        x = float(np.clip(center[0] / frame_width, 0.0, 1.0))
                        y = float(np.clip(center[1] / frame_height, 0.0, 1.0))
                        object_width = float(np.clip(np.linalg.norm(points[3] - points[0]) / frame_width, 0.0, 1.0))
                        object_height = float(np.clip(np.linalg.norm(points[1] - points[0]) / frame_height, 0.0, 1.0))

                        # 清除阶段 1 期间收到的旧命令，再向电控发送目标信息
                        start_requested.clear()
                        try:
                            while True:
                                stage_two_requested.get_nowait()
                        except queue.Empty:
                            pass
                        send_queue.put(f"{x:.4f},{y:.4f},{object_width:.4f},{object_height:.4f}\n")
                        image_count += 1
                        image_name = f"{run_name}_{image_count:04d}_{PALLET_COLOR_CLASSES[class_id]}.jpg"
                        cv2.imwrite(os.path.join(image_dir, image_name), large_pallet_frame)

                        # 粗略判断托盘上一共几种类别（形状模型训练好且开关打开时才执行）
                        if pallet_shape_runner is not None:
                            shape_boxes, shape_ids = pallet_shape_runner.infer(large_pallet_frame)
                            category_count, _ = rough_category_count(boxes, class_ids, shape_boxes, shape_ids)
                            print(f"Stage 1 rough category count: {category_count}")

                        # 阶段 1 完成，清空稳定性状态
                        last_center = last_class = None
                        stable_since = None

                        # 等待第二个 a；该 a 同时触发阶段 2 的第一次检测
                        print("Stage 1 finished, waiting for second serial command: a")
                        wait_event(start_requested, stop_event, what="a")
                        finish_requested.clear()

                        # 阶段 2：第二个 a 触发第一次检测，之后每收到一个 b 再检测一次
                        color_confidence_sums = np.zeros(len(DETECT_AREA_COLOR_CLASSES), dtype=np.float32)
                        color_best_scores = np.full(len(DETECT_AREA_COLOR_CLASSES), -1.0, dtype=np.float32)
                        color_crops = [None] * len(DETECT_AREA_COLOR_CLASSES)
                        shape_confidence_sums = np.zeros(len(SHAPE_CLASSES), dtype=np.float32)
                        label_confidence_sums = np.zeros(len(LABEL_CLASSES), dtype=np.float32)
                        label_best_scores = np.full(len(LABEL_CLASSES), -1.0, dtype=np.float32)
                        label_crops = [None] * len(LABEL_CLASSES)
                        privileged_shape_id = None
                        privileged_shape_score = -1.0
                        # 粗略类别数达到阈值（种类足够多）时，跳过贴纸识别
                        skip_label = (ENABLE_CATEGORY_PRE_CHECK and
                                      category_count is not None and
                                      category_count >= CATEGORY_COUNT_THRESHOLD)

                        for i in range(STAGE_TWO_DETECTION_COUNT):
                            if i > 0:
                                print(f"Stage 2 waiting for b ({i}/{STAGE_TWO_DETECTION_COUNT - 1})")
                                wait_stage_two_signal(stage_two_requested, stop_event)
                            detect_area_frame = get_frame(detect_area_frame_queue, stop_event)
                            if detect_area_frame is None:
                                raise CameraStopped()
                            color_points, color_ids, color_scores = (
                                detect_area_color_runner.infer(
                                    detect_area_frame, return_scores=True
                                )
                            )
                            _, shape_ids, shape_scores = detect_area_shape_runner.infer(detect_area_frame, return_scores=True)

                            # 累加各次检测中每个颜色、形状类别的全部置信度
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

                            # 贴纸检测：图案类直接标记，码/文字类需要解码；种类足够多时跳过
                            if not skip_label:
                                label_points, label_ids, label_scores = detect_area_label_runner.infer(detect_area_frame, return_scores=True)
                                for idx, detected_id in enumerate(label_ids):
                                    detected_id = int(detected_id)
                                    if not 0 <= detected_id < len(LABEL_CLASSES):
                                        continue
                                    score = float(label_scores[idx])
                                    label_confidence_sums[detected_id] += score
                                    # 保留每种类型里置信度最高的一次裁剪，供最终解码使用
                                    if score > label_best_scores[detected_id]:
                                        label_best_scores[detected_id] = score
                                        label_crops[detected_id] = crop_box(detect_area_frame, label_points[idx])

                            # 前几个角度识别完成后要求电控转向；最后一次直接发送最终结果
                            if i < STAGE_TWO_DETECTION_COUNT - 1:
                                send_queue.put("r\n")

                        has_color = bool(np.any(color_confidence_sums > 0))
                        has_shape = privileged_shape_id is not None or bool(np.any(shape_confidence_sums > 0))
                        if has_color and has_shape:
                            color_id = int(np.argmax(color_confidence_sums))
                            shape_id = privileged_shape_id
                            if shape_id is None:
                                shape_id = int(np.argmax(shape_confidence_sums))
                            # 贴纸：图案类直接标记，码/文字类解码；无贴纸或跳过贴纸则为 None
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

                            category = categorize(
                                DETECT_AREA_COLOR_CLASSES[color_id],
                                SHAPE_CLASSES[shape_id],
                                label_value,
                            )
                            if category == 0:
                                pending_display_row = None
                                send_queue.put("0\n")
                                print("Stage 2: 类别已满，无法新增类别，result=0")
                            elif category_is_full(recognition_counts, category):
                                pending_display_row = None
                                send_queue.put("0\n")
                                print(f"Stage 2: 储物盒 {category} 已满(>= {MAX_ITEMS_PER_CATEGORY} 件)，result=0")
                            else:
                                object_name = object_display_name(
                                    DETECT_AREA_COLOR_CLASSES[color_id],
                                    SHAPE_CLASSES[shape_id],
                                    label_value,
                                )
                                display_image = color_crops[color_id]
                                if display_image is None:
                                    display_image = detect_area_frame
                                pending_display_row = (category, object_name, display_image.copy())
                                send_queue.put(f"{category}\n")
                                print(f"Stage 2 result: color={DETECT_AREA_COLOR_CLASSES[color_id]}, "
                                      f"shape={SHAPE_CLASSES[shape_id]}, label={label_value}, category={category}")
                        else:
                            pending_display_row = None
                            send_queue.put("0\n")
                            print("Stage 2 failed: no valid color or shape was detected, result=0")

                        # 电控完成本次操作返回 f 后，再更新显示屏
                        print("Stage 2 waiting for serial command: f")
                        wait_event(finish_requested, stop_event, what="f")
                        if pending_display_row is not None:
                            category, object_name, display_image = pending_display_row
                            recognition_counts[category - 1] += 1
                            total_sorted += 1
                            display_row_queue.put(RecognitionRow(
                                index=category,
                                name=object_name,
                                image=display_image,
                                total=recognition_counts[category - 1],
                            ))

                        # 等待下一轮的第一个 a，再开始下一轮阶段 1
                        print("Waiting for next serial command: a")
                        wait_event(start_requested, stop_event, what="a")
                        break
                    elif not still:
                        last_center = center
                        last_class = class_id
                        stable_since = time.monotonic()
                else:
                    last_center = last_class = None
                    stable_since = None

                #if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                #    break
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
        if pallet_runner is not None:
            pallet_runner.release()
        if detect_area_color_runner is not None:
            detect_area_color_runner.release()
        if detect_area_shape_runner is not None:
            detect_area_shape_runner.release()
        if detect_area_label_runner is not None:
            detect_area_label_runner.release()
        if pallet_shape_runner is not None:
            pallet_shape_runner.release()
        if large_pallet_video_writer is not None:
            large_pallet_video_writer.release()
        if detect_area_video_writer is not None:
            detect_area_video_writer.release()
        #cv2.destroyAllWindows()
