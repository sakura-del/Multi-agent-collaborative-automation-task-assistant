"""网关与站点安全响应头（Issue #11 M-4）。

## 现状（2026-11-11 安全排查实测）

`api.veripaper.me` 只有 `Strict-Transport-Security`（Cloudflare 加）与
`X-Content-Type-Options: nosniff`，**缺** `X-Frame-Options`、`Content-Security-Policy`、
`Referrer-Policy`、`Permissions-Policy`、COOP。

影响：API 响应可被任意站点嵌进 iframe（点击劫持）；缺 CSP 也就缺了 XSS 之后的
最后一道「能加载什么」的限制。

## 为什么用测试锁 nginx 配置

这类头没有「单元测试」可写，只能对**配置文件**做断言。线上漏一个头不会报错、
不会 500，只会静默少一层防护——正是最容易在后续改动中被删掉、又最难察觉的东西。

## 为什么 `/docs` 要单独一份策略

`/docs`（Swagger UI）与 `/redoc` 由 FastAPI 直接吐 HTML，脚本/样式来自
`cdn.jsdelivr.net` 且含内联脚本。全站 `default-src 'none'` 会把文档页打成白屏。
所以 CSP 用 `map $uri` 分流：默认最严，文档路径放宽到「只允许 jsdelivr + 内联」。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = REPO_ROOT / "deploy" / "ecs" / "nginx-api-tls.conf"
SITE_HEADERS = REPO_ROOT / "frontend" / "public" / "_headers"

# 每个反代 server 块都必须带的头（缺一个就少一层防护）
REQUIRED_HEADERS = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
    "Content-Security-Policy",
)


@pytest.fixture(scope="module")
def conf_text() -> str:
    assert NGINX_CONF.is_file(), f"缺少网关配置：{NGINX_CONF}"
    return NGINX_CONF.read_text(encoding="utf-8")


def _proxy_server_blocks(conf: str) -> list[str]:
    """切出所有「真正反代后端」的 server 块（80 与 443 两块）。

    不引第三方 nginx 解析器：这里只关心「块内有没有那几行 add_header」，
    按 `server {` 切段 + 括号计数足够，而且不会随 nginx 版本的语法细节失效。
    """
    blocks: list[str] = []
    for match in re.finditer(r"\bserver\s*\{", conf):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(conf)):
            if conf[index] == "{":
                depth += 1
            elif conf[index] == "}":
                depth -= 1
                if depth == 0:
                    block = conf[start : index + 1]
                    if "proxy_pass" in block:
                        blocks.append(block)
                    break
    return blocks


class TestGatewayHeaders:
    def test_both_http_and_https_blocks_proxy_backend(self, conf_text: str) -> None:
        """80 与 443 都要覆盖：Cloudflare 加密模式可能停在 Flexible，走的就是 80。"""
        blocks = _proxy_server_blocks(conf_text)
        assert len(blocks) == 2, f"期望 2 个反代 server 块（80/443），实际 {len(blocks)}"
        assert any("listen 80" in block for block in blocks)
        assert any("listen 443" in block for block in blocks)

    @pytest.mark.parametrize("header", REQUIRED_HEADERS)
    def test_each_proxy_block_sets_header(self, conf_text: str, header: str) -> None:
        blocks = _proxy_server_blocks(conf_text)
        missing = [i for i, block in enumerate(blocks) if f"add_header {header} " not in block]
        assert missing == [], f"第 {missing} 个反代 server 块缺少 {header}"

    def test_each_add_header_uses_always(self, conf_text: str) -> None:
        """必须带 `always`：否则 4xx/5xx（恰好是探测与攻击最常看到的响应）没有这些头。"""
        for block in _proxy_server_blocks(conf_text):
            for line in block.splitlines():
                stripped = line.strip()
                if not stripped.startswith("add_header "):
                    continue
                assert stripped.endswith("always;"), f"add_header 缺少 always：{stripped}"


class TestContentSecurityPolicy:
    def test_default_policy_denies_everything(self, conf_text: str) -> None:
        """API 响应是纯数据，不该被当成文档加载任何资源。"""
        default = re.search(r"map\s+\$uri\s+\$csp_policy\s*\{(?P<body>.*?)\n\}", conf_text, re.S)
        assert default is not None, "缺少 $csp_policy 的 map（用 map 才能让 /docs 单独放宽）"

        body = default.group("body")
        default_line = next(
            (line for line in body.splitlines() if line.strip().startswith("default ")), None
        )
        assert default_line is not None, "map 缺少 default 分支"
        assert "default-src 'none'" in default_line
        assert "frame-ancestors 'none'" in default_line

    def test_docs_paths_get_a_usable_policy(self, conf_text: str) -> None:
        """`/docs` 与 `/redoc` 必须命中放宽分支，否则 Swagger UI 白屏。"""
        match = re.search(r"map\s+\$uri\s+\$csp_policy\s*\{(?P<body>.*?)\n\}", conf_text, re.S)
        assert match is not None
        body = match.group("body")

        docs_lines = [
            line.strip() for line in body.splitlines() if "docs" in line or "redoc" in line
        ]
        assert docs_lines, "map 里没有为 /docs 或 /redoc 分流"

        joined = " ".join(docs_lines)
        assert "cdn.jsdelivr.net" in joined, "Swagger UI 的资源在 jsdelivr，必须放行"
        assert "'unsafe-inline'" in joined, "Swagger UI 含内联脚本/样式，必须放行 inline"


class TestSiteHeaders:
    def test_pages_site_sets_framing_and_referrer_headers(self) -> None:
        """前端站（Cloudflare Pages）用 `_headers` 下发；至少要有防嵌套与 referrer 策略。"""
        assert SITE_HEADERS.is_file(), "缺少 frontend/public/_headers"

        text = SITE_HEADERS.read_text(encoding="utf-8")
        assert "X-Frame-Options: DENY" in text
        assert "Referrer-Policy:" in text
        assert "Permissions-Policy:" in text
        assert "X-Content-Type-Options: nosniff" in text
