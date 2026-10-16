"""主程序（LLM 版）：多线程串口循环 + Qwen-VL 识别。

串口协议、阶段编排、显示沿用早期 YOLO 版的框架。
查栏和三路固定机位分类优先 Qwen-VL；单轮超过 LLM_FALLBACK_TIMEOUT（线上 .env=15s）
或失败时回 0（YOLO_FALLBACK=0），本件不分拣。

贴纸已开放化：图案类用 LLM 开放描述；码/文字类用 label 模型定位贴纸框
后交给 yolo/decoder 确定性解码（定位关闭或未解出时降级为整帧解码）。

协议（电控 -> 视觉）：
    a  开始下一轮（直接进入阶段 2）
    b  （已废弃）机械改造前的「转向完成」：识别区改三路固定机位后不再需要，
       收到只记 debug 日志、不置位任何事件
    c  检查托盘各栏
    v  电控操作完成，请求视觉显示

视觉 -> 电控：
    （已删除）r  要求转向：识别区不再旋转，三路机位一次拍齐
    0 / 1~6  分类结果（0=无物体/失败/类别已满）
    1/0 ×5   查栏结果，每栏后带换行（如 "1\\n0\\n1\\n1\\n0\\n"）
    f        显示完成回执
"""

import logging
import os
import queue
import sys
import threading
import time
from contextlib import ExitStack

import cv2

from shared.const import (
    SERIAL_PORT, BAUDRATE, CAPTURE_DIR,
    CAMERA_PALLET_ID, DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES,
    PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS,
    DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
    MAX_TEMP_CATEGORIES, MAX_ITEMS_PER_CATEGORY, STAGE_TWO_DETECTION_COUNT, COLUMN_COUNT,
    LASER_SENSOR_ON_LEFT,
    SAVE_LLM_RESPONSES, LLM_RESPONSE_DIR,
    LABEL_CLASSES,
)
from llm.prompt import (
    PROMPT_PALLET_COLUMNS,
)
from llm.qw import qwen_realise, LLMError
from llm.shape_memory import ShapeMemory
from llm.shape_recognition import ShapeRecognizer
from llm.empty_examples import load_empty_examples, load_shape_examples
from shared.show import RecognitionRow, run_recognition_display
from shared.common import (
    CameraStopped, get_frame, get_frame_retry, wait_event,
    initial_cap, crop_pallet_llm, read_camera, communicate_serial, categorize,
    category_is_full, object_display_name, sticker_decode_enabled,
)
from yolo.decoder import decode_sticker, crop_box
from yolo.fallback import YoloFallback

logger = logging.getLogger("wave.vision")
LLM_FALLBACK_TIMEOUT = float(os.environ.get("LLM_FALLBACK_TIMEOUT", "35"))  # 单轮 LLM 总时限（秒）；线上 .env 固定 15（硬规定），超时回 0

# 显示屏「图片」列取哪个机位的画面：0=①斜上、1=②斜上、2=③正下方仰视。
# ③ 是正下方仰视，可能完全拍不到本体（只看到底面），不能当缩略图，所以默认取 ①。
DISPLAY_VIEW_INDEX = int(os.environ.get("DISPLAY_VIEW_INDEX", "0"))


def save_images_worker(image_queue, stop_event, image_dir):
    """后台落盘 stage2 原图，不占用「拍完 → 发请求」的关键路径。

    原图只用于事后排查，识别本身不用它们，所以交给本线程写盘即可；
    即使本轮 LLM 超时/报错，图片也已经在本线程里落盘，不会丢诊断记录。
    """
    original_dir = os.path.join(image_dir, "original")
    while not stop_event.is_set():
        try:
            round_no, frames = image_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        for idx, frame in enumerate(frames, start=1):
            try:
                # 文件名里的 idx 就是机位号：01=①斜上 02=②斜上 03=③正下方仰视
                cv2.imwrite(
                    os.path.join(original_dir, f"stage2_r{round_no:03d}_{idx:02d}.jpg"),
                    frame,
                )
            except Exception:  # noqa: BLE001 —— 落盘失败不得影响主流程
                logger.exception("原图保存失败 r%03d_%02d", round_no, idx)


