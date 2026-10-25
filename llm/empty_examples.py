"""加载现场拍摄的空场景原图与带标签形状示例，并应用与实时画面一致的裁剪。

命名规范（全部 ASCII，避免 Windows 下 cv2.imread 读不了中文路径）：

    <color>_<shape>[_<pose>]_<view>.jpg        view = top(①②斜上) / bottom(③正下)

- 一张图只放**一件**目标，图上不要有其它候选物体。
- 识别区是"同一物体、一上一下两个视角"，所以薄壁与实心都成对给：
  顶部图用来学形状与开口，底部图用来学"封闭端面长什么样"。
- 同一件薄壁物体**通常只有一个视角能看到开口**（开口朝上→①②看得到，开口朝下→③看得到），
  另一个视角看到的是封闭端面；那一圈平行于外形的窄边是**壁厚**，不是空腔。
"""
from pathlib import Path

import cv2

from shared.common import crop_to_square, crop_pallet_llm
from llm.qw import LLMError

EMPTY_EXAMPLE_DIR = Path(__file__).resolve().parent / "example"

# 空场景参考图：识别区每路机位看到的背景不同，要分别给。
#   ①② 斜上方 → 顶部场景；③ 正下方 → 底部场景（背光、支架、亮边都不同）
#   ④ 水平侧视 → 侧向场景（可选：文件不存在就跳过，图号会自动对齐）
# 少了必需的那张，模型就会把那一路的固定结构当成新物体或贴纸。
# 每项：(文件名, 是否必需)
EMPTY_SCENES = {
    "pallet": (("pallet_empty.jpg", True),),
    "detect_area": (("empty_top.jpg", True),
                    ("empty_bottom.jpg", True),
                    ("empty_side.jpg", False)),
}

_TOP = "斜上①②"
_BOTTOM = "正下③"

# 文件名与模型词表显式对应；示例中外围结构不属于标注的目标。
SHAPE_EXAMPLES = (
    {"filename": "black_prism4_thinwalled_top.jpg", "color": "black",
     "shape": "prism4_thinWalled", "view": _TOP,
     "note": "黑色薄壁四棱柱，开口朝上。口沿围成一圈与外形平行的边框，框内是明显下沉的内壁和内底面"
             "（不是与口沿齐平的平面）。外围粉色/白色打印件与线缆不属于目标。"},
    {"filename": "black_prism4_thinwalled_bottom.jpg", "color": "black",
     "shape": "prism4_thinWalled", "view": _BOTTOM,
     "note": "与上一张是同一件的另一侧。这一侧是封闭端面：中间一整片与边缘基本齐平的平板"
             "（第一层打印面，有细点阵纹理），边缘只有一圈很窄的平行线 = 壁厚。"
             "看不到内壁或内底面，所以这一侧不能当开口证据。"},

    {"filename": "black_prism5_thinwalled_top.jpg", "color": "black",
     "shape": "prism5_thinWalled", "view": _TOP,
     "note": "黑色薄壁五棱柱，开口朝上。五边形口沿内可见下沉的内壁与内底面，口沿与外形平行、宽度均匀。"
             "五边形容易被数成四边，按可见边逐条确认。"},
    {"filename": "black_prism5_thinwalled_bottom.jpg", "color": "black",
     "shape": "prism5_thinWalled", "view": _BOTTOM,
     "note": "同一件的另一侧。封闭端面：五边形平板与边缘齐平，边缘一圈窄线是壁厚不是内腔；"
             "左下边能看到打印纹理。"},

    {"filename": "yellow_prism4_thinwalled_top.jpg", "color": "yellow",
     "shape": "prism4_thinWalled", "view": _TOP,
     "note": "黄色薄壁四棱柱，开口朝上。彩色件的关键：开口内部是比外壁更亮、颜色更纯的同色面"
             "（内壁反光叠加），**不是**暗色空腔；仍以口沿轮廓和内壁下沉为准。"},
    {"filename": "yellow_prism4_thinwalled_bottom.jpg", "color": "yellow",
     "shape": "prism4_thinWalled", "view": _BOTTOM,
     "note": "同一件的另一侧。封闭端面：边缘一圈亮环是壁厚叠加逆光高光，中间是与环基本齐平的"
             "黄色平板，不是内腔。"},

    {"filename": "black_cube_top.jpg", "color": "black", "shape": "cube", "view": _TOP,
     "note": "黑色实心正方体。顶面是一整片完整平面；每条可见边只有一条线，没有向内偏移的平行边界；"
             "侧面的明暗渐变是光照，不是内壁。"},
    {"filename": "black_cube_bottom.jpg", "color": "black", "shape": "cube", "view": _BOTTOM,
     "note": "同一件的仰视。一整片连续外表面；右侧较亮的竖直边是侧棱被逆光照亮的高光，**不是口沿**；"
             "内部没有第二个面。"},

    {"filename": "black_prism6_top.jpg", "color": "black", "shape": "prism6", "view": _TOP,
     "note": "黑色实心六棱柱（竖放）。正六边形顶面，单线轮廓，无内缩边界；侧面为暗面。"},
    {"filename": "black_prism6_lying_top.jpg", "color": "black", "shape": "prism6", "view": _TOP,
     "note": "同一件横放。可见长条矩形侧面 + 六边形端面；面与面之间只有发散的棱线、都终止在外轮廓上，"
             "没有内腔。说明姿态变化不改变形状身份。"},
    {"filename": "black_prism6_lying_bottom.jpg", "color": "black", "shape": "prism6", "view": _BOTTOM,
     "note": "横放状态的仰视。六边形端面与长侧面的交界清楚，没有内腔；交界处那条亮线是棱的高光。"},

    # 横放对照：两者轮廓都是三角形，只能靠“有没有汇聚点”分开（2026-10-25 实测容易混）
    {"filename": "orange_prism3_top.jpg", "color": "orange", "shape": "prism3", "view": _TOP,
     "note": "横放的橙色三棱柱。看到的三角形只是**端面**：两个三角形端面彼此平行且全等，中间由矩形侧面连接；"
             "**没有任何三条以上的棱汇聚到同一个点**，所有棱只分三个方向、同向的互相平行。"},
    {"filename": "orange_pyramid4_lying_top.jpg", "color": "orange", "shape": "pyramid4",
     "view": _TOP,
     "note": "横放的橙色四棱锥。看到的三角形只是一个**侧面**：4 个三角形侧面**全部汇聚到同一个顶点**"
             "（多条棱在同一处收成一点），底面是四边形。与三棱柱的区别就在有没有这个汇聚点。"},
)


