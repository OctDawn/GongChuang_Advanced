'''贴纸内容解码：二维码 / 条码 / 文字。

从 run_all.py 的解码逻辑移植而来，只依赖 OpenCV，
pyzbar / zxingcpp / paddleocr / pytesseract 按需可选，缺失时自动跳过。

decode_sticker(crop, stype) 是唯一入口：
    stype 取 'qrcode' / 'barcode' / 'text'，解码并返回字符串，失败返回 None。
    图案类（checker / stripe / triangle）不应调用本函数，直接在外部标记。
'''

import cv2
import numpy as np

_QR_DET = None
_PADDLE = None


def crop_box(frame, points, expand=0.2):
    """按四点框裁剪并外扩，返回 BGR 裁剪图；框无效返回 None。"""
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    x1, y1 = pts.min(axis=0)
    x2, y2 = pts.max(axis=0)
    h_img, w_img = frame.shape[:2]
    w, h = x2 - x1, y2 - y1
    if w < 2 or h < 2:
        return None
    x1 = int(max(0, x1 - w * expand))
    y1 = int(max(0, y1 - h * expand))
    x2 = int(min(w_img, x2 + w * expand))
    y2 = int(min(h_img, y2 + h * expand))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return frame[y1:y2, x1:x2]


def _upscale(crop, min_side=300):
    if crop is None:
        return None
    h, w = crop.shape[:2]
    scale = max(1.0, min_side / max(h, w))
    if scale > 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)
    return crop


def decode_sticker(crop, stype):
    """crop 为 BGR 贴纸裁剪图，stype 取 'qrcode' / 'barcode' / 'text'。

    返回解码字符串，失败返回 None。
    """
    if crop is None or crop.size == 0:
        return None
    if stype == "qrcode":
        return _decode_qr(_upscale(crop))
    if stype == "barcode":
        return _decode_barcode(_upscale(crop))
    if stype == "text":
        return _decode_text(_upscale(crop, 400))
    return None


def _decode_qr(crop):
    global _QR_DET
    try:
        if _QR_DET is None:
            _QR_DET = cv2.QRCodeDetector()
        # 不同 OpenCV 版本的 detectAndDecode 返回值个数/顺序略有差异
        # （retval, decoded_info, points[, straight_qrcode]），取第一个非空字符串即可
        result = _QR_DET.detectAndDecode(crop)
        values = result if isinstance(result, (tuple, list)) else (result,)
        for item in values:
            if isinstance(item, str) and item:
                return item
    except Exception:
        pass
    try:
        import zxingcpp
        res = zxingcpp.read_barcodes(crop)
        if res:
            return res[0].text
    except Exception:
        pass
    return None


def _decode_barcode(crop):
    try:
        from pyzbar import pyzbar
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        for r in pyzbar.decode(gray):
            if r.data:
                return r.data.decode("utf-8", "ignore")
    except Exception:
        pass
    try:
        import zxingcpp
        res = zxingcpp.read_barcodes(crop)
        if res:
            return res[0].text
    except Exception:
        pass
    return None


def _order_quad(pts):
    """四点任意顺序 -> [左上, 右上, 右下, 左下]。"""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()          # x - y
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _rectify_text(crop):
    """找出白色贴纸四边形，透视矫正到正面 (320x160)。失败返回 None。"""
    if crop is None:
        return None
    try:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        c = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(c) < 0.3 * crop.shape[0] * crop.shape[1]:
            return None
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        pts = (approx.reshape(4, 2).astype(np.float32) if len(approx) == 4
               else cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32))
        pts = _order_quad(pts)
        w, h = 320, 160
        dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
                       dtype=np.float32)
        m = cv2.getPerspectiveTransform(pts, dst)
        return cv2.warpPerspective(crop, m, (w, h))
    except Exception:
        return None


def _ocr_paddle(img):
    global _PADDLE
    from paddleocr import PaddleOCR
    if _PADDLE is None:
        # paddlepaddle 3.x 默认走 oneDNN 会崩，需显式关闭
        _PADDLE = PaddleOCR(
            lang="ch",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            enable_mkldnn=False,
        )
    res = _PADDLE.predict(img)
    if res and res[0]:
        r = res[0]
        texts = (r["rec_texts"] if isinstance(r, dict)
                 else getattr(r, "rec_texts", None))
        if texts:
            return "".join(texts)
    return None


def _ocr_tesseract(img):
    import pytesseract
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    for rot in (0, 180):
        g = gray if rot == 0 else cv2.rotate(gray, cv2.ROTATE_180)
        txt = pytesseract.image_to_string(g, config="--psm 7").strip()
        if txt:
            return txt
    return None


def _decode_text(crop):
    """文字识别：先透视矫正到正面再 OCR，多引擎兜底。"""
    candidates = []
    rect = _rectify_text(crop)
    if rect is not None:
        candidates.append(rect)
    candidates.append(crop)

    for cand in candidates:
        for engine in (_ocr_paddle, _ocr_tesseract):
            try:
                val = engine(cand)
                if val:
                    return val
            except Exception:
                continue
    return None
