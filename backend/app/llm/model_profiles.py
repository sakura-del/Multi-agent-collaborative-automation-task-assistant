"""模型能力元数据（Issue #22 延伸）。

## 为什么需要

同一个 `BaseLLM.chat()` 漏斗下面，不同模型的能力差异巨大，靠逐个改代码适配
等于「踩一次坑改一次」：

- `qwen3.8-27b-uncensored-fp8`（用户本地 vLLM）：思考默认开启且会失控——
  实测同一问题思考长度 185~2000+ 字，不显式关闭会把 `max_tokens` 全部吃光、
  正文返回空。关闭参数只有 `chat_template_kwargs.enable_thinking=False`
  与 `reasoning_effort="none"` 生效（其余静默无效）。
- `qiniu/conatus-free`：共享免费池，并发≈1、RPM 极低，思考 token 计入 max_tokens。
- `deepseek-flash`：支持前缀缓存（system 锚点），无失控思考。

把这些差异集中成 profile 表，适配器发请求前按 profile 注入参数，
并给运维一个「这个模型该配多少并发/预算」的依据。

## 匹配规则

精确匹配 → 去掉 `provider/` 前缀再匹配 → 大小写无关子串 → 默认 profile。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelProfile:
    """单个模型的行为特征（只描述需要适配器/调度器感知的部分）。"""

    disable_thinking: bool = False
    """是否注入 `chat_template_kwargs.enable_thinking=False`。

    仅对「思考默认开启且不可控」的推理型模型开启；别的模型照常。
    """

    supports_prefix_cache: bool = False
    """是否支持前缀缓存（DeepSeek/OpenAI 式）。决定静态前缀优化是否有收益。"""

    min_max_tokens_with_thinking: int = 0
    """若无法关闭思考，输出预算至少要给到多少（低于此值正文大概率为空）。"""

    concurrency_friendly: bool = True
    """False = 共享池/严格限流（建议全局并发配 1-2，重试预算放宽）。"""

    recommended_max_concurrency: int = 3
    """该模型下的建议全局并发（运维配置参考，不强制）。"""


_DEFAULT = ModelProfile()

_PROFILES: dict[str, ModelProfile] = {
    # 用户本地部署（vLLM + 27B fp8）。30 tok/s、6 并发无 429、224k 上下文。
    "qwen3.8-27b-uncensored-fp8": ModelProfile(
        disable_thinking=True,
        min_max_tokens_with_thinking=2500,
        supports_prefix_cache=False,
        concurrency_friendly=True,
        recommended_max_concurrency=4,
    ),
    # 七牛云 1024 活动匿名融合模型：共享池，并发≈1，70% 请求 429。
    "qiniu/conatus-free": ModelProfile(
        concurrency_friendly=False,
        recommended_max_concurrency=1,
    ),
    # DeepSeek 官方 flash：支持前缀缓存，思考可控。
    "deepseek-flash": ModelProfile(
        supports_prefix_cache=True,
        recommended_max_concurrency=4,
    ),
}


def profile_for(model: str) -> ModelProfile:
    """按模型名查能力画像；未知模型返回默认画像（保守：不注入任何参数）。"""
    name = (model or "").strip()
    if not name:
        return _DEFAULT
    if name in _PROFILES:
        return _PROFILES[name]
    # 去掉 provider 前缀（如 "qwen/xxx"、"openai/xxx"）
    if "/" in name:
        tail = name.split("/", 1)[1]
        if tail in _PROFILES:
            return _PROFILES[tail]
    lowered = name.lower()
    for key, profile in _PROFILES.items():
        if key.lower() in lowered or lowered in key.lower():
            return profile
    return _DEFAULT


def apply_model_profile(payload: dict[str, Any], model: str) -> None:
    """按模型画像就地修改请求体（适配器发请求前调用）。

    当前只处理「思考开关」：`qwen3.8-27b-uncensored-fp8` 等模型思考默认开启，
    不关会把 `max_tokens` 烧光、正文为空（实测 2000 token 全被 reasoning 吃掉）。
    调用方已显式传 `chat_template_kwargs` 时不覆盖（尊重调用者意图）。
    """
    profile = profile_for(model)
    if not profile.disable_thinking:
        return
    template_kwargs = payload.get("chat_template_kwargs")
    if not isinstance(template_kwargs, dict):
        template_kwargs = {}
    template_kwargs.setdefault("enable_thinking", False)
    payload["chat_template_kwargs"] = template_kwargs