def load_shape_examples(directory=EMPTY_EXAMPLE_DIR):
    """加载已裁好主体的带标签示例，保留完整图片，不再按摄像头范围裁剪。"""
    examples = []
    for spec in SHAPE_EXAMPLES:
        path = Path(directory) / spec["filename"]
        if not path.is_file():
            raise LLMError(f"缺少形状示例照片: {path}")
        image = cv2.imread(str(path))
        if image is None or image.size == 0:
            raise LLMError(f"形状示例照片无法读取: {path}")
        examples.append(dict(spec, image=image))
    return examples


def load_empty_examples(directory=EMPTY_EXAMPLE_DIR):
    """加载空场景参考图。返回 {"pallet": 单张, "detect_area": [顶部, 底部]}。

    裁剪方式与实时画面保持一致：
    - 托盘：crop_pallet_llm（托盘区域 + 内缩）；
    - 识别区：crop_to_square（720x720），额外的 DETECT_LLM_INSET 在发送时统一施加。
    """
    images = {}
    for scene, entries in EMPTY_SCENES.items():
        loaded = []
        for filename, required in entries:
            path = Path(directory) / filename
            if not path.is_file():
                if not required:
                    continue          # 可选（如 empty_side）：没有就少一张，编号自动对齐
                raise LLMError(f"缺少空场景参考照片: {path}；请放入对应摄像头拍摄的未裁剪空场景原图")
            image = cv2.imread(str(path))
            if image is None or image.size == 0:
                raise LLMError(f"空场景参考照片无法读取: {path}")
            cropped = crop_pallet_llm(image) if scene == "pallet" else crop_to_square(image)
            if cropped.size == 0:
                raise LLMError(f"空场景参考照片裁剪后为空: {path}")
            loaded.append(cropped)
        if not loaded:
            raise LLMError(f"场景 {scene} 没有可用的空场景参考照片")
        # 托盘只有一路相机，保持单张（调用方按单张用）；识别区返回列表。
        images[scene] = loaded[0] if scene == "pallet" else loaded
    return images
