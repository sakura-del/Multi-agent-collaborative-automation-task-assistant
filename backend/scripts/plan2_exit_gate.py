"""Plan 2 Exit Gate：可重启端到端任务执行验收脚本。

验收链路（真实 PostgreSQL + Redis，走认证 HTTP 接口）：
    Phase 1  完整链路：注册/登录 → POST /tasks → WAITING_APPROVAL → approve
             → 持久化 Scheduler 执行 → COMPLETED → 事件回放
    Phase 2  崩溃恢复：任务执行到 n2 运行中时取消后台执行（模拟进程崩溃）
             → 重建 RuntimeContainer + TaskExecution → recover()
             → n1 不重跑、遗留 n2 重置为 READY、任务最终 COMPLETED

用法（服务器）：
    DATABASE__URL="postgresql+asyncpg://maa:***@localhost:35433/multi_agent_assistant" \
    REDIS__URL="redis://localhost:6379/0" \
    JWT_SECRET_KEY="..." \
    .venv/bin/python scripts/plan2_exit_gate.py

本地冒烟（文件型 SQLite，无 Redis 时自动回退内存事件总线）：
    DATABASE__URL="sqlite+aiosqlite:///./exit_gate.db" \
    JWT_SECRET_KEY="ci-test-secret-not-for-production" \
    .venv/bin/python scripts/plan2_exit_gate.py --init-schema

退出码：0 = 全部断言通过；1 = 存在失败（打印失败原因）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# 允许以 `python scripts/plan2_exit_gate.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx


def _patch_sqlite_pool() -> None:
    """本地冒烟用：SQLite 默认 StaticPool 让请求与后台任务共享单连接，
    事务会互相回滚；这里替换为每 session 独立连接的 NullPool。

    服务器验收走 PostgreSQL，本函数不生效。
    """
    import os

    url = os.getenv("DATABASE__URL", "")
    if not url.startswith("sqlite"):
        return

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db.session as db_session

    db_session.engine = create_async_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 10},
        poolclass=NullPool,
    )
    db_session.AsyncSessionLocal = async_sessionmaker(
        bind=db_session.engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


_patch_sqlite_pool()

from app.main import app  # noqa: E402
from app.orchestration.agents import ReviewerAgent, ValidatorAgent  # noqa: E402
from app.orchestration.agents.planner import PlannerAgent  # noqa: E402
from app.orchestration.base_agent import BaseAgent  # noqa: E402
from app.orchestration.runtime import RuntimeContainer  # noqa: E402
from app.orchestration.services.task_execution import TaskExecution  # noqa: E402
from tests.llm.deterministic_llm import DeterministicPlanLLM  # noqa: E402

PASSWORD = "exit-gate-password-123"
REQUEST_TIMEOUT = 30.0


# ===== 确定性 Agent =====


class _RecordingExecutor(BaseAgent):
    """记录节点执行次数；可对指定节点阻塞（模拟执行中被中断）。"""

    def __init__(self, *, block_node: str | None = None) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._block_node = block_node
        self._gate = asyncio.Event()

    @property
    def agent_type(self) -> str:
        return "executor"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        node_id = input_data["node_id"]
        self.calls.append(node_id)
        if node_id == self._block_node:
            await self._gate.wait()
        return {"result": f"确定性结果-{node_id}"}


def _agent_factory(executor: BaseAgent):
    """构造 Agent 工厂：确定性执行器 + 宽松校验器 + 固定复盘器。"""

    def factory(agent_type: str) -> Any:
        if agent_type == "executor":
            return executor
        if agent_type == "validator":
            return ValidatorAgent(rules={"min_length": 1, "required_keywords": []})
        if agent_type == "reviewer":
            return ReviewerAgent()
        raise ValueError(f"未知 agent_type: {agent_type}")

    return factory


# ===== HTTP 辅助 =====


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client: httpx.AsyncClient, username: str) -> str:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": PASSWORD,
        },
    )
    if register.status_code not in (200, 201):
        raise AssertionError(f"注册失败: {register.status_code} {register.text}")
    login = await client.post(
        "/api/v1/auth/login",
        json={"account": username, "password": PASSWORD},
    )
    if login.status_code != 200:
        raise AssertionError(f"登录失败: {login.status_code} {login.text}")
    return login.json()["access_token"]


async def _create_task(client: httpx.AsyncClient, token: str, topic: str) -> str:
    response = await client.post(
        "/api/v1/tasks",
        json={"topic": topic, "keywords": ["RAG", "证据核验"]},
        headers=_auth(token),
    )
    if response.status_code != 201:
        raise AssertionError(f"创建任务失败: {response.status_code} {response.text}")
    return response.json()["task_id"]


async def _snapshot(client: httpx.AsyncClient, token: str, task_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token))
    if response.status_code != 200:
        raise AssertionError(f"查询快照失败: {response.status_code} {response.text}")
    return response.json()


async def _wait_status(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
    expected: set[str],
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await _snapshot(client, token, task_id)
        if last["status"] in expected:
            return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"等待状态 {expected} 超时，当前 {last.get('status')}")


async def _wait_nodes(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
    expected: dict[str, str],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await _snapshot(client, token, task_id)
        states = {row["node_id"]: row["status"] for row in last["subtasks"]}
        if all(states.get(node) == status for node, status in expected.items()):
            return last
        await asyncio.sleep(0.05)
    states = {row["node_id"]: row["status"] for row in last.get("subtasks", [])}
    raise AssertionError(f"等待节点状态 {expected} 超时，当前 {states}")


async def _events(client: httpx.AsyncClient, token: str, task_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/v1/tasks/{task_id}/events?after=0", headers=_auth(token))
    if response.status_code != 200:
        raise AssertionError(f"事件回放失败: {response.status_code} {response.text}")
    return response.json()["events"]


def _attempts(snapshot: dict[str, Any]) -> dict[str, int]:
    return {row["node_id"]: row["retry_count"] for row in snapshot["subtasks"]}


# ===== 验收流程 =====


async def run(init_schema: bool) -> dict[str, Any]:
    if init_schema:
        from app.db.base import Base
        from app.db.session import engine

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    report: dict[str, Any] = {"phases": [], "failures": []}
    username = f"exitgate{int(time.time())}"

    async with app.router.lifespan_context(app):
        container: RuntimeContainer = app.state.container
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://exit-gate", timeout=REQUEST_TIMEOUT
        ) as client:
            token = await _register_and_login(client, username)
            report["username"] = username

            # ---------- Phase 1：完整链路 ----------
            executor = _RecordingExecutor()
            container.use_planner(lambda: PlannerAgent(llm=DeterministicPlanLLM()))
            container.use_agent_factory(_agent_factory(executor))

            task_id = await _create_task(client, token, "检索增强生成证据核验（退出验收）")
            waiting = await _wait_status(client, token, task_id, {"waiting_approval"})
            approve = await client.post(
                f"/api/v1/tasks/{task_id}/plan/approve",
                json={"revision": waiting["revision"]},
                headers=_auth(token),
            )
            if approve.status_code != 200:
                raise AssertionError(f"确认规划失败: {approve.status_code} {approve.text}")
            completed = await _wait_status(client, token, task_id, {"completed"})
            events = await _events(client, token, task_id)

            phase1 = {
                "phase": "full_run",
                "task_id": task_id,
                "final_status": completed["status"],
                "plan_version": completed["plan_version"],
                "node_statuses": {r["node_id"]: r["status"] for r in completed["subtasks"]},
                "node_attempts": _attempts(completed),
                "executor_calls": list(executor.calls),
                "event_sequence": [
                    {
                        "event_id": e["event_id"],
                        "event_type": e["event_type"],
                        "status": e["status"],
                    }
                    for e in events
                ],
            }
            report["phases"].append(phase1)
            if completed["status"] != "completed":
                report["failures"].append("Phase 1 未完成")
            if sorted(executor.calls) != ["n1", "n2", "n3"]:
                report["failures"].append(f"Phase 1 执行节点异常: {executor.calls}")
            if not any(e["event_type"] == "task_completed" for e in events):
                report["failures"].append("Phase 1 缺少 task_completed 事件")

            # ---------- Phase 2：崩溃恢复 ----------
            crash_executor = _RecordingExecutor(block_node="n2")
            container.use_agent_factory(_agent_factory(crash_executor))
            crash_task_id = await _create_task(client, token, "崩溃恢复验收任务")
            crash_waiting = await _wait_status(client, token, crash_task_id, {"waiting_approval"})
            await client.post(
                f"/api/v1/tasks/{crash_task_id}/plan/approve",
                json={"revision": crash_waiting["revision"]},
                headers=_auth(token),
            )
            # n1 完成、n2 进入运行中后模拟崩溃
            await _wait_nodes(client, token, crash_task_id, {"n1": "completed", "n2": "running"})
            before_crash = await _snapshot(client, token, crash_task_id)
            await container.cancel(uuid.UUID(crash_task_id))
            await container.close()
            await asyncio.sleep(0.2)
            crashed = await _snapshot(client, token, crash_task_id)

            # 重建运行时（同库）并恢复
            recovered_executor = _RecordingExecutor()
            container2 = RuntimeContainer()
            await container2.start()
            container2.use_planner(lambda: PlannerAgent(llm=DeterministicPlanLLM()))
            container2.use_agent_factory(_agent_factory(recovered_executor))
            execution2 = TaskExecution(container2)
            app.state.container = container2
            app.state.runtime = execution2
            await execution2.recover()

            recovered = await _wait_status(
                client, token, crash_task_id, {"completed"}, timeout=90.0
            )
            recovered_events = await _events(client, token, crash_task_id)
            recovery_events = [e for e in recovered_events if e["event_type"] == "task_recovery"]

            phase2 = {
                "phase": "crash_recovery",
                "task_id": crash_task_id,
                "status_before_crash": before_crash["status"],
                "node_statuses_before_crash": {
                    r["node_id"]: r["status"] for r in before_crash["subtasks"]
                },
                "status_after_crash": crashed["status"],
                "node_statuses_after_crash": {
                    r["node_id"]: r["status"] for r in crashed["subtasks"]
                },
                "first_runtime_executor_calls": list(crash_executor.calls),
                "recovered_runtime_executor_calls": list(recovered_executor.calls),
                "final_status": recovered["status"],
                "node_attempts": _attempts(recovered),
                "recovery_events": [
                    {"event_id": e["event_id"], "payload": e["payload"]} for e in recovery_events
                ],
                "event_count": len(recovered_events),
            }
            report["phases"].append(phase2)

            if recovered["status"] != "completed":
                report["failures"].append("Phase 2 恢复后未完成")
            if "n1" in recovered_executor.calls:
                report["failures"].append("Phase 2 重复执行了已完成的 n1")
            if "n2" not in recovered_executor.calls or "n3" not in recovered_executor.calls:
                report["failures"].append("Phase 2 未执行剩余节点 n2/n3")
            if _attempts(recovered)["n1"] != 0:
                report["failures"].append("Phase 2 n1 尝试次数不为 1")
            if not recovery_events:
                report["failures"].append("Phase 2 缺少 task_recovery 事件")
            elif recovery_events[0]["payload"].get("reset_nodes") != ["n2"]:
                report["failures"].append(
                    f"Phase 2 reset_nodes 异常: {recovery_events[0]['payload']}"
                )

            await container2.close()

    report["ok"] = not report["failures"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan 2 Exit Gate 验收脚本")
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="本地冒烟时先建表（服务器 PostgreSQL 已迁移，无需该参数）",
    )
    args = parser.parse_args()

    report = asyncio.run(run(init_schema=args.init_schema))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
