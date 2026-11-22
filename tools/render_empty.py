# -*- coding: utf-8 -*-
"""在 empty 空场景背景上渲染两个物体（RGBA + 各自 mask），供 PIL 合成。"""
import importlib.util
import os
import math
import bpy

SANDBOX = r"C:\Users\Lenovo\AppData\Local\Temp\chatbox-sandbox\b05b5ba8-c6b2-4995-9429-0a8e52e57be0"
V3 = r"D:\hrx\乘浪-视觉\26工创\generate_synthetic_v3.py"
OUT = SANDBOX + r"\empty_out"
BG = r"D:\GongChuang_Advanced\llm\example\empty_top.jpg"

spec = importlib.util.spec_from_file_location("synv3", V3)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
os.makedirs(OUT, exist_ok=True)

bg_img = bpy.data.images.load(BG)
W, H = bg_img.size
bpy.data.images.remove(bg_img)
print("BG", W, H)

scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.samples = 48
scene.cycles.use_denoising = True
scene.render.film_transparent = True
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.resolution_x = W
scene.render.resolution_y = H

m.clear_scene()
m.setup_lights()
bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
ground = bpy.context.object
ground.is_shadow_catcher = True

specs = [
    ("prism3", "orange", (-0.85, 0.0, 0), 0.4),
    ("prism4_thinWalled", "yellow", (0.85, 0.0, 0), 0.9),
]
objs = [m.add_object(k, c, loc, yaw) for k, c, loc, yaw in specs]

theta = math.radians(58)
r = 6.6
bpy.ops.object.camera_add(location=(0, -r * math.sin(theta), r * math.cos(theta)))
cam = bpy.context.object
m.look_at(cam, (0, 0, 0.25))
scene.camera = cam

scene.render.filepath = os.path.join(OUT, "rgba.png")
bpy.ops.render.render(write_still=True)
print("rgba done")

white = m._make_emission_mat("_mask_empty_white", (1, 1, 1, 1))
black = m._make_emission_mat("_mask_empty_black", (0, 0, 0, 1))
lights = [o for o in bpy.data.objects if o.type == 'LIGHT']
scene.cycles.samples = 1
scene.cycles.use_denoising = False
scene.render.image_settings.color_mode = 'RGB'
scene.render.film_transparent = False
ground.hide_render = True
for o in lights:
    o.hide_render = True
world = scene.world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
bg.inputs["Color"].default_value = (0, 0, 0, 1)
bg.inputs["Strength"].default_value = 0.0
scene.view_settings.view_transform = 'Standard'

for i, target in enumerate(objs):
    saved = {}
    for o in objs:
        saved[o] = [s.material for s in o.material_slots]
        mat = white if o is target else black
        for s in o.material_slots:
            s.material = mat
    scene.render.filepath = os.path.join(OUT, f"mask_{i}.png")
    bpy.ops.render.render(write_still=True)
    for o in objs:
        for j, s in enumerate(o.material_slots):
            s.material = saved[o][j]
    print("mask", i, "done")

print("DONE")
