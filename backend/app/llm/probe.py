"""模型端点探活与列表拉取（Issue #15 P1）。

## 为什么探活请求也要走 pin 传输层

「测试连接」是用**用户提供的 key** 向**用户提供的端点**发请求 —— 如果不做
DNS 复核，它就是一个现成的 SSRF 探针（`https://evil.com` 指向内网）。
所以这里复用 `PinnedDNSAsyncHTTPTransport`，而不是裸 `httpx`。

## 为什么探活打 `/models` 而不是发一次 chat

- 便宜：不消耗 token，不产生生成费用；
- 兼容：OpenAI 兼容端点几乎都实现 `/models`；
- 顺带拿到模型列表，前端可直接做候选。

生成能力（tools/json_mode）不做探测：那需要真实对话，成本与副作用都不合适，
留到任务首次调用时自然暴露。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from app.llm.endpoint_guard import PinnedDNSAsyncHTTPTransport

#: 探活总超时（秒）。用户在前端点按钮，超过 8 秒的等待没有意义。
PROBE_TIMEOUT_SECONDS = 8.0

#: 最多回传多少个模型 id（防某些中继站返回上千条把响应撑大）。
MAX_MODELS = 200


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    latency_ms: int
    models: list[str] = field(default_factory=list)
    error: str | None = None


def _extract_models(payload: object) -> list[str]:
    """兼容 OpenAI `{"data": [...]}` 与裸数组两种形态。"""
    rows: list[object] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            rows = data
    elif isinstance(payload, list):
        rows = payload

    models: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            model_id = row.get("id") or row.get("name")
        else:
            model_id = row
        if isinstance(model_id, str) and model_id and model_id not in models:
            models.append(model_id)
        if len(models) >= MAX_MODELS:
            break
    return models


@dataclass
class GenerationProbeResult:
    """生成能力探测结果（Issue #28）。

    ``ok`` 的含义是「按指令输出了一段**可解析且字段正确**的 JSON」——
    planner 门禁需要的就是这个能力，而不是「能聊天」。
    """

    ok: bool
    latency_ms: int
    error: str | None = None
    raw: str | None = None
    #: 401/402/403（key 无效/欠费/无权限）：这类失败应计入凭据失败计数
    auth_error: bool = False


GENERATION_PROBE_TIMEOUT_SECONDS = 8.0
GENERATION_PROBE_MAX_TOKENS = 64
#: 最小结构化输出探针：要求固定键与取值，解析失败的模型不可能产出 TaskPlan。
GENERATION_PROBE_PROMPT = (
    "只输出一个 JSON 对象，不要任何解释或代码栏标记。"
    '要求：{"status": "ok", "count": 2}——status 必须是字符串 ok，count 必须是数字 2。'
)


def _strip_code_fence(text: str) -> str:
    """剥掉 ```json ... ``` 与首尾空白（很多模型无视「不要代码栏」）。"""
    stripped = re.sub(r"```(?:json)?", "", text).strip()
    return stripped


async def probe_generation(
    *,
    base_url: str,
    api_key: str,
    model: str,
    allow_private: bool = False,
    timeout: float = GENERATION_PROBE_TIMEOUT_SECONDS,
) -> GenerationProbeResult:
    """发一次最小结构化输出请求，验证模型能否按指令产出可解析 JSON。

    ## 成本与副作用（必须在 UI 上事先说明）

    用**用户的 key** 发一条 max_tokens=64 的真实请求（约 100 token）。
    探针每月被手动触发一两次没有成本问题，但不做定时自动探测。

    ## 为什么不用 response_format

    部分中转/模型对 `response_format={"type":"json_object"}` 直接 400，
    那会把「支持 JSON 但不支持该参数」误判成不支持。门禁要的是
    「按指令能出 JSON」，提示词约束就够了。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    body = {
        "model": model,
        "messages": [{"role": "user", "content": GENERATION_PROBE_PROMPT}],
        "temperature": 0,
        "max_tokens": GENERATION_PROBE_MAX_TOKENS,
    }
    transport = PinnedDNSAsyncHTTPTransport(allow_private=allow_private)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            response = await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
    except httpx.TimeoutException:
        return GenerationProbeResult(
            False, elapsed(), error=f"探测超时（{timeout:.0f} 秒）——模型响应过慢，不适合做规划"
        )
    except httpx.HTTPError as exc:
        return GenerationProbeResult(False, elapsed(), error=f"连接失败：{type(exc).__name__}")
    except Exception as exc:
        return GenerationProbeResult(False, elapsed(), error=str(exc)[:200])

    if response.status_code in (401, 402, 403):
        return GenerationProbeResult(
            False,
            elapsed(),
            error=f"API Key 被拒绝（HTTP {response.status_code}）",
            auth_error=True,
        )
    if response.status_code == 429:
        return GenerationProbeResult(False, elapsed(), error="上游限流（429），稍后再试")
    if response.status_code >= 400:
        detail = response.text[:160]
        return GenerationProbeResult(
            False, elapsed(), error=f"端点返回 HTTP {response.status_code}：{detail}"
        )

    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError):
        return GenerationProbeResult(False, elapsed(), error="响应不是预期的 chat.completions 结构")

    raw = str(content or "")
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except ValueError:
        return GenerationProbeResult(
            False,
            elapsed(),
            error="模型没有输出可解析的 JSON（不适合做规划）",
            raw=raw[:200],
        )
    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("count") != 2:
        return GenerationProbeResult(
            False,
            elapsed(),
            error="JSON 已解析但字段不符合要求（不适合做规划）",
            raw=raw[:200],
        )
    return GenerationProbeResult(True, elapsed(), raw=raw[:200])


async def fetch_models(
    *,
    base_url: str,
    api_key: str,
    allow_private: bool = False,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> ProbeResult:
    """GET `{base_url}/models`，返回可读结果（永不抛异常）。"""
    url = f"{base_url.rstrip('/')}/models"
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    transport = PinnedDNSAsyncHTTPTransport(allow_private=allow_private)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            )
    except httpx.TimeoutException:
        return ProbeResult(False, elapsed(), error=f"连接超时（{timeout:.0f} 秒）")
    except httpx.HTTPError as exc:
        return ProbeResult(False, elapsed(), error=f"连接失败：{type(exc).__name__}")
    except Exception as exc:  # 传输层的 IP 校验失败也落到这里
        return ProbeResult(False, elapsed(), error=str(exc)[:200])

    if response.status_code in (401, 403):
        return ProbeResult(
            False,
            elapsed(),
            error=f"API Key 被拒绝（HTTP {response.status_code}），请检查密钥是否有效",
        )
    if response.status_code == 404:
        return ProbeResult(False, elapsed(), error="端点没有 /models 接口（HTTP 404）")
    if response.status_code >= 400:
        return ProbeResult(
            False, elapsed(), error=f"端点返回 HTTP {response.status_code}，请检查 base_url"
        )

    try:
        payload = response.json()
    except ValueError:
        return ProbeResult(False, elapsed(), error="端点返回的不是 JSON，请检查 base_url")

    models = _extract_models(payload)
    if not models:
        # 200 但没有模型：有的中继站关闭了 /models，不代表 key 无效
        return ProbeResult(True, elapsed(), models=[], error="连接成功，但端点未返回模型列表")
    return ProbeResult(True, elapsed(), models=models)
