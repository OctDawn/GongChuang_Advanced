"""离线回归：合成图像 + 模拟串口/显示，不使用相机、NPU 或 API。"""
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import main_laser_llm as main
from shared.common import FrameStream
from shared.laser import ChangeDetector, get_detection_frame, laser_task
from shared.show import DisplayUpdate, RecognitionRow, _drain_row_queue, run_recognition_display
from llm.shape_memory import ShapeMemory


def blank():
    return np.zeros((40, 60, 3), dtype=np.uint8)


def ready_detector(**kwargs):
    detector = ChangeDetector(stable_frames=2, **kwargs)
    now = time.perf_counter()
    for i in range(3):
        detector.update(blank(), now - 0.03 + i * 0.01)
    assert detector.ready.is_set()
    return detector


def test_roi_uses_actual_frame_size():
    assert get_detection_frame(blank()).shape == (20, 30, 3)
    with pytest.raises(ValueError):
        get_detection_frame(blank(), (0.8, 0.2, 0, 1))


def test_idle_and_outside_roi_never_trigger():
    detector = ready_detector()
    frame = blank()
    frame[:5] = 255
    assert not detector.update(frame).triggered
    detector.arm()
    assert not detector.update(frame).triggered


def test_debounce_one_shot_and_rearm():
    detector = ready_detector(trigger_frames=2)
    detector.arm()
    changed = blank() + 200
    assert not detector.update(changed).triggered
    assert detector.update(changed).triggered
    assert not detector.update(changed).triggered
    assert not detector.update(changed).triggered
    detector.arm()
    assert not detector.update(blank()).triggered
    assert detector.update(blank()).triggered


def test_frozen_reference_accumulates_slow_changes():
    detector = ready_detector()
    detector.arm()
    for value in (10, 20, 30):
        assert not detector.update(blank() + value).triggered
    assert detector.update(blank() + 40).triggered


def test_stale_frames_and_stale_baseline():
    detector = ready_detector()
    now = time.perf_counter()
    detector.arm(now=now)
    assert not detector.update(blank()+255, now-0.001).triggered
    assert detector.update(blank()+255, now+0.001).triggered
    detector = ready_detector()
    with pytest.raises(RuntimeError):
        detector.arm(now=time.perf_counter()+2)


def test_broadcast_readers_do_not_steal_frames():
    stream = FrameStream()
    original = blank()
    stream.publish(original)
    original[:] = 255
    stop = threading.Event()
    a = stream.wait_after(0, stop)
    b = stream.wait_after(0, stop)
    assert a.sequence == b.sequence == 1
    assert not a.image.any()
    assert not a.image.flags.writeable
    stop.set()
    with pytest.raises(InterruptedError):
        stream.wait_after(1, stop)


def make_controller(tmp_path):
    frame = blank()
    controller = main.LaserController(
        FrameStream(), {"left": FrameStream()}, ready_detector(), queue.Queue(),
        queue.Queue(), threading.Event(),
        SimpleNamespace(classify=lambda *a, **kw: ("red", "cube", None)),
        SimpleNamespace(classify=lambda *a, **kw: ("red", "cube", None)), tmp_path)
    controller.stable_frames = lambda after: [frame]
    return controller


def test_round_order_duplicate_commands_and_display_ack(tmp_path):
    controller = make_controller(tmp_path)
    controller.on_command("v")  # 不允许提前确认
    assert controller.jobs.empty()
    controller.on_command("a")
    controller.on_command("a")
    assert controller.jobs.qsize() == 1
    controller.on_change(None, SimpleNamespace(difference=0.4))
    controller.on_change(None, SimpleNamespace(difference=0.4))
    message = controller.outgoing.get_nowait()
    assert message.text == "s" and controller.outgoing.empty()
    message.sent.set()
    controller.process_object()
    assert controller.outgoing.get_nowait().text == "1"
    assert controller.counts == [0]*6
    controller.on_command("v")
    controller.on_command("v")
    worker = threading.Thread(target=controller.finish_display)
    worker.start()
    update = controller.display_queue.get(timeout=1)
    assert isinstance(update, DisplayUpdate)
    assert controller.outgoing.empty()  # 刷新回执之前绝不发 f
    update.done.set()
    worker.join(1)
    assert not worker.is_alive()
    assert controller.outgoing.get_nowait().text == "f"
    assert controller.counts[0] == 1 and controller.state == "IDLE"


