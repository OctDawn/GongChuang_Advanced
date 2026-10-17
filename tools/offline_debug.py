#!/usr/bin/env python3
"""识别区离线调试：各路拍照 → 现在这套 prompt → 大模型 → 记录结果/耗时/用量。

**不依赖机械、电控、串口、显示屏**。只复用线上同一套模块
（llm.qw / llm.prompt / llm.shape_recognition / llm.shape_memory / llm.empty_examples），
所以"离线跑出来什么，线上就是什么"：图号顺序、裁剪、prompt、校验、形状记忆全会一致。
不加载 YOLO、不开串口、不开 pygame。

典型用法（板子上，项目根目录，conda 环境 llm）：

    # 一步到底：各路各拍一张 + 跑 1 次
    python tools/offline_debug.py --capture

    # 拍一组并跑 3 次（看稳定性和耗时分布）
    python tools/offline_debug.py --capture --tag 蓝薄壁4棱 --runs 3

    # 用最近一次采集的图反复跑，不用再开相机
    python tools/offline_debug.py --latest --runs 5

    # 用自己准备的一组图（顺序必须：①斜上 ②斜上 ③正下仰视 ④水平侧视）
    python tools/offline_debug.py --images a.jpg b.jpg c.jpg

    # 只走流程不发请求：检查裁剪/尺寸/文件名对不对
    python tools/offline_debug.py --latest --dry-run

    # 看历史采集
    python tools/offline_debug.py --list

    # 试验第 4 路（④ 水平侧视）：同一个工具，环境变量切，图片张数自动跟着变
    STAGE_TWO_DETECTION_COUNT=4 python tools/offline_debug.py --capture --tag 四路对比 --runs 3
    # 回滚：不带这个变量就是原来的 3 路（生产代码一行没变）

输出在 capture/offline/<时间戳>[_tag]/ ：

    view_1..3.jpg      相机原始帧（1280x720，未裁剪）
    sent_1..3.jpg      模型真正看到的那张（居中正方 720x720 → 内缩 0.13 → 532x532）
    reply_01_classify.txt   模型原始回复（重试会带 _try2 后缀，保留每次尝试）
    result.jsonl       每次 run 一行：结果 / 耗时 / token / 错误
    meta.json          设备、尺寸、参数、时间戳

两个行为约定：
- 每次 run 默认新建 ShapeMemory（等于"程序刚启动"），所以 `new` 不会被上一次的
  `remembered` 干扰；要模拟"同一场次里连续第 N 件"，加 --keep-memory。
- 会自动读项目根目录的 .env（不覆盖已有环境变量）。手动跑时 systemd 那层
  EnvironmentFile 不存在，没这一步就会报"缺少环境变量 DASHSCOPE_API_KEY"。
"""
import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
from datetime import datetime

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:  # Windows 控制台默认 GBK，中文会炸
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def load_dotenv(path):
    """.env → os.environ（不覆盖已有值）。必须在 import llm.* 之前调用：

    qw.py 在模块级读 DASHSCOPE_* / LLM_*，导入后再改环境变量就不生效了。
    """
    if not os.path.isfile(path):
        return 0
    loaded = 0
    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1
    return loaded


ENV_LOADED = load_dotenv(os.path.join(ROOT, ".env"))

from shared.common import crop_detect_llm, detect_frame_preprocessed, frame_is_usable  # noqa: E402
from shared.const import (  # noqa: E402
    DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES,
    DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
    DETECT_CROP_MODE, DETECT_SEND_MAX_SIDE,
    STAGE_TWO_DETECTION_COUNT,
)
from llm.empty_examples import load_empty_examples, load_shape_examples  # noqa: E402
from llm.qw import LLMError  # noqa: E402
from llm.shape_memory import ShapeMemory  # noqa: E402
from llm.shape_recognition import ShapeRecognizer  # noqa: E402

