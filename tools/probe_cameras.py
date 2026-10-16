#!/usr/bin/env python3
"""四路相机冒烟测试：先"同时打开"四路，再"并行连读"若干帧，最后各存一张图。

为什么这样设计：四路相机挂在同一个 USB Hub 上，真正要验证的是
「带宽/供电够不够四路同时跑」——所以不是一路一路开，而是四路一起开、轮流读，
最后打印每路的实际帧率（掉到 30 以下就是总线被压住了）。

用法（项目根目录、conda 环境 llm）：
    python tools/probe_cameras.py                       # 走 shared/const.py 里的别名
    python tools/probe_cameras.py --devices /dev/video2 /dev/video0 /dev/video4 /dev/video6
                                                        # 顺序固定：托盘 / 斜上① / 斜上② / 正下③
    python tools/probe_cameras.py --rounds 120 --wait 3

输出：默认 tools/probe_out/<MMDD_HHMMSS>/，文件名前缀就是位置：
    0_pallet.jpg  1_detect_up1.jpg  2_detect_up2.jpg  3_detect_down.jpg
退出码：0 = 四路全部达标，1 = 有失败。
"""
import argparse
import os
import sys
import time

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.const import (  # noqa: E402  —— 必须在加完 sys.path 之后导入
    CAMERA_PALLET_ID,
    DETECT_CAMERA_IDS,
    PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS,
    DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
)


def open_camera(camera_id, name, width, height, fps):
    """打开并配置一路（MJPG + 单帧缓冲）；失败返回 None。"""
    if not os.path.exists(camera_id):
        print(f"[FAIL] {name:15} 设备节点不存在: {camera_id}")
        return None
    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        print(f"[FAIL] {name:15} 打不开（可能被别的进程占用，先 pgrep -af main_push_llm）: {camera_id}")
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def main():
    parser = argparse.ArgumentParser(description="四路相机冒烟测试（同时打开 + 并行连读）")
    parser.add_argument("--devices", nargs=4, default=None,
                        metavar=("PALLET", "UP1", "UP2", "DOWN"),
                        help="直接指定四个设备节点，顺序：托盘 斜上① 斜上② 正下③")
    parser.add_argument("--out", default=None, help="输出目录（默认 tools/probe_out/<时间戳>）")
    parser.add_argument("--rounds", type=int, default=90, help="并行连读轮数（默认 90，约 3 秒）")
    parser.add_argument("--wait", type=float, default=2.0, help="每路预热秒数（默认 2）")
    args = parser.parse_args()

    out_dir = args.out or os.path.join(ROOT, "tools", "probe_out", time.strftime("%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    print(f"输出目录: {out_dir}\n")

    ids = tuple(args.devices) if args.devices else (CAMERA_PALLET_ID,) + tuple(DETECT_CAMERA_IDS)
    targets = [
        ("0_pallet",      ids[0], PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS),
        ("1_detect_up1",  ids[1], DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS),
        ("2_detect_up2",  ids[2], DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS),
        ("3_detect_down", ids[3], DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS),
    ]

    # ---- ① 同时打开四路 ----
    print("① 同时打开四路 …")
    opened = []
    for name, camera_id, width, height, fps in targets:
        cap = open_camera(camera_id, name, width, height, fps)
        if cap is not None:
            opened.append([name, camera_id, cap])
            print(f"[ OK ] {name:15} {camera_id}")
    print(f"       打开成功 {len(opened)}/{len(targets)} 路\n")
    if not opened:
        return 1

    # ---- ② 预热（自动曝光稳定）----
    print(f"② 预热 {args.wait:.1f}s（这段的帧全丢）…")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        for _name, _camera_id, cap in opened:
            cap.grab()
        time.sleep(0.05)

    # ---- ③ 四路并行连读，统计每路真实帧率 ----
    print(f"③ 四路并行连读 {args.rounds} 轮（这一轮才真正压带宽/供电）…")
    stats = {name: [0, 0] for name, _camera_id, _cap in opened}   # [成功, 失败]
    frames = {}
    start = time.time()
    for _ in range(args.rounds):
        for name, _camera_id, cap in opened:
            ok, frame = cap.read()
            if ok and frame is not None:
                stats[name][0] += 1
                frames[name] = frame
            else:
                stats[name][1] += 1
    elapsed = max(time.time() - start, 1e-6)

    # ---- ④ 存图 + 报告 ----
    print("")
    ok_count = 0
    for name, camera_id, cap in opened:
        good, bad = stats[name]
        real_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        real_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        real_fps = good / elapsed
        flag = "[ OK ]" if good and bad == 0 else "[WARN]"
        if good and bad == 0:
            ok_count += 1
        print(f"{flag} {name:15} {good:3}/{args.rounds} 帧  ≈{real_fps:5.1f} fps  {real_w}x{real_h}  失败 {bad}")
        if name in frames:
            cv2.imwrite(os.path.join(out_dir, f"{name}.jpg"), frames[name])
        cap.release()

    print(f"\n四路并行总耗时 {elapsed:.2f}s（{args.rounds} 轮）；达标 {ok_count}/{len(opened)} 路")
    print(f"看图确认：{out_dir}")
    print("  0_pallet=托盘全景  1_detect_up1=斜上①  2_detect_up2=斜上②(与①相对)  3_detect_down=正下方仰视底面")
    return 0 if ok_count == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