def test_no_drop_timeout_stops_before_zero(tmp_path, monkeypatch):
    controller = make_controller(tmp_path)
    monkeypatch.setattr(main.C, "LASER_DROP_TIMEOUT", 0.01)
    controller.on_command("a")
    worker = threading.Thread(target=controller.process_object)
    worker.start()
    message = controller.outgoing.get(timeout=1)
    assert message.text == "s"
    message.sent.set()
    worker.join(1)
    assert not worker.is_alive()
    assert controller.outgoing.get_nowait().text == "0"
    assert controller.pending is None


def test_classification_failure_falls_back(tmp_path):
    controller = make_controller(tmp_path)
    def fail(*args):
        raise RuntimeError("模拟 LLM 故障")
    controller.recognizer.classify = fail
    controller.on_command("a")
    controller.request_stop(True)
    controller.outgoing.get_nowait().sent.set()
    controller.process_object()
    assert controller.outgoing.get_nowait().text == "1"


def test_full_box_returns_zero(tmp_path):
    controller = make_controller(tmp_path)
    controller.categories[("red", "cube")] = 1
    controller.counts[0] = 4
    controller.on_command("a")
    controller.request_stop(True)
    controller.outgoing.get_nowait().sent.set()
    controller.process_object()
    assert controller.outgoing.get_nowait().text == "0"
    assert controller.pending is None


def test_check_four_columns_and_direction(tmp_path, monkeypatch):
    controller = make_controller(tmp_path)
    controller.pallet.wait_after = lambda *a, **kw: SimpleNamespace(image=blank())
    from llm.pallet_monitor import PalletMonitor
    monkeypatch.setattr(main.C, "LASER_SENSOR_ON_LEFT", False)
    controller.monitor = SimpleNamespace(latest=lambda age: PalletMonitor.parse(
        {"objects": [{"lane": 1, "color": "red", "shape": "cube"}]}))
    controller.check_columns()
    assert controller.outgoing.get_nowait().text == "0\n0\n0\n1"


def test_serial_batched_input_and_single_character(tmp_path):
    stop = threading.Event()
    commands, written = [], []
    class Port:
        in_waiting = 6
        def write(self, data):
            written.append(data)
            return len(data)
        def flush(self):
            pass
        def read(self, size):
            stop.set()
            return b"c\na\rv"
    outgoing = queue.Queue()
    message = main.SerialMessage("s")
    outgoing.put(message)
    main.serial_task(Port(), outgoing, SimpleNamespace(on_command=commands.append), stop)
    assert commands == ["c", "a", "v"]
    assert written == [b"s\n"] and message.sent.is_set()


@pytest.mark.parametrize("view_count", [1, 2, 4])
def test_memory_real_view_counts(tmp_path, view_count):
    memory = ShapeMemory(tmp_path, view_count=view_count)
    assert memory.add("测试结构", [blank()] * view_count) == "unknown_001"
    references = memory.reference_images()
    assert len(references) == 1 and references[0].size > 0


