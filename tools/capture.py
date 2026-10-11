'''工具脚本：摄像头采集。

包含三种互斥的模式，运行前在文件末尾只保留需要的那一行，其余两行注释掉：
  1. manual_capture  手动拍照（输入 c 拍照，q 退出）
  2. auto_capture    自动抽帧拍照（每隔 FRAME_INTERVAL 帧自动保存一张）
  3. record_video    录制视频（Ctrl+C 停止并保存）
注：脚本运行看不到摄像头画面，日后再讨论加不加（加可能会有其他负面影响）
'''

import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.const import (CAMERA_PALLET_ID, CAMERA_DETECT_AREA_ID,
                   PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS,
                   DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)


CAMERA_TYPE = "pallet"  # 大托盘用 pallet，识别区用 detect_area
if CAMERA_TYPE == "pallet":
    CAMERA_ID = CAMERA_PALLET_ID
    CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS = PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS
    CROP_SQUARE = False
elif CAMERA_TYPE == "detect_area":
    CAMERA_ID = CAMERA_DETECT_AREA_ID
    CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS = DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS
    CROP_SQUARE = True
else:
    raise ValueError(f"Unknown CAMERA_TYPE: {CAMERA_TYPE}")

SAVE_DIR = os.path.join(os.path.dirname(__file__), "raw")      # 拍照保存目录
VIDEO_DIR = os.path.join(os.path.dirname(__file__), "videos")   # 录像保存目录

FRAME_INTERVAL = 30  # 自动抽帧：每隔多少帧保存一张（30fps 下约每秒一张）
MAX_FRAMES = 0       # 自动抽帧：最多保存多少张，0 表示不限制
VIDEO_FPS = 25.0     # 录制视频：输出帧率（摄像头 fps 读取失败时使用）
VIDEO_NAME = "record"  # 录制视频：文件名前缀


def open_camera(camera_id):
    """打开摄像头，失败抛出异常。"""
    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open camera {camera_id}")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
    print(
        f"{CAMERA_TYPE} camera initialized"
        f" | resolution: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
        f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
        f" | FPS: {cap.get(cv2.CAP_PROP_FPS):.2f}"
    )
    return cap


def prepare_frame(frame):
    """识别区画面与主流程一样，从中心裁剪为正方形。"""
    if not CROP_SQUARE:
        return frame
    height, width = frame.shape[:2]
    short_side = min(width, height)
    left = (width - short_side) // 2
    top = (height - short_side) // 2
    return frame[top:top + short_side, left:left + short_side]


def manual_capture():
    """模式一：手动拍照。输入 c 拍照，输入 q 退出。"""
    cap = open_camera(CAMERA_ID)
    os.makedirs(SAVE_DIR, exist_ok=True)
    print("手动拍照模式：输入 c 拍照，输入 q 退出")
    try:
        while True:
            command = input("> ").strip().lower()
            if command == "q":
                break
            if command != "c":
                continue

            ret, frame = cap.read()
            if not ret:
                print("Failed to read camera frame")
                continue
            frame = prepare_frame(frame)

            path = os.path.join(SAVE_DIR, f"{int(time.time() * 1000)}.jpg")
            cv2.imwrite(path, frame)
            print(f"Saved: {path}")
    finally:
        cap.release()


def auto_capture():
    """模式二：自动抽帧拍照。每隔 FRAME_INTERVAL 帧保存一张，Ctrl+C 退出。"""
    cap = open_camera(CAMERA_ID)
    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"自动抽帧模式：每 {FRAME_INTERVAL} 帧保存一张，Ctrl+C 退出")
    frame_index = 0
    saved = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to read camera frame")
                break
            frame = prepare_frame(frame)

            frame_index += 1
            if frame_index % FRAME_INTERVAL == 0:
                path = os.path.join(SAVE_DIR, f"{int(time.time() * 1000)}_{frame_index:06d}.jpg")
                cv2.imwrite(path, frame)
                saved += 1
                print(f"Saved: {path} ({saved})")
                if MAX_FRAMES and saved >= MAX_FRAMES:
                    break
    finally:
        cap.release()


def record_video():
    """模式三：录制视频。Ctrl+C 停止并保存。"""
    cap = open_camera(CAMERA_ID)
    os.makedirs(VIDEO_DIR, exist_ok=True)

    # 先读一帧拿到实际尺寸，避免某些摄像头返回 0x0
    ret, frame = cap.read()
    if not ret:
        print("Failed to read camera frame")
        cap.release()
        return
    frame = prepare_frame(frame)
    height, width = frame.shape[:2]

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = VIDEO_FPS
    path = os.path.join(VIDEO_DIR, f"{VIDEO_NAME}_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    print(f"录制视频模式：Ctrl+C 停止并保存到 {path}")
    try:
        while True:
            writer.write(frame)
            ret, frame = cap.read()
            if not ret:
                print("Failed to read camera frame")
                break
            frame = prepare_frame(frame)
    finally:
        writer.release()
        cap.release()
        print(f"Video saved: {path}")


if __name__ == "__main__":
    # 三种模式互斥：只保留要运行的一行，其余两行注释掉。
    try:
        manual_capture()
        # auto_capture()
        # record_video()
    except KeyboardInterrupt:
        print("Interrupted")
