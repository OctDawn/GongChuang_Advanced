#!/usr/bin/env python3
"""相机冒烟测试：先“同时打开”全部机位，再“并行连读”若干帧，最后各存一张图。

为什么这样设计：真正要验证的是「总线带宽/供电够不够所有机位同时跑」
——所以不是一路一路开，而是全部一起开、轮流读，看哪一路抢不到带宽。
**铁律：每条 480M USB 总线最多 2 路 MJPG**，第 3 路会 `STREAMON` 失败（dmesg `URB 0 (-28)`）。

用法（项目根目录、conda 环境 llm）：
    python tools/probe_cameras.py                       # 走 shared/const.py 里的别名，共 1+STAGE_TWO_DETECTION_COUNT 路
    python tools/probe_cameras.py --devices /dev/video2 /dev/video0 /dev/video4 /dev/video6
                                                        # 数量 = 托盘 + 识别区机位数，顺序同取帧顺序
    python tools/probe_cameras.py --rounds 120 --wait 3
    STAGE_TWO_DETECTION_COUNT=4 python tools/probe_cameras.py   # 试验第 4 路（共 5 路）

输出：默认 tools/probe_out/<MMDD_HHMMSS>/，文件名前缀就是位置：
    0_pallet.jpg  1_detect_1.jpg  2_detect_2.jpg  ...（机位数自动跟随）
退出码：0 = 全部达标，1 = 有失败。
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
    DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES, STAGE_TWO_DETECTION_COUNT,
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
    view_count = STAGE_TWO_DETECTION_COUNT
    total = 1 + view_count
    parser = argparse.ArgumentParser(
        description=f"相机冒烟测试（{total} 路同时开 + 并行连读 + 各存一张 jpg）")
    parser.add_argument("--devices", nargs=total, default=None, metavar="DEV",
                        help=f"直接指定 {total} 个设备节点（托盘 + {view_count} 路识别区，按取帧顺序）")
    parser.add_argument("--out", default=None, help="输出目录（默认 tools/probe_out/<时间戳>）")
    parser.add_argument("--rounds", type=int, default=90, help="并行连读轮数（默认 90，约 3 秒）")
    parser.add_argument("--wait", type=float, default=2.0, help="每路预热秒数（默认 2）")
    args = parser.parse_args()

    out_dir = args.out or os.path.join(ROOT, "tools", "probe_out", time.strftime("%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    print(f"输出目录: {out_dir}\n")

    ids = tuple(args.devices) if args.devices else (CAMERA_PALLET_ID,) + tuple(DETECT_CAMERA_IDS)
    targets = [("0_pallet", ids[0], PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS)]
    for index, device in enumerate(ids[1:], start=1):
        targets.append((f"{index}_detect_{index}", device,
                        DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS))

    # ---- ① 同时打开全部 ----
    print(f"① 同时打开 {len(targets)} 路 …")
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
    print(f"③ {len(opened)} 路并行连读 {args.rounds} 轮（这一轮才真正压带宽/供电）…")
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

    print(f"\n{len(opened)} 路并行总耗时 {elapsed:.2f}s（{args.rounds} 轮）；达标 {ok_count}/{len(opened)} 路")
    print(f"看图确认：{out_dir}")
    print("  " + "  ".join(f"{index}_detect_{index}={name}"
                           for index, name in enumerate(DETECT_CAMERA_NAMES, start=1)))
    return 0 if ok_count == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
