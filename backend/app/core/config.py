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
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


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

    name: str = "VeriPaper"
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
    # ===== 迁移到 Supabase 后新增（本地 postgres 保持默认即可）=====
    # Supabase 强制 TLS：开启后给 asyncpg 传 `ssl="require"`（DSN 里已写 sslmode 时不重复加）
    ssl_require: bool = False
    # transaction pooler（6543）不支持 prepared statement：
    # 开启后 asyncpg 传 statement_cache_size=0，
    # 同时让 SQLAlchemy 方言传 prepared_statement_cache_size=0。
    disable_prepared_statements: bool = False


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
    # local = 本机 SentenceTransformers；siliconflow = 托管 Embeddings API
    embedding_provider: str = "local"
    siliconflow_api_key: str | None = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    # 免费 BGE-M3 的 2000 RPM / 500000 TPM 只使用 80%，给在线查询留余量
    embedding_rpm: int = Field(default=1600, gt=0)
    embedding_tpm: int = Field(default=400000, gt=0)
    embedding_batch_size: int = Field(default=64, gt=0)
    reranker_model: str = "BAAI/bge-reranker-large"
    chunk_size: int = 512
    chunk_overlap: int = 64
    retrieval_top_k: int = 10
    rerank_top_k: int = 5
    # Chroma 服务地址。**默认空 = 不连服务端**（本地嵌入/内存模式），
    # 只有 docker-compose 显式注入 RAG__CHROMA_HOST=chroma 时才走 HTTP 客户端。
    # 历史缺陷：默认值曾是 "chroma"（容器 DNS 名），导致本地/CI 试图解析不存在的主机。
    chroma_host: str = ""
    chroma_port: int = 8000
    # ===== 向量后端选择（2026-11 迁移 Supabase pgvector）=====
    # chroma = 旧实现（回滚开关）；pgvector = Supabase PostgreSQL + pgvector。
    # **默认 chroma**：升级本身不改变既有环境行为，切换是显式动作。
    vector_backend: str = "chroma"
    # pgvector 连接串。留空 → 复用 database.url（整库迁 Supabase 后同源，少一套配置）。
    # ⚠️ 只写 .env，不写进代码/文档；支持 pooler DSN（6543）。
    pgvector_dsn: str = ""
    pgvector_table: str = "knowledge_vectors"
    # 索引类型：hnsw（默认，无需训练）/ ivfflat（省内存，需按数据量调 lists）/ none
    pgvector_index: str = "hnsw"
    pgvector_hnsw_m: int = 16
    pgvector_hnsw_ef_construction: int = 64
    pgvector_ivfflat_lists: int = 100
    # 同步连接池：向量检索是同步调用，池大小按并发检索量给（不是按请求量）
    pgvector_pool_min_size: int = 1
    pgvector_pool_max_size: int = 5
    # 15s：实测国内→Supabase eu-west-1 单次建连 6~8s，5s 会让池一直取不到连接
    pgvector_pool_timeout: float = 15.0
    pgvector_connect_timeout: int = 15
    # Supabase 的 statement_timeout 兜底：慢查询不应把请求线程长期占住
    pgvector_statement_timeout_ms: int = 15000
    # 留空 = 由 DSN 决定；Supabase 建议 "require"
    pgvector_sslmode: str = ""
    # 默认关闭 prepared statement（psycopg 传 prepare_threshold=None）。两个原因：
    # 1) transaction pooler（6543）不支持命名预编译语句；
    # 2) **通用计划用不上 HNSW 有序扫描**——`ORDER BY embedding <=> $1` 的距离依赖参数值，
    #    服务端一旦走 generic plan 就退化成 Seq Scan + Sort（2026-11-03 Supabase 实测：
    #    常量字面量能走 Index Scan，非恒定表达式走 Sort）。每次现解析的代价远小于全表排序。
    pgvector_disable_prepared_statements: bool = True
    # 仅集成测试/临时库：允许运行时建表建索引（生产由 alembic 负责）
    pgvector_auto_migrate: bool = False


class AgentConfig(BaseModel):
    """Agent 配置（阶段3填充）"""

    max_concurrency: int = 4
    message_timeout: int = 60


class EmailConfig(BaseModel):
    """邮件发送与账号生命周期配置（Phase 1 P1-C）。

    设计原则：**未配置 SMTP 时绝不阻塞注册**——`sender="logging"`（默认）只把
    验证/重置链接写进日志，前端仍可用；配置 SMTP 后自动切换到真实投递。
    """

    sender: Literal["logging", "smtp"] = "logging"
    from_address: str = "no-reply@localhost"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""  # 只进 .env，不要写进 yaml
    smtp_use_tls: bool = True
    verify_token_ttl_hours: int = 24
    reset_token_ttl_hours: int = 24
    """token 有效期（小时）。过期 token 一律拒绝。"""
    frontend_base_url: str = "http://localhost:5173"
    """用于拼接前端确认页链接（`{base}/verify-email?token=...`）。"""


