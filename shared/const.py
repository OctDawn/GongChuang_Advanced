'''
常量定义（YOLO 版 + LLM 版共用）

- 硬件（摄像头/串口）、托盘几何、串口协议、类别表为两个后端共用。
- 模型路径、形状预判等 RKNN 相关项仅 YOLO 版使用；LLM 版会忽略。
- LLM 调试相关项（SAVE_LLM_RESPONSES / LLM_RESPONSE_DIR）仅 LLM 版使用。
'''
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ================= RKNN 模型路径（仅 YOLO 版使用） =================
# 目前分三个模型，会不会太多？   偶不。。现在是五个（（
PALLET_MODEL_PATH = "models/color_large_pallet.rknn"
PALLET_SHAPE_MODEL_PATH = "models/shape_large_pallet.rknn"
DETECT_AREA_COLOR_MODEL_PATH = "models/color_detect_area.rknn"
DETECT_AREA_SHAPE_MODEL_PATH = "models/shape_detect_area.rknn"
DETECT_AREA_LABEL_MODEL_PATH = "models/label_detect_area.rknn"

# ================= 摄像头信息（两个后端共用） =================
CAMERA_PALLET_ID = "/dev/v4l/by-id/usb-Generic_USB_Camera_200901010001-video-index0"   #大托盘上方广角摄像头
# 识别区：三个固定机位（机械改造后不再旋转）
#   ① ② 水平相对、各自向下倾斜约 45° 俯视（互补看到两侧竖直面）
#   ③ 正下方仰视，看底面（薄壁开口 / 底部贴纸）
# 名字由 /etc/udev/rules.d/70-wave-cameras.rules 固定：同款相机 by-id 会撞名（假序列号），
# 所以一律用 /dev/wave/cam_* 别名，换 USB 口只改 udev 规则、不动代码。
DETECT_CAMERA_IDS = (
    os.environ.get("CAMERA_DETECT_1", "/dev/wave/cam_detect_1"),
    os.environ.get("CAMERA_DETECT_2", "/dev/wave/cam_detect_2"),
    os.environ.get("CAMERA_DETECT_3", "/dev/wave/cam_detect_3"),
)
DETECT_CAMERA_NAMES = ("识别区①斜上方", "识别区②斜上方(相对)", "识别区③正下方仰视")
CAMERA_DETECT_AREA_ID = DETECT_CAMERA_IDS[0]   # 兼容旧引用（= 识别区①）
PALLET_WIDTH = 1280
PALLET_HEIGHT = 960
PALLET_FPS = 30
DETECT_WIDTH = 1280
DETECT_HEIGHT = 720
DETECT_FPS = 30
# 为main_laser服务的摄像头信息
CAMERA_LEFT_ID = "/dev/v4l/by-id/usb-RYS_USB_Camera_200901010001-video-index0"
CAMERA_RIGHT_ID = os.environ.get("CAMERA_RIGHT_ID", "/dev/v4l/by-path/CONFIGURE_RIGHT_CAMERA-video-index0")

LASER_WIDTH = 1280
LASER_HEIGHT = 720
LASER_FPS = 30
LASER_AREA = (0.25, 0.75, 0.25, 0.75)   # 分别表示宽高的识别范围。
RGB_DIFF_THRESHOLD = 30     # 单个像素 RGB 最大通道变化超过此值，认为该像素发生变化
STABLE_THRESHOLD = 0.1   # 激光传感器稳定阈值
REQUIRED_STABLE_FRAMES = 5  # # 连续多少帧稳定后，更新 参考图片

# main_laser_llm 独立配置，不改变原推送版本。
LASER_COLUMN_COUNT = 4
# 每项：(名称, 设备路径)。增加右相机时必须使用不同的真实设备路径。
LASER_CAMERAS = (("left", CAMERA_LEFT_ID), ("right", CAMERA_RIGHT_ID))
# 顶部完整画面的上半部分，只用于识别区；须按实际机位标定。
LASER_TOP_DETECT_AREA = (0.0, 1.0, 0.0, 0.5)
LASER_QUICK_TIMEOUT = 3.0
LASER_PALLET_TIMEOUT = 20.0
LASER_PALLET_INTERVAL = 0.5
LASER_PALLET_MAX_AGE = 3.0   # 从拍摄时刻计龄，超期结果不用于 c 回复
LASER_TRIGGER_CAMERA = "left"
LASER_TRIGGER_FRAMES = 1
LASER_DROP_TIMEOUT = 10.0     # a 后无变化：先 s 停推，再回 0
LASER_SETTLE_SECONDS = 0.3    # s 发出后最少等待时间
LASER_SETTLE_TIMEOUT = 3.0    # 持续不稳定时回 0
LASER_LLM_TIMEOUT = 8.0       # 单次查栏/分类请求总上限（含重试）
LASER_DISPLAY_TIMEOUT = 3.0

# ================= 串口=================
SERIAL_PORT = "/dev/ttyS0"  # 按板上实际串口修改
BAUDRATE = 115200
CAPTURE_DIR = os.path.join(PROJECT_ROOT, "capture")

MOVE_THRESHOLD = 3
'''
想小改一下：拍照识别一次图片时间不稳定，不如换成连续5轮检测目标中心不移动。
'''
STABLE_SECONDS = 0.6  #如果在STABLE_SECONDS秒内目标中心移动不超过MOVE_THRESHOLD像素且类别都没有变化，则认为目标稳定

STABLE_TIMES = 5

