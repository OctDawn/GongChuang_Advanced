"""加载现场拍摄的空场景原图，并应用与实时画面一致的裁剪。"""

from pathlib import Path

import cv2

from shared.common import crop_to_square, crop_pallet_llm
from llm.qw import LLMError

EMPTY_EXAMPLE_DIR = Path(__file__).resolve().parent / "example"

# 文件名与模型词表显式对应；示例中外围结构不属于标注的目标。
SHAPE_EXAMPLES = (
    {"filename": "black_prism5_thinwalled.jpg", "color": "black",
     "shape": "prism5_thinWalled",
     "note": "中央黑色主体为薄壁五棱柱（开口朝上）：朝上的那个面不是完整平面，外轮廓向内回折出一条与外形平行的内边界，其内侧就是内壁/内底面。判据是这条内边界，而不是“中部发暗”（发暗只是该视角下的结果）。外围绿色物体及支架不属于标注目标。"},
    {"filename": "black_prism6.jpg", "color": "black", "shape": "prism6",
     "note": "中央黑色主体为实心六棱柱（横躺）。【反例要点】它的两个可见面亮度差异明显、中间有一条很亮的棱线，但那是从顶点向两侧发散的棱线、两端都终止在外轮廓上，物体内部没有第二层轮廓——实心件也会有强明暗分界，明暗线本身不是空腔证据。"},
    {"filename": "black_prism4_thinwalled.jpg", "color": "black",
     "shape": "prism4_thinWalled",
     "note": "中央黑色主体为薄壁四棱柱（开口朝上）：朝上的那个面不是完整平面，外轮廓向内回折出一条与外形平行的内边界，其内侧就是内壁/内底面。判据是这条内边界，而不是“中部发暗”。外围金属支架、绿色/红色物体与深色背景不属于标注目标。"},
    {"filename": "black_cube.jpg", "color": "black", "shape": "cube",
     "note": "中央黑色主体为**实心正方体**（俯视看是一个完整的方形顶面 + 下方一条很窄的侧面）。轮廓上每条边都只有一条线，没有向内偏移的第二条边界，也看不到内壁或内底面——与黑色薄壁件（能看到口沿框或内腔）形成对照。外围金属支架、绿色/红色物体与深色背景不属于标注目标。"},
    {"filename": "black_pyramid3.jpg", "color": "black", "shape": "pyramid3",
     "note": "中央黑色主体为实心三棱锥（无任何开口、无内壁）。"},
    {"filename": "yellow_prism4_thinwalled.jpg", "color": "yellow",
     "shape": "prism4_thinWalled",
     "note": "中央黄色主体为薄壁四棱柱，开口在侧面；开口内部是与外壳同色的另一面（不是暗色），靠口沿轮廓和内壁转折判断。"},
    {"filename": "white_hemisphere_star.jpg", "color": "white",
     "shape": "hemisphere",
     "note": "中央白色主体为半球，且是**底面朝上（倒置）**的摆法：朝上的那个面是球体的平整底面（圆形平面），灰色五角星图案贴纸就贴在这个平面上；贴纸是这件物体的信息，不属于形状。本例的关键在于它极易与圆柱混淆——完全俯视时两者几乎相同，必须靠侧视角分辨：倒置半球从顶面边缘往下是向内收拢的球面，侧轮廓越往下越窄、不是等宽；圆柱的侧壁上下等宽，侧轮廓是两条近乎平行的直边。外围金属支架、绿色物体与深色背景不属于标注目标。"},
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
    images = {}
    for scene in ("pallet", "detect_area"):
        path = Path(directory) / f"{scene}_empty.jpg"
        if not path.is_file():
            raise LLMError(f"缺少空场景参考照片: {path}；请放入对应摄像头拍摄的未裁剪空场景原图")
        image = cv2.imread(str(path))
        if image is None:
            raise LLMError(f"空场景参考照片无法读取: {path}")
        # 阶段1 参考图与当前托盘用同一套裁剪；阶段2 参考图只做正方裁剪，
        # 额外的 DETECT_LLM_INSET 在发送时统一施加，保证两者取景一致。
        images[scene] = crop_pallet_llm(image) if scene == "pallet" else crop_to_square(image)
        if images[scene].size == 0:
            raise LLMError(f"空场景参考照片裁剪后为空: {path}")
    return images