def test_display_ack_after_flip(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    import pygame
    stop = threading.Event()
    done = threading.Event()
    rows = queue.Queue()
    rows.put(DisplayUpdate(RecognitionRow(1, "cube", blank(), 1), done))
    real_flip = pygame.display.flip
    def flip():
        assert not done.is_set()
        real_flip()
        stop.set()
    monkeypatch.setattr(pygame.display, "flip", flip)
    run_recognition_display(rows, stop)
    assert done.is_set()


def test_existing_display_rows_still_supported():
    rows = [None]*6
    incoming = queue.Queue()
    row = RecognitionRow(1, "cube", None, 2)
    incoming.put(row)
    _drain_row_queue(incoming, rows)
    assert rows[0] == row


def test_laser_thread_and_controller_complete_round(tmp_path):
    controller = make_controller(tmp_path)
    stream = controller.cameras["left"]
    # laser_task 的停止异常由生产环境的 guarded 包装处理。
    def run():
        try:
            laser_task(stream, controller.detector, controller.on_change, controller.stop_event)
        except InterruptedError:
            pass
    thread = threading.Thread(target=run)
    thread.start()
    try:
        controller.on_command("a")
        stream.publish(blank()+255)
        message = controller.outgoing.get(timeout=1)
        assert message.text == "s"
        message.sent.set()
        controller.process_object()
        assert controller.outgoing.get(timeout=1).text == "1"
    finally:
        controller.stop_event.set()
        thread.join(1)
    assert not thread.is_alive()


def test_two_cameras_stable_frames_are_after_stop(tmp_path, monkeypatch):
    controller = make_controller(tmp_path)
    # 使用真实方法，喂入两路不同颜色且有时间戳的新帧。
    del controller.stable_frames
    controller.cameras["right"] = FrameStream()
    monkeypatch.setattr(main.C, "REQUIRED_STABLE_FRAMES", 2)
    controller.cameras["left"].publish(blank()+255, captured_at=1.0)
    after = time.perf_counter()
    def produce():
        for _ in range(10):
            time.sleep(0.01)
            controller.cameras["left"].publish(blank()+50)
            controller.cameras["right"].publish(blank()+100)
            controller.pallet.publish(blank()+150)
    producer = threading.Thread(target=produce)
    producer.start()
    frames = controller.stable_frames(after)
    producer.join()
    assert len(frames) == 3
    assert np.all(frames[0] == 50) and np.all(frames[1] == 100)
    assert np.all(frames[2] == 150) and frames[2].shape[:2] == (20, 60)


def test_yolo_custom_columns_does_not_change_default():
    from shared.common import column_occupancy
    from shared.const import COLUMN_COUNT
    assert len(column_occupancy([], (100, 100, 3))) == COLUMN_COUNT
    assert len(column_occupancy([], (100, 100, 3), column_count=4)) == 4


def test_fallback_allows_one_real_view_but_old_default_requires_four():
    from yolo.fallback import YoloFallback
    runner = SimpleNamespace(infer=lambda *a, **kw: ([], [], []))
    fallback = YoloFallback(runner, runner, runner, runner)
    with pytest.raises(ValueError):
        fallback.classify([blank()])
    assert fallback.classify([blank()], expected_count=None) == (None, None, None)


def test_display_failure_does_not_send_f(tmp_path, monkeypatch):
    controller = make_controller(tmp_path)
    monkeypatch.setattr(main.C, "LASER_DISPLAY_TIMEOUT", 0.01)
    controller.pending = (1, "cube", blank())
    with pytest.raises(TimeoutError):
        controller.finish_display()
    assert controller.outgoing.empty() and controller.counts[0] == 0


@pytest.mark.parametrize("view_count", [1, 2])
def test_recognizer_uses_real_views_and_remembers_new_shape(tmp_path, view_count):
    from llm.shape_recognition import FixedViewRecognizer
    calls = []
    def request(images, prompt, **kwargs):
        calls.append((images, kwargs["prefix_sections"]))
        result = dict(status="new" if len(calls) == 1 else "remembered", color="red",
                      shape=None if len(calls) == 1 else "unknown_001",
                      shape_description="有两个孔洞的块体", label_kind="none")
        return kwargs["validator"](result)
    recognizer = FixedViewRecognizer(ShapeMemory(tmp_path, view_count=view_count), request=request)
    frames = [blank()] * view_count
    assert recognizer.classify(frames) == ("red", "unknown_001", None)
    assert recognizer.classify(frames) == ("red", "unknown_001", None)
    assert all(len(images) == view_count for images, _ in calls)
    assert not calls[0][1] and len(calls[1][1][0][1]) == 1


def begin_drop(controller):
    controller.on_command("a")
    controller.request_stop(True)
    assert controller.outgoing.get_nowait().text == "s"
    controller.stop_message.sent.set()


def test_early_new_group_accepts_v_before_detail_finishes(tmp_path):
    controller = make_controller(tmp_path)
    controller.recognizer.quick_match = lambda *args: ("new", None)
    def detail(*args):
        assert controller.outgoing.get_nowait().text == "1"
        assert controller.state == "WAIT_V"
        controller.on_command("v")
        assert controller.state == "DISPLAY"
        return "red", "cube", None
    controller.recognizer.classify = detail
    begin_drop(controller)
    controller.process_object()
    assert controller.outgoing.empty()
    assert controller.pending[0] == 1 and controller.groups[1]["shape"] == "cube"
    assert list(controller.jobs.queue) == ["a", "v"]


def test_detail_failure_never_retracts_early_box(tmp_path):
    controller = make_controller(tmp_path)
    controller.recognizer.quick_match = lambda *args: ("new", None)
    def fail(*args, **kwargs):
        raise RuntimeError("failed")
    controller.recognizer.classify = controller.fallback.classify = fail
    begin_drop(controller)
    controller.process_object()
    assert controller.outgoing.get_nowait().text == "1" and controller.outgoing.empty()
    assert controller.pending[0] == 1 and controller.groups[1]["shape"] is None


def test_existing_quick_group_skips_detail(tmp_path):
    controller = make_controller(tmp_path)
    gid = controller.reserve_group([blank()]*3)
    controller.groups[gid].update(name="红色正方体", color="red", shape="cube")
    controller.recognizer.quick_match = lambda *args: ("existing", gid)
    controller.recognizer.classify = lambda *args: pytest.fail("不应调用详细识别")
    begin_drop(controller)
    controller.process_object()
    assert controller.outgoing.get_nowait().text == "1"


def test_early_duplicate_keeps_committed_box(tmp_path):
    controller = make_controller(tmp_path)
    controller.reserve_group([blank()]*3)
    controller.categories[("red", "cube")] = 1
    controller.recognizer.quick_match = lambda *args: ("new", None)
    begin_drop(controller)
    controller.process_object()
    assert controller.outgoing.get_nowait().text == "2"
    assert controller.pending[0] == 2 and controller.categories[("red", "cube")] == 1
    assert controller.outgoing.empty()


def test_inventory_invalidates_inflight_result_and_retries():
    from llm.pallet_monitor import PalletMonitor
    stream = SimpleNamespace(wait_after=lambda *a, **kw: SimpleNamespace(
        image=blank(), captured_at=time.perf_counter(), sequence=1))
    stop = threading.Event()
    monitor = PalletMonitor(stream, stop)
    def request(*args, **kwargs):
        monitor.invalidate()
        return kwargs["validator"]({"objects": []})
    monitor.request = request
    monitor.scan_once()
    assert monitor.latest() is None
    monitor.request = lambda *a, **kw: kw["validator"]({"objects": []})
    monitor.scan_once()
    assert monitor.latest(1)["occupancy"] == [False]*4
    monitor.invalidate()
    assert monitor.latest(1) is None and monitor.latest() is not None


def test_inventory_age_uses_capture_not_completion():
    from llm.pallet_monitor import PalletMonitor
    monitor = PalletMonitor(None, threading.Event())
    monitor.snapshot = {"captured_at": time.perf_counter()-10,
                        "completed_at": time.perf_counter(), "epoch": 0}
    assert monitor.latest(3) is None


def test_quick_match_rejects_unknown_box(tmp_path):
    from llm.shape_recognition import FixedViewRecognizer
    from llm.qw import LLMError
    recognizer = FixedViewRecognizer(ShapeMemory(tmp_path, view_count=3), request=lambda *a, **kw:
                                 kw["validator"]({"status": "existing", "group": 6}))
    with pytest.raises(LLMError):
        recognizer.quick_match([blank()]*3, {})


def test_color_shape_fallback_skips_label_model():
    from yolo.fallback import YoloFallback
    runner = SimpleNamespace(infer=lambda *a, **kw: ([], [], []))
    label = SimpleNamespace(infer=lambda *a, **kw: pytest.fail("不应推理贴纸"))
    assert YoloFallback(runner, runner, runner, label).classify(
        [blank()]*3, expected_count=None, include_labels=False) == (None, None, None)


def test_monitor_continues_after_request_failure(monkeypatch):
    from llm.pallet_monitor import PalletMonitor
    monitor = PalletMonitor(None, threading.Event())
    calls = []
    def scan():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary failure")
        monitor.stop_event.set()
    monitor.scan_once = scan
    monkeypatch.setattr(main.C, "LASER_PALLET_INTERVAL", 0.001)
    monitor.run()
    assert len(calls) == 2


def test_shutdown_suppresses_late_category(tmp_path):
    controller = make_controller(tmp_path)
    controller.stop_event.set()
    with pytest.raises(InterruptedError):
        controller.publish_category(1)
    assert controller.outgoing.empty()


@pytest.mark.parametrize("has_httpx", [False, True])
def test_merged_client_singleton_keeps_connection_options(monkeypatch, has_httpx):
    from concurrent.futures import ThreadPoolExecutor
    from llm import qw
    calls = []
    def create(**kwargs):
        time.sleep(0.01)
        calls.append(kwargs)
        return object()
    monkeypatch.setattr(qw, "_client", None)
    monkeypatch.setattr(qw, "OpenAI", create)
    monkeypatch.setattr(qw, "httpx", SimpleNamespace(
        Limits=lambda **kw: kw, Client=lambda **kw: kw) if has_httpx else None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: qw._get_client(), range(16)))
    assert len(calls) == 1 and all(client is clients[0] for client in clients)
    if has_httpx:
        assert calls[0]["http_client"]["limits"]["keepalive_expiry"] == qw.LLM_KEEPALIVE_EXPIRY
    else:
        assert "http_client" not in calls[0]


