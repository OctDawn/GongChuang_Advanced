"""按 provider 顺序发请求：OpenAI -> 千问 -> （YOLO 由主程序接手）。

设计要点（现场 15 秒硬限制下的取舍）：

1. **图片只编码一次**，所有 provider 复用同一份 base64，切家不重复花 CPU。
2. **每家有自己的 timeout / proxy / cache_mode**，互不污染。
3. **只对"换一家可能就好"的错误切家**：连接失败、鉴权失败、5xx、400 参数错误、
   以及同一家纠错重试后仍不合格的输出。JSON 解析失败先在同一家带纠错提示重试，
   因为换模型不会让格式变好、只会多花时间。
4. **冷却**：切走的 provider 在 LLM_PROVIDER_COOLDOWN（默认 60s）内不再尝试，
   避免每件货都先付一次 5 秒的连接超时。
5. **总预算**：整件受 LLM_FALLBACK_TIMEOUT 约束；剩余预算不足
   LLM_MIN_PROVIDER_BUDGET（默认 2s）时不再切下一家，直接交给 YOLO。
6. 未配置 LLM_CHAIN 时本模块不会被调用（qw.qwen_realise 走原路径）。
"""

import logging
import os
import threading
import time

import numpy as np
from openai import OpenAI

from llm import providers, qw

logger = logging.getLogger("wave.vision.llm")

_PROBE_TIMEOUT = 2.5
_CLIENTS = {}
_CLIENT_LOCK = threading.Lock()


class SwitchProvider(Exception):
    """这家不可用，换下一家。"""


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _min_provider_budget():
    return max(0.5, _env_float("LLM_MIN_PROVIDER_BUDGET", 2.0))


def _cooldown_seconds():
    """None = 交给 providers 按连续失败次数做指数退避。"""
    return None


def _probe_cooldown():
    """启动探测不通的那家，本场基本不用再试了（默认 1 小时，重启才复位）。"""
    return max(0.0, _env_float("LLM_PROBE_COOLDOWN", 3600.0))


def _remaining(deadline):
    if deadline is None:
        return None
    return deadline - time.monotonic()


# ---------------------------------------------------------------- 客户端

def _client(provider):
    """每家一个 OpenAI 客户端（各自的代理与 trust_env）。"""
    with _CLIENT_LOCK:
        client = _CLIENTS.get(provider.name)
        if client is not None:
            return client
        kwargs = {
            "base_url": provider.base_url,
            "api_key": provider.api_key,
            "timeout": provider.timeout,
        }
        if qw.httpx is not None:
            limits = qw.httpx.Limits(max_connections=8, max_keepalive_connections=4,
                                     keepalive_expiry=qw.LLM_KEEPALIVE_EXPIRY)
            http_kwargs = {
                "timeout": provider.timeout,
                "limits": limits,
                "follow_redirects": True,
                "trust_env": provider.trust_env,
            }
            proxy = provider.proxy_url
            try:
                http_client = (qw.httpx.Client(proxy=proxy, **http_kwargs) if proxy
                               else qw.httpx.Client(**http_kwargs))
            except TypeError:
                # 老版本 httpx 只认 proxies=
                http_client = (qw.httpx.Client(proxies=proxy, **http_kwargs) if proxy
                               else qw.httpx.Client(**http_kwargs))
            kwargs["http_client"] = http_client
        client = OpenAI(**kwargs)
        _CLIENTS[provider.name] = client
        return client


def _drop_client(provider):
    with _CLIENT_LOCK:
        stale = _CLIENTS.pop(provider.name, None)
    if stale is not None:
        try:
            stale.close()
        except Exception as error:  # noqa: BLE001
            logger.warning("关闭 %s 的旧客户端失败: %s", provider.name, error)


def reset_clients():
    with _CLIENT_LOCK:
        stale = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in stale:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 请求构造

def _build_messages(provider, prefix_contents, image_contents, prompt):
    messages = []
    for content in prefix_contents:
        blocks = [dict(block) for block in content]
        if provider.cache_mode == "openai_explicit" and blocks:
            blocks[-1] = dict(blocks[-1])
            blocks[-1]["prompt_cache_breakpoint"] = {"mode": "explicit"}
        messages.append({"role": "user", "content": blocks})
    messages.append({"role": "user", "content": [
        {"type": "text", "text": prompt}, *image_contents,
    ]})
    return messages


def _extra_body(provider):
    body = {}
    if provider.cache_mode == "openai_explicit":
        body["prompt_cache_options"] = {"mode": "explicit", "ttl": "30m"}
    effort = (provider.reasoning_effort or "").strip().lower()
    if effort not in ("", "off", "none", "default", "auto"):
        if provider.cache_mode == "openai_explicit":
            body["reasoning"] = {"effort": effort}
        else:
            body["reasoning_effort"] = effort
    tier = (provider.service_tier or "").strip().lower()
    if tier not in ("", "default", "off", "none"):
        body["service_tier"] = tier
    return body


