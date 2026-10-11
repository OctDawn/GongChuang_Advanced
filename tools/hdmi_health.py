#!/usr/bin/env python3
"""HDMI 链路体检 / 自愈（针对"偶发整屏玫红"）。

用法（板上，X 会话里）：
    python3 tools/hdmi_health.py                 # 只体检
    python3 tools/hdmi_health.py --prop          # 额外打印连接器的全部 xrandr 属性（找 Colorspace / max bpc）
    python3 tools/hdmi_health.py --fix           # 当前链路异常时，反复 off/auto 重新协商
    python3 tools/hdmi_health.py --fix --force   # 不管体检结果都跑一次 off/auto
    python3 tools/hdmi_health.py --log logs/hdmi_health.log

与上一版的区别（修了两个会误导判断的 bug）：
  1) 不再用 dmesg 历史当"当前是否异常"。dmesg 里的 i2c 报错可能是很久以前的，
     而且执行 xrandr off/auto 本身就会触发一次 DDC 重读（也可能报错）。
     现在"当前是否异常"只看 status 与 edid 字节数。
  2) sysfs 名字是 HDMI-A-1，xrandr 名字是 HDMI-1，上一版拿前者去匹配 xrandr
     永远匹配不上，所以色彩空间属性一条都没打出来。现在做了名字映射。
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import time

PROP_KEYS = ("colorspace", "max bpc", "broadcast rgb", "hdcp", "scaling mode",
             "link-status", "panel", "content type", "active")


def run(command, timeout=20):
    try:
        result = subprocess.run(command, shell=True, capture_output=True,
                                text=True, timeout=timeout)
        return ((result.stdout or "") + (result.stderr or "")).strip()
    except Exception as error:  # noqa: BLE001
        return f"<执行失败: {error}>"


def xrandr_name(sysfs_name):
    """'card0-HDMI-A-1' → 'HDMI-1'（sysfs 与 xrandr 的命名不同）。"""
    return sysfs_name.replace("-HDMI-A-", "-HDMI-").replace("card0-", "").replace("card1-", "")


def hdmi_connectors():
    """返回 [(xrandr名, sysfs名, sysfs路径)]。"""
    found = []
    for path in sorted(glob.glob("/sys/class/drm/card*-HDMI-A-*")):
        sysfs_name = os.path.basename(path)
        found.append((xrandr_name(sysfs_name), sysfs_name, path))
    return found


def edid_bytes(path):
    try:
        with open(os.path.join(path, "edid"), "rb") as handle:
            return len(handle.read())
    except Exception:  # noqa: BLE001
        return -1


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def xrandr_section(output):
    """截取 xrandr --prop 里该输出（含其属性）的段落。"""
    prop = run("xrandr --prop")
    lines = prop.splitlines()
    collected, inside = [], False
    for line in lines:
        if re.match(rf"^{re.escape(output)}\b", line):
            inside = True
        elif inside and line and not line[0].isspace():
            break
        if inside:
            collected.append(line)
    return "\n".join(collected)


def current_mode(output):
    section = xrandr_section(output)
    for line in section.splitlines():
        if "*" in line or "+" in line:
            return line.strip()
    return "<未知>"


def report(show_props=False):
    print("=" * 78)
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}   DISPLAY={os.environ.get('DISPLAY', '')}")
    print("=" * 78)
    healthy = True
    any_connected = False
    for output, sysfs_name, path in hdmi_connectors():
        status = read_text(os.path.join(path, "status"))
        size = edid_bytes(path)
        if status != "connected":
            print(f"[{output}] ({sysfs_name}) status={status}  —— 未接/未识别")
            continue
        any_connected = True
        ok = size > 0
        healthy = healthy and ok
        print(f"[{output}] ({sysfs_name}) status={status}  edid={size} bytes  → {'OK' if ok else '!! EDID 读不到'}")
        print(f"    当前模式: {current_mode(output)}")
        try:
            modes = [m for m in read_text(os.path.join(path, "modes")).split() if m]
            print(f"    EDID 模式 {len(modes)} 个: {' '.join(modes[:6])}"
                  + (" ..." if len(modes) > 6 else ""))
            if len(modes) <= 6:
                print("    （模式很少 → 走的是驱动内置默认表，说明这份 EDID 是假的/没读到）")
        except Exception:  # noqa: BLE001
            pass

        section = xrandr_section(output)
        interesting = [ln for ln in section.splitlines()
                       if any(k in ln.strip().lower() for k in PROP_KEYS)]
        if interesting:
            print("    连接器属性（色彩空间相关）:")
            for line in interesting:
                print(f"      {line.strip()}")
        else:
            print("    （该连接器没有暴露 Colorspace / max bpc 类属性）")
        if show_props:
            print("    --- 完整 xrandr 属性 ---")
            for line in section.splitlines():
                print(f"      {line.rstrip()}")

    if not any_connected:
        print("！没有任何 HDMI 输出处于 connected —— 先确认线/口/屏的电和握手。")
        healthy = False

    newest = run("dmesg 2>/dev/null | grep -i -E 'edid|i2c read|dwhdmi' | tail -6")
    print("\n--- dmesg 最近 6 条 HDMI/EDID 记录（注意：含历史，且 xrandr off/auto 也会产生）---")
    print(newest if newest else "<无>")

    print("\n>>> 当前状态判定：", "链路健康（EDID 正常）" if healthy else "链路异常（EDID 读不到 / 无连接）")
    if healthy:
        print(">>> 若此时画面仍然偏色，说明问题不在 EDID 这一环：看上面的连接器属性、")
        print(">>> 以及另一台设备的输入格式设置（显示器/采集卡），并做一次整机重启对比。")
    return healthy


def fix(output, rounds, interval):
    print(f"\n--- 对 {output} 重新协商（最多 {rounds} 轮，间隔 {interval}s）---")
    for index in range(1, rounds + 1):
        messages = []
        for command in (f"xrandr --output {output} --off", f"xrandr --output {output} --auto"):
            output_text = run(command)
            if output_text:
                messages.append(f"{command} -> {output_text.strip()}")
            time.sleep(interval)
        size = -1
        for name, _sysfs, path in hdmi_connectors():
            if name == output:
                size = edid_bytes(path)
        print(f"  第 {index} 轮: edid={size} bytes  当前模式={current_mode(output)}")
        for message in messages:
            print(f"    xrandr: {message}")
        if size > 0:
            print("  已恢复。")
            return True
    print("  多轮都读不到 EDID → 指向物理链路（线/接口/转接/屏）。")
    print(f"  手动试一次看报错: xrandr --output {output} --mode 1920x1080")
    return False


def main():
    parser = argparse.ArgumentParser(description="HDMI 链路体检/自愈")
    parser.add_argument("--prop", action="store_true", help="打印连接器全部 xrandr 属性")
    parser.add_argument("--fix", action="store_true", help="尝试 xrandr off/auto 重新协商")
    parser.add_argument("--force", action="store_true", help="即使体检正常也执行 --fix")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--log", default=None, help="把一行结论追加到该文件")
    args = parser.parse_args()

    healthy = report(show_props=args.prop)
    if args.fix and (not healthy or args.force):
        for output, _sysfs, path in hdmi_connectors():
            if read_text(os.path.join(path, "status")) == "connected":
                fix(output, args.rounds, args.interval)
                break
        print()
        report(show_props=False)

    if args.log:
        try:
            os.makedirs(os.path.dirname(args.log) or ".", exist_ok=True)
            rows = []
            for output, _sysfs, path in hdmi_connectors():
                rows.append(f"{output}:{read_text(os.path.join(path, 'status'))}:"
                            f"edid={edid_bytes(path)}:mode={current_mode(output).split()[0] if current_mode(output).split() else '-'}")
            with open(args.log, "a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                             f"{'OK' if healthy else 'BAD'} {' '.join(rows)}\n")
            print(f"已追加到 {args.log}")
        except Exception as error:  # noqa: BLE001
            print(f"写日志失败: {error}")


if __name__ == "__main__":
    main()