@pytest.mark.parametrize("broadcast", [False, True])
def test_shared_capture_keeps_latest_and_releases_camera(broadcast):
    from shared.common import read_camera
    stop = threading.Event()
    sink = FrameStream() if broadcast else queue.Queue(maxsize=1)
    class Camera:
        released = False
        reads = 0
        def isOpened(self):
            return True
        def get(self, prop):
            return 30
        def read(self):
            self.reads += 1
            if self.reads == 2:
                stop.set()
            return True, blank()+self.reads
        def release(self):
            self.released = True
    cap = Camera()
    fps = queue.Queue(maxsize=1)
    read_camera(cap, "test", sink, fps, stop, crop_square=True)
    frame = sink.latest().image if broadcast else sink.get_nowait()
    assert frame.shape == (40, 40, 3) and np.all(frame == 2)
    assert cap.released and fps.get_nowait() == 30


@pytest.mark.parametrize("broadcast", [False, True])
def test_shared_capture_failure_preserves_stop_order(broadcast):
    from shared.common import read_camera, CameraStopped
    stop = threading.Event()
    released = []
    cap = SimpleNamespace(isOpened=lambda: True, get=lambda _: 30,
                          read=lambda: (False, None), release=lambda: released.append(True))
    if broadcast:
        with pytest.raises(CameraStopped):
            read_camera(cap, "test", FrameStream(), None, stop)
        assert not stop.is_set()  # 主程序 guarded 先发 s，再停止串口线程
    else:
        read_camera(cap, "test", queue.Queue(), None, stop)
        assert stop.is_set()
    assert released == [True]


