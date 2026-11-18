"""摄像头稳定性压测：连续读多路 UVC，统计掉帧/读失败，用来验证「加设备会不会挤掉相机」。

典型用法（验证 4G 模组启用前后是否影响同 hub 的摄像头）：

    # 1) 4G 隔离状态下的基线
    python tools/cam_stress.py --minutes 5

    # 2) 启用 4G（另开一个终端）
    #    sudo nmcli device set enx889e96529263 managed yes
    #    sudo nmcli connection up "Wired connection X"
    # 3) 同时再跑一遍，对比两次结果
    python tools/cam_stress.py --minutes 5

输出示例：
    [00:30] detect_1 29.8fps fail=0 | detect_2 29.7fps fail=0 | ...
    汇总：detect_1 平均 29.8fps，失败 0；...

判定：加设备后某一路 fps 明显掉、或 fail 从 0 变成非 0 → 供电/带宽被挤，
优先把 4G 模组插到带外接电源的 USB hub 上。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()

import cv2  # noqa: E402

from shared.common import initial_cap  # noqa: E402
from shared.const import (  # noqa: E402
    DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
)


def _pallet_cap():
    from shared.const import CAMERA_PALLET_ID, PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS
    return CAMERA_PALLET_ID, "托盘上方广角摄像头", PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=3.0, help="压测时长（分钟）")
    parser.add_argument("--pallet", action="store_true", help="把托盘相机也算进来（共 5 路）")
    parser.add_argument("--report-every", type=float, default=30.0, help="进度打印间隔（秒）")
    args = parser.parse_args()

    targets = [(cid, name, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)
               for cid, name in zip(DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES)]
    if args.pallet:
        targets.insert(0, _pallet_cap())

    print("打开 %d 路相机…（先停主服务，否则打不开）" % len(targets))
    caps = []
    for cid, name, width, height, fps in targets:
        cap = initial_cap(cid, name, width, height, fps)
        if cap is None:
            for opened in caps:
                opened["cap"].release()
            raise SystemExit("打不开 %s（%s），先 sudo systemctl stop wave-vision-llm" % (name, cid))
        caps.append({"name": name, "cap": cap, "count": 0, "fail": 0, "first": time.monotonic()})

    deadline = time.monotonic() + args.minutes * 60
    next_report = time.monotonic() + args.report_every
    started = time.monotonic()
    try:
        while time.monotonic() < deadline:
            for item in caps:
                ok, frame = item["cap"].read()
                if ok and frame is not None:
                    item["count"] += 1
                else:
                    item["fail"] += 1
            now = time.monotonic()
            if now >= next_report:
                next_report = now + args.report_every
                parts = []
                for item in caps:
                    elapsed = max(1e-6, now - item["first"])
                    parts.append("%s %.1ffps fail=%d" % (
                        item["name"].replace("识别区", ""), item["count"] / elapsed, item["fail"]))
                print("[%05.1fs] %s" % (now - started, " | ".join(parts)))
    except KeyboardInterrupt:
        print("中断")
    finally:
        elapsed = max(1e-6, time.monotonic() - started)
        print("\n汇总（%.1f 秒）：" % elapsed)
        bad = 0
        for item in caps:
            fps = item["count"] / elapsed
            print("  %-18s %5.1f fps  失败 %d 帧" % (item["name"], fps, item["fail"]))
            if item["fail"] > 0 or fps < DETECT_FPS * 0.7:
                bad += 1
        print("  结论：%s" % ("有 %d 路异常，检查供电/带宽" % bad if bad else "全部正常"))
        for item in caps:
            item["cap"].release()


if __name__ == "__main__":
    main()
