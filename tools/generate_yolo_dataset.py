# -*- coding: utf-8 -*-
"""
Blender 合成 YOLO 训练集（离线兜底用）
====================================
参考 llm/example 里的例图：单物体、多视角、纯色空背景；自动输出带框 YOLO 标签。

- 复用 generate_synthetic_v3.py 的形状/材质/灯光（含薄壁 Solidify 壁厚）。
- 每张图单物体；背景为随机浅色纯色（无杂物），地面浅灰，带柔和阴影。
- bbox 用「像素 mask 渲染」得到，天然精确。
- 同时写「颜色」和「形状」两套标签（同一批图，两个模型分开训）。

用法:
    blender --background --python generate_yolo_dataset.py -- --num 40 --out D:/GongChuang_Advanced/yolo_offline_dataset
"""
import importlib.util
import os
import sys
import math
import random
import numpy as np
import bpy

# ---- 复用 v3 ----
V3 = r"D:\hrx\乘浪-视觉\26工创\generate_synthetic_v3.py"
spec = importlib.util.spec_from_file_location("synv3", V3)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# ---- 参数 ----
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default):
    if name in argv:
        return argv[argv.index(name) + 1]
    return default


NUM_IMAGES = int(arg("--num", "40"))
OUT = arg("--out", r"D:\GongChuang_Advanced\yolo_offline_dataset")
IMG_W, IMG_H = 1024, 576

# 与 const.py 的 16 个形状类对齐；v3 里球体 key 叫 sphere，类名是 ball
SHAPE_CLASSES = ["ball", "cone", "cube", "cuboid", "cylinder", "cylinder_thinWalled",
                 "hemisphere", "parallelepiped", "prism3", "prism3_thinWalled",
                 "prism4_thinWalled", "prism5", "prism5_thinWalled", "prism6",
                 "pyramid3", "pyramid4"]
SHAPE_KEY_TO_CLASS = {"sphere": "ball"}
SHAPE_KEYS = {
    "ball": "sphere", "cone": "cone", "cube": "cube", "cuboid": "cuboid",
    "cylinder": "cylinder", "cylinder_thinWalled": "cylinder_thinWalled",
    "hemisphere": "hemisphere", "parallelepiped": "parallelepiped",
    "prism3": "prism3", "prism3_thinWalled": "prism3_thinWalled",
    "prism4_thinWalled": "prism4_thinWalled", "prism5": "prism5",
    "prism5_thinWalled": "prism5_thinWalled", "prism6": "prism6",
    "pyramid3": "pyramid3", "pyramid4": "pyramid4",
}
COLOR_CLASSES = ["black", "blue", "brown", "deep_blue", "deep_green", "green",
                 "orange", "pink", "purple", "red", "white", "yellow"]

# 例图同款组合（权重更高）
EXEMPLAR_COMBOS = [
    ("cube", "black"), ("prism4_thinWalled", "black"), ("prism5_thinWalled", "black"),
    ("prism6", "black"), ("prism3", "orange"), ("pyramid4", "orange"),
    ("prism4_thinWalled", "yellow"), ("cylinder_thinWalled", "orange"),
    ("cube", "red"), ("ball", "blue"),
]


def pick_combo():
    if random.random() < 0.65:
        return random.choice(EXEMPLAR_COMBOS)
    return random.choice(SHAPE_CLASSES), random.choice(COLOR_CLASSES)


white_mat = None
black_mat = None


def ensure_emission():
    global white_mat, black_mat
    if white_mat is None:
        white_mat = m._make_emission_mat("_mask_yolo_white", (1, 1, 1, 1))
        black_mat = m._make_emission_mat("_mask_yolo_black", (0, 0, 0, 1))


def setup_camera():
    """随机多视角，模拟识别区 top/bottom/side/mirror。"""
    theta = math.radians(random.uniform(12, 78))
    phi = math.radians(random.uniform(0, 360))
    r = random.uniform(4.0, 7.2)
    loc = (r * math.sin(theta) * math.cos(phi),
           r * math.sin(theta) * math.sin(phi),
           r * math.cos(theta))
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.object
    m.look_at(cam, (0, 0, 0.18))
    bpy.context.scene.camera = cam
    return cam


