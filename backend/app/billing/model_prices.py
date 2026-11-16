"""模型单价目录（Issue #15 P2 成本估算）。

## 币种与口径

统一 **USD / 100 万 token**，输入与输出分开。这是各厂商公开标价的通用写法；
不做汇率换算——汇率一变，历史账单就跟着变，对账无法解释。

## 数据会过期

下面是 2026-11 的公开标价快照，只用于**估算**，不是结算依据。
厂商调价后需要更新这里；未知模型返回 `None`，调用方必须显式处理
（显示「未收录单价」并排除在合计之外，而不是按 0 计算）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

#: 100 万 token 的除数（Decimal 避免浮点误差）。
PER_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class ModelPrice:
    """输入/输出单价（USD / 1M token）。"""

    input_per_mtok: Decimal
    output_per_mtok: Decimal


def _price(input_usd: str, output_usd: str) -> ModelPrice:
    return ModelPrice(Decimal(input_usd), Decimal(output_usd))


#: 键必须是小写模型名；带日期/版本后缀的型号靠前缀匹配。
_PRICES: dict[str, ModelPrice] = {
    # OpenAI
    "gpt-4o": _price("2.50", "10.00"),
    "gpt-4o-mini": _price("0.15", "0.60"),
    "gpt-4.1": _price("2.00", "8.00"),
    "gpt-4.1-mini": _price("0.40", "1.60"),
    "gpt-4.1-nano": _price("0.10", "0.40"),
    "o3-mini": _price("1.10", "4.40"),
    "o4-mini": _price("1.10", "4.40"),
    # DeepSeek
    "deepseek-chat": _price("0.27", "1.10"),
    "deepseek-reasoner": _price("0.55", "2.19"),
    # 生产默认模型（model.rhzy.ai 中转别名）。中转站不公开单价，
    # 按 deepseek-chat 的公开标价估算；实际结算价不同时改这一行即可。
    "deepseek-v4.1-flash": _price("0.27", "1.10"),
    # 阿里云通义（国际站标价）
    "qwen-max": _price("1.60", "6.40"),
    "qwen-plus": _price("0.40", "1.20"),
    "qwen-turbo": _price("0.05", "0.20"),
    # 智谱
    "glm-4-plus": _price("0.70", "0.70"),
    "glm-4-air": _price("0.10", "0.10"),
    # Anthropic
    "claude-3-5-sonnet": _price("3.00", "15.00"),
    "claude-3-5-haiku": _price("0.80", "4.00"),
    "claude-sonnet-4": _price("3.00", "15.00"),
    "claude-opus-4": _price("15.00", "75.00"),
    # Google
    "gemini-1.5-pro": _price("1.25", "5.00"),
    "gemini-1.5-flash": _price("0.075", "0.30"),
    "gemini-2.0-flash": _price("0.10", "0.40"),
}

#: 长键优先：否则 `gpt-4o` 会先匹配掉 `gpt-4o-mini`。
_SORTED_KEYS = sorted(_PRICES, key=len, reverse=True)

#: 允许的模型名后缀分隔符（`gpt-4o-2024-08-06`、`qwen-plus:free`）。
_SUFFIX_RE = re.compile(r"^[-:/@]")


def normalize_model(model: str) -> str:
    """去掉厂商前缀与首尾空白，转小写。"""
    text = model.strip().lower()
    if "/" in text:
        # `openai/gpt-4o` → `gpt-4o`；但 `qwen/qwen-plus` 同理
        text = text.rsplit("/", 1)[-1]
    return text


def lookup_price(model: str | None) -> ModelPrice | None:
    """按模型名查单价；未知型号返回 `None`。"""
    if not model:
        return None
    name = normalize_model(model)
    if name in _PRICES:
        return _PRICES[name]
    for key in _SORTED_KEYS:
        if name.startswith(key) and _SUFFIX_RE.match(name[len(key) :]):
            return _PRICES[key]
    return None


def estimate_cost(model: str | None, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
    """估算一次调用的成本（USD）；未知型号返回 `None`。

    不做四舍五入：聚合后再格式化，避免逐条舍入造成累计误差。
    """
    price = lookup_price(model)
    if price is None:
        return None
    prompt = Decimal(max(0, int(prompt_tokens)))
    completion = Decimal(max(0, int(completion_tokens)))
    return (prompt * price.input_per_mtok + completion * price.output_per_mtok) / PER_MILLION