def test_shared_group_reservation_capacity_and_copy():
    from shared.common import reserve_group, parse_group_match
    groups, frame = {}, blank()
    assert reserve_group(groups, [frame], max_groups=1) == 1
    frame[:] = 255
    assert not groups[1]["frames"][0].any()
    assert reserve_group(groups, [frame], max_groups=1) == 0
    assert parse_group_match({"status": "existing", "group": 1}, groups) == ("existing", 1)
    with pytest.raises(ValueError):
        parse_group_match({"status": "existing", "group": True}, groups)


def test_old_four_view_recognizer_contract(tmp_path):
    from llm.shape_recognition import ShapeRecognizer
    from llm.prompt import PROMPT_CLASSIFY_CURRENT
    calls = []
    def request(images, prompt, **kwargs):
        calls.append((images, prompt, kwargs))
        return kwargs["validator"](dict(status="known", color="red", shape="cube",
                                        label_kind="pattern", label_description="红色星星"))
    recognizer = ShapeRecognizer(ShapeMemory(tmp_path), blank(), request=request)
    assert recognizer.classify([blank()]*4) == ("red", "cube", "pattern", "红色星星")
    assert calls[0][1] == PROMPT_CLASSIFY_CURRENT and len(calls[0][0]) == 4
    assert calls[0][2]["total_timeout"] == 40.0
    assert len(calls[0][2]["prefix_sections"]) == 2


def test_fixed_view_quick_match_reference_order_and_top_crop(tmp_path):
    import json
    from llm.shape_recognition import FixedViewRecognizer
    from llm.prompt import build_group_match_prompt
    frames = [blank()+10, blank()+20, np.zeros((20, 60, 3), dtype=np.uint8)]
    groups = {1: {"frames": [blank()+30]*3}, 2: {"frames": [blank()+40]*3}}
    def request(images, prompt, **kwargs):
        assert images is frames and images[-1].shape == (20, 60, 3)
        assert prompt == build_group_match_prompt(3)
        catalog_text, references = kwargs["prefix_sections"][0]
        catalog = json.loads(catalog_text[catalog_text.index('['):])
        assert [(r["first_image"], r["last_image"]) for r in catalog] == [(1, 3), (4, 6)]
        assert len(references) == 6
        return kwargs["validator"]({"status": "existing", "group": 2})
    recognizer = FixedViewRecognizer(ShapeMemory(tmp_path, view_count=3), request=request)
    assert recognizer.quick_match(frames, groups) == ("existing", 2)
