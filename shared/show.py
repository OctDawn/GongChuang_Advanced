"""使用 pygame 显示全屏分拣结果表格。

固定 6 行对应分拣类别，同类成功后更新该行名称、最近图片和本类累计数量。
未使用类别保留编号，名称/图片为空，数量为 0。
"""

import os
from dataclasses import dataclass
from queue import Empty
from typing import Optional

import pygame
import pygame.sysfont

from shared.const import MAX_TEMP_CATEGORIES

try:  # 屏幕电源控制（xset）；缺失时退化为"全黑窗口"，不影响显示主流程
    from shared.screen import screen_configure, screen_off, screen_on
except Exception:  # noqa: BLE001
    def screen_configure(*_args, **_kwargs):
        return False

    def screen_off(*_args, **_kwargs):
        return False

    def screen_on(*_args, **_kwargs):
        return False


BLACK = (0, 0, 0)          # 待机（还没收到 a/c）时的纯黑底
BACKGROUND = (17, 24, 39)
HEADER_BACKGROUND = (30, 41, 59)
ROW_BACKGROUND = (248, 250, 252)
ALTERNATE_ROW_BACKGROUND = (235, 241, 247)
GRID_COLOR = (100, 116, 139)
TEXT_COLOR = (15, 23, 42)
MUTED_TEXT_COLOR = (100, 116, 139)
TITLE_COLOR = (241, 245, 249)

# 以 720p 为基准的字号，实际按屏幕高度等比放大；整体偏小，保证单元格内显示完整
BASE_TITLE_FONT = 30
BASE_HEADER_FONT = 24
BASE_BODY_FONT = 22

# 文字列宽度比例：序号 / 货物名称 / 分拣成功总数量（和 = 1.0，按「除图片列以外的宽度」分配）。
# 图片列不按比例：它的宽度由行高决定（见 _draw_table），这样图片格在任何分辨率下都接近方形。
TEXT_COLUMN_RATIOS = (0.12, 0.55, 0.33)
IMAGE_CELL_ASPECT = 1.1        # 图片格宽 = 行高 × 该系数（只要比行高略长一点）
IMAGE_CELL_MIN_RATIO = 0.06    # 兜底最小占比，防止极端分辨率下算成 0
HEADERS = ("序号", "货物名称", "图片", "分拣成功总数量")


@dataclass(frozen=True)
class RecognitionRow:
    """保存一个类别的显示快照：index 为类别编号，total 为该类成功数量。"""

    index: int
    name: str
    image: Optional[object]
    total: int


@dataclass(frozen=True)
class DisplayUpdate:
    """可选刷新回执；row=None 仅确认当前表格已重绘。"""
    row: Optional[RecognitionRow]
    done: object


# 明确的简体中文字体文件（按优先级）。关键：NotoSansCJK-Regular.ttc 的第 0 个 face 是
# JP，而 pygame.font.Font 不能指定 face 索引，用它会把部分汉字画成日文字形（例如「类」），
# 所以优先用 sc 专用字体，或本身就是简体字库的 wqy / arphic。
SC_FONT_FILES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)
# 按名字模糊匹配时的优先级（越靠前越优先）
SC_FONT_NAME_HINTS = ("notosanscjksc", "sourcehansans", "microhei", "zenhei", "wqy",
                      "wenquanyi", "simhei", "simsun", "yahei", "fandol", "uming", "cjk")
# 这些变体排到最后：日文(jp)/韩文(kr)/繁体(tc)/等宽(mono)——它们会把部分汉字画成别的字形
SC_FONT_NAME_DEMOTE = ("jp", "kr", "tc", "mono", "emoji", "symbol")


def _load_font(size):
    """优先加载**简体中文**字体；找不到则退回默认字体（中文会变方框）。

    坑：NotoSansCJK-Regular.ttc 的第 0 个 face 是 JP，而 pygame.font.Font 无法指定
    face 索引，拿它当字体会让部分汉字显示成日文字形（例如「类」）。所以先试 sc 专用
    字体文件，再按名字模糊匹配（日/韩/繁/等宽变体降权，不当首选）。
    """
    for path in SC_FONT_FILES:
        if os.path.exists(path):
            try:
                return pygame.font.Font(path, size)
            except Exception:
                continue

    try:
        available = pygame.sysfont.get_fonts()
    except Exception:
        available = []

    ranked = []
    for name in available:
        low = name.lower()
        penalty = 100 if any(tag in low for tag in SC_FONT_NAME_DEMOTE) else 0
        for index, hint in enumerate(SC_FONT_NAME_HINTS):
            if hint in low:
                ranked.append((penalty + index, name))
                break
    for _, name in sorted(ranked):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            continue
    return pygame.font.Font(None, size)


