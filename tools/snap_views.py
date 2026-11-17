"""现场拍「识别区四视角」：逐路打开相机各抓一帧并存盘，方便离线复跑 LLM。

为什么单独做一个：test_llm.py 需要同一轮的 4 张图，而识别区 4 路相机
平时被主程序独占，手工截图既慢又容易对不齐视角。

用法（板上，项目根目录；先停主服务）：

    sudo systemctl stop wave-vision-llm
    conda activate llm
    python tools/snap_views.py --tag 红薄壁圆柱
    python tools/snap_views.py --tag 带贴纸蓝色球 --warmup 1.5

输出目录 capture/views/<时间戳>_<tag>/：
    view_1.jpg ... view_4.jpg   相机原图
    sent_1.jpg ... sent_4.jpg   实际送模型的图（crop_detect_llm 之后）

结束时会打印一条可以直接粘贴的 test_llm.py 命令。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()      # 必须在 llm.* 之前（形状示例图路径等）

import cv2  # noqa: E402

from shared.common import crop_detect_llm, initial_cap  # noqa: E402
from shared.const import (  # noqa: E402
    DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
)


def grab(camera_id, name, warmup):
    """打开一路相机，丢弃 warmup 秒的帧（等曝光/白平衡稳定），返回最后一帧。"""
    cap = initial_cap(camera_id, name, DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)
    if cap is None:
        raise SystemExit("打不开 %s（先 sudo systemctl stop wave-vision-llm）" % camera_id)
    try:
        deadline = time.time() + max(0.0, warmup)
        while time.time() < deadline:
            cap.read()
        frame = None
        for _ in range(3):            # 连读几帧，取最后一帧，避开半帧/旧帧
            ok, current = cap.read()
            if ok and current is not None:
                frame = current
        if frame is None:
            raise SystemExit("%s 读帧失败，重跑一次" % camera_id)
        return frame
    finally:
        cap.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="", help="标签，写进目录名，便于事后识别")
    parser.add_argument("--warmup", type=float, default=1.0, help="每路丢弃预热秒数")
    parser.add_argument("--out", default="", help="自定义输出目录")
    args = parser.parse_args()

    count = len(DETECT_CAMERA_IDS)
    stamp = time.strftime("%Y%m%d_%H%M%S") + ("_" + args.tag if args.tag else "")
    out_dir = args.out or os.path.join("capture", "views", stamp)
    os.makedirs(out_dir, exist_ok=True)

    print("拍 %d 路视角 -> %s" % (count, out_dir))
    print("请确保物件已摆好、不再移动（逐路开关相机，总耗时约 %d 秒）\n" % (count * 2))

    paths = []
    for index in range(count):
        camera_id = DETECT_CAMERA_IDS[index]
        name = DETECT_CAMERA_NAMES[index]
        frame = grab(camera_id, name, args.warmup)
        raw_path = os.path.join(out_dir, "view_%d.jpg" % (index + 1))
        sent_path = os.path.join(out_dir, "sent_%d.jpg" % (index + 1))
        cv2.imwrite(raw_path, frame)
        view = crop_detect_llm(frame)
        cv2.imwrite(sent_path, view)
        paths.append(raw_path)
        print("  %d/%d %-16s %s  %dx%d -> 送模型 %dx%d" % (
            index + 1, count, name, camera_id,
            frame.shape[1], frame.shape[0], view.shape[1], view.shape[0]))

    print("\n已保存：%s" % out_dir)
    print("\n把物件换掉后重复运行即可积累样本。离线复跑四视角分类：\n")
    print("python test_llm.py classify " + " ".join(paths) + " --runs 1")


if __name__ == "__main__":
    main()
