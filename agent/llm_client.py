"""agent 专用多轮会话客户端。

独立于 llm/ 包：不含固定词表 / 流程 / 记忆，只做「多轮图文会话 + JSON action 解析」。
密钥从项目根目录 .env 读取（与 main_llm 共用同一份 .env）。
"""
import base64
import json
import logging
import os
import time

import cv2
import numpy as np
from openai import OpenAI

from shared.const import PROJECT_ROOT

logger = logging.getLogger("wave.vision.agent")


class AgentLLMError(Exception):
    """agent 请求或结果解析失败。"""


def _load_dotenv():
    path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
DASHSCOPE_MODEL = os.environ.get("DASHSCOPE_MODEL", "qwen3-vl-plus")
LLM_MAX_SIDE = int(os.environ.get("LLM_MAX_SIDE", "1024"))
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=DASHSCOPE_BASE_URL, api_key=DASHSCOPE_API_KEY, timeout=LLM_TIMEOUT,
        )
    return _client


def _encode_image(img):
    """把一张 cv2 BGR 图像编码成 base64 数据 URL（最长边压缩到 LLM_MAX_SIDE）。"""
    h, w = img.shape[:2]
    side = max(h, w)
    if side > LLM_MAX_SIDE:
        scale = LLM_MAX_SIDE / side
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise AgentLLMError("图像编码失败")
    b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def parse_json(text):
    """从模型回复中提取并解析 JSON，容忍 markdown 围栏、BOM 与前后缀文字。"""
    text = (text or "").strip()
    if text.startswith("\ufeff"):
        text = text[1:].strip()
    if text.startswith("```"):
        text = text.lstrip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    if text.endswith("```"):
        text = text.rstrip("`").strip()
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        s = text.find(open_ch)
        e = text.rfind(close_ch)
        if s != -1 and e != -1 and e > s:
            return json.loads(text[s:e + 1])
    raise AgentLLMError(f"无法解析 JSON: {text!r}")


class AgentSession:
    """维护完整 message 历史的多轮会话。

    - add_view(image, text)：追加一个新视角（图 + 文字）。
    - add_text(text)：追加一条纯文本（工具执行结果等）。
    - ask()：发送请求，返回解析后的 JSON action。
    """

    def __init__(self, system_prompt):
        self.messages = [{"role": "system", "content": system_prompt}]

    def add_view(self, image, text):
        self.messages.append({
            "role": "user",
            "content": [_encode_image(image), {"type": "text", "text": text}],
        })

    def add_text(self, text):
        self.messages.append({
            "role": "user",
            "content": [{"type": "text", "text": text}],
        })

    def ask(self):
        if not DASHSCOPE_API_KEY:
            raise AgentLLMError("缺少环境变量 DASHSCOPE_API_KEY")
        client = _get_client()
        now = time.time()
        try:
            response = client.chat.completions.create(
                model=DASHSCOPE_MODEL, messages=self.messages, temperature=0,
            )
        except Exception as e:  # noqa: BLE001 —— 网络/API 错误
            raise AgentLLMError(f"LLM 请求失败: {e}") from e
        logger.info("LLM 决策耗时 %.2f 秒", time.time() - now)
        content = response.choices[0].message.content
        if not isinstance(content, str):
            content = str(content)
        logger.info("LLM action: %s", content)
        self.messages.append({"role": "assistant", "content": content})
        return parse_json(content)
