"""capture 照片粗打标：qwen3-vl-plus 逐图输出物体框 + 颜色 + 形状，转成 YOLO 检测标签。

输出 D:\\GongChuang_Advanced\\capture_labels：
  labels_color\\*.txt   颜色 12 类的检测标签
  labels_shape\\*.txt   形状 16 类的检测标签
  labels_raw\\*.json    模型原始返回，便于复核/重导
  preview_color\\ preview_shape\\  每 25 张一张可视化抽检
  stats.csv  failures.csv
"""
import base64
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")

SRC = r"D:\GongChuang_Advanced\capture"
OUT = r"D:\GongChuang_Advanced\capture_labels"
ENV_PATH = r"D:\GongChuang_Advanced\.env"
IMG_EXT = {".jpg", ".jpeg", ".png"}
WORKERS = 6
MAX_SIDE = 1024
JPEG_Q = 90
PREVIEW_EVERY = 25
MAX_TOKENS = 3000

COLORS = ["black", "blue", "brown", "deep_blue", "deep_green", "green",
          "orange", "pink", "purple", "red", "white", "yellow"]
SHAPES = ["ball", "cone", "cube", "cuboid", "cylinder", "cylinder_thinWalled",
          "hemisphere", "parallelepiped", "prism3", "prism3_thinWalled",
          "prism4_thinWalled", "prism5", "prism5_thinWalled", "prism6",
          "pyramid3", "pyramid4"]

ALIAS = {
    "deepblue": "deep_blue", "deepgreen": "deep_green", "darkblue": "deep_blue",
    "darkgreen": "deep_green", "gray": "white", "grey": "white", "silver": "white",
    "lightblue": "blue", "lightgreen": "green", "brownish": "brown",
    "thinwalledcylinder": "cylinder_thinWalled", "hollowcylinder": "cylinder_thinWalled",
    "tube": "cylinder_thinWalled", "ring": "cylinder_thinWalled",
    "square": "cube", "box": "cuboid", "rectangularbox": "cuboid",
    "sphere": "ball", "circle": "ball", "triangle": "prism3",
    "hexagon": "prism6", "pentagon": "prism5", "pyramid": "pyramid4",
    "tetrahedron": "pyramid3",
}

PROMPT = """你是工业分拣场景的标注员。图为分拣工位相机画面，可能有多个几何体，也可能为空场景；有的是多视角拼接图或网格图。
请找出所有待分拣的几何体（实心或空心），忽略托盘、蓝色边框、支架、背景板、白色台面、贴纸、文字、强反光、人手。
若一张图是多视角拼接或网格排版，请把每个子画面里的物体分别标出，不要合并成一个。
每个物体给出：
- bbox: [x1,y1,x2,y2]，用 0-1000 的整数表示（左上角、右下角），例如 [120,240,650,800]，不要用 0-1 小数
- color: 从 __COLORS__ 选一个
- shape: 从 __SHAPES__ 选一个（cylinder_thinWalled=空心圆筒；prism4_thinWalled=空心方管；cuboid=长方体；parallelepiped=斜棱柱；hemisphere=半球）
- conf: 0 到 1，你对颜色判断的把握
只输出 JSON，格式如下（不要输出解释文字）：
{"objects":[{"bbox":[100,200,400,600],"color":"orange","shape":"cube","conf":0.9}]}
空场景输出 {"objects":[]}。""".replace("__COLORS__", "、".join(COLORS)).replace("__SHAPES__", "、".join(SHAPES))

C_PAL = ["#ff3b30", "#ff9500", "#34c759", "#007aff", "#af52de", "#ff2d55"]
S_PAL = ["#00c7be", "#5856d6", "#ffcc00", "#ff6b22", "#8e8e93", "#30b0c7"]


def load_env(path=ENV_PATH):
    env = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
KEY = ENV["DASHSCOPE_API_KEY"]
BASE = ENV["DASHSCOPE_BASE_URL"].rstrip("/")
MODEL = ENV["DASHSCOPE_MODEL"]


