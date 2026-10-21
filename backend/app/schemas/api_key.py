"""API Key 相关 Pydantic 契约（Phase 2 · 开放 API）。

## 响应模型的关键区别

| 模型 | 含明文 | 用途 |
| --- | --- | --- |
| `ApiKeyCreated` | **是**（`key` 字段） | 只在 `POST /api-keys` 的 201 响应 |
| `ApiKeyOut` | **否** | 列表与后续任何读取 |

拆成两个模型而不是「一个模型 + 可选字段」：后者容易在列表里意外带上明文
（字段默认 None 但序列化时仍出现），而**独立模型让「列表不可能有明文」成为
类型层面的保证**。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApiKeyScope = Literal["tasks:read", "tasks:write", "artifacts:read", "annotations:write"]


class ApiKeyCreate(BaseModel):
    """创建 Key 请求。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, description="用户可见名称")
    scopes: list[ApiKeyScope] | None = Field(
        default=None,
        description="权限范围；不传则默认 [tasks:read, tasks:write]",
    )
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650, description="有效天数；不传=永久"
    )


class ApiKeyOut(BaseModel):
    """Key 元数据（**绝不含明文**）。"""

    model_config = ConfigDict(from_attributes=True)

    key_id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    """创建响应：**唯一**含明文的模型。

    `key` 只在这里出现一次 —— 前端必须立刻展示 + 提供复制按钮，
    用户关掉弹窗后就再也拿不到了（数据库只有哈希）。
    """

    key: str = Field(description="明文 Key，**只返回一次**，请立即保存")
