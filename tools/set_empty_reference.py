"""替换空场景参考图（llm/example/<scene>_empty.jpg）。

背景/底板换了以后必须更新，否则模型会拿旧底板当基准，把新背景误判成"新物体"。

用法（在板子上跑，项目根目录）：

    # 1) 先干跑，看会裁成什么样、有没有二次裁剪风险（不写文件）
    python tools/set_empty_reference.py \
        capture/images/20260926_165525/original/stage2_r002_01.jpg \
        --scene detect_area --dry-run

    # 2) 确认没问题再真正替换（旧文件自动备份成 *_empty.bak.jpg）
    python tools/set_empty_reference.py \
        capture/images/20260926_165525/original/stage2_r002_01.jpg \
        --scene detect_area

    # 托盘空场景同理
    python tools/set_empty_reference.py capture/images/xxx/pallet_original/stage1_r001.jpg --scene pallet

要点：**必须传未裁剪的相机原图**（detect_area 用 1280x720 原帧或 720x720 正方帧，
pallet 用 1280x960 全幅），不能用已经裁过的图，否则会被二次裁剪、取景和实时画面错位。
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as error:  # noqa: BLE001
    raise SystemExit(f"缺少依赖 {error.name}，请先安装：pip install opencv-python numpy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.common import crop_detect_llm, crop_pallet_llm  # noqa: E402

EXAMPLE_DIR = ROOT / "llm" / "example"
# 每种场景：参考图文件名、期望的原图最小边长（低于此值说明是裁过的图）
SCENES = {
    "detect_area": {"filename": "detect_area_empty.jpg", "min_side": 700},
    "pallet": {"filename": "pallet_empty.jpg", "min_side": 1000},
}


def _imread(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _imwrite(path, image):
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise SystemExit(f"写入失败: {path}")
    buffer.tofile(str(path))


def main():
    parser = argparse.ArgumentParser(description="替换空场景参考图")
    parser.add_argument("source", help="未裁剪的相机原图")
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--out-dir", default=str(EXAMPLE_DIR))
    parser.add_argument("--dry-run", action="store_true", help="只检查不写文件")
    args = parser.parse_args()

    spec = SCENES[args.scene]
    source = Path(args.source)
    if not source.is_file():
        raise SystemExit(f"找不到源图: {source}")

    image = _imread(source)
    if image is None:
        raise SystemExit(f"源图无法读取: {source}")
    height, width = image.shape[:2]

    # 走一遍线上同款裁剪链，看模型最终会看到什么
    if args.scene == "pallet":
        final = crop_pallet_llm(image)
    else:
        from shared.common import crop_to_square
        final = crop_detect_llm(crop_to_square(image))

    print(f"源图      : {source}")
    print(f"源图尺寸  : {width}x{height}")
    print(f"参考图目标: {Path(args.out_dir) / spec['filename']}")
    print(f"裁剪后模型看到的尺寸: {final.shape[1]}x{final.shape[0]}")

    problem = False
    if max(height, width) < spec["min_side"]:
        print(f"  [x] 源图边长 {max(height, width)} 小于预期 {spec['min_side']}，"
              f"看起来是**已经裁剪过**的图")
        print(f"      -> 会导致二次裁剪，取景和实时画面错位。请改用在 "
              f"capture/images/<会话>/original/ 或 pallet_original/ 下的未裁剪原图")
        problem = True
    if args.scene == "detect_area" and abs(width - height) > 8 and width / max(height, 1) > 2:
        print(f"  [!] 源图不是正方/接近正方（{width}x{height}）；若这是未裁的 1280x720 原帧可忽略")

    target = Path(args.out_dir) / spec["filename"]
    backup = target.with_name(target.stem + ".bak" + target.suffix)
    if target.is_file():
        print(f"当前参考图: {target}（将被备份为 {backup.name}）")
    else:
        print(f"当前参考图: 不存在（首次生成）")

    if problem:
        raise SystemExit("未通过一致性检查，已中止（可加 --dry-run 反复试）")
    if args.dry_run:
        print("dry-run：没有写文件")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        shutil.copyfile(target, backup)
    if source.suffix.lower() in (".jpg", ".jpeg"):
        shutil.copyfile(source, target)          # 直接拷字节，避免二次 JPEG 压缩
    else:
        _imwrite(target, image)
    print(f"已替换: {target}")
    print("记得提交/同步到板子，并跑一轮 test_llm.py 或真机确认识别正常。")


if __name__ == "__main__":
    main()
