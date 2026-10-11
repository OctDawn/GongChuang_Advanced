#!/usr/bin/env python3
"""HDMI 看门狗：盯住"屏侧重新握手时偶发丢 EDID → 整屏玫红"。

背景：主板一直通电不重启，而显示屏会自己断电/上电/进待机后唤醒。屏每次重新握手都会
让板子重读一次 EDID；读失败时驱动退到内置默认参数，色彩空间猜错 → 整屏品红，而且一直
保持到下一次握手成功为止。本脚本轮询 /sys/class/drm 的 EDID 字节数，一旦掉到 0 就立刻
用 xrandr off/auto 触发重新协商，直到恢复，并把每次事件写进日志。

用法：
    python3 tools/hdmi_watch.py --log logs/hdmi_watch.log                 # 前台跑
    nohup python3 tools/hdmi_watch.py --log logs/hdmi_watch.log >/dev/null 2>&1 &
    python3 tools/hdmi_watch.py --once                                    # 只体检一次

做成开机自启（和主程序同一个启动脚本里，后台起一行即可）：
    nohup python3 tools/hdmi_watch.py --log logs/hdmi_watch.log --interval 2 &
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import time


def run(command, timeout=20):
    try:
        result = subprocess.run(command, shell=True, capture_output=True,
                                text=True, timeout=timeout)
        return ((result.stdout or "") + (result.stderr or "")).strip()
    except Exception:  # noqa: BLE001
        return ""


def current_mode(name):
    """从 xrandr 里取该输出当前模式。"""
    output = run("xrandr")
    for line in output.splitlines():
        if line.startswith(name) and ("*" in line or "+" in line):
            return line.strip()
    return "<未知>"


def kernel_log_tail(count=8):
    """拿内核日志尾部（无需 root 的几种办法依次试）。"""
    for command in (f"dmesg 2>/dev/null | tail -{count}",
                    f"sudo -n dmesg 2>/dev/null | tail -{count}",
                    f"journalctl -k -n {count} -o short-precise 2>/dev/null"):
        text = run(command)
        if text:
            return text
    return "<拿不到内核日志：看门狗请在有 dmesg 权限的用户下跑>"


def connector_props(name):
    """解析 xrandr --prop 里该输出的属性：{属性名: {'value':…, 'supported':[…]}}。"""
    prop = run("xrandr --prop")
    lines = prop.splitlines()
    section, inside = [], False
    for line in lines:
        if re.match(rf"^{re.escape(name)}\b", line):
            inside = True
            continue
        if inside and line and not line[0].isspace():
            break
        if inside:
            section.append(line)

    props = {}
    current = None
    for line in section:
        if not line.strip():
            continue
        expanded = line.expandtabs(8)
        indent = len(expanded) - len(expanded.lstrip())
        stripped = expanded.strip()
        if indent < 8:
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                current = key.strip()
                props[current] = {"value": value.strip(), "supported": []}
            else:
                current = None
        elif current and stripped.lower().startswith("supported:"):
            listed = stripped.split(":", 1)[1]
            props[current]["supported"] = [item.strip() for item in listed.split(",") if item.strip()]
    return props


def describe_state(name, tag, log):
    """把一次握手后的关键状态打进日志：edid / 模式 / 色域 / 内核日志尾部。"""
    status, size = "unknown", -1
    for row_name, row_status, row_size in snapshot():
        if row_name == name:
            status, size = row_status, row_size
    log(f"[{tag}] {name} 状态: {status}  edid={size} bytes  模式={current_mode(name)}")
    props = connector_props(name)
    for key in ("Colorspace", "max bpc", "HDCP Content Type", "link-status"):
        if key in props:
            log(f"[{tag}] {key} = {props[key]['value']}"
                + (f"  (可选: {', '.join(props[key]['supported'])})"
                   if props[key]["supported"] else ""))
    if not any(k in props for k in ("Colorspace", "max bpc")):
        log(f"[{tag}] 该连接器没有 Colorspace/max bpc 属性（板子侧没暴露色域开关）")
    log(f"[{tag}] 内核日志尾部:\n{kernel_log_tail(8)}")
    return size, props


def xrandr_name(sysfs_name):
    return sysfs_name.replace("-HDMI-A-", "-HDMI-").replace("card0-", "").replace("card1-", "")


def snapshot():
    """返回 [(xrandr名, status, edid字节数)]。"""
    rows = []
    # 同时覆盖 HDMI-A 与 DP：本机实测屏接在 DP-1，卡上 HDMI-A-1 是 disconnected，
    # 只扫 HDMI 会让这个看门狗全程空转（而 DP 才是最需要它守的那条）。
    paths = (glob.glob("/sys/class/drm/card*-HDMI-A-*")
             + glob.glob("/sys/class/drm/card*-DP-*"))
    for path in sorted(paths):
        name = xrandr_name(os.path.basename(path))
        try:
            with open(os.path.join(path, "edid"), "rb") as handle:
                size = len(handle.read())
        except Exception:  # noqa: BLE001
            size = -1
        try:
            with open(os.path.join(path, "status"), "r", encoding="utf-8") as handle:
                status = handle.read().strip()
        except Exception:  # noqa: BLE001
            status = "unknown"
        rows.append((name, status, size))
    return rows


def bad_rows(rows):
    return [(name, status, size) for name, status, size in rows
            if status == "connected" and size <= 0]


def active_mode(name):
    """取该输出当前生效的模式（如 1920x1080）。

    current_mode() 返回的是整行（含 +0+0 偏移），不能直接拿来和 "1920x1080" 比较。
    """
    match = re.search(r"(\d+x\d+)\+\d+\+\d+", current_mode(name))
    return match.group(1) if match else ""


def degraded_mode_rows(rows, force_mode):
    """EDID 可读、但 X 把输出锁在了别的模式上（实测过 DP-1 卡在 800x480）。

    这种状态 bad_rows() 是空的，原来没人管；而 pygame 全屏会直接采用这个降级时序，
    表格会挤成一团。只在 EDID 可读（确实接着屏）时才判，避免屏没通电时反复折腾。
    """
    if not force_mode:
        return []
    degraded = []
    for name, status, size in rows:
        if status != "connected" or size <= 0:
            continue
        mode = active_mode(name)
        if mode and mode != force_mode:
            degraded.append((name, status, size))
    return degraded


class Logger:
    def __init__(self, path):
        self.path = path
        self.handle = None
        if path:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                self.handle = open(path, "a", encoding="utf-8", buffering=1)
            except Exception as error:  # noqa: BLE001
                print(f"无法写日志 {path}: {error}")

    def __call__(self, message):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
        print(line)
        if self.handle:
            self.handle.write(line + "\n")

    def close(self):
        if self.handle:
            self.handle.close()


def recover(rows, attempts, interval, log, tag, force_mode=None):
    for (name, _status, _size) in rows:
        log(f"[{tag}] ==== {name} 握手现场（EDID 丢失） ====")
        describe_state(name, tag, log)
        for index in range(1, attempts + 1):
            log(f"[{tag}] {name} 丢失 EDID，重新协商第 {index}/{attempts} 轮")
            commands = [f"xrandr --output {name} --off", f"xrandr --output {name} --auto"]
            if force_mode:
                commands.append(f"xrandr --output {name} --mode {force_mode}")
            for command in commands:
                output_text = run(command)
                if output_text:
                    log(f"[{tag}] xrandr 回显: {output_text.strip()}")
                time.sleep(interval)
            if not bad_rows(snapshot()):
                log(f"[{tag}] {name} 已恢复（EDID 回来，色彩应正常）")
                return True
        log(f"[{tag}] {name} {attempts} 轮都没恢复 —— 查线/接口/屏本身")
    return False


def resync(name, interval, log, tag="重同步", force_rgb=False, force_mode=None):
    """主动重编一次输出：屏重新握手后，强制板子重新配置一遍 CRTC/色域/模式。

    这一步同时覆盖三种情况：(a) 重读 EDID 失败；(b) 模式被降级到屏锁不住的时序；
    (c) EDID 正常但屏锁在错误格式上。
    """
    log(f"[{tag}] ==== {name} 握手现场（重同步之前） ====")
    describe_state(name, tag, log)

    messages = []
    for command in (f"xrandr --output {name} --off", f"xrandr --output {name} --auto"):
        output_text = run(command)
        if output_text:
            messages.append(output_text.strip())
        time.sleep(interval)
    for message in messages:
        log(f"[{tag}] xrandr 回显: {message}")

    if force_mode:
        echo = run(f"xrandr --output {name} --mode {force_mode}")
        log(f"[{tag}] 已把模式钉为 {force_mode}"
            + (f"（回显: {echo.strip()}）" if echo else ""))

    if force_rgb:
        props = connector_props(name)
        colorspace = props.get("Colorspace")
        if not colorspace:
            log(f"[{tag}] 该连接器无 Colorspace 属性，跳过强制 RGB")
        else:
            for candidate in ("BT2020_RGB", "opRGB", "RGB", "Default"):
                if candidate in colorspace["supported"]:
                    echo = run(f'xrandr --output {name} --set Colorspace "{candidate}"')
                    log(f"[{tag}] 已把 Colorspace 设为 {candidate}"
                        + (f"（回显: {echo.strip()}）" if echo else ""))
                    break
            else:
                log(f"[{tag}] 可选列表里没有 RGB 项，保持不变")
        if "max bpc" in props:
            echo = run(f'xrandr --output {name} --set "max bpc" 8')
            log(f"[{tag}] 已把 max bpc 设为 8"
                + (f"（回显: {echo.strip()}）" if echo else ""))

    log(f"[{tag}] ==== {name} 重同步之后 ====")
    describe_state(name, tag, log)


def main():
    parser = argparse.ArgumentParser(description="HDMI/DP EDID 看门狗")
    parser.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒，默认 2")
    parser.add_argument("--attempts", type=int, default=5, help="每次丢失的重协商轮数")
    parser.add_argument("--cooldown", type=float, default=30.0,
                        help="重协商失败后的冷却秒数，避免死循环，默认 30")
    parser.add_argument("--log", default=None, help="日志文件（如 logs/hdmi_watch.log）")
    parser.add_argument("--once", action="store_true", help="只体检一次就退出")
    parser.add_argument("--no-resync-on-connect", action="store_true",
                        help="显示器每次重新握手后不主动重同步（默认会重同步）")
    parser.add_argument("--force-rgb", action="store_true",
                        help="重同步时顺手把 Colorspace 设成 RGB 项、max bpc 设成 8（仅在连接器暴露这些属性时）")
    parser.add_argument("--force-mode", default=None,
                        help="重同步时把模式钉死，例如 --force-mode 1920x1080（推荐：避免 X 在握手后降级到 800x480 这类时序）")
    args = parser.parse_args()

    os.environ.setdefault("DISPLAY", ":0")
    log = Logger(args.log)

    rows = snapshot()
    if rows:
        log("看门狗启动: " + " | ".join(f"{n}:{s}:edid={z}" for n, s, z in rows))
    else:
        log("看门狗启动: 未发现 HDMI/DP 输出")

    if args.once:
        broken = bad_rows(rows)
        log("体检结论: " + ("正常" if not broken else f"异常 {broken}"))
        for name, status, _size in rows:
            if status == "connected":
                describe_state(name, "体检", log)
        return

    last_state = tuple((n, s, z > 0) for n, s, z in rows)
    # 启动体检：EDID 正常、模式却被降级的情况（bad_rows 抓不到）也要修一次，
    # 否则 pygame 全屏会直接采用 800x480 这种降级时序。
    for name, _status, _size in degraded_mode_rows(rows, args.force_mode):
        log(f"[模式] {name} 启动体检: 当前 {active_mode(name)}，钉回 {args.force_mode}")
        resync(name, args.interval, log, tag="模式修正",
               force_rgb=args.force_rgb, force_mode=args.force_mode)
    rows = snapshot()
    last_status = {n: s for n, s, _z in rows}
    while True:
        try:
            time.sleep(args.interval)
            rows = snapshot()
            state = tuple((n, s, z > 0) for n, s, z in rows)
            if state != last_state:
                log("状态变化: " + " | ".join(f"{n}:{s}:edid={z}" for n, s, z in rows))
                last_state = state

            # 屏每次重新握手（断开→连上）都主动重同步一次输出
            status_now = {n: s for n, s, _z in rows}
            if not args.no_resync_on_connect:
                for name, status in status_now.items():
                    previous = last_status.get(name)
                    if previous is not None and previous != "connected" and status == "connected":
                        log(f"[重连] {name} 显示器重新握手（{previous} → connected），主动重同步")
                        resync(name, args.interval, log, force_rgb=args.force_rgb,
                               force_mode=args.force_mode)
                rows = snapshot()
                state = tuple((n, s, z > 0) for n, s, z in rows)
                last_state = state
            last_status = status_now

            broken = bad_rows(rows)
            if broken:
                ok = recover(broken, args.attempts, args.interval, log, "自愈",
                             force_mode=args.force_mode)
                last_state = tuple((n, s, z > 0) for n, s, z in snapshot())
                if not ok:
                    log(f"冷却 {args.cooldown}s 后再试")
                    time.sleep(args.cooldown)

            # 第三种情况：EDID 正常、模式被降级
            degraded = degraded_mode_rows(rows, args.force_mode)
            if degraded:
                for name, _status, _size in degraded:
                    log(f"[模式] {name} 当前 {active_mode(name)}，钉回 {args.force_mode}")
                    resync(name, args.interval, log, tag="模式修正",
                           force_rgb=args.force_rgb, force_mode=args.force_mode)
                last_state = tuple((n, s, z > 0) for n, s, z in snapshot())
        except KeyboardInterrupt:
            log("收到中断，退出")
            break
        except Exception as error:  # noqa: BLE001 —— 看门狗自身不能把别的线程带崩
            log(f"看门狗异常: {error}")
            time.sleep(args.interval)
    log.close()


if __name__ == "__main__":
    main()
