"""agent 工具：贴纸解码（复用 yolo/decoder 的确定性解码能力）。

大托盘定位用 yolo/DetectRunner，在 main_agent 里直接调用；
这里只封装 agent 阶段2 需要的贴纸解码工具。
"""
from yolo.decoder import decode_sticker


def decode_current(frame, kind):
    """对识别区当前画面做贴纸解码。kind 取 qrcode / barcode / text；返回字符串或 None。"""
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    if kind not in ("qrcode", "barcode", "text"):
        return None
    return decode_sticker(frame, kind)
