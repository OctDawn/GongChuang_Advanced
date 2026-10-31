"""线上版2：单目 + 3 色 × 3 形固定词表（9 类）识别。

与两版的区别：
- 线上版1（main_online.py）：托盘俯视图整盘一次识别，最快，但目标小。
- 本版（main_online2.py）：物体推到识别区、只开 1 路相机逐件识别，
  取景大、光照稳定，鲁棒性接近线下；每件只发 1 张图、输出几十 token。

只判颜色和形状，贴纸一律当干扰（白色贴纸不算颜色、其边框不算棱）。
"""

import logging
import re

from llm.qw import LLMError, qwen_realise

logger = logging.getLogger("wave.vision")

COLORS = ("red", "orange", "blue")
SHAPES = ("cube", "ball", "cylinder_thinWalled")

# 模型可能写英文全称、大小写、空格、连字符或中文，统一归一。
# 注意：本集只存在“薄壁圆柱”一种圆桂，所以 cylinder / tube 也归到它。
COLOR_ALIASES = {
    "red": "red", "橙红": "red", "红": "red", "红色": "red",
    "orange": "orange", "橙": "orange", "橙色": "orange", "橘色": "orange", "橘": "orange",
    "blue": "blue", "蓝": "blue", "蓝色": "blue",
}
SHAPE_ALIASES = {
    "cube": "cube", "box": "cube", "正方体": "cube", "立方体": "cube", "方块": "cube",
    "ball": "ball", "sphere": "ball", "球": "ball", "球体": "ball", "圆球": "ball",
    "cylinder_thinWalled": "cylinder_thinWalled", "cylinder": "cylinder_thinWalled",
    "tube": "cylinder_thinWalled", "圆柱": "cylinder_thinWalled",
    "薄壁圆柱": "cylinder_thinWalled", "圆筒": "cylinder_thinWalled", "筒": "cylinder_thinWalled",
}


def _canon(value):
    """只保留字母数字并小写：'cylinder_thinWalled' 与 'Cylinder Thin-Walled' 同归一类。"""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


for _table in (COLOR_ALIASES, SHAPE_ALIASES):
    for _key, _value in list(_table.items()):
        _table.setdefault(_canon(_key), _value)

PROMPT_GRID = """你要判断画面里唯一一件目标几何体的颜色和形状。托盘、底板、支架、推杆、阴影都不算目标。

颜色只能是：red（红）、orange（橙）、blue（蓝）。
形状只能是：cube（正方体）、ball（球）、cylinder_thinWalled（薄壁圆柱，中空开口）。

【形状判据】
- cube：有平整顶面和直棱，面与面之间是直线分界，可见面都是凸出的外表面。
- ball：表面是光滑球面，轮廓是连续圆弧，任何角度都没有直边。
- cylinder_thinWalled：筒状，能看到口沿圆环、壁厚或内腔（内壁/内底面可能更暗或更亮），侧壁基本竖直。
- ball 与竖放圆柱最容易混：球的轮廓连续、高光是一块圆斑，没有口沿也没有内腔；圆柱有明确的口沿圆环且能看到内部。
- 横放的圆柱：两条平行直侧边 + 椭圆端面；球不会有直边。

【贴纸】物体表面可能贴白色贴纸（文字/图案/二维码/条码），一律视为干扰：
- 贴纸不属于物体，不参与判断；不要因为有贴纸就改变颜色或形状；
- 白色贴纸不算物体的颜色，取周围同色区域；
- 贴纸的方框边、二维码的方格不得当作物体的棱、面或开口。

【其它】
- 只允许上面列出的颜色/形状取值，不要自创。
- 证据不足用 uncertain（不要猜）；画面里确实没有物体用 empty。

只返回 JSON：{"status":"ok|empty|uncertain","color":"red|orange|blue|null","shape":"cube|ball|cylinder_thinWalled|null","reason":"一句话"}
"""


def _lookup(table, raw):
    key = str(raw or "").strip()
    if key in table:
        return table[key]
    low = key.lower()
    if low in table:
        return table[low]
    return table.get(_canon(low))


def parse_grid(value):
    """校验单目识别结果；ok 时 color/shape 必须在 9 类词表内。"""
    if not isinstance(value, dict):
        raise LLMError("单目识别结果必须是 JSON 对象")
    status = str(value.get("status") or "").strip().lower()
    if status not in ("ok", "empty", "uncertain"):
        raise LLMError("status 必须为 ok/empty/uncertain")
    reason = str(value.get("reason") or "").strip()[:80]
    if status != "ok":
        return {"status": status, "color": None, "shape": None, "reason": reason}
    color = _lookup(COLOR_ALIASES, value.get("color"))
    shape = _lookup(SHAPE_ALIASES, value.get("shape"))
    if color is None:
        raise LLMError(f"颜色不在允许集合 {COLORS}: {value.get('color')!r}")
    if shape is None:
        raise LLMError(f"形状不在允许集合 {SHAPES}: {value.get('shape')!r}")
    return {"status": "ok", "color": color, "shape": shape, "reason": reason}


def classify_grid(frame, total_timeout=15.0, save_path=None):
    """单帧识别：返回 {status, color, shape, reason}。"""
    return qwen_realise(
        [frame], PROMPT_GRID,
        message_save_path=save_path, validator=parse_grid,
        total_timeout=total_timeout,
    )
