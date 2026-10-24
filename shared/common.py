"""公共工具：摄像头、串口、帧取、临时分类、图像裁剪与托盘几何。

唯一入口 main_push_llm.py 的基础层（YOLO 版 main_push / main_grab 已归档删除）。
所有日志统一走 logging（logger 名 wave.vision）。
"""

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np
import serial

from shared.const import (
    SERIAL_PORT, BAUDRATE, MAX_TEMP_CATEGORIES, MAX_ITEMS_PER_CATEGORY,
    COLUMN_COUNT, TRAY_X_MIN, TRAY_X_MAX, TRAY_Y_MIN, TRAY_Y_MAX,
    LASER_SENSOR_ON_LEFT,
    PALLET_LLM_INSET, DETECT_LLM_INSET, FRAME_RETRY_TIMES,
    DETECT_CROP_MODE, DETECT_SEND_MAX_SIDE, DETECT_FULL_INSET,
)

logger = logging.getLogger("wave.vision")


class CameraStopped(Exception):
    """摄像头/串口等上游故障，要求主流程退出。"""


@dataclass(frozen=True)
class FramePacket:
    sequence: int
    captured_at: float
    image: np.ndarray


class FrameStream:
    """单采集者、多读者最新帧广播；各读者独立游标，读取不移除帧。"""
    def __init__(self):
        self._condition = threading.Condition()
        self._packet = None

    def publish(self, image, captured_at=None):
        frame = image.copy()
        frame.flags.writeable = False
        with self._condition:
            sequence = self._packet.sequence + 1 if self._packet else 1
            self._packet = FramePacket(sequence, time.perf_counter() if captured_at is None else captured_at, frame)
            self._condition.notify_all()

    def latest(self):
        with self._condition:
            return self._packet

    def wait_after(self, sequence, stop_event, timeout=2.0, after_time=0.0):
        deadline = time.perf_counter() + timeout
        with self._condition:
            while not stop_event.is_set():
                packet = self._packet
                if packet and packet.sequence > sequence and packet.captured_at > after_time:
                    return packet
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError("摄像头没有提供新画面")
                self._condition.wait(min(0.1, remaining))
        raise InterruptedError("采集已停止")


def publish_latest_frame(target, frame):
    """统一采集输出：广播供多读者使用；旧队列仍只保留最新一帧。"""
    if isinstance(target, FrameStream):
        target.publish(frame)
        return
    try:
        target.get_nowait()
    except queue.Empty:
        pass
    try:
        target.put_nowait(frame)
    except queue.Full:
        pass


# ---- 帧 / 事件等待（带超时与停止检查） ----
def get_frame(frame_queue, stop_event, timeout=1.0):
    """带超时取帧；上游故障且 stop_event 已置位则抛 CameraStopped。"""
    try:
        return frame_queue.get(timeout=timeout)
    except queue.Empty:
        if stop_event.is_set():
            raise CameraStopped()
        return None


def frame_is_usable(frame, min_std=4.0, min_mean=12.0, max_mean=248.0):
    """判断帧是否"有内容"：挡掉近纯色帧（镜头未就绪 / 过曝全白 / 全黑 / 掉线）。

    实测踩过的坑：capture/images/20260929_163117/original/stage2_r001_02.jpg 存下来是
    一张纯白帧（mean=254、std=1.8），于是第 1 件只送了 3 个视角 —— 而第 1 件正是
    给类别号"定调"的那一件（categorize 按首次出现顺序分配 1~6）。

    阈值故意取得很保守（真实取景的 std 通常在 20 以上，实测废帧只有 1.8），
    只挡真正的废帧；万一误判，也只是多取几帧、多一条告警，不会改变结果。
    """
    if frame is None:
        return False
    std = float(frame.std())
    mean = float(frame.mean())
    return std >= min_std and min_mean <= mean <= max_mean