OUT_ROOT = os.path.join(ROOT, "capture", "offline")
# 与 shared/const.py 的机位名保持一致，避免两处名字漂移
VIEW_NAMES = tuple(DETECT_CAMERA_NAMES)


# ===================== 收集 qw.py 的日志（用量 / 耗时 / 原始回复） =====================
class QwCollector(logging.Handler):
    """把 qw.py 打的关键行收成结构化数据，避免去改主链路。"""

    USAGE = re.compile(r"prompt_tokens=(\S+) cached_tokens=(\S+) cache_write_tokens=(\S+) "
                       r"completion_tokens=(\S+) reasoning_tokens=(\S+)")
    DONE = re.compile(r"LLM 请求结束，耗时 ([\d.]+) 秒")
    PAYLOAD = re.compile(r"LLM 图片载荷：(\d+) 张，共 ([\d.]+) KB")
    RAW_PREFIX = "LLM 原始回复："

    def __init__(self):
        super().__init__(logging.INFO)
        self.reset()

    def reset(self):
        self.usage = {}
        self.request_seconds = None
        self.payload = None
        self.raw_reply = None
        self.requests = 0

    def emit(self, record):
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        if message.startswith("LLM 请求："):
            self.requests += 1
        elif message.startswith("LLM 用量:"):
            found = self.USAGE.search(message)
            if found:
                keys = ("prompt_tokens", "cached_tokens", "cache_write_tokens",
                        "completion_tokens", "reasoning_tokens")
                for key, value in zip(keys, found.groups()):
                    self.usage[key] = value if value == "unknown" else int(value)
        elif message.startswith("LLM 请求结束"):
            found = self.DONE.search(message)
            if found:
                self.request_seconds = float(found.group(1))
        elif message.startswith("LLM 图片载荷"):
            found = self.PAYLOAD.search(message)
            if found:
                self.payload = {"images": int(found.group(1)), "kb": float(found.group(2))}
        elif message.startswith("LLM 原始回复"):
            # 不能用正则 .*：模型的回复可能带换行（``` 围栏、格式化 JSON），
            # 一旦被截成第一行，extract_json 就解析不出 status 了。
            if self.RAW_PREFIX in message:
                self.raw_reply = message.split(self.RAW_PREFIX, 1)[1]


def extract_json(text):
    """从模型回复里抠 JSON（容忍 ``` 围栏和前后废话）。"""
    if not text:
        return None
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        pass
    left, right = body.find("{"), body.rfind("}")
    if 0 <= left < right:
        try:
            return json.loads(body[left:right + 1])
        except (ValueError, TypeError):
            return None
    return None


# ===================== 采集 =====================
def capture_views(devices, session_dir, warmup, drop_frames):
    """各路各取一张（MJPG + 单帧缓冲），保存 view_1..N.jpg。"""
    if len(devices) != STAGE_TWO_DETECTION_COUNT:
        raise SystemExit(f"需要 {STAGE_TWO_DETECTION_COUNT} 个设备，实际给了 {len(devices)} 个")

    caps = []
    try:
        for index, device in enumerate(devices, start=1):
            if not os.path.exists(device):
                raise SystemExit(
                    f"[{index}] 设备不存在: {device}\n"
                    f"    先确认 udev 别名在：ls -l /dev/wave/；"
                    f"或用 --devices 显式指定 /dev/videoN")
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
            if not cap.isOpened():
                raise SystemExit(
                    f"[{index}] 打不开: {device}\n"
                    f"    多半是主程序占着相机：sudo systemctl stop wave-vision-llm\n"
                    f"    或者该口带宽不足（四路挤同一条 480M 总线）")
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, DETECT_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, DETECT_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, DETECT_FPS)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:  # noqa: BLE001 —— 个别 V4L2 后端不支持
                pass
            caps.append(cap)

        print(f"预热 {warmup:.1f}s + 丢弃 {drop_frames} 帧（让自动曝光/白平衡收敛）…")
        deadline = time.monotonic() + warmup
        while time.monotonic() < deadline:
            for cap in caps:
                cap.grab()
            time.sleep(0.02)
        for _ in range(drop_frames):
            for cap in caps:
                cap.grab()

        frames = []
        for index, (cap, device) in enumerate(zip(caps, devices), start=1):
            ok, frame = cap.read()
            if not ok or frame is None:
                raise SystemExit(f"[{index}] 读帧失败: {device}")
            frames.append(frame)
    finally:
        for cap in caps:
            cap.release()

    os.makedirs(session_dir, exist_ok=True)
    for index, frame in enumerate(frames, start=1):
        path = os.path.join(session_dir, f"view_{index}.jpg")
        if not cv2.imwrite(path, frame):
            raise SystemExit(f"写图失败: {path}")

    print()
    for index, frame in enumerate(frames, start=1):
        height, width = frame.shape[:2]
        usable = frame_is_usable(frame)
        print(f"  机位{index} {VIEW_NAMES[index - 1]:14} {width}x{height}  "
              f"mean={frame.mean():5.1f} std={frame.std():5.1f}  "
              f"{'正常' if usable else '⚠ 疑似废帧（过曝/全黑/未就绪），建议重拍'}")
    print(f"\n已保存: {session_dir}")
    return frames


