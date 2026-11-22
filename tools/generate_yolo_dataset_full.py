# -*- coding: utf-8 -*-
"""
Blender 合成 YOLO 训练集（白背景 + empty 背景，离线兜底）
==========================================================
- 类别按 D:\\GongChuang_Advanced\\shared\\const.py：12 色 × 16 形状，全排列组合。
- 背景两种：
    white —— 随机浅色纯背景 + 地面柔和阴影（颜色最准）
    empty —— 用 llm/example 的空场景图做「相机背景板」，物体渲染在前景（copy-paste 增广）
- 输出颜色/形状两套 YOLO 标签，自动切 train/val。

用法（量大，建议本地跑）:
    blender --background --python generate_yolo_dataset_full.py -- `
        --per-combo 12 --empty-ratio 0.4 --samples 10 --out D:/GongChuang_Advanced/yolo_full

参数:
    --per-combo    每个「颜色×形状」组合生成几张（12×16=192 组合，12 → 约 2300 张）
    --empty-ratio  empty 背景占比（0~1）
    --samples      Cycles 采样数（10 约 8~9 秒/张）
    --val-ratio    验证集比例（默认 0.15）
"""
import importlib.util
import os
import sys
import math
import random
import numpy as np
import bpy

V3 = r"D:\hrx\乘浪-视觉\26工创\generate_synthetic_v3.py"
EMPTY_DIR = r"D:\GongChuang_Advanced\llm\example"
EMPTY_IMAGES = ["empty_top.jpg", "empty_bottom.jpg", "empty_side.jpg", "empty_mirror.jpg"]

spec = importlib.util.spec_from_file_location("synv3", V3)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default):
    return argv[argv.index(name) + 1] if name in argv else default


PER_COMBO = int(arg("--per-combo", "12"))
EMPTY_RATIO = float(arg("--empty-ratio", "0.4"))
SAMPLES = int(arg("--samples", "10"))
VAL_RATIO = float(arg("--val-ratio", "0.15"))
OUT = arg("--out", r"D:\GongChuang_Advanced\yolo_full")
WHITE_W, WHITE_H = 1024, 576

# ---- 与 const.py 对齐 ----
COLOR_CLASSES = ["black", "blue", "brown", "deep_blue", "deep_green", "green",
                 "orange", "pink", "purple", "red", "white", "yellow"]
SHAPE_CLASSES = ["ball", "cone", "cube", "cuboid", "cylinder", "cylinder_thinWalled",
                 "hemisphere", "parallelepiped", "prism3", "prism3_thinWalled",
                 "prism4_thinWalled", "prism5", "prism5_thinWalled", "prism6",
                 "pyramid3", "pyramid4"]
SHAPE_KEYS = {
    "ball": "sphere", "cone": "cone", "cube": "cube", "cuboid": "cuboid",
    "cylinder": "cylinder", "cylinder_thinWalled": "cylinder_thinWalled",
    "hemisphere": "hemisphere", "parallelepiped": "parallelepiped",
    "prism3": "prism3", "prism3_thinWalled": "prism3_thinWalled",
    "prism4_thinWalled": "prism4_thinWalled", "prism5": "prism5",
    "prism5_thinWalled": "prism5_thinWalled", "prism6": "prism6",
    "pyramid3": "pyramid3", "pyramid4": "pyramid4",
}


def set_world_color(v):
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("w")
        bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (v, v, v, 1.0)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


def set_world_image(path):
    """相机看到 empty 图；其他射线看到中性灰（稳定光照/颜色）。"""
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("w")
        bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    coord = nt.nodes.new("ShaderNodeTexCoord")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(path)
    bg_img = nt.nodes.new("ShaderNodeBackground")
    bg_img.inputs["Strength"].default_value = 1.0
    bg_gray = nt.nodes.new("ShaderNodeBackground")
    bg_gray.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    bg_gray.inputs["Strength"].default_value = 1.0
    lp = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(coord.outputs["Window"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], bg_img.inputs["Color"])
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
    nt.links.new(bg_gray.outputs["Background"], mix.inputs[1])
    nt.links.new(bg_img.outputs["Background"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])


def setup_camera(theta_deg=None):
    theta = math.radians(theta_deg if theta_deg is not None else random.uniform(18, 70))
    phi = math.radians(random.uniform(0, 360))
    r = random.uniform(4.0, 7.2)
    loc = (r * math.sin(theta) * math.cos(phi),
           r * math.sin(theta) * math.sin(phi),
           r * math.cos(theta))
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.object
    m.look_at(cam, (0, 0, 0.22))
    bpy.context.scene.camera = cam
    return cam