def get_frame_retry(frame_queue, stop_event, name="摄像头", retries=None):
    """取帧并容忍短暂抖动：连续 retries 次都取不到**有效帧**才判故障。

    超时、以及取到近纯色的废帧都算一次失败（废帧直接丢弃、继续取下一帧）。
    两者退出语义不同，这点很重要：
      - 一次帧都没收到 → 返回 None（调用方按摄像头故障处理，原有语义不变）；
      - 收到过帧但都不合格 → **退回最后一帧**并告警，不当成故障（宁肯送一张可疑帧，
        也不能因为"镜头正对着干净白底"就把整场判成硬件故障而停机）。
    stop_event 置位时仍按原语义抛 CameraStopped。
    """
    times = FRAME_RETRY_TIMES if retries is None else retries
    bad = 0
    last = None
    for attempt in range(1, times + 1):
        frame = get_frame(frame_queue, stop_event)
        if frame is None:
            continue
        last = frame
        if not frame_is_usable(frame):
            bad += 1
            logger.warning("%s 第 %d 次取到废帧（mean=%.0f std=%.1f，疑似未就绪/过曝），丢弃重取",
                           name, attempt, float(frame.mean()), float(frame.std()))
            continue
        if attempt > 1 or bad:
            logger.warning("%s 第 %d 次取到有效帧（前 %d 次超时/废帧）", name, attempt, attempt - 1)
        return frame
    if last is not None:
        logger.error("%s 连续 %d 次未取到有效帧（废帧 %d 次），退回最后一帧继续", name, times, bad)
        return last
    logger.error("%s 连续 %d 次取帧超时，判定摄像头故障", name, times)
    return None


def wait_event(event, stop_event, timeout=1.0, what=""):
    """等待事件；stop_event 置位则抛 CameraStopped，超时仅提示并继续。"""
    while not event.is_set():
        if stop_event.is_set():
            raise CameraStopped()
        if event.wait(timeout):
            break
        if what:
            logger.info("等待串口信号 %s 超时，继续等待…", what)
    event.clear()


# ---- 摄像头 ----
def initial_cap(camera_id, camera_name, width, height, fps):
    """初始化并配置摄像头，返回已打开的 VideoCapture；失败返回 None。"""
    logger.info("%s 初始化中...", camera_name)
    cap = cv2.VideoCapture(camera_id, cv2.CAP_V4L2)
    if not cap.isOpened():
        logger.error("%s 打开失败", camera_name)
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    # 开机稳定期：UVC 摄像头刚上电时自动曝光/白平衡还没收敛，第一张图常常整幅过曝。
    # 先等 ~0.8s 再空读 30 帧（约 1s@30fps）丢弃，保证交付的第一次取帧是已收敛的。
#    time.sleep(0.8)
#    for _ in range(30):
#        cap.read()

    real_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    real_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    real_fps = cap.get(cv2.CAP_PROP_FPS)
    if not np.isfinite(real_fps) or real_fps <= 0:
        real_fps = fps
    logger.info("%s 初始化完成 | 分辨率 %dx%d | FPS %.2f", camera_name, real_w, real_h, real_fps)
    return cap