def render_image(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def mask_bbox(obj, scene, ground):
    """像素 mask 取可见区域 bbox（像素坐标 x1,y1,x2,y2）。"""
    ensure_emission()
    lights = [o for o in bpy.data.objects if o.type == 'LIGHT']
    for o in lights:
        o.hide_render = True
    ground.hide_render = True

    saved = [s.material for s in obj.material_slots]
    for s in obj.material_slots:
        s.material = white_mat

    orig_samples = scene.cycles.samples
    orig_denoise = scene.cycles.use_denoising
    orig_mode = scene.render.image_settings.color_mode
    orig_transparent = scene.render.film_transparent
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.image_settings.color_mode = 'RGB'
    scene.render.film_transparent = False

    world = scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0, 0, 0, 1)
    bg.inputs["Strength"].default_value = 0.0
    scene.view_settings.view_transform = 'Standard'

    tmp = os.path.join(OUT, "_mask_tmp.png")
    scene.render.filepath = tmp
    bpy.ops.render.render(write_still=True)
    bbox = None
    img = bpy.data.images.load(tmp)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape((h, w, 4))
    mask = px[:, :, 0] > 0.5
    if mask.any():
        rows, cols = np.where(mask)
        x1 = int(cols.min())
        x2 = int(cols.max()) + 1
        # Blender image.pixels 的行从图像底部开始，需翻转 y 才得到图像坐标
        y1 = h - int(rows.max()) - 1
        y2 = h - int(rows.min())
        if x2 - x1 >= 4 and y2 - y1 >= 4:
            bbox = (x1, y1, x2, y2)
    bpy.data.images.remove(img)

    for s, mat in zip(obj.material_slots, saved):
        s.material = mat
    for o in lights:
        o.hide_render = False
    ground.hide_render = False
    scene.cycles.samples = orig_samples
    scene.cycles.use_denoising = orig_denoise
    scene.render.image_settings.color_mode = orig_mode
    scene.render.film_transparent = orig_transparent
    scene.view_settings.view_transform = 'AgX'
    return bbox


def main():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = int(arg("--samples", "10"))
    scene.cycles.use_denoising = True
    scene.render.resolution_x = IMG_W
    scene.render.resolution_y = IMG_H
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'

    img_dir = os.path.join(OUT, "images")
    lc_dir = os.path.join(OUT, "labels_color")
    ls_dir = os.path.join(OUT, "labels_shape")
    for d in (img_dir, lc_dir, ls_dir):
        os.makedirs(d, exist_ok=True)

    for cls_file, names in (("classes_color.txt", COLOR_CLASSES),
                            ("classes_shape.txt", SHAPE_CLASSES)):
        with open(os.path.join(OUT, cls_file), "w", encoding="utf-8") as f:
            f.write("\n".join(names) + "\n")

    start = int(arg("--start", "0"))
    made = start
    attempts = 0
    while made < start + NUM_IMAGES and attempts < NUM_IMAGES * 5:
        attempts += 1
        shape_cls, color = pick_combo()
        shape_key = SHAPE_KEYS[shape_cls]

        m.clear_scene()
        # 空背景：随机浅色
        v = random.uniform(0.72, 0.98)
        m.set_world_background((v, v, v, 1.0))
        m.setup_lights()
        ground = m.create_ground()

        obj = m.add_object(shape_key, color, (0, 0, 0), random.uniform(0, 2 * math.pi))
        cam = setup_camera()

        name = f"img_{made:06d}.png"
        render_image(os.path.join(img_dir, name))
        bbox = mask_bbox(obj, scene, ground)
        if bbox is None:
            print("skip (no mask)", shape_cls, color, flush=True)
            continue

        x1, y1, x2, y2 = bbox
        if x1 < 3 or y1 < 3 or x2 > IMG_W - 3 or y2 > IMG_H - 3:
            print("skip (touches border)", shape_cls, color, flush=True)
            continue
        cx = (x1 + x2) / 2.0 / IMG_W
        cy = (y1 + y2) / 2.0 / IMG_H
        bw = (x2 - x1) / IMG_W
        bh = (y2 - y1) / IMG_H
        with open(os.path.join(lc_dir, f"img_{made:06d}.txt"), "w") as f:
            f.write(f"{COLOR_CLASSES.index(color)} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")
        with open(os.path.join(ls_dir, f"img_{made:06d}.txt"), "w") as f:
            f.write(f"{SHAPE_CLASSES.index(shape_cls)} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")

        made += 1
        if made % 10 == 0:
            print(f"generated {made}/{NUM_IMAGES}", flush=True)

    # 训练配置（用户自己切 train/val）
    for tag, cls_file in (("color", "classes_color.txt"), ("shape", "classes_shape.txt")):
        names = COLOR_CLASSES if tag == "color" else SHAPE_CLASSES
        with open(os.path.join(OUT, f"data_{tag}.yaml"), "w", encoding="utf-8") as f:
            f.write("path: .\ntrain: images\nval: images\nnames:\n")
            for i, n in enumerate(names):
                f.write(f"  {i}: {n}\n")

    print(f"DONE: {made} images -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