class RateLimitRule(BaseModel):
    """单条限流规则（Phase 1 P1-D）：窗口内允许次数 + 窗口长度。"""

    limit: int = Field(default=60, ge=0)
    """窗口内允许请求数；``0`` = 全部拒绝。"""

    window_seconds: int = Field(default=60, ge=1)


class RateLimitRules(BaseModel):
    """各动作独立限额。字段固定（便于用 `RATE_LIMIT__RULES__AUTH_LOGIN__LIMIT` 覆盖）。

    ⚠️ 每条规则都必须有实际路由使用（`tests/test_rate_limit_coverage_and_quota.py`
    双向校验：所有写端点有规则、所有规则标签存在）。
    """

    auth_login: RateLimitRule = RateLimitRule(limit=5, window_seconds=60)
    """登录：60 秒 5 次（Issue #11 M-3，原 10 次）。

    实测 10 次/60 秒时「10 次失败都不触发 429」，第 11 次才限——慢速爆破一天能试 7200 次。
    收紧到 5 次后配合账号维度的失败退避（见 `login_failure_limit`）才形成两层防护。
    """
    auth_register: RateLimitRule = RateLimitRule(limit=5, window_seconds=3600)
    task_create: RateLimitRule = RateLimitRule(limit=30, window_seconds=3600)
    task_control: RateLimitRule = RateLimitRule(limit=60, window_seconds=3600)
    """任务确认/人工控制/WS ticket：高频交互操作，限额相对宽松。"""

    upload: RateLimitRule = RateLimitRule(limit=20, window_seconds=3600)
    search: RateLimitRule = RateLimitRule(limit=60, window_seconds=60)
    verify: RateLimitRule = RateLimitRule(limit=30, window_seconds=60)
    template_write: RateLimitRule = RateLimitRule(limit=20, window_seconds=3600)
    """模板上传/删除：要解压校验 docx（zip 解析 + XML 扫描），不宜高频。"""

    template_render: RateLimitRule = RateLimitRule(limit=10, window_seconds=60)
    """模板渲染：跑 docxtpl + python-docx 重打包，单次开销最大，限额最严。"""


class RateLimitConfig(BaseModel):
    """速率限制与按用户配额（Phase 1 P1-D）。

    设计原则：**Redis 不可用时全部 fail-open 放行**——限流是旁路能力，
    组件故障绝不能拖垮业务请求。
    """

    enabled: bool = True
    backend: Literal["redis", "memory"] = "redis"
    """`redis` 多进程共享；`memory` 单进程（测试/单机）。"""

    key_prefix: str = "ratelimit"
    trust_proxy: bool = False
    """是否信任网关传来的客户端 IP。默认 False（直连 / 本地开发）。

    开启后只读 **`X-Real-IP`**（由 nginx 无条件覆盖写），**不读 `X-Forwarded-For`**：
    XFF 的第一段是客户端自己写的，信它等于让任何人换一个限流桶就绕过限额。
    开启前提：源站只接受可信反代（Cloudflare）回源，见 `deploy/ecs/nginx-api-tls.conf`
    的 `set_real_ip_from` 与 `ecs-deployment.md` §17。
    """

    login_failure_limit: int = Field(default=10, ge=1)
    """同一账号在窗口内允许的连续失败次数，超过即临时锁定（Issue #11 M-3）。"""

    login_failure_window_seconds: int = Field(default=900, ge=60)
    """失败计数窗口 / 锁定时长（秒）。默认 15 分钟。"""

    rules: RateLimitRules = RateLimitRules()


class QuotaConfig(BaseModel):
    """按用户 token 配额（Phase 1 P1-D）。"""

    enabled: bool = True
    daily_token_limit: int = Field(default=1_000_000, ge=-1)
    """每日 token 上限；``-1`` = 不限制，``0`` = 全拒。与 llm.cost_budget 数值对齐。"""

    window_seconds: int = Field(default=86_400, ge=1)
    """配额窗口长度；默认 86400 = 按 **UTC 日** 重置。"""

    key_prefix: str = "quota"


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
    """礼貌池联系方式。⚠️ 受已知配置优先级缺陷影响（YAML 作为 init 参数优先级高于环境变量），
    当前只能改 `config/default.yaml`；写 `REFERENCE_VERIFICATION__CONTACT_EMAIL` 环境变量不生效。"""

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
    #: 按正则放行（用于 Cloudflare Pages 的 `<hash>.<project>.pages.dev` 预览域名）。
    #: 为什么不能只靠 allow_origins：预览域名每次部署都变，逐个枚举不可维护；
    #: 而 `allow_credentials=True` 时浏览器禁止 `*`。**必须限定到本项目前缀**。
    allow_origin_regex: str = ""
    allow_credentials: bool = True
    allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    allow_headers: list[str] = Field(default_factory=lambda: ["*"])
    #: 跨域时需要暴露给前端的响应头。`Content-Disposition` 不带上的话，
    #: 前端拿不到下载文件名（浏览器默认只暴露 6 个安全头）。
    expose_headers: list[str] = Field(default_factory=lambda: ["Content-Disposition"])


