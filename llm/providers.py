"""LLM provider 链：定义"有哪几家、谁在前、谁的配置是什么、谁在冷却"。

只做配置与状态，不发请求（请求在 llm/chain.py）。
未配置 LLM_CHAIN 时返回空列表，调用方继续走 llm/qw.py 原来的单 provider 路径，
所以不写这几行环境变量时，行为和之前完全一致。

.env 示例（顺序 = 优先级）：

    LLM_CHAIN=openai,qwen

    LLM_OPENAI_API_KEY=sk-proj-...
    LLM_OPENAI_BASE_URL=https://api.openai.com/v1
    LLM_OPENAI_MODEL=gpt-6-astra
    LLM_OPENAI_CACHE_MODE=openai_explicit
    LLM_OPENAI_PROXY=http://127.0.0.1:7890
    LLM_OPENAI_TIMEOUT=6

    LLM_QWEN_API_KEY=sk-...
    LLM_QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    LLM_QWEN_MODEL=qwen3-vl-plus
    LLM_QWEN_CACHE_MODE=portable
    LLM_QWEN_PROXY=direct          # direct = 强制直连，忽略系统 HTTPS_PROXY
    LLM_QWEN_TIMEOUT=5

约定的默认值：
    CACHE_MODE      portable（openai_explicit 需要 /responses + 显式缓存断点）
    REASONING_EFFORT off（千问等不支持 reasoning_effort，传了会 400）
    SERVICE_TIER    空（ultrafast 之类只对 OpenRouter 有意义）
    PROXY           空 = 尊重进程环境变量（HTTPS_PROXY/NO_PROXY）
                    direct/none/off = 强制不走代理
                    其它 = 用这个代理地址，且不读环境变量

名称兼容：provider 名写 qwen 或 dashscope 时，缺的字段会回退到旧的
DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL / DASHSCOPE_MODEL / LLM_CACHE_MODE，
方便从单 provider 配置平滑迁移。
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("wave.vision.llm")

_DEFAULT_TIMEOUT = 6.0
_DEFAULT_COOLDOWN = 60.0
_VALID_CACHE_MODES = ("portable", "openai_explicit")
_LEGACY_NAMES = ("qwen", "dashscope", "dashscope-intl")

_NO_PROXY_VALUES = ("direct", "none", "off", "false", "0")


@dataclass(frozen=True)
class Provider:
    """一家服务商的完整调用配置。"""

    name: str
    api_key: str
    base_url: str
    model: str
    cache_mode: str = "portable"
    service_tier: str = ""
    reasoning_effort: str = "off"
    proxy: str = ""
    timeout: float = _DEFAULT_TIMEOUT

    @property
    def label(self) -> str:
        return f"{self.name}({self.model})"

    @property
    def trust_env(self) -> bool:
        """没有显式代理时，允许 httpx 读进程环境变量。

        显式写了代理（或写了 direct）就不读环境变量——否则 .env 里那条全局
        HTTPS_PROXY 会偷偷覆盖 per-provider 设置，正是现场最容易踩的坑。
        """
        return not self.proxy

    @property
    def proxy_url(self) -> Optional[str]:
        if not self.proxy or self.proxy.lower() in _NO_PROXY_VALUES:
            return None
        return self.proxy


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _as_bool(value, default=True):
    if value is None or value == "":
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _load_provider(name):
    """读取单个 provider 的配置；缺关键字段返回 None。"""
    upper = name.strip().upper()
    prefix = f"LLM_{upper}_"
    legacy = name.strip().lower() in _LEGACY_NAMES

    api_key = _env(prefix + "API_KEY")
    base_url = _env(prefix + "BASE_URL")
    model = _env(prefix + "MODEL")
    cache_mode = _env(prefix + "CACHE_MODE").lower()
    service_tier = _env(prefix + "SERVICE_TIER").lower()
    reasoning_effort = _env(prefix + "REASONING_EFFORT").lower()
    proxy = _env(prefix + "PROXY")
    timeout_raw = _env(prefix + "TIMEOUT")

    if legacy:
        # 从旧的单 provider 配置平滑迁移：只补空缺，不覆盖显式值。
        api_key = api_key or _env("DASHSCOPE_API_KEY")
        base_url = base_url or _env("DASHSCOPE_BASE_URL")
        model = model or _env("DASHSCOPE_MODEL")
        cache_mode = cache_mode or _env("LLM_CACHE_MODE").lower()
        service_tier = service_tier or _env("LLM_SERVICE_TIER").lower()
        reasoning_effort = reasoning_effort or _env("LLM_REASONING_EFFORT").lower()

    if not api_key or not base_url or not model:
        logger.error("LLM 链：provider %s 缺少 API_KEY / BASE_URL / MODEL，已忽略", name)
        return None

    if not cache_mode:
        cache_mode = "portable"
    if cache_mode not in _VALID_CACHE_MODES:
        logger.error("LLM 链：provider %s 的 CACHE_MODE=%r 非法（只能是 %s），已忽略",
                     name, cache_mode, "/".join(_VALID_CACHE_MODES))
        return None

    if not reasoning_effort:
        reasoning_effort = "off"

    try:
        timeout = float(timeout_raw) if timeout_raw else _DEFAULT_TIMEOUT
    except ValueError:
        logger.warning("LLM 链：provider %s 的 TIMEOUT=%r 不是数字，用默认 %.1fs",
                       name, timeout_raw, _DEFAULT_TIMEOUT)
        timeout = _DEFAULT_TIMEOUT

    return Provider(
        name=name.strip().lower(),
        api_key=api_key,
        base_url=base_url,
        model=model,
        cache_mode=cache_mode,
        service_tier=service_tier,
        reasoning_effort=reasoning_effort,
        proxy=proxy,
        timeout=max(1.0, timeout),
    )


_lock = threading.Lock()
_chain = None            # type: Optional[List[Provider]]
_chain_loaded = False
_cooldown_until = {}
_fail_counts = {}


def _base_cooldown():
    try:
        return max(0.0, float(os.environ.get("LLM_PROVIDER_COOLDOWN", "60")))
    except ValueError:
        return 60.0


def _load_chain():
    raw = _env("LLM_CHAIN")
    if not raw:
        return []
    names = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    loaded, seen = [], set()
    for name in names:
        key = name.lower()
        if key in seen:
            logger.warning("LLM_CHAIN 里 provider %s 重复，已忽略后一个", name)
            continue
        seen.add(key)
        provider = _load_provider(name)
        if provider is not None:
            loaded.append(provider)
    if loaded:
        logger.info("LLM 链已加载：%s", " -> ".join(p.label for p in loaded))
    else:
        logger.error("LLM_CHAIN=%r 但没有任何 provider 配置有效，回退单 provider 模式", raw)
    return loaded


def get_chain():
    """返回配置好的 provider 列表（只解析一次）。"""
    global _chain, _chain_loaded
    with _lock:
        if not _chain_loaded:
            _chain = _load_chain()
            _chain_loaded = True
        return _chain


def configured():
    """是否启用了多 provider 链（未配置时调用方走旧路径）。"""
    return bool(get_chain())


def mark_failed(provider, seconds=None):
    """把一家标记为冷却，期间不再尝试。

    seconds=None 时按连续失败次数指数退避（60s → 120s → 240s … 封顶 900s）：
    启动探测就不通的那家，不该在 60s 后又被每件货试一次、每次白付 5 秒超时。
    显式传 seconds 则用固定值（测试/手动干预用）。
    """
    with _lock:
        count = _fail_counts.get(provider.name, 0) + 1
        _fail_counts[provider.name] = count
        if seconds is None:
            cooldown = min(_base_cooldown() * (2 ** (count - 1)), 900.0)
        else:
            cooldown = max(0.0, float(seconds))
        _cooldown_until[provider.name] = time.monotonic() + cooldown
    logger.warning("LLM provider %s 进入冷却 %.0fs（连续失败 %d 次）",
                   provider.label, cooldown, count)


def mark_ok(provider):
    with _lock:
        _cooldown_until.pop(provider.name, None)
        _fail_counts.pop(provider.name, None)


def reset_cooldowns():
    with _lock:
        _cooldown_until.clear()
        _fail_counts.clear()


def available(chain=None):
    """按配置顺序返回当前不在冷却期内的 provider。"""
    chain = chain if chain is not None else get_chain()
    now = time.monotonic()
    with _lock:
        return [p for p in chain if _cooldown_until.get(p.name, 0.0) <= now]


def describe():
    """一行摘要，用于启动日志。"""
    chain = get_chain()
    if not chain:
        return "单 provider（未配置 LLM_CHAIN）"
    return " -> ".join(p.label for p in chain)
