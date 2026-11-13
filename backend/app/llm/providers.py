"""模型厂商目录与自定义端点校验（Issue #15 · BYOK P0）。

## 为什么要「预置目录」而不是让用户随便填

1. **端点由服务端决定**：预置厂商的 `base_url` 写死，用户只填 key。
   否则用户可以把平台的 OpenAI key 导向自己的服务器（key 被截获），
   或把请求指向内网（SSRF）。
2. **模型名仍有自由**：厂商上新模型不需要改代码。

## 自定义端点的校验策略（创建期第一道闸）

真正的 SSRF 防护必须发生在**发起请求时**（DNS 解析之后），因为
`evil.com` 可以解析到 `127.0.0.1`。本模块只做创建期的静态拦截：

| 规则 | 拦住的典型输入 |
| --- | --- |
| 仅 https（开发可放开） | `http://relay.example.com` |
| 端口 443（开发 80/443） | `https://relay.example.com:6379` |
| 禁 URL 内嵌凭据 | `https://user:pass@host` |
| 禁 query / fragment | `https://host/v1?target=...` |
| 禁私网/环回/链路本地 IP 字面量 | `https://169.254.169.254`、`https://10.0.0.8` |
| 禁单标签主机名 | `https://redis:6379`、`https://intranet` |

DNS 解析后的 IP 复核在运行时适配器里做（P0 第二块），两者缺一不可。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.exceptions import ValidationException


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """一家模型厂商的静态描述（不含任何密钥）。"""

    provider: str
    label: str
    base_url: str | None
    """`None` 表示必须由用户提供（`custom`）。"""

    @property
    def requires_base_url(self) -> bool:
        return self.base_url is None


#: 预置厂商。**新增一家只改这里**，端点/前端目录都从它派生。
PRESET_PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec("openai", "OpenAI", "https://api.openai.com/v1"),
    "deepseek": ProviderSpec("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
    "glm": ProviderSpec("glm", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4"),
    "qwen": ProviderSpec("qwen", "通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "siliconflow": ProviderSpec("siliconflow", "硅基流动", "https://api.siliconflow.cn/v1"),
    "moonshot": ProviderSpec("moonshot", "Moonshot", "https://api.moonshot.cn/v1"),
    # 自定义：OpenAI 兼容协议（中继站、vLLM、OpenRouter、one-api 都落在这里）
    "custom": ProviderSpec("custom", "自定义（OpenAI 兼容）", None),
}

#: 对外暴露的稳定顺序（前端下拉框按此渲染）。
PROVIDER_ORDER: tuple[str, ...] = (
    "deepseek",
    "openai",
    "glm",
    "qwen",
    "siliconflow",
    "moonshot",
    "custom",
)


def get_provider_spec(provider: str) -> ProviderSpec:
    """按 provider 键取描述；未知键抛 422（不是 500）。"""
    spec = PRESET_PROVIDERS.get(provider)
    if spec is None:
        raise ValidationException(
            f"不支持的模型厂商：{provider}",
            details={"supported": list(PRESET_PROVIDERS)},
        )
    return spec


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def normalize_base_url(raw: str, *, allow_insecure: bool = False) -> str:
    """校验并规范化自定义端点；不合法抛 422。

    规范化只做两件事：主机小写、去掉尾部 `/`。**不补 `/v1`** ——
    补错路径比报错更难排查。
    """
    value = (raw or "").strip()
    if not value:
        raise ValidationException("自定义端点必须提供 base_url")

    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in {"https", "http"}:
        raise ValidationException("base_url 必须以 https:// 开头")

    if scheme == "http" and not allow_insecure:
        raise ValidationException("base_url 必须使用 https（当前环境未允许明文端点）")

    if parts.username or parts.password:
        raise ValidationException("base_url 不能包含用户名或密码")
    if parts.query or parts.fragment:
        raise ValidationException("base_url 不能带查询参数或锚点")

    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValidationException("base_url 缺少主机名")

    try:
        port = parts.port
    except ValueError as exc:
        raise ValidationException("base_url 的端口不合法") from exc

    allowed_ports = {80, 443} if allow_insecure and scheme == "http" else {443}
    effective_port = port or (443 if scheme == "https" else 80)
    if effective_port not in allowed_ports:
        raise ValidationException(
            f"base_url 只允许 {'/'.join(map(str, sorted(allowed_ports)))} 端口"
        )

    ip = _as_ip(host)
    if ip is not None:
        if _is_blocked_ip(ip):
            raise ValidationException("base_url 不能指向内网、环回或链路本地地址")
    else:
        if host == "localhost" or host.endswith(".localhost"):
            raise ValidationException("base_url 不能指向本机")
        # 单标签主机名（redis / gateway / intranet）只会出现在内网 DNS 里
        if "." not in host:
            raise ValidationException("base_url 的主机名不合法（需要完整域名）")

    # IPv6 字面量在拼接时必须带方括号
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parts.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"