class StorageConfig(BaseModel):
    """文档存储配置：本地磁盘 / S3 兼容对象存储（七牛云 Kodo、Cloudflare R2）。

    **默认 local**：不配置任何变量时行为与引入对象存储之前完全一致，
    单元测试与开源本地部署都不需要云凭据。

    七牛云的两处硬性要求（否则报 SignatureDoesNotMatch）：

    - 签名版本必须是 `s3v4`；
    - 寻址必须是 **virtual-hosted**（`bucket.endpoint/key`），path 样式不被接受。
    """

    backend: str = "local"  # local | s3
    s3_endpoint: str = ""
    s3_region: str = "cn-east-1"
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    #: 对象键前缀（最终 key = `{prefix}/{user_id}/{uuid}{ext}`）
    s3_prefix: str = "uploads"
    s3_addressing_style: str = "virtual"
    #: 预签名直链有效期（秒）。S3 协议上限 604800（7 天）。
    presign_expires_seconds: int = 900


# ===== 顶层 Settings =====


class Settings(BaseSettings):
    """全局配置入口。

    加载顺序（**优先级由低到高**）：
    1. 类字段默认值
    2. `config/default.yaml`（仅作为默认值基线）
    3. `.env` 文件
    4. 系统环境变量（嵌套字段用 `__` 分隔）

    历史缺陷（P1，2026-10-19 修复）：旧实现 `Settings(**yaml_defaults)` 把 yaml 当作
    **init 参数**传入，而 pydantic-settings 中 init 参数优先级最高，导致
    「yaml 压过 .env / 环境变量」——Issue #3 的 `contact_email` 因此只能手改 yaml。
    现改为 `settings_customise_sources` 里插入**最低优先级**的 yaml 源。
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
    email: EmailConfig = EmailConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    quota: QuotaConfig = QuotaConfig()
    cors: CORSConfig = CORSConfig()
    storage: StorageConfig = StorageConfig()

    # ===== JWT 鉴权（阶段0任务3） =====
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7
    jwt_refresh_reuse_grace_seconds: int = Field(default=60, ge=0)
    """旧 refresh 被重放时的宽限期（秒），见 Issue #11 M-1。

    为什么需要：浏览器开两个标签页时，access 过期会让两边**同时**发起刷新，
    第二个必然拿到 401。若一律按「令牌被窃取」处理并作废整个家族，用户每小时
    都会被自己的浏览器踢下线。宽限期内只拒绝这次重放、不连坐；
    超过宽限期仍被重放才认定是窃取，作废该用户全部令牌。``0`` = 关闭宽限（最严格）。
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        """把 `default.yaml` 插到**最低优先级**（仅高于类默认值）。

        pydantic-settings 按返回顺序依次尝试，**先命中者赢**。因此
        「env → dotenv → yaml」保证环境变量能覆盖 yaml，而 yaml 仍作为基线生效。
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlDefaultsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator(
        "app",
        "database",
        "redis",
        "llm",
        "rag",
        "agent",
        "orchestration",
        "reference_verification",
        "email",
        "rate_limit",
        "quota",
        "cors",
        "storage",
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


class _YamlDefaultsSource(PydanticBaseSettingsSource):
    """把 `config/default.yaml` 作为**最低优先级**配置源（P1 缺陷修复）。

    为什么不用 `Settings(**yaml_defaults)`：那是 init 参数，优先级高于环境变量，
    会让 `.env` / 部署环境变量彻底失效（Issue #3 的 `contact_email` 即受此害）。
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        # 本源一次性返回整个 yaml 字典，故逐字段读取不走这里
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return _load_yaml_defaults()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。

    使用 lru_cache 保证全应用共享同一份配置对象，
    避免重复读取文件。测试时调用 `get_settings.cache_clear()` 重置。

    yaml 基线由 `Settings.settings_customise_sources` 以最低优先级注入，
    因此 `.env` 与系统环境变量可以正常覆盖它（P1 缺陷修复）。
    """
    return Settings()


# 便于直接 `from app.core.config import settings`
settings = get_settings()
