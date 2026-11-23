"""公开健康检查不得被 SPA 吞掉（Issue #29）。

## 背景

`https://veripaper.me/api/health` 曾返回 200 + 首页 HTML（`_redirects` 的
`/* /index.html 200` 把它兜走了），后端挂了对外探针仍显示正常。修复分两层：

1. 前端 `_redirects` 把 `/api/*` 反代到 `api.veripaper.me`（见
   `frontend/src/pages-redirects.test.ts`）；
2. 后端提供**不带版本前缀**的 `/api/health` 存活别名——否则反代过去还是 404。

这里锁第 2 层：`/api/health` 必须返回 JSON（而不是 404/HTML），
且不能依赖 DB/Redis（存活探针挂了依赖也要能答）。
"""

from __future__ import annotations


def test_api_health_returns_json(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "VeriPaper"
    assert "version" in body


def test_versioned_health_still_works(client) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
