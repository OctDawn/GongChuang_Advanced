"""视觉识别客户端（通用 Chat Completions / OpenAI Responses）。

密钥通过环境变量 DASHSCOPE_API_KEY 提供，不写死在代码里。
本地开发可从项目根目录的 .env 读取（见 _load_dotenv），
生产环境建议用 systemd 的 EnvironmentFile 注入（见 deploy/wave-vision.service）。

v2 变更：
- OpenAI 客户端改为模块级懒加载单例，复用连接池。
- 重试按错误类型分流：
    * 鉴权/权限错误（401/403）—— 立即失败，不再空转重试；
    * 网络 / 超时 / 5xx —— 原样重试；
* JSON 解析失败 / validator 校验失败 —— 带纠错提示重试；
    * 连接类错误（代理/节点不可达、隧道被断开）—— 丢弃连接池重建后短退避重试，
      额外给 LLM_CONNECTION_RETRIES 次（现场最常见的故障就是这一类）。
- 新增 validator 回调：对解析后的结果做词表 / 字段校验，
  校验失败时自动追加纠错提示重新请求，而非直接判死。
- portable 使用 Chat Completions；openai_explicit 使用 Responses 和段末缓存断点。
"""

import base64
import json
import logging
import os
import queue
import threading
import time

import cv2
import numpy as np
from openai import OpenAI
from llm.prompt import build_json_correction_prompt

logger = logging.getLogger("wave.vision.llm")


def _load_httpx():
    """取 httpx 模块。

    有些环境的 openai SDK 把 httpx 换名（本机日志里的 logger 名是 httpx2），
    直接 import httpx 会失败，因此按几种可能依次尝试；都失败返回 None，
    由 _get_client 退回 SDK 默认客户端（连接保活只有 5 秒）。
    """
    try:
        import httpx as module
        return module
    except ImportError:
        pass
    for name in ("httpx2",):
        try:
            return __import__(name)
        except ImportError:
            continue
    try:
        from openai import _base_client
        return getattr(_base_client, "httpx", None)
    except Exception:
        return None


httpx = _load_httpx()
if httpx is None:
    logger.warning("未找到 httpx 模块，连接保活优化未生效（沿用 SDK 默认 5 秒）")
else:
    logger.info("httpx 模块: %s", getattr(httpx, "__name__", httpx))


class LLMError(Exception):
    """LLM 请求或结果解析失败。"""


class LLMDeadlineExceeded(LLMError):
    """整次识别已过期，调用方不得再发送该轮结果。"""


