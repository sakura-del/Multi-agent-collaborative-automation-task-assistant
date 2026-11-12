"""锁住「测试不得写到容器绝对路径」。

背景（2026-11-12 主干 CI 第一次真正跑单测时暴露）：`storage.upload_root` 的生产默认值是
容器内路径 `/app/data/uploads`，`init_kb()` 构造存储时会 mkdir 它。
GitHub runner 没有写 `/app` 的权限 → `PermissionError`。

**这个 bug 在本地是绿的**：Windows 上 `/app` 会解析成 `C:/app`，恰好可创建。
所以只用一条断言把它钉住：测试环境下存储根必须是可写的临时目录。
"""

from __future__ import annotations

from app.core.config import get_settings


class TestStorageRootsAreWritableInTests:
    def test_upload_root_not_container_path(self) -> None:
        root = str(get_settings().app.upload_root)
        assert not root.startswith(
            "/app"
        ), f"upload_root={root} 仍是容器内路径；测试应写到临时目录（见 conftest）"

    def test_artifact_root_not_container_path(self) -> None:
        root = str(get_settings().app.artifact_root)
        assert not root.startswith(
            "/app"
        ), f"artifact_root={root} 仍是容器内路径；测试应写到临时目录（见 conftest）"
