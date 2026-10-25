"""线上赛段专用：托盘一次批量识别（6 张类别参考图 + 1 张托盘俯视）。

目标：把「每件一次 4 视角识别」换成「整盘一次识别」，24 件的识别时间从
24×3~5s 压到一次 5~9s。代价是只看得见一张俯视图——薄壁/实心、棱长比这类
细节判据基本失效，所以**六类参考图必须选彼此差别很大的**，由使用者自定。

取件顺序（与电控约定，程序负责排序，不依赖模型输出顺序）：
共 COLUMN_COUNT 栏，从最右一列开始往左；同一列内从画面顶部往下。
即 lane 从大到小，同一 lane 内 y 从小到大。
"""

import logging
from pathlib import Path

import cv2

from llm.qw import LLMError, qwen_realise
from shared.const import COLUMN_COUNT

logger = logging.getLogger("wave.vision")

EXAMPLE_DIR = Path(__file__).resolve().parent / "example"

# 六个类别：列表下标 + 1 = 类别号（1..6）。换自己的参考图只改这里。
# 参考图越"一眼可辨"越好：颜色和外形都要拉开，别放两件都是黑薄壁四棱柱。
ONLINE_REFS = (
    {"filename": "black_cube_top.jpg", "name": "黑色正方体"},
    {"filename": "black_prism4_thinwalled_top.jpg", "name": "黑色薄壁四棱柱"},
    {"filename": "black_prism5_thinwalled_top.jpg", "name": "黑色薄壁五棱柱"},
    {"filename": "yellow_prism4_thinwalled_top.jpg", "name": "黄色薄壁四棱柱"},
    {"filename": "black_prism6_top.jpg", "name": "黑色六棱柱"},
    {"filename": "orange_prism3_top.jpg", "name": "橙色三棱柱"},
)

PROMPT_REFS = (
    "以下是 %d 张类别参考图，编号从 1 开始、按顺序一一对应："
    "图1=类别1 … 图%d=类别%d。每张只含一件物体，只用于比对颜色与外形。"
    % (len(ONLINE_REFS), len(ONLINE_REFS), len(ONLINE_REFS))
)

PROMPT_CURRENT = f"""最后一张是托盘俯视图。托盘按画面从左到右均分为 {COLUMN_COUNT} 栏（栏号 1..{COLUMN_COUNT}，1 最左、{COLUMN_COUNT} 最右）。
请找出托盘上的每个独立几何体（排除底板、支架、推杆、阴影），为每个物体输出：
- lane：所在栏号 1..{COLUMN_COUNT}
- y：物体中心在画面中的纵向相对位置，0=最上方、1=最下方，保留两位小数
- cls：与哪张参考图的类别最像（1..{len(ONLINE_REFS)}）；都不像用 0
只输出 JSON：{{"objects":[{{"lane":5,"y":0.12,"cls":1}}, ...]}}
不要输出解释、Markdown 或前后缀文字。物体在俯视图里可能很小，逐个看清再回答，不要漏件也不要重复计数。"""

_refs_cache = None


def ref_names():
    """六个类别的显示名，顺序 = 类别号 1..6。"""
    return [spec["name"] for spec in ONLINE_REFS]


def load_refs():
    """读取六张参考图（只读一次）。缺失时给出明确报错。"""
    global _refs_cache
    if _refs_cache is None:
        images = []
        for spec in ONLINE_REFS:
            path = EXAMPLE_DIR / spec["filename"]
            image = cv2.imread(str(path))
            if image is None or image.size == 0:
                raise LLMError(f"缺少线上类别参考图: {path}")
            images.append(image)
        _refs_cache = images
    return _refs_cache


def parse_objects(value, lane_count=COLUMN_COUNT, class_count=None):
    """校验并排序批量结果；顺序 = 电控取件顺序（列从右到左，同列从上到下）。"""
    class_count = class_count or len(ONLINE_REFS)
    if not isinstance(value, dict) or not isinstance(value.get("objects"), list):
        raise LLMError('批量结果必须是 {"objects": [...]} 对象')
    objects = []
    for index, raw in enumerate(value["objects"]):
        if not isinstance(raw, dict):
            logger.warning("批量结果第 %d 项不是对象，已跳过: %r", index + 1, raw)
            continue
        try:
            lane = int(raw["lane"])
            y = float(raw["y"])
            cls = int(raw["cls"])
        except (KeyError, TypeError, ValueError):
            logger.warning("批量结果第 %d 项字段非法，已跳过: %r", index + 1, raw)
            continue
        if not 1 <= lane <= lane_count or not 0 <= cls <= class_count:
            logger.warning("批量结果第 %d 项越界，已跳过: lane=%s cls=%s", index + 1, lane, cls)
            continue
        objects.append({"lane": lane, "y": min(1.0, max(0.0, y)), "cls": cls})
    if not objects:
        raise LLMError("托盘上没有解析到任何物体")
    objects.sort(key=lambda item: (-item["lane"], item["y"]))
    return objects


def classify_pallet(tray_image, total_timeout=40.0, save_path=None):
    """整盘一次识别：返回按取件顺序排好的 [{lane, y, cls}, ...]。"""
    return qwen_realise(
        [tray_image], PROMPT_CURRENT,
        message_save_path=save_path, validator=parse_objects,
        total_timeout=total_timeout,
        prefix_sections=[(PROMPT_REFS, load_refs())],
    )