def _fit_text(font, text, max_width):
    text = str(text)
    if font.size(text)[0] <= max_width:
        return text
    suffix = "..."
    while text and font.size(text + suffix)[0] > max_width:
        text = text[:-1]
    return text + suffix


def _draw_centered_text(surface, font, text, color, rect):
    text = _fit_text(font, text, rect.width - 12)
    rendered = font.render(text, True, color)
    surface.blit(rendered, rendered.get_rect(center=rect.center))


def _drain_row_queue(row_queue, rows, acknowledgements=None):
    """取出类别快照并覆盖对应行，同类重复出现不新增行。"""
    while True:
        try:
            row = row_queue.get_nowait()
        except Empty:
            return
        if isinstance(row, DisplayUpdate):
            if acknowledgements is not None:
                acknowledgements.append(row.done)
            row = row.row
            if row is None:
                continue
        if 1 <= row.index <= len(rows):
            rows[row.index - 1] = row


def _bgr_to_surface(image, max_width, max_height):
    if image is None or getattr(image, "size", 0) == 0:
        return None
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        return None
    rgb_bytes = image[:, :, :3][:, :, ::-1].tobytes()
    image_loader = getattr(pygame.image, "frombytes", pygame.image.frombuffer)
    source = image_loader(rgb_bytes, (width, height), "RGB")
    scale = min(max_width / width, max_height / height)
    target_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return pygame.transform.smoothscale(source, target_size)


def _draw_table(screen, rows, fonts, row_count):
    width, height = screen.get_size()
    title_font, header_font, body_font = fonts
    screen.fill(BACKGROUND)

    scale = height / 720.0
    margin = int(18 * scale)
    title_h = int(34 * scale)
    table_top = title_h + int(6 * scale)
    header_h = int(44 * scale)
    body_height = height - table_top - header_h - margin
    row_h = body_height // row_count if row_count > 0 else 1

    title = title_font.render("分拣结果", True, TITLE_COLOR)
    screen.blit(title, (margin, int(6 * scale)))

    table_width = width - 2 * margin
    # 图片列宽由行高决定（≈ 行高 × IMAGE_CELL_ASPECT，接近方形）；剩余宽度按比例分给三个文字列。
    image_width = max(int(row_h * IMAGE_CELL_ASPECT), int(table_width * IMAGE_CELL_MIN_RATIO))
    text_width = table_width - image_width
    text_cols = [int(text_width * ratio) for ratio in TEXT_COLUMN_RATIOS]
    col_widths = [text_cols[0], text_cols[1], image_width, text_cols[2]]
    col_widths[-1] = table_width - sum(col_widths[:-1])   # 末列吃掉取整误差，避免漏格

    x = margin
    for heading, cw in zip(HEADERS, col_widths):
        rect = pygame.Rect(x, table_top, cw, header_h)
        pygame.draw.rect(screen, HEADER_BACKGROUND, rect)
        pygame.draw.rect(screen, GRID_COLOR, rect, 1)
        _draw_centered_text(screen, header_font, heading, TITLE_COLOR, rect)
        x += cw

    for i in range(row_count):
        y = table_top + header_h + i * row_h
        row = rows[i] if i < len(rows) else None
        background = ROW_BACKGROUND if i % 2 == 0 else ALTERNATE_ROW_BACKGROUND
        if i == row_count - 1:
            # 末行「未归类」：留痕类别已满 / 储物盒已满而被丢弃的货物
            values = ("未归类", row.name if row else "", None,
                      str(row.total) if row else "0")
        else:
            values = (str(i + 1), row.name if row else "", None,
                      str(row.total) if row else "0")
        x = margin
        for ci, (value, cw) in enumerate(zip(values, col_widths)):
            rect = pygame.Rect(x, y, cw, row_h)
            pygame.draw.rect(screen, background, rect)
            pygame.draw.rect(screen, GRID_COLOR, rect, 1)
            if ci == 2:
                image_surface = _bgr_to_surface(
                    row.image if row else None,
                    max(1, rect.width - 8), max(1, rect.height - 8),
                )
                if image_surface is not None:
                    screen.blit(image_surface, image_surface.get_rect(center=rect.center))
                # 空行图片列保持留白
            else:
                _draw_centered_text(screen, body_font, value, TEXT_COLOR, rect)
            x += cw