def _call_once(provider, messages, budget):
    """发一次请求，返回文本内容；异常原样抛给上层分流。"""
    client = _client(provider).with_options(timeout=budget, max_retries=0)
    options = _extra_body(provider)
    started = time.monotonic()
    if provider.cache_mode == "openai_explicit":
        response = client.responses.create(
            model=provider.model, input=qw._build_responses_input(messages), **options)
        if getattr(response, "status", None) != "completed":
            raise qw.LLMError("Responses 未完成: status=%s" % getattr(response, "status", None))
        content = response.output_text
    else:
        response = client.chat.completions.create(
            model=provider.model, messages=messages, **options)
        content = response.choices[0].message.content
    logger.info("LLM[%s] 请求结束，耗时 %.2f 秒", provider.name, time.monotonic() - started)
    qw._log_usage(response)
    logger.info("LLM[%s] 响应: id=%s request_id=%s service_tier=%s", provider.name,
                getattr(response, "id", "unknown"),
                getattr(response, "_request_id", "unknown"),
                getattr(response, "service_tier", "unknown"))
    return content if isinstance(content, str) else str(content)


# ---------------------------------------------------------------- 单家尝试

def _attempt(provider, prefix_contents, image_contents, prompt, deadline,
             message_save_path, validator):
    attempt_prompt = prompt
    parse_left = max(1, qw.LLM_RETRIES)
    connection_left = max(1, qw.LLM_CONNECTION_RETRIES)
    attempt = 0
    while True:
        attempt += 1
        remaining = _remaining(deadline)
        if remaining is not None and remaining <= 0:
            raise qw.LLMDeadlineExceeded("识别总时限已到，停止重试")
        budget = provider.timeout if remaining is None else min(provider.timeout, max(0.5, remaining))
        messages = _build_messages(provider, prefix_contents, image_contents, attempt_prompt)
        try:
            content = _call_once(provider, messages, budget)
        except qw.LLMDeadlineExceeded:
            raise
        except Exception as error:  # noqa: BLE001 —— 按错误类型分流
            if qw._is_auth_error(error):
                raise SwitchProvider("鉴权失败(%s): %s" % (
                    getattr(error, "status_code", "?"), error)) from error
            if qw._is_connection_error(error):
                if connection_left <= 0:
                    raise SwitchProvider("连接失败: %s" % error) from error
                connection_left -= 1
                _drop_client(provider)          # 池里可能有僵尸连接
                time.sleep(0.3)
                logger.warning("LLM[%s] 连接失败，重建连接后重试: %s", provider.name, error)
                continue
            status = getattr(error, "status_code", None)
            if status == 400:
                detail = str(error)
                if "cache" in detail.lower():
                    logger.error("LLM[%s] 显式缓存被拒绝，核对 CACHE_MODE/model: %s",
                                 provider.name, detail)
                else:
                    logger.error("LLM[%s] 400 参数或模型错误（reasoning/service_tier/图片格式）: %s",
                                 provider.name, detail)
                raise SwitchProvider("400: %s" % detail[:160]) from error
            if isinstance(status, int) and 500 <= status < 600:
                raise SwitchProvider("上游 %d" % status) from error
            raise SwitchProvider("请求失败: %s" % error) from error

        logger.info("LLM[%s] 原始回复：%s", provider.name, content)
        if message_save_path:
            try:
                qw._save_response(message_save_path, content, attempt - 1)
            except Exception as error:  # noqa: BLE001 —— 存盘失败不影响识别
                logger.warning("保存 LLM 回复失败: %s", error)

        try:
            result = qw._parse_json(content)
            if validator is not None:
                result = validator(result)
            return result
        except qw.LLMError as error:
            logger.warning("LLM[%s] 结果解析/校验失败(第 %d 次): %s", provider.name, attempt, error)
            if parse_left <= 0:
                raise SwitchProvider("输出不合格: %s" % error) from error
            parse_left -= 1
            attempt_prompt = qw._correction_prompt(prompt, error)


# ---------------------------------------------------------------- 对外接口

