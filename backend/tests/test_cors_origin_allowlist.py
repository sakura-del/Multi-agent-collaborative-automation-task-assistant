"""CORS 允许来源白名单（Issue #11 Low，2026-11-12 收紧）。

## 背景

安全排查实测：`https://evil.multi-agent-assistant.pages.dev` 能拿到
`access-control-allow-origin`。原因是正则写成了「任意单级子域」。

为什么不能干脆只允许正式域名：Cloudflare Pages **每次部署都会生成新的预览子域**
（`<hash|branch>.multi-agent-assistant.pages.dev`），逐条枚举不现实；
而 `allow_credentials=True` 时浏览器禁止 `*`。

## 本次收紧的实际动作

1. `www.veripaper.me` 从白名单移除——它对根域是 301，浏览器里的 Origin 只会是根域，
   留着等于多开一扇没用的门；
2. 预览别名只允许**单级**、必须以字母或数字开头、长度有上限（原来是 `[a-z0-9-]+`，
   允许以 `-` 开头这种非法 DNS 形态）。

## 残留风险（明确写下来，不假装解决了）

同项目下的**任意**别名仍然被允许（例如别人在 Pages 里新建一个分支别名）。
Cloudflare 侧创建别名需要项目权限，因此这条按「Low + 依赖平台权限」接受，
不再为了消除它而牺牲预览域名——预览通道本身是发版前的验证手段。
"""

from __future__ import annotations

import re

import pytest

from app.core.config import settings


@pytest.fixture(scope="module")
def pattern() -> re.Pattern[str]:
    regex = settings.cors.allow_origin_regex
    assert regex, "CORS 白名单正则不能为空（空 = 不限制来源）"
    assert regex.strip() != ".*", "不能使用通配：allow_credentials=True 时等于全开"
    return re.compile(regex)


class TestAllowedOrigins:
    @pytest.mark.parametrize(
        "origin",
        [
            "https://veripaper.me",
            "https://multi-agent-assistant.pages.dev",
            "https://abc12345.multi-agent-assistant.pages.dev",
            "https://feature-csp.multi-agent-assistant.pages.dev",
        ],
    )
    def test_allowed(self, pattern: re.Pattern[str], origin: str) -> None:
        assert pattern.fullmatch(origin) is not None, f"应当允许：{origin}"

    def test_credentials_enabled_requires_explicit_origins(self) -> None:
        """允许携带凭据时必须有明确白名单——这条是上面正则断言的前提。"""
        assert settings.cors.allow_credentials is True


class TestRejectedOrigins:
    @pytest.mark.parametrize(
        "origin",
        [
            "https://evil.example.com",
            "http://veripaper.me",  # 只允许 https
            "https://www.veripaper.me",  # 301 到根域，浏览器 Origin 不会是它
            "https://veripaper.me.evil.com",
            "https://multi-agent-assistant.pages.dev.evil.com",
            "https://other-project.pages.dev",
            "https://a.b.multi-agent-assistant.pages.dev",  # 多级子域
            "https://-bad.multi-agent-assistant.pages.dev",  # 非法标签（以 - 开头）
        ],
    )
    def test_rejected(self, pattern: re.Pattern[str], origin: str) -> None:
        assert pattern.fullmatch(origin) is None, f"不应允许：{origin}"
