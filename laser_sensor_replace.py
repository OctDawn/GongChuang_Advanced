import cv2
import numpy as np
from shared.const import (CAMERA_LASER_ID, LASER_WIDTH, LASER_HEIGHT, LASER_AREA, LASER_FPS, 
                          RGB_DIFF_THRESHOLD, STABLE_THRESHOLD, REQUIRED_STABLE_FRAMES)
# from shared.common import initial_cap

import time


def initial_cap(camera_id, camera_name, width, height, fps):
    """初始化并配置摄像头，返回 (已打开的 VideoCapture, 实际 FPS)。"""
    print(f" {camera_name} 初始化中...")
    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        raise RuntimeError(f"{camera_name}打开失败")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))     # 设置格式为MJPG,'YUYV' 没必要
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)

    # 获取摄像头实际参数
    real_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    real_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    real_fps = cap.get(cv2.CAP_PROP_FPS)
    if not np.isfinite(real_fps) or real_fps <= 0:
        real_fps = fps  # 读不到实际帧率时用请求值兜底

    print(
        f"{camera_name} 初始化完成"
        f" | 分辨率: {real_width}x{real_height}"
        f" | FPS: {real_fps:.2f}"
    )
    
    return cap
def get_detection_frame(frame):
    # 裁剪固定检测区域，并降低分辨率。
    x1 = int(LASER_WIDTH * LASER_AREA[0])
    x2 = int(LASER_WIDTH * LASER_AREA[1])
    y1 = int(LASER_HEIGHT * LASER_AREA[2])
    y2 = int(LASER_HEIGHT * LASER_AREA[3])
    roi = frame[y1:y2, x1:x2]
    return roi 
    
def calculate_rgb_difference(current, reference):
    # 计算 current 与 reference 的 RGB 区别度。
    # 目前算法：每个像素三个通道中，只要有一个通道变化超过阈值，就认为这个像素发生了明显变化。
    diff = cv2.absdiff(current, reference)

    changed_pixels = np.max(diff, axis=2) > RGB_DIFF_THRESHOLD
    difference = np.mean(changed_pixels)
    return difference

def main():
    # 初始化
    laser_cap = initial_cap(CAMERA_LASER_ID, "激光检测摄像头", LASER_WIDTH, LASER_HEIGHT, LASER_FPS)
    ret, frame = laser_cap.read()
    reference_frame = get_detection_frame(frame)
    stable_count = 0            # 稳定帧数
    loop_count = 0   # 调试，计算帧率
    start_time = time.perf_counter()
    while True:
        loop_count += 1
        ret, frame = laser_cap.read()

        current_frame = get_detection_frame(frame)

        # 计算 current 与 reference 的 RGB 区别度
        difference = calculate_rgb_difference(
            current_frame,
            reference_frame
        )


        # 判断与稳定帧的差别
        if difference < STABLE_THRESHOLD:
            stable_count += 1
        else:
            stable_count = 0
            reference_frame = current_frame.copy()
            print("图片突变，更新参考稳定帧")
            print(
                f"Difference: {difference:.4f} "
                f"({difference * 100:.2f}%)"
            )
        if stable_count >= REQUIRED_STABLE_FRAMES:
            reference_frame = current_frame.copy()
            stable_count = 0
            print("长期微变，更新参考稳定帧")
            print(
                f"Difference: {difference:.4f} "
                f"({difference * 100:.2f}%)"
            )
        cv2.imshow("Current", current_frame)
        cv2.imshow("Reference", reference_frame)

        # RGB 差异图，仅用于观察哪里发生变化
        diff_image = cv2.absdiff(
            current_frame,
            reference_frame
        )

        cv2.imshow("RGB Difference", diff_image)

        if loop_count == 60:
            print(f"60次循环耗时: {time.perf_counter() - start_time:.4f} 秒")
            loop_count = 0
            start_time = time.perf_counter()
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    laser_cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()