def collect(src=SRC):
    out = []
    for root, _d, names in os.walk(src):
        for n in names:
            if os.path.splitext(n)[1].lower() not in IMG_EXT:
                continue
            full = os.path.join(root, n)
            if "pallet" in os.path.relpath(full, src).lower():
                continue
            out.append(full)
    return sorted(out)


def flat_name(full, src=SRC):
    return os.path.relpath(full, src).replace("\\", "_")


def norm_key(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


ALIAS_N = {norm_key(a): b for a, b in ALIAS.items()}


def to_class(v, table):
    raw = str(v).strip()
    if raw in table:
        return raw
    k = norm_key(raw)
    if k in ALIAS_N and ALIAS_N[k] in table:
        return ALIAS_N[k]
    for t in table:
        if norm_key(t) == k:
            return t
    for t in table:
        tk = norm_key(t)
        if k and (k in tk or tk in k):
            return t
    return None


def encode(path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = min(1.0, MAX_SIDE / max(w, h))
    if s < 1.0:
        im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=JPEG_Q)
    return base64.b64encode(buf.getvalue()).decode(), (w, h)


def request(path):
    b64, size = encode(path)
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            {"type": "text", "text": PROMPT},
        ]}],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode()
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                BASE + "/chat/completions", data=data,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
            with urllib.request.urlopen(req, timeout=150) as r:
                d = json.loads(r.read())
            return d["choices"][0]["message"]["content"], size, d.get("usage", {})
        except urllib.error.HTTPError as e:
            last = "HTTP %s: %s" % (e.code, e.read()[:200].decode("utf-8", "ignore"))
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(last)
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last)


def to_norm(vals, size):
    """把 4 个坐标统一到 0-1，容忍 0-1 / 0-1000 / 像素刻度混用。"""
    span = max(size) if size else 1000.0
    out = []
    for v in vals:
        if v <= 1.0 + 1e-6:
            out.append(v)
        elif v > 1000.0:
            out.append(v / span)
        else:
            out.append(v / 1000.0)
    return out


def parse(txt, size=None):
    t = txt.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.lower().startswith("json"):
            t = t[4:]
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    d = json.loads(t)
    objs = d.get("objects") or d.get("result") or []
    out = []
    for o in objs:
        if not isinstance(o, dict):
            continue
        bb = o.get("bbox") or o.get("box")
        if not (isinstance(bb, (list, tuple)) and len(bb) == 4):
            continue
        try:
            vals = [float(v) for v in bb]
        except Exception:
            continue
        x1, y1, x2, y2 = to_norm(vals, size)
        x1, x2 = sorted((min(max(x1, 0.0), 1.0), min(max(x2, 0.0), 1.0)))
        y1, y2 = sorted((min(max(y1, 0.0), 1.0), min(max(y2, 0.0), 1.0)))
        if x2 - x1 < 0.005 or y2 - y1 < 0.005:
            continue
        c = to_class(o.get("color", ""), COLORS)
        s = to_class(o.get("shape", ""), SHAPES)
        if c is None or s is None:
            continue
        out.append({"bbox": [x1, y1, x2, y2], "color": c, "shape": s,
                    "conf": float(o.get("conf", 0.5) or 0.5)})
    return out


