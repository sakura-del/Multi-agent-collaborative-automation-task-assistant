# 生产安全排查报告（2026-11-11）

> 范围：`veripaper.me` / `api.veripaper.me` 公网面、认证与会话、授权与多租户、
> 注入与模板、密钥与主机、依赖漏洞。
> 方法：只读探测 + 代码审计 + 本地沙箱复现。**未在生产执行任何破坏性测试**，
> 未创建/修改任何业务数据；测试期间占用的限流计数已清理。

## 结论

**当前不能认为"安全有保证"**：存在 **1 个 Critical（远程命令执行）**，
以及 2 个高危凭据问题。其余为中低危配置加固项。

### Issue 跟踪

| Issue | 级别 | 标题 |
| --- | --- | --- |
| [#9](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/9) | Critical | 用户上传 Word 模板可触发 SSTI，导致远程命令执行 |
| [#10](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/10) | High | demo 与 e2e 账号已知密码在生产有效，另有 25 个历史测试账号 |
| [#11](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/11) | 汇总 | 生产安全排查结果与修复计划（含全部 Medium/Low 清单） |

---

## 一、Critical

### C-1 用户上传的 Word 模板可触发 Jinja2 SSTI，进而远程执行命令

- **位置**：`backend/app/tools/templates/`（`inspector.py` 校验、`renderer.py` 渲染）
- **根因**：`docxtpl` 使用默认 Jinja2 `Environment`（非 SandboxedEnvironment）。
  现有校验只做三件事：包结构/宏拒绝、段落级标签校验、抽取占位符用于契约校验，
  **都不限制模板里的 Jinja 表达式**。
- **实测（本地沙箱，无害回显）**：
  - `{{ 7 * 7 }}` → 输出 `49`（任意表达式被执行）
  - `{{ (''.__class__.__mro__[1].__subclasses__()|selectattr('__name__','equalto','_wrap_close')|first).__init__.__globals__['__builtins__']['__import__']('os').popen('echo ...').read() }}`
    → **命令成功执行**，输出回显
  - 该 payload 的 `find_undeclared_variables()` 结果为 `set()`，
    因此**契约校验完全拦不住**（契约只校验"占位符是否都有值"）
- **影响**：任何认证用户（注册即可）上传恶意 `.docx` 模板并调用渲染，
  即以 `appuser` 身份在 backend 容器执行任意命令 → 可读取容器内 `.env`
  全部密钥（数据库/JWT/七牛/SiliconFlow）、访问数据库、横向影响所有用户数据。
- **修复（已验证有效）**：
  1. **根本修复**：`DocxTemplate.render(context, jinja_env=SandboxedEnvironment(autoescape=True))`。
     实测直接抛 `SecurityError: access to attribute '__class__' of 'str' object is unsafe`，
     且正常占位符渲染不受影响。
  2. **纵深防御**：上传时对 Word XML 中的 `{{ }}` / `{% %}` 做白名单正则，
     只允许 `{{ identifier }}`、`{%p identifier %}`、`{%tr identifier %}`。
     实测：正常模板 0 拒绝；恶意表达式与 `{{ 7*7 }}` 均被拒。
  3. 补回归测试：恶意模板必须被拒，正常模板必须仍可渲染。

---

## 二、High

### H-1 `demo` / `demo12345` 弱口令仍可登录，且是 admin

- 实测登录成功，`role=admin`，access 60 分钟、refresh 7 天。
- 该密码写在仓库 `AGENTS.md` 与多份文档里，等于公开。
- 影响：任何看过仓库/文档的人可直接接管系统（demo 名下 101 个任务、
  知识库 28341 分块、billing）。
- 修复：立即改为强随机密码并只保存在 `.env`/口令管理器；若必须保留演示入口，
  改为受限只读角色并单独限流。

### H-2 `e2efull` / `e2ereconn` 已知密码在生产有效

- 两个账号密码 `e2e-password-2026` 公开在 `AGENTS.md`，实测均能登录（200）。
- 生产库还残留 25 个历史测试账号（`exitgate*`、`literature-demo*`、`rh*`、`p1*`、
  `i2*`、`scr_*`），全部 `role=user`。
- 修复：轮换或删除 e2e 账号；清理历史测试账号；E2E 账号只允许测试环境使用。

---

## 三、Medium

### M-1 refresh token 轮换未使旧令牌失效

- 实测：用 refresh_token 换新令牌（`轮换=True`）后，**旧 refresh_token 再次使用仍返回 200**。
- 影响：若 refresh token 被窃取，即使合法用户刷新过一次，攻击者在 7 天内仍可继续
  获取新访问令牌，且无法通过"重新登录"踢掉。
- 修复：refresh 时把旧令牌加入黑名单（jti + TTL）或提升 `token_invalid_before`。

### M-2 限流主体是代理容器 IP，所有用户共享同一配额桶

- `RATE_LIMIT__TRUST_PROXY` 默认 `false`；生产在 nginx 之后，`request.client.host`
  恒为 nginx 容器内网 IP。实测 Redis key 为
  `ratelimit:3600:auth_register:ip:172.18.0.5`。
- 影响：注册 5 次/小时是**全局**配额，任意客户端耗尽后**所有真实用户 1 小时内无法注册**；
  登录 10 次/分钟同理。既是可用性缺陷，也是低成本 DoS 面。
- 修复：`RATE_LIMIT__TRUST_PROXY=true` **且** 源站只允许 Cloudflare 回源 IP
  （否则可伪造 `X-Forwarded-For` 绕过）。两者必须同时做。

### M-3 登录限流 10 次/60 秒偏宽松且无账号锁定

- 实测 10 次失败登录未触发 429（限额内），11 次后才会限。bcrypt 有成本，
  但长期慢速爆破仍可行。建议登录规则收紧到 5/60s，并对同一账号做失败计数退避。

### M-4 API 缺少安全响应头

- `api.veripaper.me` 实测只有 `Strict-Transport-Security` 与
  `X-Content-Type-Options: nosniff`；**缺** `X-Frame-Options`、
  `Content-Security-Policy`、`Referrer-Policy`、`Permissions-Policy`、COOP/CORP。
- 影响：API 响应可被框架嵌入（点击劫持），缺 CSP 削弱 XSS 纵深防御。
- 修复：在 nginx 网关统一加 `X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、
  `Permissions-Policy` 与最小 CSP（API 可 `default-src 'none'`）。
  站点侧补 CSP（需与 Vite 产物兼容）。

### M-5 依赖存在 21 条 HIGH/CRITICAL 公告，需按可达性处置

按 OSV 实测（168 个包）。逐条判断可达性后：

| 包 | 最高分 | 可达性判断 | 建议 |
| --- | --- | --- | --- |
| `python-jose` 3.5.0 | 9.5（算法混淆） | **不可利用**：`decode_token` 已锁定 `algorithms=[HS256]`，无公钥混淆路径 | 计划升级 |
| `chromadb` 1.5.9 | 9.5（代码注入/RBAC） | **生产不可达**：向量后端为 pgvector，无 chroma 服务暴露 | 评估移除依赖 |
| `certifi` 2026.7.22 | 9.5（CA 供应链） | 可达：用于校验外部 HTTPS | **优先升级** |
| `aiohttp` / `urllib3` | 8.0 | 部分可达（外部 HTTP 调用） | 建议升级 |
| `cryptography` / `pyasn1` / `ecdsa` | 8.0 | 不可达（HS256 不走 ASN.1/PKCS#7/ECDSA） | 随版本升级 |
| `oauthlib` / `dnspython` / `filelock` | 8.0 | 当前未使用相关功能 | 随版本升级 |

---

## 四、Low

| 项 | 现状 | 建议 |
| --- | --- | --- |
| UFW | `inactive`，iptables 默认 ACCEPT | 依赖阿里云安全组；建议启用 UFW 仅放行 22/80/443 |
| fail2ban | 未安装 | 建议安装，保护 SSH |
| SSH `X11Forwarding` / `MaxAuthTries 6` | 开启 / 6 | 关 X11，降到 3 |
| `.env.pre-bge`、`.env.pre-m3-cutover` | 明文密钥备份，600 | 确认回滚结束后删除 |
| 系统包 | 10 个可升级 | 评估含安全更新的部分 |
| `/openapi.json`、`/docs`、`/redoc` | 生产公开 | 对开放 API 产品可接受；如需收敛可用环境开关关闭 |
| CORS 允许 `*.multi-agent-assistant.pages.dev` + `allow-credentials` | 实测 `evil.*` 子域也被放行 | 风险低（子域需项目权限才能创建）；可收紧为精确域名 |
| 登录/密保等端点错误信息 | 统一"账号或密码错误"（无用户枚举） | ✅ 已正确 |

---

## 五、做得对的部分（已实测确认）

- **传输**：TLS 1.2/1.3 可用，HTTP→HTTPS 301，HSTS `max-age=15768000; includeSubDomains`。
- **未认证访问**：`/tasks`、`/workspaces`、`/auth/me`、`/api-keys`、`/templates` 等全部 401。
- **跨用户隔离**：普通用户读他人 task/workspace/events/bib 均返回 **404**；
  admin 也不能跨用户读取（严格隔离，符合设计）。
- **JWT**：篡改 `role=admin` 被拒；access 不能冒充 refresh；算法锁定 HS256。
- **用户枚举**：不存在账号与错误密码返回完全相同的 401 响应体。
- **密钥**：`.env` 权限 600 root:root；JWT 64 位、PG 密码 32 位、七牛 40 位，
  无占位符；backend 日志近 24h 无密钥字样。
- **容器**：全部非特权、backend 以 `appuser` 运行、无 `docker.sock` 挂载。
- **暴露面**：宿主机只监听 22/80/443，数据库/Redis 不对外。
- **SSH**：`PasswordAuthentication no`、`PermitRootLogin without-password`、仅 2 把密钥。
- **模板加固（除 SSTI 外）**：拒绝 `.docm` 宏、剥离外部关系（attachedTemplate /
  `TargetMode="External"` / altChunk）防 SSRF、`autoescape=True`。
- **限流**：注册 5/3600s 生效（第 6 次 429）；429 带 Retry-After。

---

## 六、修复优先级

| 优先级 | 事项 | 预计工作量 |
| --- | --- | --- |
| P0 | C-1 模板 SSTI：改用 SandboxedEnvironment + 白名单校验 + 回归测试 | 小 |
| P0 | H-1/H-2 轮换 demo 与 e2e 密码，清理历史测试账号 | 小（需改数据） |
| P1 | M-1 refresh 旧令牌失效 | 中 |
| P1 | M-2 限流真实 IP + 源站仅允许 Cloudflare | 中（改配置 + 网关） |
| P1 | M-4 安全响应头 | 小 |
| P2 | M-5 依赖升级（先 certifi，再其余） | 中 |
| P2 | M-3 登录限流收紧 | 小 |
| P3 | L 系列主机加固 | 小 |

---

## 附：本次测试副作用与清理

- 注册限流测试占用了 `ratelimit:3600:auth_register:ip:172.18.0.5` 计数，
  已 `DEL` 清理（核查后 `ratelimit:*` 数量为 0，`quota:*` 未受影响）。
- 未创建任何账号、未上传模板、未修改业务数据。
- 所有 payload 均在本地沙箱验证，未在生产执行命令。
