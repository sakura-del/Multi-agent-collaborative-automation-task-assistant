"""应用配置系统。

基于 Pydantic Settings 实现：
- 默认值来自 `config/default.yaml`
- 环境变量（.env）覆盖默认配置
- 按子模块分组：app / database / redis / llm / rag / agent / orchestration
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录解析（多种部署形态都要能读到 config/default.yaml）。
#
# 需要覆盖的三种布局：
#   1) 仓库内：<root>/backend/app/core/config.py      → <root>
#   2) 容器内（COPY 后）：/app/app/core/config.py      → /app（config 与 app 同级）
#   3) editable 安装：config.py 位于 site-packages，但 sys.path 里有真实包目录
#
# Plan 5 Task 8 两次踩坑：先是 parents[3] 在镜像里越界到 "/"；修完后又因为
# editable 安装让 __file__ 指向 site-packages，仍然找不到 /app/config。
# 因此这里综合「环境变量 > 包目录候选 > 当前工作目录」逐级探测。
def _resolve_project_root() -> Path:
    # 1) 显式覆盖（容器/自定义部署可用 MAA_PROJECT_ROOT 指定）
    explicit = os.getenv("MAA_PROJECT_ROOT")
    if explicit and (Path(explicit) / "config").is_dir():
        return Path(explicit).resolve()

    candidates: list[Path] = []

    # 2) 从 app 包的实际位置推断（优先，editable 安装下 __file__ 不可靠）
    try:
        import app as _app_pkg

        pkg_dir = Path(_app_pkg.__file__).resolve().parent  # .../app
        candidates.extend(
            [
                pkg_dir.parent,  # /app（容器）或 backend/（仓库）
                pkg_dir.parent.parent,  # 仓库根
                pkg_dir,  # 兜底
            ]
        )
    except Exception:  # pragma: no cover - 极端环境下导入失败
        pass

    # 3) 从本文件位置推断
    file_dir = Path(__file__).resolve().parent
    candidates.extend([file_dir.parents[i] for i in range(min(3, len(file_dir.parents)))])

    # 4) 当前工作目录（容器 WORKDIR=/app 时命中）
    candidates.append(Path.cwd())

    for candidate in candidates:
        if candidate and (candidate / "config" / "default.yaml").is_file():
            return candidate
    # 都没命中时返回最可能的根，后续读取会给出明确告警
    return candidates[0] if candidates else file_dir


PROJECT_ROOT = _resolve_project_root()
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
    # DeepSeek：OpenAI 兼容协议，作为主模型 5xx 时的保底通道（Plan 5 答辩保障）
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"


class RAGConfig(BaseModel):
    """RAG 知识库配置（阶段5填充）"""

    embedding_model: str = "BAAI/bge-large-zh"
    reranker_model: str = "BAAI/bge-reranker-large"
    chunk_size: int = 512
    chunk_overlap: int = 64
    retrieval_top_k: int = 10
    rerank_top_k: int = 5
    # Chroma 服务地址（容器内走服务名；本地开发默认 localhost）
    chroma_host: str = "chroma"
    chroma_port: int = 8000


class AgentConfig(BaseModel):
    """Agent 配置（阶段3填充）"""

    max_concurrency: int = 4
    message_timeout: int = 60


class OrchestrationConfig(BaseModel):
    """调度器配置（阶段4填充）"""

    max_retry: int = 3
    retry_backoff_base: int = 1
    retry_backoff_factor: int = 3


class ReferenceVerificationConfig(BaseModel):
    """引用真实性核验配置（Issue #3 / 计划 §2.5）。

    核验本身不调用 LLM，只与外部权威元数据源（CrossRef / OpenAlex）比对；
    `mode` 决定核验结论对校验结果的影响力度：
    - ``off``：不追加核验检查（报告形状与历史版本一致）；
    - ``report``（默认）：advisory，只记录不参与通过判定，避免外部源波动误伤任务；
    - ``enforce``：未找到 / 元数据不符导致校验不通过（可定向重生成修复）。
    """

    enabled: bool = True
    mode: Literal["off", "report", "enforce"] = "report"
    intercept_unverified: bool = False
    """开启时未通过核验（元数据不符 / 未找到）的条目会被剔出参考文献列表。"""

    similarity_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    """标题相似度阈值：低于该值视为「未找到」（DOI 已解析成功时判「元数据不符」）。"""

    year_tolerance: int = Field(default=1, ge=0)
    cache_ttl_seconds: int = Field(default=604800, ge=0)
    """核验结论缓存 TTL（秒），默认 7 天。"""

    request_timeout_seconds: float = Field(default=20.0, gt=0.0)
    total_timeout_seconds: float = Field(default=20.0, gt=0.0)
    """单节点核验总预算（秒）：整体超预算时全部候选降级为「未核验」，不阻塞任务。"""

    max_concurrency: int = Field(default=2, ge=1)
    """核验外呼并发上限；未配 `contact_email` 时处于公共池（约 1 req/s），故默认压到 2。"""

    verify_offline_tasks: bool = False
    """离线清单任务（冻结语料）是否也做外部核验；默认 false（零外呼、可复现）。"""

    contact_email: str = ""
    """礼貌池联系方式；建议经环境变量注入，不写进代码仓库。"""

    user_agent: str = (
        "maa-literature-review/0.1 "
        "(+https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant)"
    )
    max_retries: int = Field(default=1, ge=0)


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
    reference_verification: ReferenceVerificationConfig = ReferenceVerificationConfig()
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
        "reference_verification",
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