def mask_bbox(obj, scene, ground, out_dir):
    """alpha 通道取 bbox（film_transparent），y 已翻转成图像坐标。"""
    orig = (scene.render.film_transparent, scene.render.image_settings.color_mode,
            scene.cycles.samples, scene.cycles.use_denoising,
            scene.view_settings.view_transform)
    if ground is not None:
        ground.hide_render = True
    scene.render.film_transparent = True
    scene.render.image_settings.color_mode = 'RGBA'
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.view_settings.view_transform = 'Standard'

    tmp = os.path.join(out_dir, "_mask_tmp.png")
    scene.render.filepath = tmp
    bpy.ops.render.render(write_still=True)

    bbox = None
    img = bpy.data.images.load(tmp)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape((h, w, 4))
    mask = px[:, :, 3] > 0.5
    if mask.any():
        rows, cols = np.where(mask)
        x1 = int(cols.min())
        x2 = int(cols.max()) + 1
        y1 = h - int(rows.max()) - 1
        y2 = h - int(rows.min())
        if x2 - x1 >= 4 and y2 - y1 >= 4:
            bbox = (x1, y1, x2, y2)
    bpy.data.images.remove(img)

    if ground is not None:
        ground.hide_render = False
    (scene.render.film_transparent, scene.render.image_settings.color_mode,
     scene.cycles.samples, scene.cycles.use_denoising,
     scene.view_settings.view_transform) = orig
    return bbox


def main():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.resolution_percentage = 100

    for sub in ("images/train", "images/val", "labels_color/train", "labels_color/val",
                "labels_shape/train", "labels_shape/val"):
        os.makedirs(os.path.join(OUT, sub), exist_ok=True)

    with open(os.path.join(OUT, "classes_color.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(COLOR_CLASSES) + "\n")
    with open(os.path.join(OUT, "classes_shape.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(SHAPE_CLASSES) + "\n")

    MAX = int(arg("--max-samples", "0"))
    made = 0
    total = len(COLOR_CLASSES) * len(SHAPE_CLASSES) * PER_COMBO
    for color in COLOR_CLASSES:
        if MAX and made >= MAX:
            break
        for shape in SHAPE_CLASSES:
            shape_key = SHAPE_KEYS[shape]
            got = 0
            attempts = 0
            while got < PER_COMBO and attempts < PER_COMBO * 6 and (not MAX or made < MAX):
                attempts += 1
                use_empty = random.random() < EMPTY_RATIO
                if use_empty:
                    bg_path = os.path.join(EMPTY_DIR, random.choice(EMPTY_IMAGES))
                    bg = bpy.data.images.load(bg_path)
                    W, H = bg.size
                    bpy.data.images.remove(bg)
                    scene.render.resolution_x = W
                    scene.render.resolution_y = H
                    m.clear_scene()
                    set_world_image(bg_path)
                    ground = None
                    theta = random.uniform(20, 65)
                else:
                    W, H = WHITE_W, WHITE_H
                    scene.render.resolution_x = W
                    scene.render.resolution_y = H
                    m.clear_scene()
                    set_world_color(random.uniform(0.72, 0.98))
                    ground = m.create_ground()
                    theta = None

                m.setup_lights()
                loc = (random.uniform(-0.6, 0.6), random.uniform(-0.6, 0.6), 0)
                obj = m.add_object(shape_key, color, loc, random.uniform(0, 2 * math.pi))
                setup_camera(theta)

                bbox = None
                tmp_img = os.path.join(OUT, "_main_tmp.png")
                for _ in range(3):
                    scene.render.filepath = tmp_img
                    bpy.ops.render.render(write_still=True)
                    bbox = mask_bbox(obj, scene, ground, OUT)
                    if bbox is None:
                        break
                    x1, y1, x2, y2 = bbox
                    if x1 >= 3 and y1 >= 3 and x2 <= W - 3 and y2 <= H - 3:
                        break
                    bbox = None
                    setup_camera(theta)

                if bbox is None:
                    continue

                x1, y1, x2, y2 = bbox
                split = "val" if random.random() < VAL_RATIO else "train"
                stem = f"{shape}_{color}_{made:06d}"
                # 主图（带背景）落盘
                os.replace(tmp_img, os.path.join(OUT, "images", split, stem + ".png"))

                cx = (x1 + x2) / 2.0 / W
                cy = (y1 + y2) / 2.0 / H
                bw = (x2 - x1) / float(W)
                bh = (y2 - y1) / float(H)
                for tag, names, cid in (("color", COLOR_CLASSES, COLOR_CLASSES.index(color)),
                                        ("shape", SHAPE_CLASSES, SHAPE_CLASSES.index(shape))):
                    with open(os.path.join(OUT, f"labels_{tag}", split, stem + ".txt"), "w") as f:
                        f.write(f"{cid} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")

                made += 1
                got += 1
                if made % 50 == 0:
                    print(f"generated {made}/{total}", flush=True)

    for tag, names in (("color", COLOR_CLASSES), ("shape", SHAPE_CLASSES)):
        with open(os.path.join(OUT, f"data_{tag}.yaml"), "w", encoding="utf-8") as f:
            f.write(f"path: {OUT}\ntrain: images/train\nval: images/val\nnames:\n")
            for i, n in enumerate(names):
                f.write(f"  {i}: {n}\n")

    print(f"DONE: {made} images -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
