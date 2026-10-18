"""Compose 发布冒烟客户端（Plan 5 Task 3）。

在**网络内部**跑完整用户链路（不依赖宿主机端口、不依赖真实模型密钥）：
健康检查 → 注册登录 → 上传 TXT → 创建离线任务 → 确认规划 → 等待完成
→ 下载 Markdown 与 DOCX → 校验产物非空与引用标记。

任一环节不符合预期即以非零码退出，便于 CI/发布流程直接判断。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
import zipfile
from io import BytesIO
from typing import Any

import httpx

BASE_URL = os.getenv("BASE_URL", "http://nginx").rstrip("/")
READY_TIMEOUT = float(os.getenv("SMOKE_READY_TIMEOUT", "120"))
TASK_TIMEOUT = float(os.getenv("SMOKE_TASK_TIMEOUT", "600"))
PASSWORD = "smoke-password-2026"


class SmokeError(RuntimeError):
    """冒烟断言失败。"""


def _fail(message: str) -> SmokeError:
    return SmokeError(message)


def wait_ready(client: httpx.Client) -> dict[str, Any]:
    """等待就绪探针通过（最多 READY_TIMEOUT 秒）。"""
    deadline = time.monotonic() + READY_TIMEOUT
    last = "unknown"
    while time.monotonic() < deadline:
        try:
            response = client.get(f"{BASE_URL}/api/v1/health/ready", timeout=5.0)
            if response.status_code == 200:
                payload = response.json()
                checks = payload.get("checks", {})
                print(f"readiness ok: {checks}")
                return payload
            last = f"{response.status_code}"
        except httpx.HTTPError as exc:  # 容器启动中连接会被拒绝
            last = type(exc).__name__
        time.sleep(2.0)
    raise _fail(f"就绪探针超时（{READY_TIMEOUT}s），最后状态: {last}")


def register_and_login(client: httpx.Client) -> tuple[str, dict[str, str]]:
    username = f"smoke{int(time.time())}{uuid.uuid4().hex[:4]}"
    client.post(
        f"{BASE_URL}/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
        timeout=30.0,
    )
    response = client.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"account": username, "password": PASSWORD},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise _fail(f"登录失败: {response.status_code} {response.text[:200]}")
    return username, {"Authorization": f"Bearer {response.json()['access_token']}"}


def upload_document(client: httpx.Client, headers: dict[str, str]) -> dict[str, Any]:
    body = (
        "检索增强生成（RAG）通过外部知识检索降低大模型的事实性错误。"
        "忠实度与上下文相关性是核心评估维度。"
    ).encode()
    response = client.post(
        f"{BASE_URL}/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("smoke.txt", body, "text/plain")},
        data={"title": "冒烟样本文档", "strategy": "recursive"},
        timeout=120.0,
    )
    if response.status_code != 201:
        raise _fail(f"上传失败: {response.status_code} {response.text[:200]}")
    document = response.json()
    if document["index_status"] != "ready":
        raise _fail(f"文档未就绪: {document['index_status']}")
    print(f"uploaded document: {document['id']} chunks={document['chunk_count']}")
    return document


def wait_status(
    client: httpx.Client,
    headers: dict[str, str],
    task_id: str,
    expected: set[str],
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=headers, timeout=30.0)
        if response.status_code != 200:
            raise _fail(f"查询任务失败: {response.status_code}")
        last = response.json()
        if last["status"] in expected:
            return last
        if last["status"] in {"failed", "cancelled"}:
            raise _fail(f"任务提前终止: {last['status']}")
        time.sleep(2.0)
    raise _fail(f"等待状态 {expected} 超时，当前 {last.get('status')}")


def create_and_run_task(client: httpx.Client, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        f"{BASE_URL}/api/v1/tasks",
        headers=headers,
        json={
            "topic": "检索增强生成证据核验冒烟",
            "keywords": ["smoke"],
            "max_documents": 3,
            "offline_dataset": True,
        },
        timeout=60.0,
    )
    if response.status_code != 201:
        raise _fail(f"创建任务失败: {response.status_code} {response.text[:200]}")
    task_id = response.json()["task_id"]
    print(f"created task: {task_id}")

    snapshot = wait_status(client, headers, task_id, {"waiting_approval"}, timeout=TASK_TIMEOUT)
    approved = client.post(
        f"{BASE_URL}/api/v1/tasks/{task_id}/plan/approve",
        headers=headers,
        json={"revision": snapshot["revision"]},
        timeout=60.0,
    )
    if approved.status_code != 200:
        raise _fail(f"确认规划失败: {approved.status_code} {approved.text[:200]}")

    final = wait_status(client, headers, task_id, {"completed"}, timeout=TASK_TIMEOUT)
    return final


def verify_artifacts(client: httpx.Client, headers: dict[str, str], final: dict[str, Any]) -> None:
    task_id = final["task_id"]
    report = final.get("review_report") or {}
    markdown = report.get("final_markdown", "")
    if "[E" not in markdown:
        raise _fail("最终综述缺少 [E{n}] 引用标记")

    artifacts = final.get("artifacts") or []
    if len(artifacts) < 2:
        raise _fail(f"产物数量不足: {len(artifacts)}")

    kinds: set[str] = set()
    for artifact in artifacts:
        response = client.get(
            f"{BASE_URL}/api/v1/tasks/{task_id}/artifacts/{artifact['artifact_id']}",
            headers=headers,
            timeout=120.0,
        )
        if response.status_code != 200:
            raise _fail(f"下载产物失败 {artifact['kind']}: {response.status_code}")
        payload = response.content
        if not payload:
            raise _fail(f"产物为空: {artifact['kind']}")
        kinds.add(artifact["kind"])
        if artifact["kind"] == "docx":
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise _fail("DOCX 不是合法 Word 文档")
        print(f"artifact {artifact['kind']}: {len(payload)} bytes")

    if kinds != {"markdown", "docx"}:
        raise _fail(f"产物类型不全: {sorted(kinds)}")

    events = client.get(
        f"{BASE_URL}/api/v1/tasks/{task_id}/events", headers=headers, timeout=30.0
    ).json()["events"]
    event_ids = [item["event_id"] for item in events]
    if event_ids != sorted(event_ids) or len(set(event_ids)) != len(event_ids):
        raise _fail("事件 ID 不是严格递增/唯一")
    print(f"events: {len(event_ids)} (strictly increasing)")


def main() -> int:
    token_id = uuid.uuid4().hex[:6]
    print(f"smoke run {token_id} against {BASE_URL}")
    with httpx.Client(timeout=30.0) as client:
        try:
            wait_ready(client)
            username, headers = register_and_login(client)
            print(f"registered user: {username}")
            upload_document(client, headers)
            final = create_and_run_task(client, headers)
            verify_artifacts(client, headers, final)
        except SmokeError as exc:
            print(f"SMOKE FAILED: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"SMOKE ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    print(
        "SMOKE OK "
        + json.dumps(
            {
                "task_id": final["task_id"],
                "status": final["status"],
                "artifacts": len(final["artifacts"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