def _load_dotenv():
    """极简 .env 读取（不引入额外依赖），已存在的环境变量优先。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
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
    "DASHSCOPE_BASE_URL", "https://openrouter.ai/api/v1"
)
DASHSCOPE_MODEL = os.environ.get("DASHSCOPE_MODEL", "openai/gpt-6-astra")

LLM_MAX_SIDE = int(os.environ.get("LLM_MAX_SIDE", "1024"))   # 发送前图片最长边像素
LLM_IMAGE_SCALE = float(os.environ.get("LLM_IMAGE_SCALE", "1.0"))  # 发送前等比缩放系数（1.0=不缩放，0.75=长宽各缩到 75%）
LLM_JPEG_QUALITY = int(os.environ.get("LLM_JPEG_QUALITY", "92"))   # 发送前 JPEG 质量（1~100）；85 会把薄壁口沿这类低对比度边界压掉
LLM_REF_MAX_SIDE = int(os.environ.get("LLM_REF_MAX_SIDE", "0"))   # 前缀参考图单独的最长边像素（0=与 LLM_MAX_SIDE 相同）
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "low").strip().lower()  # minimal/low/medium/high；off=不传，用服务端默认
# 连接空闲保活秒数。别设太长：保活时间超过代理/节点自身的空闲超时，就会复用一条
# 已被对端单方面关掉的「僵尸连接」，表现为 LLM 请求报 Connection error。
LLM_KEEPALIVE_EXPIRY = float(os.environ.get("LLM_KEEPALIVE_EXPIRY", "20"))
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))     # 单次请求超时（秒）
LLM_RETRIES = int(os.environ.get("LLM_RETRIES", "2"))        # 失败重试次数（总尝试 = 1 + retries）
# 连接类错误（代理/节点不可达、隧道被对端断开）的**额外**重试次数：这类错误换一条
# 新连接通常立刻就好，所以单独给预算；总时长仍受 total_timeout（LLM_FALLBACK_TIMEOUT）约束。
LLM_CONNECTION_RETRIES = int(os.environ.get("LLM_CONNECTION_RETRIES", "2"))
LLM_CACHE_MODE = os.environ.get("LLM_CACHE_MODE", "openai_explicit").strip().lower()
# 上游服务档位（OpenRouter 请求参数 service_tier）：flex / priority（fast 为其别名）/ ultrafast / auto。
# 空 = 不传，用 OpenRouter 默认路由。ultrafast 会优先走 ultrafast 端点，失败回落 priority，再回落默认端点。
LLM_SERVICE_TIER = os.environ.get("LLM_SERVICE_TIER", "").strip().lower()

# 排查用的传输层计时：LLM_DEBUG_TIMING=1 时打开 httpcore/httpx 的 DEBUG 日志。
# 日志里的 send_request_body / receive_response_headers / response_closed 各自带时间戳，
# 能直接分清“秒花在发送 body、等首字节、还是读响应”。
if os.environ.get("LLM_DEBUG_TIMING", "0").strip().lower() not in ("0", "false", "no", "off"):
    for name in ("httpcore", "httpcore2", "httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.DEBUG)


def _debug_timing_enabled():
    return os.environ.get("LLM_DEBUG_TIMING", "0").strip().lower() not in ("0", "false", "no", "off")


def _timing_hooks():
    """LLM_DEBUG_TIMING=1 时给 httpx 挂事件钩子，打出“开始发送 / 收到响应头”两个时刻。

    不依赖 httpcore 的 logger 名字（本机 openai SDK 把 httpx 换名成了 httpx2，
    底层传输层的 logger 名也不一定是 httpcore），所以直接挂在 httpx 事件上。
    含义：收到响应头 - 开始发送 = 建连 + 上传 body + 上游等待。
    """
    if not _debug_timing_enabled():
        return None
    marks = {}

    def on_request(request):
        marks["t0"] = time.time()
        content = getattr(request, "content", None) or b""
        size = len(content)
        logger.info("[timing] -> 准备发送 body %.0f KB 到 %s", size / 1024.0,
                    getattr(getattr(request, "url", None), "host", "?"))
        dump = os.environ.get("LLM_DEBUG_DUMP")
        if dump and content:
            try:
                with open(dump, "wb") as f:
                    f.write(content)
                logger.info("[timing] 请求体已写入 %s（%d 字节），可用 curl 原样重发做对比", dump, size)
            except OSError as error:
                logger.warning("[timing] 写 %s 失败: %s", dump, error)

    def on_response(response):
        now = time.time()
        logger.info("[timing] <- 收到响应头：建连+上传+上游等待 = %.2f 秒（HTTP %s）；后续读响应体看 'LLM 请求结束'",
                    now - marks.get("t0", now), getattr(response, "status_code", "?"))

    return {"request": [on_request], "response": [on_response]}

_client = None
_client_lock = threading.Lock()


def _get_client():
    """懒加载 OpenAI 客户端单例，复用底层连接池。

    注意：httpx 默认 keepalive_expiry=5 秒，而阶段2 相邻两轮请求间隔 10~20 秒
    （识别 + 机械动作 + 等 v），会让每次请求都重新做 TCP+TLS 握手。
    实测首次请求比复用连接慢 1.5~2 秒，因此这里把空闲保活时间拉长。
    """
    global _client
    with _client_lock:
        if _client is None:
            if httpx is None:
                # 缺少 httpx 时保留 SDK 默认客户端。
                _client = OpenAI(
                    base_url=DASHSCOPE_BASE_URL,
                    api_key=DASHSCOPE_API_KEY,
                    timeout=LLM_TIMEOUT,
                )
            else:
                limits = httpx.Limits(max_connections=8, max_keepalive_connections=4,
                                      keepalive_expiry=LLM_KEEPALIVE_EXPIRY)
                _client = OpenAI(
                    base_url=DASHSCOPE_BASE_URL,
                    api_key=DASHSCOPE_API_KEY,
                    timeout=LLM_TIMEOUT,
                    http_client=httpx.Client(timeout=LLM_TIMEOUT, limits=limits,
                                             follow_redirects=True,
                                             event_hooks=_timing_hooks()),
                )
    return _client


def _downscale(image, max_side=None):
    """先按 LLM_IMAGE_SCALE 等比缩小，再用最长边封顶；不放大。max_side 覆盖 LLM_MAX_SIDE。"""
    h, w = image.shape[:2]
    side = max(h, w)
    scale = min(1.0, LLM_IMAGE_SCALE)
    limit = max_side or LLM_MAX_SIDE
    if side * scale > limit:
        scale = limit / side
    if scale >= 1.0:
        return image
    return cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))),
                      interpolation=cv2.INTER_AREA)


# 固定参考图的编码缓存：示例图/空场景图每轮都完全相同，不缓存的话每轮都要重新
# 缩放 + JPEG 编码 + base64。key 里带 id，而缓存值持有原图引用，防止 id 被复用。
_ENCODE_CACHE = {}
_ENCODE_CACHE_ORDER = []
_ENCODE_CACHE_LIMIT = 16


def _encode_image(img, stats=None, max_side=None, cache=False):
    """把一张 cv2 图像（numpy.ndarray, BGR）编码成 base64 数据 URL。

    stats: 可选列表，追加 (宽, 高, KB)，用于打印本轮图片载荷，便于调优。
    cache: 固定参考图可置 True，避免每轮重复缩放/编码/base64（本轮目标图不要开）。
    """
    key = None
    if cache:
        key = (id(img), img.shape, max_side, LLM_JPEG_QUALITY)
        hit = _ENCODE_CACHE.get(key)
        if hit is not None:
            if stats is not None:
                stats.append(hit[2])
            return dict(hit[1])
    original = img
    img = _downscale(img, max_side)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, LLM_JPEG_QUALITY])
    if not ok:
        raise LLMError("图像编码失败")
    b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
    h, w = img.shape[:2]
    meta = (w, h, len(buf) / 1024)
    if stats is not None:
        stats.append(meta)
    block = {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
    }
    if key is not None:
        _ENCODE_CACHE[key] = (original, block, meta)
        _ENCODE_CACHE_ORDER.append(key)
        while len(_ENCODE_CACHE_ORDER) > _ENCODE_CACHE_LIMIT:
            _ENCODE_CACHE.pop(_ENCODE_CACHE_ORDER.pop(0), None)
    return dict(block)


def _parse_json(text):
    """从模型回复中提取并解析 JSON，容忍 markdown 围栏、BOM 与前后缀文字。"""
    text = (text or "").strip()
    if text.startswith("\ufeff"):
        text = text[1:].strip()
    # 去掉开头的 markdown 围栏（``` 或 ```json 等语言标记）
    if text.startswith("```"):
        text = text.lstrip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # 去掉结尾的 markdown 围栏
    if text.endswith("```"):
        text = text.rstrip("`").strip()
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        s = text.find(open_ch)
        e = text.rfind(close_ch)
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError as error:
                raise LLMError(f"JSON 格式不合法: {error}") from error
    raise LLMError(f"无法从模型回复中解析 JSON: {text!r}")


def _is_auth_error(e):
    """判断是否为鉴权/权限类错误（此类重试无意义）。"""
    status = getattr(e, "status_code", None)
    if status in (401, 403):
        return True
    name = type(e).__name__.lower()
    return "auth" in name or "permission" in name


def _is_connection_error(e):
    """判断是否为连接类错误（代理/节点不可达、隧道被对端断开）。

    这类错误换一条新连接重试通常立刻就好，所以走独立的额外重试预算。
    """
    try:
        from openai import APIConnectionError
        if isinstance(e, APIConnectionError):
            return True
    except Exception:  # noqa: BLE001 —— 老版 SDK 没有这个类时退回名字/文案判断
        pass
    name = type(e).__name__.lower()
    if "connection" in name or name in ("connecterror", "proxyerror",
                                        "remoteprotocolerror", "readerror"):
        return True
    return "connection error" in str(e).lower()


def _reset_client():
    """丢弃当前连接池，下一次请求重新建连。

    连接类错误后必须这么做：httpx 只把出错的那条连接剔除，池里可能还有别的
    「僵尸连接」（保活期间被对端单方面关掉的），直接重试会再撞一次同样的错误。
    """
    global _client
    with _client_lock:
        stale, _client = _client, None
    if stale is not None:
        try:
            stale.close()
        except Exception as error:  # noqa: BLE001 —— 关不掉也不影响新建
            logger.warning("关闭旧 LLM 客户端失败: %s", error)


def _correction_prompt(prompt, error):
    """在原 prompt 后追加纠错提示，用于格式/词表校验失败后的重试。"""
    return build_json_correction_prompt(prompt, error)


def _save_response(path, content, attempt):
    """保存模型返回原文；重试时按 attempt 追加后缀，保留每一次尝试现场。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if attempt > 0:
        root, ext = os.path.splitext(path)
        path = f"{root}_try{attempt + 1}{ext}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def qwen_realise(images, prompt, message_save_path=None, validator=None, total_timeout=None,
                 *, prefix_sections=()):
    """可选整次调用时限（包含编码及重试）；到期丢弃后台迟到结果。"""
    if total_timeout is None:
        return _qwen_realise(images, prompt, message_save_path, validator,
                            prefix_sections=prefix_sections)
    deadline = time.monotonic() + total_timeout
    outcome = queue.Queue(maxsize=1)

    def work():
        try:
            value = _qwen_realise(images, prompt, message_save_path, validator, deadline,
                                  prefix_sections=prefix_sections)
            outcome.put((True, value))
        except Exception as error:
            outcome.put((False, error))

    # 只把无业务状态的 API 工作放入后台，形状登记仍在调用线程执行。
    threading.Thread(target=work, name="llm-request", daemon=True).start()
    try:
        ok, value = outcome.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        raise LLMDeadlineExceeded(f"识别超过 {total_timeout:g} 秒，已放弃") from None
    if time.monotonic() >= deadline:
        raise LLMDeadlineExceeded(f"识别超过 {total_timeout:g} 秒，已放弃")
    if not ok:
        raise value
    return value


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise LLMDeadlineExceeded("识别总时限已到，停止重试并丢弃结果")


