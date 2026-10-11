"""顶部托盘持续盘点；单个工作线程串行请求，保留最近成功快照。

盘点不发送箱号。epoch 隔离推送/入盒前拍摄但迟到的结果。
"""
import copy
import logging
import threading
import time

from shared import const as C
from shared.common import crop_tray_region
from llm.qw import qwen_realise, LLMError
from llm.prompt import build_pallet_inventory_prompt

logger = logging.getLogger("wave.vision.pallet")


class PalletMonitor:
    def __init__(self, stream, stop_event, request=qwen_realise):
        self.stream, self.stop_event, self.request = stream, stop_event, request
        self.lock = threading.Lock()
        self.epoch = 0
        self.snapshot = None

    def invalidate(self):
        with self.lock:
            self.epoch += 1

    def latest(self, max_age=None):
        """不带 max_age 可查看旧盘点；c 只使用同 epoch 的新鲜快照。"""
        with self.lock:
            result = self.snapshot
            if result is None:
                return None
            if max_age is not None and (result["epoch"] != self.epoch or
                                       time.perf_counter()-result["captured_at"] > max_age):
                return None
            return copy.deepcopy(result)

    @staticmethod
    def parse(value):
        if not isinstance(value, dict) or not isinstance(value.get("objects"), list):
            raise LLMError("盘点必须包含 objects 数组")
        objects = []
        for item in value["objects"]:
            if not isinstance(item, dict):
                raise LLMError("盘点物体必须是对象")
            lane = item.get("lane")
            if type(lane) is not int or not 1 <= lane <= C.LASER_COLUMN_COUNT:
                raise LLMError("盘点道位越界")
            if any(not isinstance(item.get(k), str) or not item[k].strip()
                   or len(item[k]) > 100 for k in ("color", "shape")):
                raise LLMError("物体须包含简短 color/shape 描述，不确定时写 unknown")
            if not C.LASER_SENSOR_ON_LEFT:
                lane = C.LASER_COLUMN_COUNT + 1 - lane
            objects.append({"lane": lane, "color": item["color"], "shape": item["shape"]})
        occupancy = [False] * C.LASER_COLUMN_COUNT
        for item in objects:
            occupancy[item["lane"]-1] = True
        return {"objects": objects, "occupancy": occupancy}

    def scan_once(self):
        with self.lock:
            epoch = self.epoch
        packet = self.stream.wait_after(0, self.stop_event, after_time=time.perf_counter())
        result = self.request(
            [crop_tray_region(packet.image)],
            build_pallet_inventory_prompt(C.LASER_COLUMN_COUNT),
            validator=self.parse, total_timeout=C.LASER_PALLET_TIMEOUT,
        )
        if self.stop_event.is_set():
            return
        with self.lock:
            if epoch != self.epoch:
                logger.info("丢弃运动之前拍摄的迟到盘点结果")
                return
            self.snapshot = dict(result, epoch=epoch, sequence=packet.sequence,
                                 captured_at=packet.captured_at, completed_at=time.perf_counter())
        logger.info("托盘盘点（道位从传感器侧起算）: %s", result["objects"])

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.scan_once()
            except InterruptedError:
                return
            except Exception:
                logger.exception("托盘盘点失败，下一轮重试")
            self.stop_event.wait(C.LASER_PALLET_INTERVAL)
