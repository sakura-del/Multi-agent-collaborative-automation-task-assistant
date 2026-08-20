"""应用配置系统。

基于 Pydantic Settings 实现：
- 默认值来自 `config/default.yaml`
- 环境变量（.env）覆盖默认配置
- 按子模块分组：app / database / redis / llm / rag / agent / orchestration
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：backend/ 的上一层
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"
ENV_FILE = PROJECT_ROOT / ".env"


# ===== 子配置模型 =====


class AppConfig(BaseModel):
    """应用基础配置"""

    name: str = "多智能体协作自动化任务助手"
    env: str = "development"  # development / staging / production
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "DEBUG"
    log_format: str = "json"  # json / console
    # 文档上传与任务产物的受控存储目录（用户专属子目录隔离）
    upload_root: str = "/app/data/uploads"
    artifact_root: str = "/app/data/artifacts"
    max_upload_bytes: int = 30 * 1024 * 1024


class DatabaseConfig(BaseModel):
    """PostgreSQL 数据库配置"""

    url: str = Field(
        default="postgresql+asyncpg://maa:maa_secret_pwd@postgres:5432/multi_agent_assistant",
        description="异步数据库连接字符串",
    )
    pool_size: int = 10
    max_overflow: int = 20
    pool_recycle: int = 1800
    echo: bool = False


class RedisConfig(BaseModel):
    """Redis 配置"""

    url: str = "redis://redis:6379/0"
    max_connections: int = 50
    default_ttl: int = 3600


class LLMCostBudget(BaseModel):
    """LLM 成本预算（阶段0任务4）"""

    daily_token_limit: int = 1_000_000
    monthly_token_limit: int = 20_000_000
    alert_threshold: float = 0.8


class LLMFallbackItem(BaseModel):
    """模型降级链单项"""

    provider: str
    model: str


class LLMConfig(BaseModel):
    """LLM 配置"""

    default_provider: str = "openai"
    default_model: str = "gpt-4o-mini"
    request_timeout: int = 30
    max_retries: int = 3
    cost_budget: LLMCostBudget = LLMCostBudget()
    fallback_chain: list[LLMFallbackItem] = Field(default_factory=list)

    # 各厂商 API Key（从环境变量注入）
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    glm_api_key: str | None = None
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    qwen_api_key: str | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class RAGConfig(BaseModel):
    """RAG 知识库配置（阶段5填充）"""

    embedding_model: str = "BAAI/bge-large-zh"
    reranker_model: str = "BAAI/bge-reranker-large"
    chunk_size: int = 512
    chunk_overlap: int = 64
    retrieval_top_k: int = 10
    rerank_top_k: int = 5


class AgentConfig(BaseModel):
    """Agent 配置（阶段3填充）"""

    max_concurrency: int = 4
    message_timeout: int = 60


class OrchestrationConfig(BaseModel):
    """调度器配置（阶段4填充）"""

    max_retry: int = 3
    retry_backoff_base: int = 1
    retry_backoff_factor: int = 3


class CORSConfig(BaseModel):
    """CORS 跨域配置"""

    allow_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )
    allow_credentials: bool = True
    allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    allow_headers: list[str] = Field(default_factory=lambda: ["*"])


# ===== 顶层 Settings =====


class Settings(BaseSettings):
    """全局配置入口。

    加载顺序：
    1. `config/default.yaml` 提供默认值
    2. `.env` 文件与系统环境变量覆盖（嵌套字段用 `__` 分隔）
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    app: AppConfig = AppConfig()
    database: DatabaseConfig = DatabaseConfig()
    redis: RedisConfig = RedisConfig()
    llm: LLMConfig = LLMConfig()
    rag: RAGConfig = RAGConfig()
    agent: AgentConfig = AgentConfig()
    orchestration: OrchestrationConfig = OrchestrationConfig()
    cors: CORSConfig = CORSConfig()

    # ===== JWT 鉴权（阶段0任务3） =====
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    @field_validator(
        "app",
        "database",
        "redis",
        "llm",
        "rag",
        "agent",
        "orchestration",
        "cors",
        mode="before",
    )
    @classmethod
    def _ensure_dict(cls, v: Any) -> Any:
        """允许 Pydantic 把环境变量注入的字符串/字典正确解析为子模型。"""
        if v is None:
            return {}
        return v


def _load_yaml_defaults() -> dict:
    """加载 default.yaml 作为默认配置基线。文件不存在时返回空字典。"""
    if not DEFAULT_CONFIG_PATH.exists():
        return {}
    with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。

    使用 lru_cache 保证全应用共享同一份配置对象，
    避免重复读取文件。测试时调用 `get_settings.cache_clear()` 重置。
    """
    defaults = _load_yaml_defaults()
    # 把 default.yaml 内容作为构造参数；环境变量仍会通过 BaseSettings 机制覆盖
    return Settings(**defaults)


# 便于直接 `from app.core.config import settings`
settings = get_settings()
