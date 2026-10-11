"""显示屏电源控制：上电后先不亮屏，等电控第一次发 a/c 再点亮。

只做三件事（都走 X11 的 xset，需要 x11-xserver-utils）：

    configure()  关屏保 + 关 DPMS 自动休眠（避免比赛途中自己黑屏）
    screen_off() 手动灭背光（DPMS force off）
    screen_on()  手动点亮（DPMS force on）

拿不到 xset / 没有 DISPLAY 时静默返回 False，不抛异常、不阻塞主流程；
调用方可以据此退化成"全黑窗口"方案（这条路径不需要任何外部依赖）。
"""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger("wave.vision")


def _run_xset(*args):
    """执行 xset，成功返回 True；xset 不存在或失败返回 False。"""
    exe = shutil.which("xset")
    if exe is None:
        logger.debug("找不到 xset（apt install x11-xserver-utils），跳过屏幕电源控制")
        return False
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    try:
        subprocess.run(
            [exe, *args],
            env=env,
            timeout=5,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as error:  # noqa: BLE001 —— 屏幕控制失败不该影响分拣主流程
        logger.warning("xset %s 失败: %s", " ".join(args), error)
        return False


def screen_configure():
    """关屏保、关自动黑屏：DPMS 超时全设 0（不自动休眠），但仍允许手动 force off/on。"""
    ok = _run_xset("s", "off")
    _run_xset("s", "noblank")
    _run_xset("dpms", "0", "0", "0")
    return ok


def screen_off():
    """灭背光（HDMI 信号保持，显示器不会被判定为无信号）。"""
    return _run_xset("dpms", "force", "off")


def screen_on():
    """点亮屏幕（从 force off 唤醒通常 <1 秒）。"""
    return _run_xset("dpms", "force", "on")
