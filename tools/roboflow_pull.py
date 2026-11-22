"""从 Roboflow 拉数据集：列项目 / 列版本 / 指定版本下载。

比网页 zip 稳的地方：可以只拉一个版本、指定格式、失败可重跑（已下载的目录会被覆盖重拉）。

用法（PC 或 WSL，先 pip install roboflow）：

    # 1) 看这个 workspace 下有哪些项目
    python tools/roboflow_pull.py --key <API_KEY> --workspace lijun-mzcyb --list

    # 2) 看某个项目有哪些版本
    python tools/roboflow_pull.py --key <API_KEY> --workspace lijun-mzcyb --project <项目名> --versions

    # 3) 下载指定版本（YOLOv8 格式）
    python tools/roboflow_pull.py --key <API_KEY> --workspace lijun-mzcyb \
        --project <项目名> --version 3 --format yolov8 --out D:/datasets/real_shape

说明：
- API key 在 app.roboflow.com → Settings → API Keys（也可以放环境变量 ROBOFLOW_API_KEY）。
- 模型训练用的格式一般是 yolov8；OBB 数据集要用 yolov8-obb。
- 如果下载很慢，先用网页生成一个“Resize 640”的新版本，再拉——体积能小一个数量级。
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", default=os.environ.get("ROBOFLOW_API_KEY", ""))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", default="")
    parser.add_argument("--version", type=int, default=0)
    parser.add_argument("--format", default="yolov8")
    parser.add_argument("--out", default="")
    parser.add_argument("--list", action="store_true", help="列出 workspace 下的项目")
    parser.add_argument("--versions", action="store_true", help="列出项目版本")
    args = parser.parse_args()

    if not args.key:
        raise SystemExit("缺 API key：--key 或环境变量 ROBOFLOW_API_KEY")

    try:
        from roboflow import Roboflow
    except ImportError:
        raise SystemExit("缺 roboflow：pip install roboflow")

    rf = Roboflow(api_key=args.key)
    workspace = rf.workspace(args.workspace)

    if args.list:
        print("workspace:", args.workspace)
        for project in workspace.projects():
            print("  %-40s %s" % (project.name, getattr(project, "id", "")))
        return

    if not args.project:
        raise SystemExit("需要 --project（或先用 --list 看有哪些）")

    project = workspace.project(args.project)
    if args.versions:
        print("project:", args.project)
        for version in project.versions():
            print("  v%-4s id=%s" % (version.version, getattr(version, "id", "")))
        return

    if not args.version:
        raise SystemExit("需要 --version（或先用 --versions 看）")

    version = project.version(args.version)
    target = args.out or os.path.join("datasets", "%s_v%d" % (args.project, args.version))
    print("下载 %s/%s v%d 格式 %s -> %s" % (
        args.workspace, args.project, args.version, args.format, target))
    dataset = version.download(args.format, location=target, overwrite=True)
    print("完成:", dataset.location)


if __name__ == "__main__":
    main()