def read_camera(capture, camera_name, frame_queue, fps_queue, stop_event, crop_square=False):
    """共用采集循环，输出到旧队列或 FrameStream；fps_queue 可为 None。

    旧队列故障置位 stop_event；广播故障抛出，由主程序先停推再停止线程。
    """
    def report_fps(value):
        if fps_queue is not None:
            try:
                fps_queue.put_nowait(value)
            except queue.Full:
                pass

    if capture is None or not capture.isOpened():
        logger.error("%s 打开失败，请检查设备", camera_name)
        report_fps(0.0)
        if capture is not None:
            capture.release()
        if isinstance(frame_queue, FrameStream):
            raise CameraStopped(f"{camera_name} 打开失败")
        stop_event.set()
        return

    try:
        report_fps(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        while not stop_event.is_set():
            ret, frame = capture.read()
            if not ret:
                raise CameraStopped(f"读取 {camera_name} 帧失败")
            if crop_square:
                frame = crop_to_square(frame)
            publish_latest_frame(frame_queue, frame)
    except Exception:
        if isinstance(frame_queue, FrameStream):
            # 广播入口先由 guarded 喊停，再置位全局停止标志。
            raise
        logger.exception("%s 采集故障", camera_name)
        stop_event.set()
    finally:
        capture.release()


# ---- 串口（电控 → 视觉：a / c / v；视觉 → 电控：0~6 / f） ----
def communicate_serial(send_queue, stop_event, start_requested,
                       column_check_requested, display_requested):
    """独立处理串口收发，避免串口 I/O 阻塞视觉主流程。

    监听电控指令 a / c / v，分别置位对应事件。
    机械改造后识别区改四路固定机位、不再旋转，`b`（转向完成）已废弃：
    仍然接收并记日志（以免被当成未知指令），但不再置位任何事件。
    """
    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=0.01)
    except (serial.SerialException, OSError) as error:
        logger.error("打开串口失败: %s", error)
        stop_event.set()
        return

    while not stop_event.is_set():
        try:
            sen = send_queue.get(timeout=0.01)
            ser.write(sen.encode())
            logger.info("发送: %s", sen.strip())
        except queue.Empty:
            pass
        except (serial.SerialException, OSError) as error:
            logger.error("串口写入失败: %s", error)
            stop_event.set()
            break

        try:
            if ser.in_waiting:
                reply = ser.readline().decode(errors="replace").strip()
                logger.info("接收: %s", reply)
                if reply == "a":
                    start_requested.set()
                elif reply == "b":
                    logger.debug("收到已废弃的 b（转向完成），忽略")  # 识别区不再旋转，无人等待
                elif reply == "c":
                    column_check_requested.set()
                elif reply == "v":
                    display_requested.set()
        except (serial.SerialException, OSError) as error:
            logger.error("串口读取失败: %s", error)
            stop_event.set()
            break
        time.sleep(0.005)

    ser.close()


# ---- 临时分类映射（内存态，重启自动清空重排） ----
allCategories = {}


def parse_group_match(value, groups):
    """校验新组/已有组判断，仅处理数据，不请求模型或修改分组。"""
    if not isinstance(value, dict) or value.get("status") not in ("existing", "new", "empty", "uncertain"):
        raise ValueError("快速判断格式无效")
    gid = value.get("group")
    if value["status"] == "existing":
        if type(gid) is not int or gid not in groups:
            raise ValueError("快速判断引用了不存在的箱号")
    elif gid is not None:
        raise ValueError("非 existing 时 group 必须为 null")
    return value["status"], gid


def categorize(color_name, shape_name, label=None):
    """按首次出现顺序为「颜色+形状+贴纸」组合分配 1~N 的临时类别。

    参数为颜色/形状的英文名（两个后端统一用字符串名，不再用类别 ID）。
    label 为 None（无贴纸）、图案类型名或解码结果，仅作为分类标准参与编号。

    返回：
        0   —— 无物体（color 与 shape 均为 None）或类别已满
        1..N —— 分配的临时类别
    """
    if color_name is None and shape_name is None:
        return 0
    combination = (color_name, shape_name, label)
    if combination in allCategories:
        return allCategories[combination]
    if len(allCategories) >= MAX_TEMP_CATEGORIES:
        logger.error("临时类别已满(>=%d)，无法为 %s 分配编号", MAX_TEMP_CATEGORIES, combination)
        return 0
    category = len(allCategories) + 1
    allCategories[combination] = category
    logger.info("类别 %d -> %s", category, combination)
    return category


def category_is_full(recognition_counts, category):
    """判断储物盒 category 是否已满（已分拣件数达到 MAX_ITEMS_PER_CATEGORY）。

    category 取 1..MAX_TEMP_CATEGORIES；越界返回 False，交由上层已有逻辑处理。
    """
    if category is None or category <= 0 or category > len(recognition_counts):
        return False
    return recognition_counts[category - 1] >= MAX_ITEMS_PER_CATEGORY


