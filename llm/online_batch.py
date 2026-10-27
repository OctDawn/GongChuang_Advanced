"""线上赛段专用：托盘一次批量识别（6 类参考图 + 1 张托盘俯视）。

目标：把「每件一次 4 视角识别」换成「整盘一次识别」，24 件的识别时间从
24×3~5s 压到一次 5~9s。代价是只看得见一张俯视图——薄壁/实心、棱长比这类
细节判据基本失效，所以**类别之间必须差别很大**，由使用者自定。

**一个类别可以给多张参考图**（同一物体的不同姿态，比如薄壁圆柱直立/横放）。
多张图会一并作为前缀发出，类别号仍是同一个。

取件顺序（与电控约定，程序负责排序，不依赖模型输出顺序）：
共 COLUMN_COUNT 栏，从最右一列开始往左；同一列内从画面顶部往下。
即 lane 从大到小，同一 lane 内 y 从小到大。
"""

import logging
from pathlib import Path

import cv2

from llm.qw import LLMError, qwen_realise
from shared.const import COLUMN_COUNT, MAX_TEMP_CATEGORIES

logger = logging.getLogger("wave.vision")

EXAMPLE_DIR = Path(__file__).resolve().parent / "example"


def _class(name, *files):
    return {"name": name, "files": tuple(files)}


# 六个类别：列表下标 + 1 = 类别号。换参考图只改这里（文件名必须是 ASCII）。
# 一个类别可以写多张：同一件物体的不同姿态都算同一类。
# 2026-10-27：蓝色球 → 橙色正方体（类别 4）、蓝色薄壁圆柱 → 橙色薄壁圆柱（类别 6），
# 类别号保持不变，机械/电控那边的盒号映射不用改。
# 同日再次调整：类别 3 从红色球改回蓝色球（实物已换），复用旧的 online_blue_ball.jpg。
ONLINE_CLASSES = (
    _class("红色正方体", "online_red_cube.jpg"),
    _class("蓝色正方体", "online_blue_cube.jpg"),
    _class("蓝色球", "online_blue_ball.jpg"),
    _class("橙色正方体", "online_orange_cube.jpg"),
    _class("红色薄壁圆柱", "online_red_tube_up.jpg", "online_red_tube_lying.jpg"),
    _class("橙色薄壁圆柱", "online_orange_tube_lying.jpg"),
)

if len(ONLINE_CLASSES) > MAX_TEMP_CATEGORIES:
    raise RuntimeError(
        f"线上类别数 {len(ONLINE_CLASSES)} 超过储物盒上限 {MAX_TEMP_CATEGORIES}："
        "请合并类别或减少参考图组"
    )

PROMPT_CURRENT = f"""最后一张是托盘俯视图。托盘按画面从左到右均分为 {COLUMN_COUNT} 栏（栏号 1..{COLUMN_COUNT}，1 最左、{COLUMN_COUNT} 最右）。
请找出托盘上的每个独立几何体（排除底板、支架、推杆、阴影），为每个物体输出：
- lane：所在栏号 1..{COLUMN_COUNT}
- y：物体中心在画面中的纵向相对位置，0=最上方、1=最下方，保留两位小数
- cls：所属类别号（见前面的参考图说明）；都不像用 0
只输出 JSON：{{"objects":[{{"lane":5,"y":0.12,"cls":1}}, ...]}}
不要输出解释、Markdown 或前后缀文字。物体在俯视图里可能很小，逐个看清再回答，不要漏件也不要重复计数。"""

_refs_cache = None
_classes_cache = None


def class_names():
    """各类别显示名，顺序 = 类别号 1..N。"""
    return [spec["name"] for spec in ONLINE_CLASSES]


def load_refs():
    """读取全部参考图，返回 (图片列表, 每张图对应的类别号)。只读一次。"""
    global _refs_cache, _classes_cache
    if _refs_cache is None:
        images, classes = [], []
        for class_id, spec in enumerate(ONLINE_CLASSES, start=1):
            for filename in spec["files"]:
                path = EXAMPLE_DIR / filename
                image = cv2.imread(str(path))
                if image is None or image.size == 0:
                    raise LLMError(f"缺少线上类别参考图: {path}")
                images.append(image)
                classes.append(class_id)
        _refs_cache, _classes_cache = images, classes
    return _refs_cache, _classes_cache


def build_refs_prompt(classes):
    """把"第几张图属于第几类"写成明确清单（多姿态时靠它说清）。"""
    mapping = "、".join(f"图{index}=类别{class_id}"
                        for index, class_id in enumerate(classes, start=1))
    return (f"以下是 {len(classes)} 张类别参考图，编号即图片顺序：{mapping}。"
            "同一个类别可能给了多张（同一物体的不同姿态），它们都算那个类别。"
            "每张只含一件物体，只用于比对颜色与外形，不要照搬背景。")


def assign_box_numbers(objects, names, limit=None):
    """把参考类别按「首次出现顺序」映射成盒号 1..limit（与线下 categorize 同语义）。

    返回 (assignments, mapping)：
        assignments —— 与 objects 等长的 [(box, name), ...]，box=0 表示未匹配或类别已满
        mapping     —— {参考类别号: 盒号}，用于打日志核对
    """
    limit = MAX_TEMP_CATEGORIES if limit is None else int(limit)
    mapping = {}
    assignments = []
    for item in objects:
        cls = item["cls"]
        name = names[cls - 1] if 1 <= cls <= len(names) else "未匹配"
        if cls <= 0:
            assignments.append((0, name))
            continue
        if cls not in mapping:
            if len(mapping) >= limit:
                logger.error("参考类别数超过盒子上限(%d)，类别 %d 回 0", limit, cls)
                assignments.append((0, name))
                continue
            mapping[cls] = len(mapping) + 1
        assignments.append((mapping[cls], name))
    return assignments, mapping


def parse_objects(value, lane_count=COLUMN_COUNT, class_count=None):
    """校验并排序批量结果；顺序 = 电控取件顺序（列从右到左，同列从上到下）。"""
    class_count = class_count or len(ONLINE_CLASSES)
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
    refs, classes = load_refs()
    return qwen_realise(
        [tray_image], PROMPT_CURRENT,
        message_save_path=save_path, validator=parse_objects,
        total_timeout=total_timeout,
        prefix_sections=[(build_refs_prompt(classes), refs)],
    )
