"""离线测试：不接摄像头/串口，用本地图片验证 agent 自主决策与分组。

每个子目录 = 一件货物，目录内多张图 = 不同角度（第1张为首视角，其余按需 rotate 取用）。
rotate_cb 从该货物剩余图取下一张；decode_cb 返回 None（或可自行注入假结果）。

用法：
    python agent/test_offline.py <图片目录> [--expected expected.json] [--save results.json] [--max-groups 6]

expected.json（标签相同 = 期望同组）：
    {"红色立方体": "A", "红色立方体_第二件": "A", "蓝色圆柱": "B"}
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2

from agent.loop import StageTwoAgent
from agent.memory import GroupMemory
from agent.llm_client import AgentLLMError


def load_images(folder):
    imgs = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
            img = cv2.imread(os.path.join(folder, name))
            if img is not None:
                imgs.append(img)
    return imgs


def evaluate(results, expected):
    tag_to_groups = {}
    for label, tag in expected.items():
        got = results.get(label)
        g = got["group"] if got else None
        tag_to_groups.setdefault(tag, set()).add(g)

    print("\n===== 分组比对 =====")
    for label in sorted(expected):
        got = results.get(label)
        g = got["group"] if got else None
        print(f"  {label:20s} -> 组 {g}")

    split_errors, merge_errors = [], []
    for tag, gs in tag_to_groups.items():
        nonnull = sorted(g for g in gs if g is not None)
        if len(nonnull) > 1:
            split_errors.append((tag, nonnull))
        elif len(nonnull) == 0:
            split_errors.append((tag, ["全部识别失败"]))
    group_to_tags = {}
    for tag, gs in tag_to_groups.items():
        for g in gs:
            if g is None:
                continue
            group_to_tags.setdefault(g, set()).add(tag)
    for g, tags in group_to_tags.items():
        if len(tags) > 1:
            merge_errors.append((g, sorted(tags)))

    print("\n[误拆分]（同一期望标签被分到多个组）:", split_errors if split_errors else "无")
    print("[误合并]（不同期望标签被归到同一组）:", merge_errors if merge_errors else "无")


def main():
    ap = argparse.ArgumentParser(description="离线验证 agent 自主决策与分组")
    ap.add_argument("image_dir")
    ap.add_argument("--expected", help="期望分组 json（可选，自动比对）")
    ap.add_argument("--save", default="agent_results.json", help="结果保存路径")
    ap.add_argument("--max-groups", type=int, default=6, help="储物盒数量上限")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    folders = sorted(
        os.path.join(args.image_dir, d) for d in os.listdir(args.image_dir)
        if os.path.isdir(os.path.join(args.image_dir, d))
    )
    if not folders:
        print(f"未在 {args.image_dir} 找到任何子目录")
        return

    memory = GroupMemory(max_groups=args.max_groups, save_path=None)

    results = {}
    for folder in folders:
        label = os.path.basename(folder)
        imgs = load_images(folder)
        if not imgs:
            print(f"[跳过] {label}: 无图片")
            continue

        idx = {"i": 0}

        def rotate_cb():
            idx["i"] += 1
            if idx["i"] >= len(imgs):
                return None  # 没有更多角度
            return imgs[idx["i"]]

        agent = StageTwoAgent(memory, rotate_cb=rotate_cb,
                              decode_cb=lambda frame, kind: None)
        try:
            gid, name = agent.run(imgs[0])
        except AgentLLMError as e:
            print(f"[失败] {label}: {e}")
            results[label] = None
            continue

        results[label] = {"group": gid, "name": name}
        print(f"[{label}] -> 组 {gid} | {name}")

    print("\n===== 最终分组（group memory）=====")
    for g in memory.groups:
        print(f"  组{g['id']}: {g['name']}  x{g['count']}")

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump({"results": results, "groups": memory.groups}, f,
                      ensure_ascii=False, indent=2)
        print(f"\n结果已保存: {args.save}")

    if args.expected:
        with open(args.expected, encoding="utf-8") as f:
            expected = json.load(f)
        evaluate(results, expected)


if __name__ == "__main__":
    main()
