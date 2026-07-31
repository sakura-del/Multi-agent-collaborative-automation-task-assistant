"""GLM 适配器（智谱 AI）。

智谱 GLM 提供 OpenAI 兼容接口（bigmodel.cn/api/paas/v4），
复用 OpenAIAdapter 的请求/响应逻辑，仅覆写 provider 与默认 base_url。
"""

from __future__ import annotations

from app.llm.adapters.openai import OpenAIAdapter


class GLMAdapter(OpenAIAdapter):
    """智谱 GLM 系列适配器，支持 glm-4 / glm-4-flash / glm-4-air 等模型。"""

    def __init__(
        self,
        model: str = "glm-4-flash",
        *,
        api_key: str | None = None,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        timeout: int = 30,
        max_retries: int = 3,
        default_temperature: float = 0.7,
        default_max_tokens: int | None = None,
        embedding_model: str = "embedding-3",
    ) -> None:
        # 调用父类构造，但覆写 provider 标识，便于日志与降级链识别
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
            embedding_model=embedding_model,
        )
        self.provider = "glm"