def _wait_for_x_display(stop_event, timeout=90.0):
    """等 DISPLAY 真能打开，再初始化 pygame。

    冷启动时本服务由 systemd 随 graphical.target 拉起，而 lightdm 的用户会话（:0）
    可能还要几秒才就绪；在那之前建窗口会直接抛错，整个显示线程当场结束——主程序
    察觉不到（stop_on_exit=False），后面整场都没有画面，等于触发「无显示本轮结束」。
    """
    import time as _time
    deadline = _time.monotonic() + timeout
    attempt = 0
    while not stop_event.is_set() and _time.monotonic() < deadline:
        attempt += 1
        try:
            pygame.display.init()
            pygame.display.quit()
            if attempt > 1:
                print(f"X 显示已就绪（第 {attempt} 次探测）")
            return True
        except Exception as error:  # noqa: BLE001 —— 还没就绪，继续等
            print(f"显示屏未就绪（第 {attempt} 次，1 秒后重试）: {error}")
            _time.sleep(1)
    return False


def run_recognition_display(row_queue, stop_event, stop_on_exit=False,
                            gate_events=(), dpms=False):
    """运行 pygame 事件与绘制循环，作为独立显示线程的入口。

    gate_events 非空时进入"上电待机"：先只铺纯黑（dpms=True 时同时用 xset 灭背光），
    等其中任意一个 Event 被置位（主程序传的是 start_requested / column_check_requested，
    即电控第一次发 a 或 c）才点亮并开始画表格。点亮后永久保持，不受主程序 clear() 影响。
    gate_events 为空（默认）= 老行为：一启动就画表格。
    """

    rows = [None] * (MAX_TEMP_CATEGORIES + 1)

    # 这块板子的 rockchip GL 驱动加载失败（日志里的 "glx: failed to create dri3 screen" /
    # "failed to load driver: rockchip"），SDL 自动挑到的 visual/renderer 会把画面渲染成
    # 整片玫红（甚至是全黑、窗口根本画不出来）。强制走 X11 + 软件渲染即可正常显示。
    # 用 setdefault（而不是赋值），外部显式设置仍然优先。
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
    os.environ.setdefault("SDL_RENDER_DRIVER", "software")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

    # 冷启动时 systemd 会先于用户会话拉起本进程：等 X 真正可用再建窗口，
    # 否则显示线程会当场结束、整场无画面（主程序不会重启它）。
    if not _wait_for_x_display(stop_event):
        print("显示屏: 等 X 会话超时（多半是 lightdm 自动登录没生效），放弃显示线程")
        return

    try:
        pygame.init()
        try:
            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        except pygame.error:
            screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        pygame.display.set_caption("分拣结果")

        scale = max(1.0, screen.get_height() / 720.0)
        fonts = (
            _load_font(int(BASE_TITLE_FONT * scale)),
            _load_font(int(BASE_HEADER_FONT * scale)),
            _load_font(int(BASE_BODY_FONT * scale)),
        )

        # 每个类别始终占一行，最后一行固定为「未归类」，不随屏幕分辨率改变。
        row_count = MAX_TEMP_CATEGORIES + 1

        clock = pygame.time.Clock()
        screen_configure()  # 关屏保 / 关自动休眠，避免比赛中途自己黑屏
        activated = not gate_events
        if not activated:
            if dpms:
                screen_off()
            screen.fill(BLACK)
            pygame.display.flip()
            print("显示屏待机中（纯黑）：等电控第一次发 a/c 后点亮")

        running = True
        while running and not stop_event.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
            if not activated:
                if any(event.is_set() for event in gate_events):
                    activated = True
                    if dpms:
                        screen_on()
                    print("首次收到 a/c，点亮显示屏")
                else:
                    screen.fill(BLACK)
                    pygame.display.flip()
                    clock.tick(20)
                    continue
            acknowledgements = []
            _drain_row_queue(row_queue, rows, acknowledgements)
            _draw_table(screen, rows, fonts, row_count)
            pygame.display.flip()
            for done in acknowledgements:
                done.set()
            clock.tick(20)
    except Exception as error:
        print(f"Recognition display stopped: {error}")
    finally:
        pygame.quit()
        if stop_on_exit:
            stop_event.set()
