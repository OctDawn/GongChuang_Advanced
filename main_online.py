"""线上赛段入口：托盘一次批量预判 + 固定取件顺序，逐件立即应答。

与 `main_push_llm.py` 的区别只有一点：
    离线（默认）每件都在识别区拍 4 路、调一次大模型（约 3~5s/件）；
    线上（本入口）在 `c`（或第一件 `a`）时用托盘俯视图**一次**识别整盘，
    然后按「右→左列、同列上→下」的顺序逐件直接回类别（约 0.1s/件）。
    识别区相机仍会打开，队列为空或批量失败时自动回退到原来的逐件识别。

用法（板上，项目根目录）：
    python main_online.py

环境变量（也可写进 .env，见 .env.online.example）：
    ONLINE_MODE=1              本入口会自动置 1（已存在则不覆盖）
    ONLINE_BATCH_TIMEOUT=40    整盘一次识别的时限（秒）
    ONLINE_REFS 在 llm/online_batch.py 里改（6 张参考图 + 中文名）

先决条件（必须满足，否则整条流水线全错）：
    1. 电控按「最右一列开始往左；同一列从画面顶部往下」取件；
    2. 盘中 24 件的类别与 ONLINE_REFS 的 6 类一一对应，且彼此差别明显。
"""

import os

# 必须在 import main_push_llm 之前设置：shared.const / llm.qw 都在 import 期读环境变量
os.environ.setdefault("ONLINE_MODE", "1")

import main_push_llm  # noqa: E402

if __name__ == "__main__":
    main_push_llm.main()