# ---- 中文显示名（显示屏货物名称：颜色 + 形状）----
COLOR_DISPLAY_CN = {
    'black': '黑色', 'blue': '蓝色', 'brown': '棕色', 'deep_blue': '深蓝色',
    'deep_green': '深绿色', 'green': '绿色', 'orange': '橙色', 'pink': '粉色',
    'purple': '紫色', 'red': '红色', 'white': '白色', 'yellow': '黄色',
}
SHAPE_DISPLAY_CN = {
    'ball': '球', 'cone': '圆锥', 'cube': '正方体', 'cuboid': '长方体',
    'cylinder': '圆柱', 'cylinder_thinWalled': '薄壁圆柱', 'hemisphere': '半球',
    'parallelepiped': '平行六面体', 'prism3': '三棱柱', 'prism3_thinWalled': '薄壁三棱柱',
    'prism4_thinWalled': '薄壁四棱柱', 'prism5': '五棱柱', 'prism5_thinWalled': '薄壁五棱柱',
    'prism6': '六棱柱', 'prism6_thinWalled': '薄壁六棱柱', 'pyramid3': '三棱锥', 'pyramid4': '四棱锥',
    'octahedron': '正八面体', 'dodecahedron': '正十二面体', 'icosahedron': '正二十面体',
}


def object_display_name(color_name, shape_name, label=None, shape_display=None):
    """把颜色/形状英文键转成中文显示名，如 blue/prism3 -> 蓝色三棱柱。

    shape_display：词表外形状（unknown_00N）的显示名，由 ShapeMemory.display_name() 提供；
        为 None 时按词表查。**词表键永远优先**：只有在词表里查不到时才用它。
    """
    color_cn = COLOR_DISPLAY_CN.get(color_name, str(color_name or "未知颜色"))
    if shape_name in SHAPE_DISPLAY_CN:
        shape_cn = SHAPE_DISPLAY_CN[shape_name]
    elif shape_display:
        shape_cn = shape_display
    else:
        shape_cn = str(shape_name or "未知形状")
    name = f"{color_cn}{shape_cn}"
    if label:
        name += f"/{label}"
    return name


# ---- 图像裁剪 ----
def crop_to_square(image):
    """将图像从中心裁剪为正方形。"""
    h, w = image.shape[:2]
    s = min(h, w)
    left = (w - s) // 2
    top = (h - s) // 2
    return image[top:top + s, left:left + s]


def crop_tray_region(frame):
    """按 TRAY_* 比例裁剪出托盘区域。"""
    h, w = frame.shape[:2]
    x0 = int(TRAY_X_MIN * w)
    x1 = int(TRAY_X_MAX * w)
    y0 = int(TRAY_Y_MIN * h)
    y1 = int(TRAY_Y_MAX * h)
    return frame[y0:y1, x0:x1]


def sticker_decode_enabled():
    """贴纸解码总开关：默认关闭（LLM_STICKER_DECODE=1 才开）。

    关闭时 label 直接用 LLM 给的 label_kind / label_description：类别键更稳定
    （避免"解码成功用内容、失败用类型"把同一件货拆成两个类别），也省掉整帧扫描
    1 秒以上的开销；LLM 路径与 YOLO fallback 共用本开关，保证两条路径键一致。
    """
    return os.environ.get("LLM_STICKER_DECODE", "0").strip().lower() not in ("0", "false", "no", "off")

def crop_by_ratio(image, left=0.0, top=0.0, right=0.0, bottom=0.0):
    """按四边相对比例裁剪（比例相对当前图的宽/高，四边独立）。

    裁剪后小于 16 像素或比例非法时记警告并返回原图，避免配错就取到空图。
    """
    h, w = image.shape[:2]
    try:
        x0 = int(round(w * float(left)))
        x1 = w - int(round(w * float(right)))
        y0 = int(round(h * float(top)))
        y1 = h - int(round(h * float(bottom)))
    except (TypeError, ValueError):
        logger.warning("裁剪比例非法，已忽略: left=%r top=%r right=%r bottom=%r",
                       left, top, right, bottom)
        return image
    if x1 - x0 < 16 or y1 - y0 < 16:
        logger.warning("裁剪比例过大，已忽略: left=%s top=%s right=%s bottom=%s",
                       left, top, right, bottom)
        return image
    return image[y0:y1, x0:x1]


