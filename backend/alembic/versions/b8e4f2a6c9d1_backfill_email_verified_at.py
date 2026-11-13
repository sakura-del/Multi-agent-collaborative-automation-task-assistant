"""回填历史用户的 email_verified_at（邮箱验证门禁前置，2026-11-13）。

## 背景

登录新增「邮箱必须已验证」门禁（``EMAIL__REQUIRE_VERIFIED_LOGIN=true``）。
``email_verified_at`` 列上线于 Phase 1 P1-C，但**从未被强制**——登录不检查它，
前端注册后还自动登录。真实发信通道（Resend SMTP）2026-11-13 才接入，
所以迁移时刻**已存在的用户不可能验证过邮箱**：他们要么是邮件通道启用前注册的
真实用户，要么是管理员/测试账号。若不回填，门禁一开就把所有人锁在外面。

## 口径

- **回填 = now()**：把「门禁生效前的存量用户」视为已验证；
- 门禁生效后注册的用户由应用写真实验证时间，不经过本迁移；
- 门禁默认关闭（``require_verified_login=False``），本地/CI 不受本迁移影响。

Revision ID: b8e4f2a6c9d1
Revises: a7d3e9b6c1f4
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4f2a6c9d1"
down_revision: str | None = "a7d3e9b6c1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CURRENT_TIMESTAMP 而非 now()：迁移测试与 CI 跑在 SQLite 上，now() 是 PG 专用。
    op.execute(
        sa.text(
            'UPDATE "user" SET email_verified_at = '
            "COALESCE(email_verified_at, CURRENT_TIMESTAMP) "
            "WHERE email_verified_at IS NULL"
        )
    )


def downgrade() -> None:
    # 不可逆：无法区分「迁移回填的验证时间」与「用户真实点击邮件的验证时间」。
    # 门禁按配置开关控制，降级时关闭配置即可，不动数据。
    pass
