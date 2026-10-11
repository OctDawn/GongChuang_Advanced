#!/usr/bin/env python3
"""显示屏排障脚本：判定"整屏玫红"发生在哪一层。

只用 pygame，不依赖 numpy / cv2 / PIL。

板上用法（在 X 桌面会话里、项目根目录下执行）：
    python3 tools/probe_display.py                  # 先窗口 960x540，再全屏，逐条测试
    python3 tools/probe_display.py --fullscreen     # 只跑全屏
    python3 tools/probe_display.py --no-env         # 不注入 SDL_* 变量（对照实验）
    python3 tools/probe_display.py --info-only      # 只打印环境/驱动信息，不开窗

绕开 X 直接走 KMS/DRM（需先停桌面，见 --help 末尾）：
    sudo systemctl stop lightdm            # 或 stop 对应 display manager
    sudo systemctl isolate multi-user.target
    SDL_VIDEODRIVER=kmsdrm python3 tools/probe_display.py --fullscreen --no-env

输出分两段：
  1) 环境/驱动信息（可以直接整段贴回来）
  2) 9 个测试画面，每步 --secs 秒自动切换，ESC 退出
     —— 请记录"第几步看到什么颜色"（例如"第 5 步是玫红"）。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

# ---------------------------------------------------------------- 测试画面设计

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
APP_BACKGROUND = (17, 24, 39)      # shared/show.py 的 BACKGROUND（深藏青）
APP_ROW = (248, 250, 252)          # shared/show.py 的 ROW_BACKGROUND
MAGENTA = (255, 0, 255)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _bgr_test_image(width=240, height=90):
    """构造一张"BGR 顺序"的假相机图，再按 shared/show.py 的方式转成 pygame 表面。

    左边的色块在 BGR 里是 (B=255,G=0,R=0)，即蓝色；中间绿；右边红。
    期望：屏幕上看到 蓝 | 绿 | 红。若看到 红 | 绿 | 蓝，说明通道被多转/少转了一次。
    """
    bgr = bytearray()
    third = width // 3
    for _ in range(height):
        for x in range(width):
            if x < third:
                bgr += bytes((255, 0, 0))          # B
            elif x < 2 * third:
                bgr += bytes((0, 255, 0))          # G
            else:
                bgr += bytes((0, 0, 255))          # R
    rgb = bytearray(len(bgr))
    rgb[0::3] = bgr[2::3]
    rgb[1::3] = bgr[1::3]
    rgb[2::3] = bgr[0::3]
    loader = getattr(__import__("pygame").image, "frombytes", None)
    import pygame
    loader = loader or pygame.image.frombuffer
    return loader(bytes(rgb), (width, height), "RGB")


def _build_steps(pygame):
    """返回 [(标题, 期望看到, 绘制函数)] —— 绘制函数只画，不 flip。"""

    def solid(color):
        def draw(screen):
            screen.fill(color)
        return draw

    def bars(screen):
        w, h = screen.get_size()
        colors = [RED, GREEN, BLUE, WHITE, MAGENTA, APP_BACKGROUND]
        step = max(1, w // len(colors))
        for i, color in enumerate(colors):
            screen.fill(color, (i * step, 0, step, h))

    def checker(screen):
        w, h = screen.get_size()
        screen.fill(BLACK)
        size = 8
        for y in range(0, h, size):
            for x in range(0, w, size):
                if ((x // size) + (y // size)) % 2 == 0:
                    screen.fill(WHITE, (x, y, size, size))

    def app_look(screen):
        w, h = screen.get_size()
        screen.fill(APP_BACKGROUND)
        screen.fill(APP_ROW, (40, h // 3, w - 80, h // 4))
        screen.fill((30, 41, 59), (40, h // 6, w - 80, 70))
        screen.fill((100, 116, 139), (40, h // 6, w - 80, 70), 2)

    return [
        ("纯红", "整屏纯红", solid(RED)),
        ("纯绿", "整屏纯绿", solid(GREEN)),
        ("纯蓝", "整屏纯蓝", solid(BLUE)),
        ("纯白", "整屏纯白", solid(WHITE)),
        ("纯黑", "整屏纯黑（这是 SCREEN_OFF_UNTIL_START=1 待机时的画面）", solid(BLACK)),
        ("应用深色底", "深藏青 RGB(17,24,39)；若偏棕红 → 通道互换", solid(APP_BACKGROUND)),
        ("色条+棋盘", "红|绿|蓝|白|玫红|深藏青 竖条，且棋盘格清晰", bars),
        ("表格配色", "深底 + 浅灰行 + 灰边框（仿真实表格）", app_look),
        ("中文与图片", "三行简体中文（「类」不是日文字形）+ 蓝|绿|红 三色块", None),
    ]


# ---------------------------------------------------------------- 信息采集

INFO_COMMANDS = [
    ("系统", "uname -a; cat /proc/device-tree/model 2>/dev/null; echo; grep PRETTY /etc/os-release"),
    ("会话/环境变量", "echo DISPLAY=$DISPLAY XDG_SESSION_TYPE=$XDG_SESSION_TYPE "
                  "XDG_CURRENT_DESKTOP=$XDG_CURRENT_DESKTOP WAYLAND_DISPLAY=$WAYLAND_DISPLAY; "
                  "env | grep -i -E '^(SDL_|LIBGL|MESA_|DISPLAY|XDG_)' | sort"),
    ("桌面/合成器进程", "ps -eo comm | grep -E 'xfwm4|compton|picom|mutter|weston|wayfire|labwc|kwin' | sort -u"),
    ("xfwm4 合成开关", "xfconf-query -c xfwm4 -p /general/use_compositing 2>&1"),
    ("xrandr", "xrandr --current 2>&1 | head -25"),
    ("xrandr 连接器属性", "xrandr --prop 2>&1 | head -90"),
    ("HDMI 链路/EDID", "for f in /sys/class/drm/card*-HDMI-A-*/; do echo \"== $f\"; "
                     "cat $f/status 2>/dev/null; printf 'edid bytes: '; wc -c < $f/edid 2>/dev/null; "
                     "echo '-- modes --'; head -6 $f/modes 2>/dev/null; done; "
                     "echo '--- hdmi/color dmesg ---'; "
                     "dmesg 2>/dev/null | grep -i -E 'hdmi|edid|colou?r|bpc|yuv|rgb' | tail -20"),
    ("drm 当前模式/格式", "cat /sys/kernel/debug/dri/0/summary 2>&1 | head -30"),
    ("内核 cmdline", "cat /proc/cmdline"),
    ("EDID 固件", "ls /lib/firmware/edid/ 2>&1 | head; ls /usr/lib/firmware/edid/ 2>&1 | head"),
    ("xdpyinfo", "xdpyinfo 2>&1 | grep -E 'dimensions|depth of root|depth of default|class:|visual:' | head -20"),
    ("glxinfo(去掉软件GL污染)", "env -u LIBGL_ALWAYS_SOFTWARE -u SDL_RENDER_DRIVER glxinfo -B 2>&1 | head -25"),
    ("fb/drm 设备", "cat /proc/fb; ls -l /dev/fb* /dev/dri/* 2>&1 | head -20"),
    ("GL/EGL/mali 库", "ls /usr/lib/aarch64-linux-gnu/ 2>/dev/null | grep -i -E 'mali|libGL|libEGL|libgbm' | head -20; "
                   "ls /usr/lib/aarch64-linux-gnu/libmali* 2>&1 | head"),
    ("内核 drm/panel 日志", "dmesg 2>/dev/null | grep -i -E 'drm|panel|mali|hdmi|vop|dsi' | tail -25"),
    ("显示管理器", "systemctl list-units --type=service --state=running 2>/dev/null | "
              "grep -i -E 'lightdm|gdm|sddm|display-manager' | head"),
]


def collect_info():
    print("=" * 78)
    print("【第 1 段】环境 / 驱动信息（整段贴回来最有用）")
    print("=" * 78)
    for title, command in INFO_COMMANDS:
        print(f"\n--- {title} ---")
        try:
            result = subprocess.run(command, shell=True, capture_output=True,
                                    text=True, timeout=15)
            out = ((result.stdout or "") + (result.stderr or "")).strip()
        except Exception as error:  # noqa: BLE001
            out = f"<执行失败: {error}>"
        print(out if out else "<无输出>")

    try:
        import pygame
    except Exception as error:  # noqa: BLE001
        print(f"\n!! 无法 import pygame: {error}")
        return
    print("\n--- pygame / SDL ---")
    print(f"pygame {pygame.version.ver}  |  SDL {'.'.join(str(x) for x in pygame.get_sdl_version())}")
    print(f"编译时视频后端: {pygame.display.get_driver.__doc__.strip().splitlines()[0] if pygame.display.get_driver.__doc__ else '-'}")

    # 字体候选：哪一个会被 shared/show.py 选中
    print("\n--- 中文字体候选 ---")
    try:
        from shared.show import SC_FONT_FILES
        for path in SC_FONT_FILES:
            print(f"  {'OK  ' if os.path.exists(path) else 'MISS'}  {path}")
    except Exception as error:  # noqa: BLE001
        print(f"  <导入 SC_FONT_FILES 失败: {error}>")

    print("\n提示：若 DISPLAY 为空，先在会话里 export DISPLAY=:0 再跑本脚本。")


# ---------------------------------------------------------------- 主流程

def main():
    parser = argparse.ArgumentParser(
        description="显示屏玫红排障",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="看到玫红的那一步，请连同第 1 段信息一起反馈。")
    parser.add_argument("--fullscreen", action="store_true", help="只跑全屏模式")
    parser.add_argument("--windowed", action="store_true", help="只跑窗口模式")
    parser.add_argument("--no-env", action="store_true",
                        help="不注入 SDL_VIDEODRIVER/SDL_RENDER_DRIVER/LIBGL_ALWAYS_SOFTWARE")
    parser.add_argument("--info-only", action="store_true", help="只打印信息，不开窗口")
    parser.add_argument("--secs", type=float, default=3.0, help="每步显示秒数（默认 3）")
    parser.add_argument("--size", default="960x540", help="窗口模式尺寸，默认 960x540")
    args = parser.parse_args()

    if not args.no_env:
        # 与 shared/show.py 保持一致；--no-env 时不设，用来对照
        os.environ.setdefault("SDL_VIDEODRIVER", "x11")
        os.environ.setdefault("SDL_RENDER_DRIVER", "software")
        os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

    collect_info()
    if args.info_only:
        return

    try:
        import pygame
    except Exception as error:  # noqa: BLE001
        print(f"无法 import pygame: {error}")
        return

    if args.fullscreen:
        modes = [("fullscreen", None)]
    elif args.windowed:
        width, height = (int(v) for v in args.size.lower().split("x"))
        modes = [("windowed", (width, height))]
    else:
        width, height = (int(v) for v in args.size.lower().split("x"))
        modes = [("windowed", (width, height)), ("fullscreen", None)]

    for mode_name, size in modes:
        print("\n" + "=" * 78)
        print(f"【第 2 段】显示测试：{mode_name}"
              f"{'（全屏 (0,0)+FULLSCREEN）' if size is None else f'（{size[0]}x{size[1]} 窗口）'}")
        print("=" * 78)
        try:
            pygame.init()
            if size is None:
                screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            else:
                screen = pygame.display.set_mode(size)
        except Exception as error:  # noqa: BLE001
            print(f"!! set_mode 失败（{mode_name}）: {error}")
            pygame.quit()
            continue

        print(f"driver={pygame.display.get_driver()}  "
              f"size={screen.get_size()}  bitsize={screen.get_bitsize()}  "
              f"masks(R,G,B,A)={screen.get_masks()}")
        print("（bitsize 应为 32，masks 常见为 (16711680, 65280, 255, 0)；"
              "若 bitsize=16 或 masks 异常，问题就在像素格式层）")

        font = None
        try:
            from shared.show import _load_font
            font = _load_font(max(28, screen.get_height() // 18))
        except Exception:
            pass
        if font is None:
            try:
                font = pygame.font.SysFont(None, max(28, screen.get_height() // 18))
            except Exception:
                font = None

        steps = _build_steps(pygame)
        clock = pygame.time.Clock()
        running = True
        for index, (title, expect, draw) in enumerate(steps, start=1):
            if not running:
                break
            print(f"  第 {index} 步 / {len(steps)}：{title}　期望：{expect}")
            screen.fill(BLACK)
            if title == "中文与图片":
                try:
                    from shared.show import _load_font
                    size_px = max(28, screen.get_height() // 18)
                    step_font = _load_font(size_px)
                    lines = ["类别编号 货物名称 图片 本类成功数量",
                             "分拣结果 · 类 · 未归类",
                             "正方体 薄壁四棱柱 六棱柱 三棱锥 圆锥 球"]
                    line_h = max(36, screen.get_height() // 12)
                    for i, text in enumerate(lines):
                        surface = step_font.render(text, True, (255, 255, 255))
                        screen.blit(surface, (30, 30 + i * line_h))
                    pic = _bgr_test_image()
                    pic = pygame.transform.smoothscale(pic, (pic.get_width() * 2, pic.get_height() * 2))
                    screen.blit(pic, pic.get_rect(center=(screen.get_width() // 2,
                                                          screen.get_height() - 120)))
                except Exception as error:  # noqa: BLE001
                    print(f"    中文/图片步骤异常: {error}")
            elif draw is not None:
                draw(screen)
            pygame.display.flip()

            elapsed = 0.0
            while elapsed < args.secs:
                if not running:
                    break
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        running = False
                pygame.time.delay(50)
                elapsed += 0.05

        pygame.quit()

    print("\n" + "=" * 78)
    print("【怎么读结果】")
    print("=" * 78)
    print("  A. 连纯黑/纯白都偏色（例如纯黑发紫、纯白偏品红）→ 不是颜色转换问题，")
    print("     是显示平面/驱动层在送垃圾数据：优先做 T2（关合成器）→ T4（kmsdrm 绕开 X）。")
    print("  B. 只有全屏是玫红、窗口正常 → 全屏窗口被提升到硬件 overlay 平面：")
    print("     改用无边框窗口铺满屏幕（tools/probe_display.py --windowed --size <屏宽>x<屏高> "
          "配合窗口位置 0,0）。")
    print("  C. 红/蓝对调（纯红显示成蓝色）→ 通道顺序错误，改 _bgr_to_surface 的转换即可。")
    print("  D. 窗口/全屏都正常 → 说明是启动方式问题（比如经 systemd 启动时没有 X/GL 环境），")
    print("     在真实启动路径下重跑本脚本。")
    print("  E. 第 9 步中文出现日文字形 → 字体选择仍未命中简体字库。")
    print("  F. 【重点】若桌面/开机 logo 也偏色，且 'edid bytes: 0'、xrandr 显示 '0mm x 0mm'、"
          "dmesg 有 'failed to get edid' / 'i2c read err' → 与 pygame 无关，是 HDMI 链路读不到 EDID；"
          "先在 xrandr --prop 里看 Colorspace / max bpc 当前值。")


if __name__ == "__main__":
    main()