# ===================== 找最近一次采集 =====================
def latest_session(out_root):
    if not os.path.isdir(out_root):
        return None
    candidates = []
    for name in os.listdir(out_root):
        directory = os.path.join(out_root, name)
        if all(os.path.isfile(os.path.join(directory, f"view_{i}.jpg"))
               for i in range(1, STAGE_TWO_DETECTION_COUNT + 1)):
            candidates.append(directory)
    return max(candidates, key=os.path.getmtime) if candidates else None


def load_views(session_dir):
    frames = []
    for index in range(1, STAGE_TWO_DETECTION_COUNT + 1):
        path = os.path.join(session_dir, f"view_{index}.jpg")
        frame = cv2.imread(path)
        if frame is None:
            raise SystemExit(f"读不到图: {path}")
        frames.append(frame)
    return frames


def list_sessions(out_root):
    if not os.path.isdir(out_root):
        print(f"{out_root} 还不存在（还没采集过）")
        return
    names = sorted(os.listdir(out_root), reverse=True)
    if not names:
        print("还没有采集记录")
        return
    for name in names:
        directory = os.path.join(out_root, name)
        record = os.path.join(directory, "result.jsonl")
        count = sum(1 for _ in open(record, encoding="utf-8")) if os.path.isfile(record) else 0
        print(f"  {name:38} runs={count}")


# ===================== 单次识别 =====================
def save_sent(frames, session_dir):
    """把模型真正看到的图存下来（与线上 crop_detect_llm 一致）。"""
    sent = [crop_detect_llm(frame) for frame in frames]
    for index, image in enumerate(sent, start=1):
        cv2.imwrite(os.path.join(session_dir, f"sent_{index}.jpg"), image)
    return sent


def build_recognizer(session_dir, empty, shape_examples, memory=None):
    """新建一个识别器；memory 为空则新建本次运行的形状记忆。"""
    if memory is None:
        memory = ShapeMemory(
            os.path.join(session_dir, "memory"),
            limit=int(os.environ.get("LLM_SHAPE_MEMORY_LIMIT", "12")),
            view_count=STAGE_TWO_DETECTION_COUNT,
        )
    return ShapeRecognizer(memory, empty["detect_area"], shape_examples=shape_examples)


