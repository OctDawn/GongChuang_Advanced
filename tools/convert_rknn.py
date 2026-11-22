# -*- coding: utf-8 -*-
"""ONNX -> RKNN（RK3588，FP16 不量化，最稳）。"""
import sys
from rknn.api import RKNN

onnx_path, out_path = sys.argv[1], sys.argv[2]
rknn = RKNN(verbose=False)
print("config:", rknn.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]],
                            target_platform="rk3588"))
print("load_onnx:", rknn.load_onnx(model=onnx_path))
print("build:", rknn.build(do_quantization=False))
print("export:", rknn.export_rknn(out_path))
rknn.release()
print("OK ->", out_path)
