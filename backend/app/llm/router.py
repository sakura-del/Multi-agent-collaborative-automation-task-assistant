"""节点级模型路由（混合处理：本地 + 云端）。

## 为什么

单一 provider 无法同时满足：长输入抽取（本地 27B 零成本、224k 上下文）、
高逻辑要求的规划（云端更强）。用户明确要求「不单靠一个 API」。

## 设计（零签名改动）

- `BaseLLM.chat()` 是所有调用的唯一漏斗，在入口处检查「当前节点类型」；
- 节点类型由 workflow 在执行期间写入 ContextVar（`set_node_type`），
  覆盖 planner / search / extract / write / validate / review 六类；
- 路由命中且目标客户端 ≠ 当前实例时，**委派**给目标客户端的 chat；
  目标客户端自身再进入时 target is self，不会递归；
- 路由表全部来自配置（`llm.node_routing` / `llm.routing_targets`），
  未配置则所有节点走任务选择的模型，行为与改造前一致。

## 配置示例（.env，JSON 字符串，避免嵌套键解析歧义）

```
LLM__NODE_ROUTING={"extract":"local","validate":"local","review":"local"}
LLM__ROUTING_TARGETS={"local":{"provider":"custom","model":"qwen3.8-27b-uncensored-fp8","base_url":"https://model.rhdata.cn/v1","api_key":"sk-..."}}
```
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 当前执行节点的类型（planner/search/extract/write/validate/review）
_node_type: ContextVar[str] = ContextVar("llm_node_type", default="")


def set_node_type(node_type: str) -> Any:
    """设置当前节点类型，返回可用于恢复的 token。"""
    return _node_type.set(node_type or "")


def reset_node_type(token: Any) -> None:
    _node_type.reset(token)


@contextmanager
def node_type_scope(node_type: str) -> Any:
    """在作用域内声明节点类型（异常安全）。"""
    token = set_node_type(node_type)
    try:
        yield
    finally:
        reset_node_type(token)


def current_node_type() -> str:
    return _node_type.get()


class ModelRouter:
    """按节点类型解析目标 LLM 客户端（懒加载，进程级缓存）。"""

    def __init__(
        self, routing: dict[str, str] | None = None, targets: dict[str, Any] | None = None
    ):
        self._routing = dict(routing or {})
        self._targets_cfg = dict(targets or {})
        self._clients: dict[str, Any] = {}

    # ===== 查询 =====

    def target_name_for(self, node_type: str) -> str | None:
        return self._routing.get(node_type)

    def routed_types(self) -> set[str]:
        return set(self._routing)

    # ===== 客户端构建 =====

    def _build_client(self, name: str) -> Any:
        cfg = self._targets_cfg.get(name)
        if cfg is None:
            logger.warning("llm_route_target_missing", target=name)
            return None
        data = cfg if isinstance(cfg, dict) else getattr(cfg, "model_dump", lambda: {})()
        provider = str(data.get("provider") or "custom")
        model = str(data.get("model") or "")
        if not model:
            logger.warning("llm_route_target_no_model", target=name)
            return None
        # 所有目标均按 OpenAI 兼容协议接入（本地 vLLM / 各家中转站）
        from app.llm.adapters.openai import OpenAIAdapter

        client = OpenAIAdapter(
            model=model,
            api_key=data.get("api_key") or None,
            base_url=data.get("base_url") or None,
            timeout=int(data.get("request_timeout") or 90),
            max_retries=int(data.get("max_retries") or 3),
        )
        # 保留配置里的 provider 标签（仅用于日志/观测，不影响协议）
        client.provider = provider
        return client

    def resolve(self, node_type: str) -> Any:
        """返回该节点类型应使用的客户端；None = 使用任务默认模型。"""
        name = self._routing.get(node_type)
        if not name:
            return None
        client = self._clients.get(name)
        if client is None:
            client = self._build_client(name)
            if client is None:
                return None
            self._clients[name] = client
            logger.info(
                "llm_route_client_ready",
                target=name,
                model=getattr(client, "model", ""),
                nodes=[k for k, v in self._routing.items() if v == name],
            )
        return client


def build_router_from_settings() -> ModelRouter:
    """从全局配置构造路由（配置读取失败时返回空路由 = 全走默认）。"""
    try:
        from app.core.config import get_settings

        llm_cfg = get_settings().llm
        return ModelRouter(
            routing=dict(getattr(llm_cfg, "node_routing", {}) or {}),
            targets=dict(getattr(llm_cfg, "routing_targets", {}) or {}),
        )
    except Exception:  # pragma: no cover - 配置异常不阻断启动
        return ModelRouter()


_router: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    """进程级单例（首次调用时按配置构建）。"""
    global _router
    if _router is None:
        _router = build_router_from_settings()
    return _router


def reset_router_for_tests() -> None:
    global _router
    _router = None