def yolo_line(cls, bb):
    x1, y1, x2, y2 = bb
    return "%d %.6f %.6f %.6f %.6f" % (cls, (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1)


def draw(path, objs, key, table, pal, tag, dst):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = 1000 / max(w, h)
    im = im.resize((int(w * s), int(h * s)))
    W, H = im.size
    d = ImageDraw.Draw(im)
    for o in objs:
        x1, y1, x2, y2 = [o["bbox"][0] * W, o["bbox"][1] * H, o["bbox"][2] * W, o["bbox"][3] * H]
        col = pal[table.index(o[key]) % len(pal)]
        d.rectangle([x1, y1, x2, y2], outline=col, width=3)
        d.text((x1 + 4, min(H - 12, y1 + 2)), "%s %.2f" % (o[key], o["conf"]), fill=col)
    d.text((6, 4), tag, fill="#ff0000")
    im.save(dst, quality=88)


def write_labels(stem, objs):
    for tag, key, table in (("labels_color", "color", COLORS), ("labels_shape", "shape", SHAPES)):
        lines = [yolo_line(table.index(o[key]), o["bbox"]) for o in objs]
        with open(os.path.join(OUT, tag, stem + ".txt"), "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + ("\n" if lines else ""))


def work(full):
    name = flat_name(full)
    stem = os.path.splitext(name)[0]
    raw_path = os.path.join(OUT, "labels_raw", stem + ".json")
    if os.path.exists(raw_path):
        try:
            saved = json.load(open(raw_path, encoding="utf-8"))
            return name, saved["objects"], {}, "cached"
        except Exception:
            pass
    txt, size, usage = request(full)
    objs = parse(txt, size)
    json.dump({"image": name, "size": size, "objects": objs, "usage": usage, "raw": txt},
              open(raw_path, "w", encoding="utf-8"), ensure_ascii=False)
    return name, objs, usage, "ok"


def main():
    t0 = time.time()
    files = collect()
    for d in ("labels_color", "labels_shape", "labels_raw", "preview_color", "preview_shape"):
        os.makedirs(os.path.join(OUT, d), exist_ok=True)
    todo = [f for f in files if not os.path.exists(
        os.path.join(OUT, "labels_raw", os.path.splitext(flat_name(f))[0] + ".json"))]
    print("images=%d todo=%d workers=%d" % (len(files), len(todo), WORKERS), flush=True)

    rows, fails = [], []
    done = 0
    tok = {"in": 0, "out": 0}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(work, f): f for f in todo}
        for fu in as_completed(futs):
            f = futs[fu]
            name = flat_name(f)
            stem = os.path.splitext(name)[0]
            try:
                _nm, objs, usage, how = fu.result()
            except Exception as e:
                fails.append([name, "%s: %s" % (type(e).__name__, e)])
                print("  !!", name, e, flush=True)
                continue
            done += 1
            tok["in"] += usage.get("prompt_tokens", 0)
            tok["out"] += usage.get("completion_tokens", 0)
            write_labels(stem, objs)
            rows.append([name, len(objs), "|".join(sorted({o["color"] for o in objs})),
                         "|".join(sorted({o["shape"] for o in objs})), how])
            if done % PREVIEW_EVERY == 0 or done == len(todo):
                try:
                    draw(f, objs, "color", COLORS, C_PAL, "COLOR", os.path.join(OUT, "preview_color", stem + ".jpg"))
                    draw(f, objs, "shape", SHAPES, S_PAL, "SHAPE", os.path.join(OUT, "preview_shape", stem + ".jpg"))
                except Exception:
                    pass
            if done % 50 == 0:
                el = time.time() - t0
                print("  %d/%d %.0fs %.2f img/s empty=%d fail=%d"
                      % (done, len(todo), el, done / max(el, 1e-9),
                         sum(1 for r in rows if r[1] == 0), len(fails)), flush=True)

    rows.sort()
    with open(os.path.join(OUT, "stats.csv"), "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.writer(fp)
        wr.writerow(["image", "n_objects", "colors", "shapes", "source"])
        wr.writerows(rows)
    with open(os.path.join(OUT, "failures.csv"), "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.writer(fp)
        wr.writerow(["image", "error"])
        wr.writerows(fails)
    print("done %.0fs images=%d fail=%d empty=%d tokens=%d/%d"
          % (time.time() - t0, len(rows), len(fails),
             sum(1 for r in rows if r[1] == 0), tok["in"], tok["out"]), flush=True)


if __name__ == "__main__":
    main()