CONTAIN_THRESHOLD = 0.8
MAX_TEMP_CATEGORIES = 6
MAX_ITEMS_PER_CATEGORY = 16  # 每盒最多 16 件（同一类别可放的上限；改这里同时影响 main_push_llm / main_push_yolo / main_laser_llm / agent）
STAGE_TWO_DETECTION_COUNT = 3  # 阶段2 识别区拍摄的视角数：3 个角度，相邻 120°（与电控约定一致）

# 推铲（pusher）相关配置
COLUMN_COUNT = 5  # 盘子均分的列数

# 取帧重试：单次 get_frame 超时 1 秒，连续失败该次数才判定摄像头故障。
# USB 摄像头偶发卡顿（帧线程被抢占、USB 抖动）不应该让整个流程退出。
FRAME_RETRY_TIMES = 5

# 托盘在画面中的范围（相对图像宽/高的比例，0~1），用于把画面坐标映射到托盘坐标系。
# 画面与托盘大小/位置不同时，只需修改这里的范围即可。
TRAY_X_MIN = 0.225          # 托盘最左边界
TRAY_X_MAX = 0.854          # 托盘最右边界
TRAY_Y_MIN = 0.208          # 出口端 bottom（画面顶端）
TRAY_Y_MAX = 0.753          # 推铲端 top（画面底端）

# 激光传感器在画面哪一侧；协议里「第 1 栏」= 靠近激光传感器的一栏。
# True=激光传感器在画面左侧（第 1 栏=最左一栏），False=在画面右侧（第 1 栏=最右一栏）。
LASER_SENSOR_ON_LEFT = True

# 托盘沿推送方向的真实长度（单位与电控一致，如 mm）。
# 设为 None 时推送距离按相对托盘长度的比例 [0,1] 输出；否则换算成真实长度输出。
TRAY_LENGTH_REAL = None

# 推铲额外多推的距离（相对托盘长度比例，0~1），用于保证末端物体完全掉落。
PUSH_MARGIN = 0.0

# ================= LLM 输入裁剪（仅影响发给大模型的图） =================
# 比例相对「当前图」的宽/高，四边独立可调，0 表示该边不裁。
# 只作用于送大模型的图；YOLO fallback 与 capture 下保存的原图都不受影响。
#
# 阶段1（托盘俯视）：在 crop_tray_region 裁出的托盘区域基础上再内缩。
#   当前 1280x960 画面裁出托盘区域约 805x523，四边各 0.125 后约 604x393。
#   注意：内缩过多会把最左/最右栏的边缘切掉，影响查栏；先小后大，用 test_llm.py 验证。
PALLET_LLM_INSET = {"left": 0.01, "top": 0.01, "right": 0.03, "bottom": 0.03}

# 阶段2（识别区多视角，现为 3 个角度 × 120°）：在摄像头已裁成的正方形（1280x720 -> 720x720）基础上再内缩。
#   右、上各 1/6 后约 600x600；左、下暂不裁。
DETECT_LLM_INSET = {"left": 0.13 , "top": 0.13, "right": 0.13, "bottom": 0.13}

# 需要优先判定的特权形状名称，检测到后，如果置信度高于 PRIVILEGED_SHAPE_CONFIDENCE，则直接使用该形状类别而不考虑其他形状的置信度
PRIVILEGED_SHAPES = ['cylinder_thinWalled', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5_thinWalled']
PRIVILEGED_SHAPE_CONFIDENCE = 0.8

# 阶段1粗略类别数预判：默认关闭（形状模型尚未训练好），训练好后置 True
ENABLE_CATEGORY_PRE_CHECK = False
CATEGORY_COUNT_THRESHOLD = 6  # 粗略类别数达到该值时，阶段2跳过贴纸识别，直接按颜色+形状分类

# 类别顺序必须与对应模型训练时使用的 data.yaml(models中) 一致，模型更新时记得更新。
# 大托盘颜色模型（color_large_pallet.rknn）
PALLET_COLOR_CLASSES = (
    'black', 'blue', 'brown', 'deep_blue', 'deep_green', 'green', 'orange', 'pink', 'purple', 'red', 'white', 'yellow'
)
# 识别区颜色模型（color_detect_area.rknn）
DETECT_AREA_COLOR_CLASSES = (
    'black', 'blue', 'brown', 'deep_blue', 'deep_green', 'green', 'orange', 'pink', 'purple', 'red', 'white', 'yellow'
)
SHAPE_CLASSES = (
    'ball', 'cone', 'cube', 'cuboid', 'cylinder', 'cylinder_thinWalled', 'hemisphere', 'parallelepiped', 'prism3', 'prism3_thinWalled', 'prism4_thinWalled', 'prism5', 'prism5_thinWalled', 'prism6', 'pyramid3', 'pyramid4'
)
# 贴纸模型类别（顺序与 models 里 label_detect_area 的 data.yaml 一致）：
# 图案类识别出直接标记，码/文字类才解码
LABEL_CLASSES = (
    'barcode', 'blank_paper', 'checker', 'cross', 'dots', 'qrcode', 'ring', 'star', 'stripe', 'text', 'triangle'
)

LABEL_PATTERN_TYPES = {'blank_paper', 'checker', 'cross', 'dots',
                       'ring', 'star', 'stripe', 'triangle'}   # 不需要解码的贴纸
LABEL_DECODE_TYPES = {'barcode', 'qrcode', 'text'}             # 需要解码的贴纸类型

# ================= LLM 调试（仅 LLM 版使用） =================
SAVE_LLM_RESPONSES = True     # 是否把模型原始回复保存到 LLM_RESPONSE_DIR（排查用，生产可关）
LLM_RESPONSE_DIR = os.path.join(PROJECT_ROOT, "llm_responses")
