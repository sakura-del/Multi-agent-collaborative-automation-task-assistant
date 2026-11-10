"""DSN 选项拼装：含空格的值必须编码成 %20，不能是 `+`。

2026-11-10 真机事故：pgvector 连接串由 `with_options` 追加
`options=-c statement_timeout=15000`，`urlencode` 把空格编成 `+`，
psycopg 按 RFC 3986 解析后得到 `-c+statement_timeout=15000`，
libpq 把它当作名为 `+statement_timeout` 的参数 →
`FATAL: unrecognized configuration parameter "+statement_timeout"`，
健康检查直接 `vector: failed`（503）。
"""

from __future__ import annotations

from psycopg.conninfo import conninfo_to_dict

from app.knowledge.services.vector_backends.dsn import with_options


class TestWithOptions:
    def test_space_in_value_is_percent_encoded(self) -> None:
        dsn = with_options("postgresql://u:p@h:5432/db", options="-c statement_timeout=15000")

        query = dsn.partition("?")[2]
        assert "+" not in query
        assert "statement_timeout" in query
        # 关键：psycopg 解析回来必须与传入值一致（不多出 `+`）
        assert conninfo_to_dict(dsn)["options"] == "-c statement_timeout=15000"

    def test_existing_query_keys_are_kept(self) -> None:
        dsn = with_options(
            "postgresql://u:p@h:5432/db?sslmode=require",
            options="-c statement_timeout=1000",
            connect_timeout=5,
        )

        parsed = conninfo_to_dict(dsn)
        assert parsed["sslmode"] == "require"
        assert parsed["connect_timeout"] == "5"
        assert parsed["options"] == "-c statement_timeout=1000"

    def test_none_values_are_skipped(self) -> None:
        dsn = with_options("postgresql://u:p@h:5432/db", sslmode=None, options="")

        assert "?" not in dsn or dsn.endswith("?")
        assert "sslmode" not in dsn
