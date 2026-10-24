"""摄像头实时预览（带托盘框 / 分栏线 / 识别区裁剪框叠加），用于调整摄像头安装位置。

预览用的是与主程序完全相同的设备路径、分辨率、帧率（直接复用 initial_cap 和
shared/const.py），叠加框也直接读 const 里的几何参数，所以"你看到的"就是
"程序看到的"。

用法（板子上跑，项目根目录）：

    # 【推荐：远程调机】网页预览，不依赖 X11 转发，PC 浏览器直接看
    python tools/preview_camera.py --http 8080
    # 然后在 PC 浏览器打开  http://<板子IP>:8080/

    # 托盘上方广角摄像头（默认），窗口显示在板子自己的屏上
    DISPLAY=:0 python tools/preview_camera.py

    # 想在 MobaXterm 的 X11 窗口里看（不改 DISPLAY）
    python tools/preview_camera.py --display pc

    # 识别区某一路机位（①② 斜上相对、③ 正下方仰视、④ 水平侧视）
    python tools/preview_camera.py --camera detect_up1 --http 8080
    python tools/preview_camera.py --camera detect_up2
    python tools/preview_camera.py --camera detect_down
    python tools/preview_camera.py --camera detect_side

    # 同时弹一个窗，显示"实际发给大模型的图"
    DISPLAY=:0 python tools/preview_camera.py --show-llm

叠加框跟着 `DETECT_CROP_MODE` 变：square 画「居中正方形 + 内缩后的送图范围」；
full 画「不裁边、全幅缩到长边 `DETECT_SEND_MAX_SIDE`」的实际情况。

按键（本地窗口模式）：q / Esc 退出，s 存快照，o 开关托盘框，c 开关分栏线
网页模式：页面上有 tray / columns / snapshot 按钮
所有叠加框默认关闭（画面就是摄像头原图），需要时按 o（红框）/ c（分栏线）或加 --tray / --columns
"""

import argparse
import os
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.common import (  # noqa: E402
    initial_cap, crop_pallet_llm, crop_detect_llm, detect_frame_preprocessed,
)
from shared.const import (  # noqa: E402
    CAMERA_PALLET_ID, DETECT_CAMERA_IDS, DETECT_CAMERA_NAMES,
    PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS,
    DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS,
    TRAY_X_MIN, TRAY_X_MAX, TRAY_Y_MIN, TRAY_Y_MAX, COLUMN_COUNT,
    DETECT_LLM_INSET, DETECT_CROP_MODE, DETECT_SEND_MAX_SIDE, DETECT_FULL_INSET,
)

CAMERAS = {
    "pallet": (CAMERA_PALLET_ID, "托盘上方广角摄像头", PALLET_WIDTH, PALLET_HEIGHT, PALLET_FPS),
}

# 识别区按 DETECT_CAMERA_IDS 的个数生成（默认 4 路），名字直接取 const：
# 这里写死三路的话，④ 永远选不到，而且换机位数时也不会跟着变。
_DETECT_ALIASES = ("detect_up1", "detect_up2", "detect_down", "detect_side")
for _index, _camera_id in enumerate(DETECT_CAMERA_IDS):
    _alias = (_DETECT_ALIASES[_index] if _index < len(_DETECT_ALIASES)
              else "detect_%d" % (_index + 1))
    CAMERAS[_alias] = (_camera_id, DETECT_CAMERA_NAMES[_index],
                       DETECT_WIDTH, DETECT_HEIGHT, DETECT_FPS)
# 旧命令兼容：--camera detect 等价于 detect_up1
CAMERAS["detect"] = CAMERAS["detect_up1"]

# 供 HTTP 线程读取的共享状态
STATE = {
    "jpeg": None,      # 最新一帧的 jpeg bytes
    "shown": None,     # 最新一帧的 BGR（带叠加），用于存快照
    "overlay": False,  # 红色托盘框，默认关闭
    "columns": False,  # 黄色 5 栏分栏线，默认关闭
    "save_dir": os.path.join("capture", "preview"),
    "fps": 0.0,
}