class PushController:
    """托盘推送主控制流程（LLM 版）。

    与早期 YOLO 版 PushController 的职责一致：阶段 1 查栏、阶段 2 三路固定机位识别、
    阶段 3 显示回执，共享状态统一收在实例里。
    """

    def __init__(self, label_runner, pallet_frame_queue, detect_area_frame_queues, send_queue,
                 display_row_queue,
                 start_requested, column_check_requested, display_requested,
                 stop_event, video_dir, image_dir, run_name, pallet_fps, detect_area_fps,
                 empty_examples, shape_examples, yolo_fallback, display_wake=None):
        self.label_runner = label_runner
        self.yolo_fallback = yolo_fallback
        self.empty_examples = empty_examples
        self.pallet_frame_queue = pallet_frame_queue
        self.detect_area_frame_queues = tuple(detect_area_frame_queues)
        self.send_queue = send_queue
        # 识别区改三路固定机位后不再等 b 握手，原 stage_two_requested 队列已整体移除
        self.display_row_queue = display_row_queue
        self.start_requested = start_requested
        self.column_check_requested = column_check_requested
        self.display_requested = display_requested
        self.display_wake = display_wake   # 亮屏门控：置位后永不清除
        self.stop_event = stop_event

        self.video_dir = video_dir
        self.image_dir = image_dir
        self.run_name = run_name
        self.pallet_fps = pallet_fps
        self.detect_area_fps = detect_area_fps

        self.large_pallet_video_writer = None
        self.detect_area_video_writer = None
        self.recognition_counts = [0] * MAX_TEMP_CATEGORIES
        self.discarded = 0      # 类别满/盒满被丢弃的件数（显示屏「未归类」行）
        self.total_sorted = 0
        self.pending_display_row = None
        self.stage1_round = 0
        self.stage2_round = 0
        self._resp_counter = 0
        self.shape_recognizer = ShapeRecognizer(ShapeMemory(
            image_dir, limit=int(os.environ.get("LLM_SHAPE_MEMORY_LIMIT", "12")),
            view_count=STAGE_TWO_DETECTION_COUNT,
        ), empty_examples["detect_area"], shape_examples=shape_examples)
        logger.info("本次运行形状记忆目录: %s（不载入旧会话）", self.shape_recognizer.memory.root)
        # 原图落盘改为后台线程：写盘不再阻塞「拍完 → 发请求」。
        # 队列满时丢弃原图（只影响诊断图，不影响识别），绝不阻塞采集流程。
        self.image_queue = queue.Queue(maxsize=4)
        self.image_writer = threading.Thread(
            target=save_images_worker,
            args=(self.image_queue, stop_event, image_dir),
            name="image-writer", daemon=True,
        )
        self.image_writer.start()

    # ---- LLM 响应落盘（调试） ----
    def _response_path(self, kind):
        self._resp_counter += 1
        os.makedirs(LLM_RESPONSE_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        return os.path.join(LLM_RESPONSE_DIR, f"{ts}_{self._resp_counter:04d}_{kind}.txt")

    # ---- 录像 ----
    def record_streams(self, pallet_frame, detect_frame):
        if self.large_pallet_video_writer is None:
            h, w = pallet_frame.shape[:2]
            self.large_pallet_video_writer = cv2.VideoWriter(
                os.path.join(self.video_dir, f"{self.run_name}_large_pallet.mp4"),
                cv2.VideoWriter_fourcc(*"mp4v"), self.pallet_fps, (w, h),
            )
        # 只录识别区①（三路 720p 同时写 mp4 会明显抢 IO；要全录就再各建一个 writer）
        if self.detect_area_video_writer is None:
            h, w = detect_frame.shape[:2]
            self.detect_area_video_writer = cv2.VideoWriter(
                os.path.join(self.video_dir, f"{self.run_name}_detect_area.mp4"),
                cv2.VideoWriter_fourcc(*"mp4v"), self.detect_area_fps, (w, h),
            )
        self.large_pallet_video_writer.write(pallet_frame)
        self.detect_area_video_writer.write(detect_frame)

    # ---- 阶段 1：等待 c（查栏）或 a（开始下一轮） ----
    def wait_for_start_or_check(self):
        logger.info("等待 c（查栏）或 a（开始下一轮）")
        while not (self.column_check_requested.is_set() or self.start_requested.is_set()):
            if self.stop_event.is_set():
                raise CameraStopped()
            pallet_frame = get_frame(self.pallet_frame_queue, self.stop_event)
            detect_frame = get_frame(self.detect_area_frame_queues[0], self.stop_event)
            if pallet_frame is None or detect_frame is None:
                continue
            self.record_streams(pallet_frame, detect_frame)

        # 亮屏门控用的事件必须“永不清除”：显示线程 20fps 才采样一次，而下面两个 Event
        # 会被立即 clear()，直接采样它们有概率整段错过（表现为一直黑屏）。
        if self.display_wake is not None:
            self.display_wake.set()
        if self.column_check_requested.is_set():
            self.column_check_requested.clear()
            return "c"
        self.start_requested.clear()
        return "a"

    # ---- 阶段 1：查栏 ----
    def check_columns(self):
        self.stage1_round += 1
        pallet_frame = get_frame_retry(self.pallet_frame_queue, self.stop_event, "托盘摄像头")
        if pallet_frame is None:
            raise CameraStopped()

        # 保存原图
        cv2.imwrite(
            os.path.join(self.image_dir, "pallet_original", f"stage1_r{self.stage1_round:03d}.jpg"),
            pallet_frame,
        )
        # 裁剪托盘区域后再交给大模型
        tray = crop_pallet_llm(pallet_frame)
        cv2.imwrite(os.path.join(self.image_dir, f"stage1_r{self.stage1_round:03d}_tray.jpg"), tray)

        save_path = self._response_path("pallet") if SAVE_LLM_RESPONSES else None
        try:
            occupancy = qwen_realise(
                [tray], f"以下是第 2 张图片，即当前托盘。只返回 {COLUMN_COUNT} 栏占用的 JSON 数组。",
                message_save_path=save_path, validator=self._parse_pallet_result,
                total_timeout=LLM_FALLBACK_TIMEOUT,
                prefix_sections=[(PROMPT_PALLET_COLUMNS, [self.empty_examples["pallet"]])],
            )   # LLM 返回从左到右的布尔数组
            if not LASER_SENSOR_ON_LEFT:
                occupancy.reverse()
        except LLMError as error:
            if self.yolo_fallback is None:
                # YOLO_FALLBACK=0：不回落，交给 run() 的“查栏失败保守回全 1”处理
                logger.error("托盘 LLM 失败（YOLO_FALLBACK=0，不回落）: %s", error)
                raise
            logger.warning("托盘 LLM 超时或失败，切换 YOLO: %s", error)
            self._check_running()
            try:
                # YOLO 几何函数已经处理传感器方向，不再反转。
                started = time.monotonic()
                occupancy = self.yolo_fallback.check_columns(pallet_frame)
                logger.info("托盘 YOLO fallback 耗时 %.2f 秒", time.monotonic() - started)
            except CameraStopped:
                raise
            except Exception as error:
                self._check_running()
                raise LLMError(f"托盘 YOLO fallback 失败: {error}") from error
        self._check_running()
        reply = "".join("1\n" if x else "0\n" for x in occupancy)
        self.send_queue.put(reply)
        logger.info("托盘栏占用: %s", reply.strip().replace("\n", ""))

    def _parse_pallet_result(self, result):
        if not isinstance(result, list) or len(result) != COLUMN_COUNT:
            raise LLMError(f"托盘结果应为长度 {COLUMN_COUNT} 的数组: {result!r}")
        out = []
        for v in result:
            if isinstance(v, bool):
                out.append(v)
            elif isinstance(v, (int, float)) and v in (0, 1):
                out.append(bool(v))
            elif isinstance(v, str) and v.strip().lower() in ("0", "1", "true", "false", "yes", "no"):
                out.append(v.strip().lower() in ("1", "true", "yes"))
            else:
                raise LLMError(f"托盘结果值非法: {result!r}")
        return out

    def _fresh_frame(self, frame_queue, name):
        """取 a 之后拍到的最新一帧：先丢掉积压（a 之前或上一件的旧画面），再等新帧。

        每路相机线程都以 maxsize=1 持续刷新，所以「丢一次 + 取一次」既保证画面新，
        又不会等太久（约一个帧周期）。"""
        while True:
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                break
        return get_frame_retry(frame_queue, self.stop_event, name)

    # ---- 阶段 2：三路固定机位一次拍齐 + 单轮历史参考比对 ----
    def run_stage_two(self):
        self.stage2_round += 1
        frames = []

        # 机械改造：识别区不再旋转，a 一到就从三路各自取「最新一帧」（误差约 1 帧 ≈ 33ms）
        if len(self.detect_area_frame_queues) != STAGE_TWO_DETECTION_COUNT:
            logger.warning("识别区机位数(%d)与 STAGE_TWO_DETECTION_COUNT(%d) 不一致",
                           len(self.detect_area_frame_queues), STAGE_TWO_DETECTION_COUNT)
        for index, (frame_queue, name) in enumerate(
                zip(self.detect_area_frame_queues, DETECT_CAMERA_NAMES), start=1):
            frame = self._fresh_frame(frame_queue, name)
            if frame is None:
                raise CameraStopped()
            logger.info("阶段 2 取帧 %d/%d：%s", index, STAGE_TWO_DETECTION_COUNT, name)
            frames.append(frame)

        # 原图交给后台线程落盘：写盘不再占用「拍完 → 发请求」的关键路径
        try:
            self.image_queue.put_nowait((self.stage2_round, frames))
        except queue.Full:
            logger.warning("原图落盘队列已满，丢弃第 %d 轮原图（不影响识别）", self.stage2_round)

        self._finalize_stage_two(frames)

    def _finalize_stage_two(self, frames):
        self.pending_display_row = None
        try:
            color, shape, label_kind, label_description = self.shape_recognizer.classify(
                frames, response_path=self._response_path if SAVE_LLM_RESPONSES else None,
                total_timeout=LLM_FALLBACK_TIMEOUT,
            )
        except LLMError as e:
            if self.yolo_fallback is None:
                # YOLO_FALLBACK=0：非法/不确定输出直接回 0，本件不分拣
                logger.error("识别区 LLM 失败（YOLO_FALLBACK=0），本轮回 0: %s", e)
                self._check_running()
                self.send_queue.put("0\n")
                return
            logger.warning("识别区 LLM 超时或失败，切换多视角 YOLO: %s", e)
            self._check_running()
            try:
                started = time.monotonic()
                color, shape, label = self.yolo_fallback.classify(frames)
                logger.info("识别区 YOLO fallback 耗时 %.2f 秒", time.monotonic() - started)
            except CameraStopped:
                raise
            except Exception as error:
                logger.error("识别区 YOLO fallback 失败: %s", error)
                self._check_running()
                self.send_queue.put("0\n")
                return
        else:
            # 正常 LLM 结果仍采用开放图案描述与原有贴纸解码。
            self._check_running()
            label = (self._resolve_label(label_kind, label_description, frames)
                     if color is not None or shape is not None else None)

        self._check_running()

        if color is None and shape is None:
            logger.info("阶段 2 结果：无物体")
            self.pending_display_row = None
            self.send_queue.put("0\n")
            return

        category = categorize(color, shape, label)
        if category == 0:
            logger.error("阶段 2：类别已满，无法新增类别，result=0")
            self._show_discarded(color, shape, label, frames, "类别已满")
            return
        if category_is_full(self.recognition_counts, category):
            logger.error("阶段 2：储物盒 %d 已满(>= %d 件)，result=0", category, MAX_ITEMS_PER_CATEGORY)
            self._show_discarded(color, shape, label, frames, "储物盒已满")
            return

        object_name = object_display_name(color, shape, label)
        display_image = (frames[DISPLAY_VIEW_INDEX]
                         if len(frames) > DISPLAY_VIEW_INDEX else None)
        self.pending_display_row = (category, object_name, display_image.copy() if display_image is not None else None)
        self.send_queue.put(f"{category}\n")
        logger.info("阶段 2 结果: color=%s shape=%s label=%s category=%d", color, shape, label, category)

    def _show_discarded(self, color, shape, label, frames, reason):
        """类别满/盒满：照旧回 0，但在显示屏「未归类」行留痕，避免静默丢件。"""
        self.discarded += 1
        self.pending_display_row = None
        name = object_display_name(color, shape, label)
        image = (frames[DISPLAY_VIEW_INDEX].copy()
                 if len(frames) > DISPLAY_VIEW_INDEX else None)
        self.display_row_queue.put(RecognitionRow(
            index=MAX_TEMP_CATEGORIES + 1, name=f"{name}（{reason}）",
            image=image, total=self.discarded))
        logger.warning("未归类第 %d 件: %s（%s）", self.discarded, name, reason)
        self.send_queue.put("0\n")

    def _check_running(self):
        if self.stop_event.is_set():
            raise CameraStopped()

    # ---- 贴纸开放化：图案描述 + 码/文字确定性解码 ----
    def _resolve_label(self, label_kind, label_description, frames):
        """把 LLM 的开放贴纸判断落成最终 label 字符串。"""
        if label_kind == "none":
            return None
        if label_kind == "pattern":
            return label_description or "图案"
        # 解码总开关关闭（默认）时，直接用 LLM 给的 kind/描述作类别键，不做任何解码：
        # 既省掉整帧扫描的开销，也避免"解码成功=内容 / 失败=kind"把同类货拆成两类。
        if not sticker_decode_enabled():
            return label_description or label_kind
        # text/qrcode/barcode：优先 label 模型定位框 crop 解码，退化为整帧解码
        decoded = None
        if self.label_runner is not None:
            decoded = self._decode_with_label_boxes(label_kind, frames)
        if not decoded:
            decoded = self._decode_whole_frame(label_kind, frames)
        if decoded:
            logger.info("贴纸解码(%s): %s", label_kind, decoded)
            return decoded
        return label_description or label_kind

    def _decode_with_label_boxes(self, label_kind, frames):
        """用 label 模型定位贴纸框 → crop → decoder 解码。"""
        if label_kind not in LABEL_CLASSES:
            return None
        target_id = LABEL_CLASSES.index(label_kind)
        for frame in frames:
            try:
                points, class_ids, scores = self.label_runner.infer(frame, return_scores=True)
            except Exception as e:  # noqa: BLE001 —— rknn 推理失败则降级
                logger.warning("label 模型推理失败: %s", e)
                return None
            best = None
            for pts, cid, sc in zip(points, class_ids, scores):
                if int(cid) == target_id and (best is None or sc > best[0]):
                    best = (sc, pts)
            if best is None:
                continue
            crop = crop_box(frame, best[1])
            decoded = decode_sticker(crop, label_kind)
            if decoded:
                return decoded
        return None

    def _decode_whole_frame(self, label_kind, frames):
        """退化：对整帧做解码（二维码/条码可全图扫，文字效果较差）。"""
        for frame in frames:
            decoded = decode_sticker(frame, label_kind)
            if decoded:
                return decoded
        return None

    # ---- 阶段 3：等 v 上屏，回 f ----
    def finish_display(self):
        logger.info("等待 v")
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
        if self.large_pallet_video_writer is not None:
            self.large_pallet_video_writer.release()
        if self.detect_area_video_writer is not None:
            self.detect_area_video_writer.release()
        # 等后台把队列里的原图写完（退出时不丢诊断图；最多等 3 秒）
        if self.image_writer is not None:
            self.image_writer.join(timeout=3.0)

    def run(self):
        while True:
            command = self.wait_for_start_or_check()
            if command == "c":
                try:
                    self.check_columns()
                except LLMError as e:
                    # 查栏失败保守回全 1（视为都有物体），避免误判为空导致漏推
                    logger.error("查栏失败，保守回全 1: %s", e)
                    self.send_queue.put("".join("1\n" for _ in range(COLUMN_COUNT)))
                continue

            self.run_stage_two()
            self.finish_display()

def _run(empty_examples, shape_examples, fallback):
    run_name = time.strftime("%Y%m%d_%H%M%S")
    video_dir = os.path.join(CAPTURE_DIR, "videos")
    image_dir = os.path.join(CAPTURE_DIR, "images", run_name)

    large_pallet_frame_queue = queue.Queue(maxsize=1)
    large_pallet_fps_queue = queue.Queue(maxsize=1)
    # 识别区三路固定机位：每路一个「最新帧」队列（各由一个采集线程持续刷新）
    detect_frame_queues = [queue.Queue(maxsize=1) for _ in DETECT_CAMERA_IDS]
    detect_fps_queues = [queue.Queue(maxsize=1) for _ in DETECT_CAMERA_IDS]
    send_queue = queue.Queue()
    display_row_queue = queue.Queue()
    stop_event = threading.Event()
    start_requested = threading.Event()
    column_check_requested = threading.Event()
    display_requested = threading.Event()

    os.makedirs(video_dir, exist_ok=True)
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(os.path.join(image_dir, "pallet_original"), exist_ok=True)
    os.makedirs(os.path.join(image_dir, "original"), exist_ok=True)

    large_pallet_cap = initial_cap(CAMERA_PALLET_ID, "托盘上方广角摄像头", PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS)
    detect_caps = [
        initial_cap(camera_id, name, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)
        for camera_id, name in zip(DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES)
    ]
    if large_pallet_cap is None or any(cap is None for cap in detect_caps):
        logger.error("摄像头初始化失败，程序退出")
        sys.exit(1)

    large_pallet_camera = threading.Thread(
        target=read_camera,
        args=(large_pallet_cap, "托盘上方广角摄像头", large_pallet_frame_queue,
              large_pallet_fps_queue, stop_event, False),
        daemon=True,
    )
    detect_cameras = [
        threading.Thread(
            target=read_camera,
            args=(cap, name, frame_queue, fps_queue, stop_event, True),
            daemon=True,
        )
        for cap, name, frame_queue, fps_queue in zip(
            detect_caps, DETECT_CAMERA_NAMES, detect_frame_queues, detect_fps_queues)
    ]
    serial_worker = threading.Thread(
        target=communicate_serial,
        args=(send_queue, stop_event, start_requested,
              column_check_requested, display_requested),
        daemon=True,
    )
    # 上电后先不亮屏：显示线程先只铺纯黑，等电控第一次发 a/c 再点亮并画表格。
    # SCREEN_OFF_UNTIL_START=0 可关掉这个门控（调试时方便，一启动就出表格）；
    # SCREEN_DPMS=1 时额外用 xset 真灭背光（需要 x11-xserver-utils）。
    # 亮屏门控：不能直接采样 start_requested / column_check_requested —— 它们会被主循环
    # 立即 clear()，而显示线程 20fps 才采样一次，有概率整段错过（表现为“一直黑屏”）。
    # 这里用一个永不清除的事件 display_wake，收到 a/c 即置位；同时把两个原始事件也一并
    # 传进去，谁能先被看到都算点亮。SCREEN_OFF_UNTIL_START=0 时完全不走门控。
    screen_gate_on = os.environ.get("SCREEN_OFF_UNTIL_START", "1").strip().lower() not in ("0", "false", "no", "off")
    screen_dpms = os.environ.get("SCREEN_DPMS", "0").strip().lower() not in ("0", "false", "no", "off")
    display_wake = threading.Event() if screen_gate_on else None
    display_worker = threading.Thread(
        target=run_recognition_display,
        args=(display_row_queue, stop_event, False,
              (display_wake, start_requested, column_check_requested) if screen_gate_on else (),
              screen_dpms),
        name="recognition-display",
        daemon=True,
    )
    large_pallet_camera.start()
    for thread in detect_cameras:
        thread.start()
    serial_worker.start()
    display_worker.start()

    # 贴纸解码总开关（默认关闭）：LLM_STICKER_DECODE=1 才加载 label 定位模型并做解码；
    # =0（默认）完全不解码，label 直接取 LLM 给的 label_kind / label_description。
    label_runner = None
    if os.environ.get("LLM_STICKER_DECODE", "0").strip().lower() not in ("0", "false", "no", "off"):
        label_runner = fallback.label_runner if fallback is not None else None

    controller = None
    try:
        try:
            pallet_fps = large_pallet_fps_queue.get(timeout=5.0)
        except queue.Empty:
            pallet_fps = 25.0
        if not pallet_fps or pallet_fps <= 0:
            pallet_fps = 25.0
        try:
            detect_area_fps = detect_fps_queues[0].get(timeout=5.0)
        except queue.Empty:
            detect_area_fps = 25.0
        if not detect_area_fps or detect_area_fps <= 0:
            detect_area_fps = 25.0

        controller = PushController(
            label_runner,
            large_pallet_frame_queue, detect_frame_queues, send_queue,
            display_row_queue,
            start_requested, column_check_requested, display_requested,
            stop_event,
            video_dir, image_dir, run_name, pallet_fps, detect_area_fps,
            empty_examples, shape_examples, fallback, display_wake,
        )
        controller.run()

    except KeyboardInterrupt:
        pass
    except CameraStopped as error:
        logger.error("流程因故障停止: %s", error)
    finally:
        stop_event.set()
        large_pallet_camera.join(timeout=1)
        for thread in detect_cameras:
            thread.join(timeout=1)
        serial_worker.join(timeout=1)
        display_worker.join(timeout=1)
        if controller is not None:
            controller.release_writers()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    empty_examples = load_empty_examples()
    shape_examples = load_shape_examples()
    # YOLO_FALLBACK=0：完全不加载 YOLO 回落模型（省启动时间与内存）；
    # 识别区 LLM 非法/失败/uncertain 时直接回 0；查栏失败仍按原逻辑保守回全 1。
    yolo_fallback_on = os.environ.get("YOLO_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")
    if not yolo_fallback_on:
        logger.warning("YOLO_FALLBACK=0：不加载 YOLO 回落模型，识别区失败即回 0")
    with ExitStack() as models:
        fallback = YoloFallback.load(models) if yolo_fallback_on else None
        _run(empty_examples, shape_examples, fallback)
