"""生产账号卫生脚本的纯逻辑（Issue #10）。

只测不依赖外部服务的函数：口令生成、测试账号识别、向量表选择、清理计划。
真实轮换与删除在服务器上按 runbook 执行（`--dry-run` 先看清单），不放进单测——
单测跑的是 SQLite，删不到生产库，写"集成测试"只会给人虚假的安全感。

这里守的是三条"删错就不可逆"的规则：

1. `demo` 永远不许被清理（它承载演示数据与全部历史）；
2. **任何 admin 都不许被清理**（`demo` 之外再多一个 admin 也要先人工确认）；
3. 名字不像测试账号的，即使出现在请求里也拒绝（防手抖把真实用户带进去）。
"""

from __future__ import annotations

import string

import pytest

from scripts.account_hygiene import (
    PROTECTED_USERNAMES,
    PurgePlan,
    UserRow,
    generate_password,
    is_test_account,
    plan_purge,
    select_vector_tables,
)


def _row(username: str, role: str = "user") -> UserRow:
    return UserRow(
        username=username,
        role=role,
        is_active=True,
        tasks=0,
        knowledge_docs=0,
        workspaces=0,
        api_keys=0,
        subscriptions=0,
        created_at="2026-10-17",
    )


class TestGeneratePassword:
    def test_default_length_is_long_enough(self) -> None:
        assert len(generate_password()) >= 32

    def test_respects_requested_length(self) -> None:
        assert len(generate_password(12)) == 12

    def test_contains_all_character_classes(self) -> None:
        """四类字符都要有——很多"复杂度校验"会据此拒绝。"""
        for _ in range(20):
            password = generate_password()
            assert any(ch.islower() for ch in password)
            assert any(ch.isupper() for ch in password)
            assert any(ch.isdigit() for ch in password)
            assert any(ch in "!@#$%^&*-_=+" for ch in password)

    def test_avoids_ambiguous_characters(self) -> None:
        """手抄到 .env 时 0/O、1/l/I 最容易抄错，直接不生成。"""
        allowed = set(string.ascii_letters + string.digits + "!@#$%^&*-_=+") - set("0O1lI")
        for _ in range(20):
            assert set(generate_password()) <= allowed

    def test_is_random(self) -> None:
        assert len({generate_password() for _ in range(20)}) == 20

    def test_rejects_too_short_length(self) -> None:
        with pytest.raises(ValueError):
            generate_password(8)


class TestIsTestAccount:
    @pytest.mark.parametrize(
        "username",
        [
            "exitgate1792241768",
            "literature-demo",
            "literature-demo7",
            "literature-gate2",
            "rh1792319889",
            "rh21792320556",
            "p192461901",
            "i293452392",
            "scr_71f1ba63",
            "final1792325980",
            "e2efull",
            "e2ereconn",
            "diag-user",
        ],
    )
    def test_matches_historical_test_accounts(self, username: str) -> None:
        assert is_test_account(username) is True

    @pytest.mark.parametrize(
        "username",
        [
            "demo",
            "alice",
            "rhino",  # 前缀像 rh，但不是 rh+数字
            "p1",
            "bob12345",
            "literature-reviewer",  # 真实用户可能就叫这个
        ],
    )
    def test_does_not_match_real_looking_accounts(self, username: str) -> None:
        assert is_test_account(username) is False


class TestPlanPurge:
    def test_targets_only_test_accounts(self) -> None:
        rows = [_row("exitgate1792241768"), _row("demo")]
        plan = plan_purge(rows, ["exitgate1792241768"])

        assert isinstance(plan, PurgePlan)
        assert [row.username for row in plan.targets] == ["exitgate1792241768"]
        assert plan.refusals == ()

    def test_refuses_protected_username(self) -> None:
        assert "demo" in PROTECTED_USERNAMES
        plan = plan_purge([_row("demo", role="admin")], ["demo"])

        assert plan.targets == ()
        assert any("受保护" in reason for reason in plan.refusals)

    def test_refuses_admin_even_if_not_protected(self) -> None:
        """第二个 admin 可能是真人运维建的，绝不能按"名字像测试账号"删掉。"""
        plan = plan_purge([_row("e2efull", role="admin")], ["e2efull"])

        assert plan.targets == ()
        assert any("admin" in reason for reason in plan.refusals)

    def test_refuses_unknown_username(self) -> None:
        """拼错名字时必须炸掉，而不是静默跳过——静默跳过会让人以为已经清理了。"""
        plan = plan_purge([_row("exitgate1792241768")], ["exitgate1792241769"])

        assert plan.targets == ()
        assert any("不存在" in reason for reason in plan.refusals)

    def test_refuses_real_looking_account(self) -> None:
        plan = plan_purge([_row("alice")], ["alice"])

        assert plan.targets == ()
        assert any("不像测试账号" in reason for reason in plan.refusals)

    def test_deduplicates_requested_usernames(self) -> None:
        plan = plan_purge([_row("e2efull")], ["e2efull", "e2efull"])

        assert [row.username for row in plan.targets] == ["e2efull"]

    def test_reports_all_refusals_at_once(self) -> None:
        """一次报全，避免"改一个跑一次"的来回。"""
        rows = [_row("demo", role="admin"), _row("alice")]
        plan = plan_purge(rows, ["demo", "alice", "ghost"])

        assert plan.targets == ()
        assert len(plan.refusals) == 3


class TestSelectVectorTables:
    def test_includes_all_prefixed_tables(self) -> None:
        """生产实际有两张向量表：新表 knowledge_vectors_bge 与切换前的 knowledge_vectors。

        两张都是裸表（没有外键指向知识文档），删文档不会级联清向量，
        漏掉任何一张都会留下孤儿向量——检索时命中已删除的文档。
        """
        tables = select_vector_tables(
            existing=["knowledge_vectors", "knowledge_vectors_bge", "doc_chunk"],
            configured="knowledge_vectors_bge",
        )

        assert tables == ("knowledge_vectors", "knowledge_vectors_bge")

    def test_always_includes_configured_table(self) -> None:
        tables = select_vector_tables(existing=["doc_chunk"], configured="knowledge_vectors_bge")

        assert tables == ("knowledge_vectors_bge",)

    def test_excludes_unrelated_tables(self) -> None:
        tables = select_vector_tables(
            existing=["knowledge_doc", "pgvector_test", "vector_index"],
            configured="knowledge_vectors_bge",
        )

        assert tables == ("knowledge_vectors_bge",)