def realise(images, prompt, message_save_path=None, validator=None, total_timeout=None,
            *, prefix_sections=()):
    """按链顺序识别；全部失败抛 qw.LLMError。"""
    chain = providers.get_chain()
    if not chain:
        raise qw.LLMError("未配置 LLM_CHAIN")
    if len(prefix_sections) > 4:
        raise qw.LLMError("最多支持四段可复用前缀")

    deadline = None if total_timeout is None else time.monotonic() + float(total_timeout)

    # 编码只做一次：切家不重复花 CPU，也保证各家的图片字节完全一致。
    stats = []
    prefix_contents = [
        [{"type": "text", "text": text}]
        + [qw._encode_image(img, stats, qw.LLM_REF_MAX_SIDE or None, cache=True)
           for img in refs]
        for text, refs in prefix_sections
    ]
    if isinstance(images, np.ndarray):
        images = [images]
    image_contents = [qw._encode_image(img, stats) for img in images]
    logger.info("LLM 图片载荷：%d 张，共 %.0f KB（%s）",
                len(image_contents) + sum(len(c) - 1 for c in prefix_contents),
                sum(kb for _, _, kb in stats),
                ", ".join("%dx%d" % (w, h) for w, h, _ in stats))

    errors = []
    min_budget = _min_provider_budget()
    for provider in providers.available(chain):
        remaining = _remaining(deadline)
        if remaining is not None and remaining < min_budget:
            logger.warning("LLM 剩余预算 %.1fs < %.1fs，不再尝试 %s，直接交给 YOLO",
                           remaining, min_budget, provider.label)
            break
        logger.info("LLM 尝试 %s（单次上限 %.1fs，缓存模式 %s）",
                    provider.label,
                    provider.timeout if remaining is None else min(provider.timeout, remaining),
                    provider.cache_mode)
        try:
            return _attempt(provider, prefix_contents, image_contents, prompt,
                            deadline, message_save_path, validator)
        except qw.LLMDeadlineExceeded:
            raise
        except SwitchProvider as error:
            errors.append("%s: %s" % (provider.name, error))
            providers.mark_failed(provider, _cooldown_seconds())
            logger.warning("LLM provider %s 不可用，切下一家：%s", provider.label, error)

    raise qw.LLMError("LLM 链全部失败: " + ("; ".join(errors) if errors else "无可用 provider"))


def _probe_call(provider, budget):
    client = _client(provider).with_options(timeout=budget, max_retries=0)
    if provider.cache_mode == "openai_explicit":
        client.responses.create(
            model=provider.model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": "ping"}]}])
    else:
        client.chat.completions.create(
            model=provider.model, messages=[{"role": "user", "content": "ping"}])


def probe(timeout=None, mark_failures=True):
    """逐个 provider 发极小请求，返回 [(provider, ok, seconds, error)]。"""
    budget = float(timeout or _PROBE_TIMEOUT)
    providers.reset_cooldowns()
    results = []
    for provider in providers.get_chain():
        started = time.monotonic()
        ok, message = True, ""
        try:
            _probe_call(provider, budget)
        except Exception as error:  # noqa: BLE001 —— 探测失败是正常分支
            ok, message = False, ("%s: %s" % (type(error).__name__, error))[:200]
        elapsed = time.monotonic() - started
        results.append((provider, ok, elapsed, message))
        if not ok and mark_failures:
            providers.mark_failed(provider, _probe_cooldown())
        logger.info("LLM 探测 %s：%s，%.2f 秒%s", provider.label,
                    "可用" if ok else "不可用", elapsed, (" | " + message) if message else "")
    return results


def warm_up(prefix_sections=(), prompt=None):
    """探测 + 只预热主用 provider（备用家在首次真实请求时自行建连）。"""
    if not qw.LLM_WARMUP:
        logger.info("LLM 预热已关闭（LLM_WARMUP=0）")
        return None

    results = probe()
    usable = [provider for provider, ok, _, _ in results if ok]
    if not usable:
        logger.error("LLM 链无可用 provider：本场只能依赖 YOLO 兜底（YOLO_FALLBACK）")
        return None

    primary = usable[0]
    logger.info("LLM 主用: %s；备用: %s", primary.label,
                ", ".join(p.name for p in usable[1:]) or "无")

    started = time.time()
    try:
        stats = []
        prefix_contents = [
            [{"type": "text", "text": text}]
            + [qw._encode_image(img, stats, qw.LLM_REF_MAX_SIDE or None, cache=True)
               for img in refs]
            for text, refs in prefix_sections
        ]
        messages = _build_messages(primary, prefix_contents, [], prompt or qw.WARMUP_PROMPT)
        _call_once(primary, messages, primary.timeout)
        elapsed = time.time() - started
        logger.info("LLM[%s] 预热完成，耗时 %.2f 秒（首轮应看到 cached_tokens 命中）",
                    primary.name, elapsed)
        return elapsed
    except Exception as error:  # noqa: BLE001 —— 预热永远不能影响主流程
        logger.warning("LLM[%s] 预热失败（忽略，首轮仍会自行建连）: %s", primary.name, error)
        providers.mark_failed(primary, _cooldown_seconds())
        return None
