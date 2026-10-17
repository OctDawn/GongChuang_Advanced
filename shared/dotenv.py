"""读取项目根目录的 `.env`（给手动启动和工具脚本用）。

为什么需要它：systemd 用 `EnvironmentFile=.env` 注入环境变量，但手动
`python main_push_llm.py` 不会有。而 `shared.const` / `llm.qw` 都在 **import 期**
就读环境变量，所以必须在这些 import **之前**调用本模块，否则读到的是默认值
（表现：送图模式变成 square、key/代理缺失、超时变成 35 秒）。

规则：**不覆盖已有环境变量** —— systemd 注入的、或命令行 `FOO=1 python main.py`
传进来的优先。
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / ".env"


def load_dotenv(path=None, override=False):
    """把 `KEY=VALUE` 读进 os.environ，返回实际写入的条数。"""
    target = Path(path) if path else DEFAULT_PATH
    if not target.is_file():
        return 0
    count = 0
    with open(target, encoding="utf-8") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if key in os.environ and not override:
                continue
            os.environ[key] = value
            count += 1
    return count
