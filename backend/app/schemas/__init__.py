"""Pydantic 请求/响应模型层（Schema 层）。

按业务模块分子文件，统一从此处导出，便于路由层引用。
与 ORM 模型分离，避免数据库结构直接暴露给 API。
"""

from __future__ import annotations
