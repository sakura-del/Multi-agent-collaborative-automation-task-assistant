"""ORM 模型聚合导出。

阶段0任务2：13 张核心表的 ORM 定义。
按业务聚合分文件，统一从此处导出，便于 Alembic 自动发现 metadata。

表清单：
  user.py:        USER
  task.py:        TASK / SUBTASK / TASK_DAG
  agent.py:       AGENT / AGENT_INTERACTION
  log.py:         EXECUTION_LOG / VALIDATION_RESULT / FAILURE_CASE
  feedback.py:    HUMAN_FEEDBACK
  knowledge.py:   KNOWLEDGE_DOC / DOC_CHUNK / VECTOR_INDEX
  citation_style.py: CITATION_STYLE（Issue #4 M2：用户自定义 CSL 样式）
  email_token.py: EMAIL_TOKEN（Phase 1 P1-C：邮箱验证/密码重置令牌）
  user_template.py: USER_TEMPLATE（Phase 1 P1-A：用户上传的 Word 模板）
  screening.py:   SEARCH_LOG / SCREENING_RECORD / SCREENING_DECISION
                  （Phase 2：PRISMA 筛选工作台——检索日志/候选快照/追加式决策）
  workspace.py:   WORKSPACE / WORKSPACE_MEMBER / WORKSPACE_INVITE
                  （Phase 2：团队工作区 + 席位 + 邀请码）
  annotation.py:  TASK_ANNOTATION（Phase 2：导师批注，段落指纹锚点）
  api_key.py:     API_KEY（Phase 2：开放 API / MCP 凭证，只存哈希）
"""

from app.db.models.agent import Agent, AgentInteraction
from app.db.models.annotation import TaskAnnotation
from app.db.models.api_key import ApiKey
from app.db.models.citation_style import CitationStyle
from app.db.models.email_token import EmailToken
from app.db.models.feedback import HumanFeedback
from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex
from app.db.models.log import ExecutionLog, FailureCase, ValidationResult
from app.db.models.screening import (
    ScreeningDecision,
    ScreeningRecord,
    SearchLog,
)
from app.db.models.task import Subtask, Task, TaskDag
from app.db.models.user import User
from app.db.models.user_template import UserTemplate
from app.db.models.workspace import Workspace, WorkspaceInvite, WorkspaceMember

__all__ = [
    "Agent",
    "AgentInteraction",
    "ApiKey",
    "CitationStyle",
    "DocChunk",
    "EmailToken",
    "ExecutionLog",
    "FailureCase",
    "HumanFeedback",
    "KnowledgeDoc",
    "ScreeningDecision",
    "ScreeningRecord",
    "SearchLog",
    "Subtask",
    "Task",
    "TaskAnnotation",
    "TaskDag",
    "User",
    "UserTemplate",
    "ValidationResult",
    "VectorIndex",
    "Workspace",
    "WorkspaceInvite",
    "WorkspaceMember",
]