def _build_messages(prefix_contents, image_contents, prompt):
    """每个前缀段单独成消息；复制断点所在内容块，防止重试污染原前缀。"""
    messages = []
    for content in prefix_contents:
        blocks = [dict(block) for block in content]
        if LLM_CACHE_MODE == "openai_explicit":
            blocks[-1]["prompt_cache_breakpoint"] = {"mode": "explicit"}
        messages.append({"role": "user", "content": blocks})
    messages.append({"role": "user", "content": [
        {"type": "text", "text": prompt}, *image_contents,
    ]})
    return messages


def _build_responses_input(messages):
    """保持消息和图片顺序，将兼容格式转换为 Responses 内容块。"""
    inputs = []
    for message in messages:
        content = []
        for block in message["content"]:
            if block["type"] == "text":
                part = {"type": "input_text", "text": block["text"]}
            else:
                part = {"type": "input_image", "image_url": block["image_url"]["url"],
                        "detail": block["image_url"].get("detail", "auto")}
            if "prompt_cache_breakpoint" in block:
                part["prompt_cache_breakpoint"] = dict(block["prompt_cache_breakpoint"])
            content.append(part)
        inputs.append({"role": message["role"], "content": content})
    return inputs


def _log_usage(response):
    """兼容 SDK 对象和 dict；服务端未提供用量时明确记为 unknown。"""
    def field(value, name):
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    usage = field(response, "usage")
    is_responses = field(usage, "input_tokens") is not None
    input_name = "input_tokens" if is_responses else "prompt_tokens"
    output_name = "output_tokens" if is_responses else "completion_tokens"
    prompt_details = field(usage, input_name + "_details")
    completion_details = field(usage, output_name + "_details")
    values = (
        field(usage, input_name), field(prompt_details, "cached_tokens"),
        field(prompt_details, "cache_write_tokens"),
        field(usage, output_name), field(completion_details, "reasoning_tokens"),
    )
    logger.info("LLM 用量: prompt_tokens=%s cached_tokens=%s cache_write_tokens=%s completion_tokens=%s reasoning_tokens=%s",
                *(value if value is not None else "unknown" for value in values))