def crop_pallet_llm(frame):
    """阶段1 送大模型的托盘图：托盘区域 + PALLET_LLM_INSET 内缩。"""
    return crop_by_ratio(crop_tray_region(frame), **PALLET_LLM_INSET)


def _resize_long_side(image, max_side):
    """等比缩小到长边不超过 max_side（只缩不放；max_side<=0 视为不限）。"""
    height, width = image.shape[:2]
    longest = max(height, width)
    if max_side <= 0 or longest <= max_side:
        return image
    scale = max_side / float(longest)
    return cv2.resize(image,
                      (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                      interpolation=cv2.INTER_AREA)


def detect_frame_preprocessed(frame):
    """采集端预处理，与 main_push_llm 传给 read_camera 的 crop_square 保持一致。

    square 模式：裁居中方正（1280x720 -> 720x720）。
    full   模式：原样返回，全幅交给 crop_detect_llm 统一缩放。
    """
    if DETECT_CROP_MODE == "full":
        return frame
    return crop_to_square(frame)


def crop_detect_llm(frame):
    """阶段2 送大模型的识别区图；具体形状由 DETECT_CROP_MODE 决定。

    square（默认）：正方形画面 + DETECT_LLM_INSET 内缩。
        前提是 frame 已经过 crop_to_square（线上在 read_camera(crop_square=True) 里做）。
        忘了这一步不会报错，只会静默得到一个长方形（1280x720 → 947x533），
        所以这里加条告警，避免这种错悄无声息地进生产。
    full：全幅等比缩小 + DETECT_FULL_INSET 微调，不裁边。
    """
    if DETECT_CROP_MODE == "full":
        return crop_by_ratio(_resize_long_side(frame, DETECT_SEND_MAX_SIDE),
                             **DETECT_FULL_INSET)

    height, width = frame.shape[:2]
    if abs(height - width) > 2:
        logger.warning("识别区图不是正方形（%dx%d）：应先 crop_to_square，"
                       "否则内缩比例会把它裁成矩形", width, height)
    return crop_by_ratio(frame, **DETECT_LLM_INSET)


# ---- 托盘几何（YOLO 查栏用） ----
def tray_bounds_px(frame_shape):
    """把托盘范围常量（相对画面比例）换算成画面像素边界。"""
    height, width = frame_shape[:2]
    return (TRAY_X_MIN * width, TRAY_X_MAX * width,
            TRAY_Y_MIN * height, TRAY_Y_MAX * height)


def column_of(center_x, x_min, x_max, column_count=COLUMN_COUNT):
    """根据物体中心 x 判断所属列，返回 1~COLUMN_COUNT（最左为第 1 列）。"""
    column_width = (x_max - x_min) / column_count
    if column_width <= 0:
        return 1
    column = int((center_x - x_min) // column_width) + 1
    return int(np.clip(column, 1, column_count))


def column_occupancy(points, frame_shape, column_count=COLUMN_COUNT):
    """返回长度 COLUMN_COUNT 的布尔数组：每一列是否有物体。

    只统计检测框中心位于托盘 x/y 范围内的物体。
    默认下标 0 对应画面最左一列；若 LASER_SENSOR_ON_LEFT 为 False，
    则反转顺序，使下标 0 对应画面最右一列（靠近激光传感器的一栏）。
    """
    occupancy = [False] * column_count
    if points is None or len(points) == 0:
        return occupancy
    x_min, x_max, y_min, y_max = tray_bounds_px(frame_shape)
    for box in points:
        center_x = float(np.mean(box[:, 0]))
        center_y = float(np.mean(box[:, 1]))
        if not (x_min <= center_x <= x_max and y_min <= center_y <= y_max):
            continue
        column = column_of(center_x, x_min, x_max, column_count)
        occupancy[column - 1] = True
    if not LASER_SENSOR_ON_LEFT:
        occupancy.reverse()
    return occupancy