INDEX_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>WaveRider camera preview</title>
<style>
  body{background:#111;color:#eee;font-family:system-ui,Segoe UI,sans-serif;margin:0;padding:16px;text-align:center}
  img{max-width:100%;height:auto;border:1px solid #333;image-rendering:auto}
  button{margin:10px 6px;padding:8px 16px;font-size:14px;border-radius:6px;border:1px solid #555;background:#222;color:#eee;cursor:pointer}
  button:hover{background:#333}
  .hint{color:#888;font-size:13px}
</style></head>
<body>
  <h3 id="title">preview</h3>
  <img id="v" src="/stream.mjpg" alt="stream">
  <div>
    <button onclick="fetch('/overlay')">托盘框 开/关</button>
    <button onclick="fetch('/columns')">分栏线 开/关</button>
    <button onclick="fetch('/snapshot')">存快照</button>
    <button onclick="location.reload()">重连</button>
  </div>
  <p class="hint">若画面卡住：点"重连"；若仍不动，重新跑一次脚本。</p>
</body></html>
"""


def put(image, text, org, color=(0, 255, 255), scale=0.6, thickness=1):
    cv2.putText(image, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def draw_pallet_overlay(frame, tray=True, columns=False):
    """（可选）红色托盘框 +（可选）5 栏分栏线（与 column_occupancy 一致：托盘框内均分）。"""
    height, width = frame.shape[:2]
    x0, x1 = int(TRAY_X_MIN * width), int(TRAY_X_MAX * width)
    y0, y1 = int(TRAY_Y_MIN * height), int(TRAY_Y_MAX * height)
    if tray:
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 0, 255), 2)
    if not columns:
        return frame
    span = x1 - x0
    for index in range(1, COLUMN_COUNT):
        x = x0 + int(span * index / COLUMN_COUNT)
        cv2.line(frame, (x, y0), (x, y1), (0, 255, 255), 1)
    for index in range(COLUMN_COUNT):
        cx = x0 + int(span * (index + 0.5) / COLUMN_COUNT)
        put(frame, str(index + 1), (cx - 8, y0 + 28), (0, 255, 255), 0.8, 2)
    return frame


def draw_detect_overlay(frame):
    """按 DETECT_CROP_MODE 画「采集端裁的范围」与「真正发给大模型的范围」。

    square：蓝框 = 居中正方形（crop_to_square），红框 = 再按 DETECT_LLM_INSET 内缩后的送图范围。
    full  ：不裁边，红框 = 全幅按 DETECT_FULL_INSET 微调（当前为 0，即整个画面）。
    """
    height, width = frame.shape[:2]
    if DETECT_CROP_MODE == "full":
        longest = max(height, width)
        scale = (DETECT_SEND_MAX_SIDE / float(longest)
                 if DETECT_SEND_MAX_SIDE > 0 and longest > DETECT_SEND_MAX_SIDE else 1.0)
        put(frame, "full: no crop -> %dx%d (max_side %d)"
            % (int(round(width * scale)), int(round(height * scale)), DETECT_SEND_MAX_SIDE),
            (10, 26))
        left = DETECT_FULL_INSET["left"]
        top = DETECT_FULL_INSET["top"]
        right = DETECT_FULL_INSET["right"]
        bottom = DETECT_FULL_INSET["bottom"]
        ix0, iy0 = int(width * left), int(height * top)
        ix1, iy1 = width - int(width * right), height - int(height * bottom)
        cv2.rectangle(frame, (ix0, iy0), (ix1, iy1), (0, 0, 255), 2)
        if (left, top, right, bottom) != (0.0, 0.0, 0.0, 0.0):
            put(frame, "red = DETECT_FULL_INSET L%.2f T%.2f R%.2f B%.2f"
                % (left, top, right, bottom), (10, 52))
        return frame

    side = min(height, width)
    sx0, sy0 = (width - side) // 2, (height - side) // 2
    cv2.rectangle(frame, (sx0, sy0), (sx0 + side, sy0 + side), (255, 128, 0), 2)
    left = DETECT_LLM_INSET["left"]
    top = DETECT_LLM_INSET["top"]
    right = DETECT_LLM_INSET["right"]
    bottom = DETECT_LLM_INSET["bottom"]
    ix0, iy0 = sx0 + int(side * left), sy0 + int(side * top)
    ix1, iy1 = sx0 + side - int(side * right), sy0 + side - int(side * bottom)
    cv2.rectangle(frame, (ix0, iy0), (ix1, iy1), (0, 0, 255), 2)
    put(frame, "blue = crop_square, red = LLM input (DETECT_LLM_INSET)", (10, 26))
    put(frame, "inset L%.2f T%.2f R%.2f B%.2f" % (left, top, right, bottom), (10, 52))
    return frame


def llm_view(frame, scene):
    """真正发给大模型的那张图。

    与线上 read_camera → crop_detect_llm 的链路保持一致：**先按 DETECT_CROP_MODE
    预处理**（square 才裁方，full 原样），再交给 crop_detect_llm。
    以前这里无条件 crop_to_square，full 模式下会把全幅两侧裁掉再缩放，看到的是假图。
    """
    if scene == "pallet":
        return crop_pallet_llm(frame)
    return crop_detect_llm(detect_frame_preprocessed(frame))


def annotate(frame, scene):
    """按当前开关叠加托盘框 / 识别区裁剪框 / 分栏线，返回新图（不修改入参）。

    三个开关默认全关 → 画面就是摄像头原图（仅左下角一行 fps/分辨率）。
    """
    shown = frame.copy()
    if scene == "pallet":
        if STATE["overlay"] or STATE["columns"]:
            draw_pallet_overlay(shown, STATE["overlay"], STATE["columns"])
    elif STATE["overlay"]:
        draw_detect_overlay(shown)
    put(shown, "%.1f fps   %dx%d" % (STATE["fps"], shown.shape[1], shown.shape[0]),
        (10, shown.shape[0] - 14), (0, 255, 0), 0.7, 2)
    return shown


def save_snapshot(scene):
    shown = STATE.get("shown")
    if shown is None:
        return "还没有可用帧"
    os.makedirs(STATE["save_dir"], exist_ok=True)
    path = os.path.join(STATE["save_dir"],
                        "preview_%s_%s.jpg" % (scene, datetime.now().strftime("%H%M%S")))
    cv2.imwrite(path, shown)
    print("[快照] %s" % path)
    return path


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静音访问日志
        pass

    def _send(self, code, body, content_type="text/plain; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._send(200, INDEX_HTML, "text/html; charset=utf-8")
            elif path in ("/stream.mjpg", "/stream"):
                self._stream()
            elif path == "/overlay":
                STATE["overlay"] = not STATE["overlay"]
                self._send(200, "overlay=%s" % STATE["overlay"])
            elif path == "/columns":
                STATE["columns"] = not STATE["columns"]
                self._send(200, "columns=%s" % STATE["columns"])
            elif path == "/snapshot":
                self._send(200, save_snapshot(STATE.get("scene", "pallet")))
            else:
                self._send(404, "not found")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                jpeg = STATE.get("jpeg")
                if jpeg:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(("Content-Length: %d\r\n\r\n" % len(jpeg)).encode("ascii"))
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def primary_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # 只是取路由出口 IP，不发包
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="摄像头预览 + 几何叠加（用于调位置）")
    parser.add_argument("--camera", choices=sorted(CAMERAS), default="pallet")
    parser.add_argument("--display", default=":0",
                        help="窗口输出目标：:0=板子本地屏(默认)  pc=通过 MobaXterm 显示在 PC 上  auto=不改 DISPLAY")
    parser.add_argument("--show-llm", action="store_true", help="另开一窗显示实际发给大模型的图")
    parser.add_argument("--http", type=int, nargs="?", const=8080, default=None,
                        help="开启网页预览（MJPEG），可指定端口，默认 8080；开启后不建本地窗口")
    parser.add_argument("--columns", action="store_true",
                        help="显示 5 栏分栏线（默认不显示，只留红色托盘框）")
    parser.add_argument("--tray", action="store_true",
                        help="显示红色托盘框（默认不显示，画面为摄像头原图）")
    parser.add_argument("--quality", type=int, default=80, help="网页预览 jpeg 质量，默认 80")
    parser.add_argument("--save-dir", default=os.path.join("capture", "preview"))
    args = parser.parse_args()

    STATE["save_dir"] = args.save_dir
    STATE["scene"] = args.camera
    STATE["columns"] = args.columns
    STATE["overlay"] = args.tray

    use_http = args.http is not None
    if not use_http:
        if args.display == "pc":
            # 通过 MobaXterm 的 X11 转发显示在 PC 上（.bashrc 里固定了 DISPLAY=:0，这里显式覆盖）
            os.environ["DISPLAY"] = "localhost:10.0"
        elif args.display != "auto":
            os.environ["DISPLAY"] = args.display
        if not os.environ.get("DISPLAY"):
            os.environ["DISPLAY"] = ":0"
            print("[提示] 环境里没有 DISPLAY，已默认用 :0（板子本地屏）")

    camera_id, camera_name, width, height, fps = CAMERAS[args.camera]
    print("设备: %s" % camera_id)
    print("参数: %dx%d @ %dfps" % (width, height, fps))
    if use_http:
        print("模式: 网页预览 (MJPEG)  端口 %d" % args.http)
    else:
        print("模式: 本地窗口          DISPLAY=%s" % os.environ["DISPLAY"])

    cap = initial_cap(camera_id, camera_name, width, height, fps)
    if cap is None:
        raise SystemExit("%s 打开失败，确认设备在不在：ls -l %s" % (camera_name, camera_id))

    os.makedirs(args.save_dir, exist_ok=True)

    if use_http:
        server = ThreadingHTTPServer(("0.0.0.0", args.http), PreviewHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print("在 PC 浏览器打开：  http://%s:%d/   （Ctrl+C 退出）" % (primary_ip(), args.http))
        print("  同网段用板子 IP；若走 SSH 隧道：ssh -L %d:localhost:%d orangepi@<板子IP> 后开 http://localhost:%d/"
              % (args.http, args.http, args.http))

    # 本地窗口模式用 ASCII 标题，避免远程 X server 没有中文字体时显示成 ??????
    window = "preview: %s (q=quit  s=snapshot  o=overlay)" % args.camera.upper()

    frames = 0
    failed = 0
    start = time.time()
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(args.quality)]
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                failed += 1
                print("[警告] 读帧失败 %d/10" % failed)
                if failed >= 10:
                    break
                time.sleep(0.1)
                continue
            failed = 0
            frames += 1
            elapsed = max(time.time() - start, 1e-6)
            STATE["fps"] = frames / elapsed
            shown = annotate(frame, args.camera)
            STATE["shown"] = shown

            if use_http:
                ok_enc, buf = cv2.imencode(".jpg", shown, encode_params)
                if ok_enc:
                    STATE["jpeg"] = buf.tobytes()
                if frames % 100 == 0:
                    print("  %.1f fps  jpeg %.0f KB" % (STATE["fps"], len(STATE["jpeg"] or b"") / 1024.0))
                time.sleep(0.005)
                continue

            try:
                cv2.imshow(window, shown)
                if args.show_llm:
                    cv2.imshow(window + " | LLM input", llm_view(frame, args.camera))
            except cv2.error as error:
                print("无法创建窗口（DISPLAY=%s）：%s" % (os.environ["DISPLAY"], error))
                print("建议改用网页模式： python tools/preview_camera.py --http 8080")
                print("或在板子自己的屏上跑： DISPLAY=:0 python tools/preview_camera.py")
                break
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("o"):
                STATE["overlay"] = not STATE["overlay"]
            if key == ord("c"):
                STATE["columns"] = not STATE["columns"]
            if key == ord("s"):
                save_snapshot(args.camera)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if not use_http:
            cv2.destroyAllWindows()
        print("退出：共 %d 帧，平均 %.1f fps" % (frames, frames / max(time.time() - start, 1e-6)))


if __name__ == "__main__":
    main()