def run_once(frames, session_dir, run_index, timeout, collector, make_recognizer, dry_run):
    """跑一次识别；返回记录 dict。"""
    sent = save_sent(frames, session_dir)
    height, width = sent[0].shape[:2]
    record = {
        "run": run_index,
        "sent_size": f"{width}x{height}",
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    if dry_run:
        record["dry_run"] = True
        return record

    collector.reset()
    recognizer = make_recognizer()

    reply_path = os.path.join(session_dir, f"reply_{run_index:02d}_classify.txt")
    started = time.monotonic()
    try:
        color, shape, label_kind, label_desc = recognizer.classify(
            frames,
            response_path=lambda _kind: reply_path,
            total_timeout=timeout,
        )
        record.update(color=color, shape=shape,
                      label_kind=label_kind, label_desc=label_desc, error=None)
    except LLMError as error:
        record.update(color=None, shape=None, label_kind=None, label_desc=None,
                      error=str(error))
    elapsed = time.monotonic() - started

    # 状态（known/remembered/new/uncertain/empty）只能从模型原始回复里读
    parsed = extract_json(collector.raw_reply)
    record["status"] = (parsed or {}).get("status")
    record["raw_reply"] = collector.raw_reply
    record["seconds"] = round(elapsed, 3)
    record["request_seconds"] = collector.request_seconds
    record["requests"] = collector.requests
    record["usage"] = dict(collector.usage)
    record["payload"] = collector.payload
    return record


# ===================== 汇总输出 =====================
def describe(record):
    if record.get("error"):
        return f"失败: {record['error'][:60]}"
    if record.get("dry_run"):
        return "dry-run（未发请求）"
    status = record.get("status") or "?"
    name = f"{record.get('color')}/{record.get('shape')}"
    if record.get("label_kind") and record["label_kind"] != "none":
        name += f" +贴纸:{record['label_kind']}"
    return f"{status:11} {name}"


def print_summary(records):
    real = [r for r in records if not r.get("dry_run")]
    if not real:
        return
    times = [r["seconds"] for r in real if r.get("seconds") is not None]
    print("\n===== 汇总 =====")
    if times:
        print(f"耗时  中位 {statistics.median(times):.2f}s   均值 {statistics.mean(times):.2f}s"
              f"   最小 {min(times):.2f}s   最大 {max(times):.2f}s   （n={len(times)}）")
    request_times = [r["request_seconds"] for r in real if r.get("request_seconds")]
    if request_times:
        print(f"其中真实请求（LLM 请求→响应）中位 {statistics.median(request_times):.2f}s")
    keys = []
    for record in real:
        if record.get("error"):
            keys.append("__error__")
        else:
            keys.append((record.get("status"), record.get("color"),
                         record.get("shape"), record.get("label_kind"), record.get("label_desc")))
    unique = len(set(keys))
    success = sum(1 for key in keys if key != "__error__")
    print(f"一致性：{success}/{len(real)} 次成功，结果有 {unique} 种")
    usage = [r["usage"] for r in real if r.get("usage")]
    if usage:
        cached = [u.get("cached_tokens") for u in usage if isinstance(u.get("cached_tokens"), int)]
        prompt = [u.get("prompt_tokens") for u in usage if isinstance(u.get("prompt_tokens"), int)]
        if prompt and cached:
            ratio = sum(cached) / max(sum(prompt), 1) * 100
            print(f"缓存命中 {ratio:.0f}%  （prompt≈{prompt[-1]} tokens，cached≈{cached[-1]}）")
    failures = sum(1 for r in real if r.get("error"))
    if failures:
        print(f"⚠ 失败 {failures}/{len(real)} 次")
    print("================")


# ===================== 主流程 =====================
def main():
    parser = argparse.ArgumentParser(
        description="识别区离线调试：各路拍照 → 现有 prompt → 大模型 → 记录结果/耗时/用量")
    parser.add_argument("--capture", action="store_true", help="先采集一组（各路各一张）")
    parser.add_argument("--latest", action="store_true", help="用最近一次采集的那组")
    parser.add_argument("--images", nargs=STAGE_TWO_DETECTION_COUNT, metavar="IMG",
                        help="直接用 N 张图（N=STAGE_TWO_DETECTION_COUNT；顺序：①斜上 ②斜上 ③正下 [④侧视]）")
    parser.add_argument("--tag", default="", help="给这次采集起个名字，便于以后 --list 里认")
    parser.add_argument("--runs", type=int, default=1, help="重复识别次数（默认 1）")
    parser.add_argument("--timeout", type=float, default=None,
                        help="单轮总时限（默认读 .env 的 LLM_FALLBACK_TIMEOUT，再退到 15）")
    parser.add_argument("--out", default=OUT_ROOT, help="输出根目录（默认 capture/offline）")
    parser.add_argument("--devices", nargs=STAGE_TWO_DETECTION_COUNT, metavar="DEV",
                        help="覆盖各路设备路径（默认 const 里的 /dev/wave/cam_detect_1..N）")
    parser.add_argument("--warmup", type=float, default=1.5, help="采集预热秒数（默认 1.5）")
    parser.add_argument("--drop", type=int, default=20, help="预热后再丢弃的帧数（默认 20）")
    parser.add_argument("--keep-memory", action="store_true",
                        help="多次 run 共用同一份形状记忆（模拟同一场次连续第 N 件）")
    parser.add_argument("--dry-run", action="store_true", help="只走流程不发请求")
    parser.add_argument("--list", action="store_true", help="列出历史采集")
    parser.add_argument("--quiet", action="store_true", help="不打印 LLM 每步日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    collector = QwCollector()
    logging.getLogger().addHandler(collector)

    if args.list:
        list_sessions(args.out)
        return

    if not (args.capture or args.latest or args.images):
        parser.error("至少要给一个来源：--capture / --latest / --images")

    timeout = args.timeout
    if timeout is None:
        timeout = float(os.environ.get("LLM_FALLBACK_TIMEOUT", "15"))

    # 坑：qw.py 发送前还有一道 LLM_MAX_SIDE 封顶。DETECT_SEND_MAX_SIDE 设得比它大
    # 不会报错，但实际送图还是被压到 LLM_MAX_SIDE（payload/token 完全一样）。
    llm_max_side = int(os.environ.get("LLM_MAX_SIDE", "1024"))
    if DETECT_CROP_MODE == "full" and DETECT_SEND_MAX_SIDE > llm_max_side:
        print(f"⚠ DETECT_SEND_MAX_SIDE={DETECT_SEND_MAX_SIDE} 但 LLM_MAX_SIDE={llm_max_side}："
              f"发送前会被封顶到 {llm_max_side}px，实际送图不是你要的尺寸。"
              f"要真发原图，把 LLM_MAX_SIDE 也提到 {DETECT_SEND_MAX_SIDE}。")

    print(f".env 载入 {ENV_LOADED} 项 | 单轮时限 {timeout:g}s | "
          f"模型 {os.environ.get('DASHSCOPE_MODEL', '?')}")
    if not args.dry_run and not os.environ.get("DASHSCOPE_API_KEY"):
        raise SystemExit("缺少 DASHSCOPE_API_KEY：确认项目根目录有 .env，或先 export（--dry-run 不需要 key）")

    # ---- 1) 取图 ----
    session_dir = None
    if args.images:
        session_dir = os.path.join(args.out, datetime.now().strftime("%m%d_%H%M%S") + "_images")
        os.makedirs(session_dir, exist_ok=True)
        for index, path in enumerate(args.images, start=1):
            frame = cv2.imread(path)
            if frame is None:
                raise SystemExit(f"读不到图: {path}")
            cv2.imwrite(os.path.join(session_dir, f"view_{index}.jpg"), frame)
        frames = load_views(session_dir)
        print(f"使用指定图片，已归档到: {session_dir}")
    elif args.capture:
        stamp = datetime.now().strftime("%m%d_%H%M%S")
        name = f"{stamp}_{args.tag}" if args.tag else stamp
        session_dir = os.path.join(args.out, name)
        devices = tuple(args.devices) if args.devices else tuple(DETECT_CAMERA_IDS)
        for index, device in enumerate(devices, start=1):
            print(f"  机位{index} {VIEW_NAMES[index - 1]:14} {device}")
        frames = capture_views(devices, session_dir, args.warmup, args.drop)
    else:  # --latest
        session_dir = latest_session(args.out)
        if session_dir is None:
            raise SystemExit(f"{args.out} 下没有可用的采集（先跑 --capture）")
        frames = load_views(session_dir)
        print(f"使用最近一次采集: {session_dir}")

    # 与线上采集线程一致：square 模式在这里裁居中方正（720x720），
    # full 模式保持全幅交给 crop_detect_llm 等比缩小。少了这一步，内缩比例会作用在
    # 1280x720 上，变成 947x533 的长方形——线上根本不会这样送图。
    native_w, native_h = frames[0].shape[1], frames[0].shape[0]
    frames = [detect_frame_preprocessed(frame) for frame in frames]
    square_w, square_h = frames[0].shape[1], frames[0].shape[0]
    if DETECT_CROP_MODE == "full":
        print(f"送图模式 full：原始 {native_w}x{native_h} -> 全幅，长边 <= {DETECT_SEND_MAX_SIDE}px")
    else:
        print(f"送图模式 square：原始 {native_w}x{native_h} -> 居中正方 {square_w}x{square_h}"
              f"（左右各切 {max(0, (native_w - square_w) // 2)} px）")

    # ---- 2) 起识别器（每次 run 内部各自新建记忆，除非 --keep-memory） ----
    empty = load_empty_examples()
    shape_examples = load_shape_examples()

    # 共用记忆（--keep-memory）时只建一次；否则每次 run 各建一份（等于"程序刚启动"）
    shared_memory = None
    if args.keep_memory and not args.dry_run:
        shared_memory = ShapeMemory(
            os.path.join(session_dir, "memory"),
            limit=int(os.environ.get("LLM_SHAPE_MEMORY_LIMIT", "12")),
            view_count=STAGE_TWO_DETECTION_COUNT,
        )

    def make_recognizer():
        return build_recognizer(session_dir, empty, shape_examples, memory=shared_memory)

    records = []
    print(f"\n开始识别（{STAGE_TWO_DETECTION_COUNT} 张图，{args.runs} 次"
          f"{'，共用形状记忆' if shared_memory else ''}）…")
    for run_index in range(1, args.runs + 1):
        record = run_once(frames, session_dir, run_index, timeout,
                          collector, make_recognizer, args.dry_run)
        records.append(record)
        seconds = record.get("seconds")
        timing = f"{seconds:5.2f}s" if seconds is not None else "  -   "
        print(f"  run {run_index}/{args.runs}  {timing}  {describe(record)}")

    # ---- 3) 落盘 ----
    os.makedirs(session_dir, exist_ok=True)
    with open(os.path.join(session_dir, "result.jsonl"), "a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    meta_path = os.path.join(session_dir, "meta.json")
    meta = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as stream:
                meta = json.load(stream)
        except (OSError, ValueError):
            meta = {}
    meta.setdefault("created", datetime.now().isoformat(timespec="seconds"))
    meta.setdefault("devices", list(args.devices) if args.devices else list(DETECT_CAMERA_IDS))
    meta.update({
        "model": os.environ.get("DASHSCOPE_MODEL"),
        "cache_mode": os.environ.get("LLM_CACHE_MODE"),
        "reasoning": os.environ.get("LLM_REASONING_EFFORT"),
        "jpeg_quality": os.environ.get("LLM_JPEG_QUALITY"),
        "fallback_timeout": timeout,
        "view_count": STAGE_TWO_DETECTION_COUNT,
        "view_order": list(VIEW_NAMES),
        "session": os.path.basename(session_dir),
        "tag": args.tag or None,
    })
    with open(meta_path, "w", encoding="utf-8") as stream:
        json.dump(meta, stream, ensure_ascii=False, indent=2)

    print_summary(records)
    print(f"\n明细: {os.path.join(session_dir, 'result.jsonl')}")
    print(f"原始回复: {session_dir}/reply_*_classify.txt")


if __name__ == "__main__":
    main()
