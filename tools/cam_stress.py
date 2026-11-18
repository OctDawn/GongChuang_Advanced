"""摄像头稳定性压测：每路一个线程连续读，统计 fps 与读帧失败。

为什么要多线程：VideoCapture.read() 会阻塞等下一帧，四路写在一个循环里顺序读，
每路只能拿到 ~15fps（实测），会被误判成"相机有问题"。主程序是每路一个线程，
本工具保持一致，测出来的 fps 才能和现场对齐。

典型用法（验证 4G 模组启用前后是否影响同 hub 的摄像头）：

    # 1) 4G 隔离状态下的基线
    python tools/cam_stress.py --minutes 5

    # 2) 启用 4G（另开终端）
    #    sudo nmcli device set enx889e96529263 managed yes
    #    sudo nmcli connection up "Wired connection X"
    # 3) 4G 带流量时再跑一遍，对比
    python tools/cam_stress.py --minutes 5

判定：加设备后某一路 fps 明显掉、或 fail 从 0 变成非 0 → 供电/带宽被挤，
优先把 4G 模组插到带外接电源的 USB hub 上。
"""

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()

from shared.common import initial_cap  # noqa: E402
from shared.const import (  # noqa: E402
    DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
)


def _pallet_target():
    from shared.const import CAMERA_PALLET_ID, PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS
    return (CAMERA_PALLET_ID, "托盘上方广角摄像头", PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS)


def _reader(item, deadline, stop):
    """每路一个线程：一直读到截止时间，统计帧数与失败次数。"""
    cap = item["cap"]
    while not stop.is_set() and time.monotonic() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None:
            item["count"] += 1
        else:
            item["fail"] += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=3.0, help="压测时长（分钟）")
    parser.add_argument("--pallet", action="store_true", help="把托盘相机也算进来（共 5 路）")
    parser.add_argument("--report-every", type=float, default=30.0, help="进度打印间隔（秒）")
    args = parser.parse_args()

    targets = [(cid, name, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)
               for cid, name in zip(DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES)]
    if args.pallet:
        targets.insert(0, _pallet_target())

    print("打开 %d 路相机…（先停主服务，否则打不开）" % len(targets))
    items = []
    for cid, name, width, height, fps in targets:
        cap = initial_cap(cid, name, width, height, fps)
        if cap is None:
            for item in items:
                item["cap"].release()
            raise SystemExit("打不开 %s（%s），先 sudo systemctl stop wave-vision-llm" % (name, cid))
        items.append({"name": name, "cap": cap, "count": 0, "fail": 0})

    stop = threading.Event()
    deadline = time.monotonic() + args.minutes * 60
    threads = [threading.Thread(target=_reader, args=(item, deadline, stop), daemon=True)
               for item in items]

    started = time.monotonic()
    for thread in threads:
        thread.start()

    next_report = started + args.report_every
    try:
        while time.monotonic() < deadline and any(t.is_alive() for t in threads):
            time.sleep(0.5)
            now = time.monotonic()
            if now >= next_report:
                next_report = now + args.report_every
                elapsed = max(1e-6, now - started)
                print("[%05.1fs] %s" % (elapsed, " | ".join(
                    "%s %.1ffps fail=%d" % (item["name"].replace("识别区", ""),
                                            item["count"] / elapsed, item["fail"])
                    for item in items)))
    except KeyboardInterrupt:
        print("中断")
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2.0)
        elapsed = max(1e-6, time.monotonic() - started)
        print("\n汇总（%.1f 秒）：" % elapsed)
        bad = 0
        for item in items:
            fps = item["count"] / elapsed
            print("  %-18s %5.1f fps  失败 %d 帧" % (item["name"], fps, item["fail"]))
            if item["fail"] > 0 or fps < DETECT_FPS * 0.7:
                bad += 1
        print("  结论：%s" % ("有 %d 路异常，检查供电/带宽" % bad if bad else "全部正常（约 30fps、0 失败）"))
        for item in items:
            item["cap"].release()


if __name__ == "__main__":
    main()
