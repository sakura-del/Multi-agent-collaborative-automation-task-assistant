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
"""

from app.db.models.agent import Agent, AgentInteraction
from app.db.models.citation_style import CitationStyle
from app.db.models.email_token import EmailToken
from app.db.models.feedback import HumanFeedback
from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex
from app.db.models.log import ExecutionLog, FailureCase, ValidationResult
from app.db.models.task import Subtask, Task, TaskDag
from app.db.models.user import User
from app.db.models.user_template import UserTemplate

__all__ = [
    "Agent",
    "AgentInteraction",
    "CitationStyle",
    "DocChunk",
    "EmailToken",
    "ExecutionLog",
    "FailureCase",
    "HumanFeedback",
    "KnowledgeDoc",
    "Subtask",
    "Task",
    "TaskDag",
    "User",
    "UserTemplate",
    "ValidationResult",
    "VectorIndex",
]