def _qwen_realise(images, prompt, message_save_path=None, validator=None, deadline=None,
                  *, prefix_sections=()):
    """通过兼容接口识别图片。

    images: 单张 numpy 图像（ndarray）或多张图像列表。
    prompt: 提示词文本。
    prefix_sections: 最多四个 (固定文字, 参考图片序列)；依次前置，专用模式在各段末尾标断点。
        images 仅含最后一段的本轮图片；图号由调用方按全部消息中的图片顺序编排。
    message_save_path: 可选，把模型返回原文保存到该文件（便于排查）。
    validator: 可选回调，接收解析后的 JSON 并返回规范化结果；
        校验失败抛 LLMError 时，会自动带纠错提示重试。
    返回: validator 的返回值（未提供 validator 时返回 JSON 解析结果）；
        失败抛 LLMError。
    """
    if not DASHSCOPE_API_KEY:
        raise LLMError("缺少环境变量 DASHSCOPE_API_KEY")
    if LLM_CACHE_MODE not in ("portable", "openai_explicit"):
        raise LLMError("LLM_CACHE_MODE 必须为 portable 或 openai_explicit")
    if len(prefix_sections) > 4:
        raise LLMError("最多支持四段可复用前缀")

    image_stats = []
    prefix_contents = [
        [{"type": "text", "text": text}] + [_encode_image(img, image_stats, LLM_REF_MAX_SIDE or None, cache=True) for img in refs]
        for text, refs in prefix_sections
    ]
    request_options = {}
    extra_body = {}
    if LLM_CACHE_MODE == "openai_explicit":
        # 显式缓存（OpenRouter 等转发服务支持；OpenAI 官方 API 不认识这些字段，会 400）
        extra_body["prompt_cache_options"] = {"mode": "explicit", "ttl": "30m"}
    if LLM_REASONING_EFFORT not in ("", "off", "none", "default", "auto"):
        if LLM_CACHE_MODE == "openai_explicit":
            extra_body["reasoning"] = {"effort": LLM_REASONING_EFFORT}      # Responses 形状
        else:
            extra_body["reasoning_effort"] = LLM_REASONING_EFFORT           # Chat Completions 形状
    if LLM_SERVICE_TIER not in ("", "default", "off", "none"):
        # 服务档位（各家取值不同）：flex / priority / ultrafast …
        # 服务端实际落的档位看日志里的 “LLM 响应: … service_tier=”。
        extra_body["service_tier"] = LLM_SERVICE_TIER
    if extra_body:
        request_options["extra_body"] = extra_body

    if isinstance(images, np.ndarray):
        images = [images]
    image_contents = [_encode_image(img, image_stats) for img in images]
    if image_stats:
        logger.info("LLM 图片载荷：%d 张，共 %.0f KB（%s）", len(image_stats),
                    sum(kb for _, _, kb in image_stats),
                    ", ".join(f"{w}x{h}" for w, h, _ in image_stats))

    last_err = None
    attempt_prompt = prompt
    attempt = 0                                  # 已发起的请求次数（从 1 起）
    plain_left = LLM_RETRIES                     # 常规重试余额（解析/校验失败、非连接类 API 错误）
    connection_left = LLM_CONNECTION_RETRIES     # 连接类错误的额外余额
    while True:
        attempt += 1
        _check_deadline(deadline)
        try:
            image_count = len(image_contents) + sum(len(blocks) - 1 for blocks in prefix_contents)
            logger.info("LLM 请求：%d 张图片，模型 %s，缓存模式 %s，前缀段 %d，reasoning=%s，service_tier=%s",
                        image_count, DASHSCOPE_MODEL, LLM_CACHE_MODE, len(prefix_contents),
                        LLM_REASONING_EFFORT or "服务端默认", LLM_SERVICE_TIER or "默认")
            now = time.time()
            client = _get_client()          # 连接类错误后连接池会被丢掉，所以每次都要重新取
            request_client = client
            if deadline is not None:
                request_client = client.with_options(
                    timeout=min(LLM_TIMEOUT, max(0.001, deadline - time.monotonic())),
                    max_retries=0,
                )
            messages = _build_messages(prefix_contents, image_contents, attempt_prompt)
            if LLM_CACHE_MODE == "openai_explicit":
                response = request_client.responses.create(
                    model=DASHSCOPE_MODEL,
                    input=_build_responses_input(messages),
                    **request_options,
                )
            else:
                response = request_client.chat.completions.create(
                    model=DASHSCOPE_MODEL, messages=messages,
                    **request_options,
                )
            # 不全局强制 JSON 对象：查栏返回数组，分类返回对象。
            _check_deadline(deadline)
            logger.info("LLM 请求结束，耗时 %.2f 秒", time.time() - now)
            _log_usage(response)
            logger.info("LLM 响应: id=%s request_id=%s service_tier=%s", getattr(response, "id", "unknown"),
                        getattr(response, "_request_id", "unknown"),
                        getattr(response, "service_tier", "unknown"))
            if LLM_CACHE_MODE == "openai_explicit":
                if response.status != "completed":
                    raise LLMError(f"Responses 未完成: status={response.status}, "
                                   f"details={getattr(response, 'incomplete_details', None)}")
                content = response.output_text
            else:
                content = response.choices[0].message.content
            if not isinstance(content, str):
                content = str(content)
            logger.info("LLM 原始回复：%s", content)
            if message_save_path:
                _save_response(message_save_path, content, attempt - 1)

            result = _parse_json(content)
            if validator is not None:
                result = validator(result)   # 校验通过则返回规范化后的结果
            return result
        except LLMDeadlineExceeded:
            raise
        except LLMError as e:
            # 解析 / 校验失败：带纠错提示重试，而非原样重试烧钱
            last_err = e
            logger.warning("LLM 结果解析/校验失败(第 %d 次): %s", attempt, e)
            attempt_prompt = _correction_prompt(prompt, e)
            if plain_left <= 0:
                break
            plain_left -= 1
            delay = min(2 * attempt, 10)
        except Exception as e:  # noqa: BLE001 —— 网络/API 错误
            last_err = e
            if _is_auth_error(e):
                logger.error("LLM 鉴权/权限失败，停止重试: %s", e)
                break
            if LLM_CACHE_MODE == "openai_explicit" and getattr(e, "status_code", None) == 400:
                logger.error("显式缓存请求被拒绝；请核对模型/端点支持，或设置 LLM_CACHE_MODE=portable")
                break
            if _is_connection_error(e):
                # 现场最常见的故障就是这一类（代理/节点瞬断）：先把整个连接池丢掉再重试，
                # 否则重试用的还是池里那条已经不通的连接。
                if connection_left <= 0:
                    logger.error("LLM 连接失败(第 %d 次)，额外重试已用尽: %s", attempt, e)
                    break
                connection_left -= 1
                # 已过总时限就别再动连接池：本轮结果反正要丢，重建反而会骚扰下一轮
                # 在途的请求（超时的后台线程可能还在跑）。
                _check_deadline(deadline)
                _reset_client()
                delay = min(float(attempt), 4.0)
                logger.warning("LLM 连接失败(第 %d 次)，已重建连接池，%.1fs 后重试: %s",
                               attempt, delay, e)
            else:
                logger.warning("LLM 请求失败(第 %d 次): %s", attempt, e)
                if plain_left <= 0:
                    break
                plain_left -= 1
                delay = min(2 * attempt, 10)

        _check_deadline(deadline)
        if deadline is not None:
            delay = min(delay, max(0, deadline - time.monotonic()))
        if delay > 0:
            time.sleep(delay)

    _check_deadline(deadline)
    raise LLMError(f"LLM 请求最终失败: {last_err}")
