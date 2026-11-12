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

### C-1 用户上传的 Word 模板可触发 Jinja2 SSTI，进而远程执行命令 —— ✅ 已修复（2026-11-11）

> 修复提交 `5421a98`，生产镜像 `bd6cb7ad39f5`，Issue #9 已关闭。
> 详见文末「修复状态」。

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

### H-1 `demo` 弱口令仍可登录，且是 admin

- 实测登录成功，`role=admin`，access 60 分钟、refresh 7 天。
- 该口令（弱口令，8 位数字结尾）写在仓库 `AGENTS.md` 与多份文档里，等于公开。
- 影响：任何看过仓库/文档的人可直接接管系统（demo 名下 101 个任务、
  知识库 28341 分块、billing）。
- 修复：立即改为强随机密码并只保存在 `.env`/口令管理器；若必须保留演示入口，
  改为受限只读角色并单独限流。

> 2026-11-11 已修复：`demo` 口令轮换为 32 位强随机（只有服务器 600 文件与口令管理器持有），
> 旧口令实测 401，新口令 200 且 `role` 仍为 `admin`；仓库内所有明文口令已清除并被测试锁住。

### H-2 `e2efull` / `e2ereconn` 已知口令在生产有效

- 两个账号口令公开在 `AGENTS.md`，实测均能登录（200）。
- 生产库还残留 25 个历史测试账号（`exitgate*`、`literature-demo*`、`rh*`、`p1*`、
  `i2*`、`scr_*`），全部 `role=user`。
- 修复：轮换或删除 e2e 账号；清理历史测试账号；E2E 账号只允许测试环境使用。

> 2026-11-11 已修复：`e2efull` / `e2ereconn` 与 24 个历史测试账号已从生产库删除
> （含 `knowledge_vectors*` 孤儿向量清理）；E2E 用例改为只从环境变量读凭据。

---

## 三、Medium

### M-1 refresh token 轮换未使旧令牌失效

- 实测：用 refresh_token 换新令牌（`轮换=True`）后，**旧 refresh_token 再次使用仍返回 200**。
- 影响：若 refresh token 被窃取，即使合法用户刷新过一次，攻击者在 7 天内仍可继续
  获取新访问令牌，且无法通过"重新登录"踢掉。
- 修复：refresh 时把旧令牌加入黑名单（jti + TTL）或提升 `token_invalid_before`。

> 2026-11-12 已修复：refresh 令牌带唯一 `jti`，每次轮换把旧 `jti` 写入
> `revoked_refresh_token`（存活期 = 令牌自身 `exp`），旧令牌再用返回 **401**；
> 超过 60 秒宽限期仍被重放则认定泄露，作废该用户全部令牌。
> 修复前签发的令牌没有 `jti`，一律拒绝（需重新登录）。
> 实测证据见 `docs/runbooks/ecs-deployment.md` §15。

### M-2 限流主体是代理容器 IP，所有用户共享同一配额桶

- `RATE_LIMIT__TRUST_PROXY` 默认 `false`；生产在 nginx 之后，`request.client.host`
  恒为 nginx 容器内网 IP。实测 Redis key 为
  `ratelimit:3600:auth_register:ip:172.18.0.5`。
- 影响：注册 5 次/小时是**全局**配额，任意客户端耗尽后**所有真实用户 1 小时内无法注册**；
  登录 10 次/分钟同理。既是可用性缺陷，也是低成本 DoS 面。
- 修复：`RATE_LIMIT__TRUST_PROXY=true` **且** 源站只允许 Cloudflare 回源 IP
  （否则可伪造 `X-Forwarded-For` 绕过）。两者必须同时做。

> 2026-11-12 已修复：网关加 `set_real_ip_from`（22 个 Cloudflare 网段）+
> `real_ip_header CF-Connecting-IP`；应用只读网关覆盖写的 `X-Real-IP` 并校验合法性，
> **不读 `X-Forwarded-For`**。实测公网请求的限流 key 已是真实 IP（`13.125.5.87`），
> 伪造 XFF 不再产生新桶。证据见 `docs/runbooks/ecs-deployment.md` §17。

### M-3 登录限流 10 次/60 秒偏宽松且无账号锁定

- 实测 10 次失败登录未触发 429（限额内），11 次后才会限。bcrypt 有成本，
  但长期慢速爆破仍可行。建议登录规则收紧到 5/60s，并对同一账号做失败计数退避。

> 2026-11-12 已修复：登录限流收紧到 **5 次/60 秒**；新增账号级退避（失败 10 次锁 900 秒，
> 期间密码正确也拒绝，`reason=account_locked`），计数按规范化账号名（去空格+小写）哈希后存 Redis。
> 生产实测：连续 10 次失败后，正确密码返回 429 `account_locked`。证据见 `ecs-deployment.md` §17。

### M-4 API 缺少安全响应头

- `api.veripaper.me` 实测只有 `Strict-Transport-Security` 与
  `X-Content-Type-Options: nosniff`；**缺** `X-Frame-Options`、
  `Content-Security-Policy`、`Referrer-Policy`、`Permissions-Policy`、COOP/CORP。
- 影响：API 响应可被框架嵌入（点击劫持），缺 CSP 削弱 XSS 纵深防御。
- 修复：在 nginx 网关统一加 `X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、
  `Permissions-Policy` 与最小 CSP（API 可 `default-src 'none'`）。
  站点侧补 CSP（需与 Vite 产物兼容）。

> 2026-11-12 已修复：网关 80/443 两个反代块补齐 6 个头（全部 `always`），
> CSP 用 `map $uri` 分流（API `default-src 'none'`，`/docs` 放宽到 jsdelivr + CF 探针）；
> 站点通过 Pages `_headers` 下发 nosniff / X-Frame-Options / Referrer-Policy / Permissions-Policy。
> **站点 CSP 仍待办**（打崩风险需真实浏览器回归，方案见 `ecs-deployment.md` §16.5）。
> 实测证据见 `docs/runbooks/ecs-deployment.md` §16。

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
| UFW | **2026-11-12 已启用**：`default deny incoming`，只放行 22/80/443 | **注意 UFW 管不到 docker 发布的端口**（走 nat DNAT，不经 INPUT）；回源限制必须在 `DOCKER-USER` 链做，见下一行 |
| fail2ban | 未安装 | 建议安装，保护 SSH |
| SSH `X11Forwarding` / `MaxAuthTries` | **2026-11-12 已加固**：X11 关、`MaxAuthTries 3`、`LoginGraceTime 30`、`ClientAliveInterval 300/2` | 见 `ecs-deployment.md` §19.1（含 sshd 取值优先级说明） |
| `.env.pre-bge`、`.env.pre-m3-cutover` | **2026-11-12 已删除**（键名比对确认无唯一键丢失后 `shred` 覆盖） | 现仅剩 `.env`、`.env.example`；ext4 日志仍可能留旧块，彻底杜绝需轮换密钥 |
| 系统包 | **2026-11-12 已升 11 个**；剩 4 个（3 个 Ubuntu phasing + `cloud-init` 大版本跳跃），**均来自 `noble-updates`，非安全更新**（`apt-check`：0 条可立即安装的安全更新） | 无需重启（无内核更新） |
| **源站可绕过 Cloudflare 直连**（2026-11-12 排查时新增发现） | 实测 `DOCKER-USER` 链为空，任何人知道 `47.253.81.6` 即可直连源站，绕过 WAF / DDoS 清洗 / Bot 规则 / 按真实 IP 的限流 | **2026-11-12 已修**：`DOCKER-USER` 仅放行 22 个 Cloudflare 网段到 80/443，其余丢弃；systemd 单元随 docker 重放、每周刷新网段。第三方节点探测 **443 = 0/4 可连**（22 仍可连作对照） |
| `/openapi.json`、`/docs`、`/redoc` | 生产公开 | 对开放 API 产品可接受；如需收敛可用环境开关关闭 |
| CORS 允许 `*.multi-agent-assistant.pages.dev` + `allow-credentials` | 2026-11-12 已收紧：去掉 `www.veripaper.me`、标签必须以字母数字开头且有长度上限；白名单正则进仓库并有测试 | **残留**：同项目下任意单级别名仍被允许（预览部署需要），创建别名需 Cloudflare 项目权限，按 Low 接受 |
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
- **暴露面**：宿主机只监听 22/80/443，数据库/Redis 不对外；80/443 在 11-12 之后**只接受 Cloudflare 网段回源**（见 Low 表新增行）。
- **SSH**：`PasswordAuthentication no`、`PermitRootLogin without-password`、**3 把密钥**（`lingrove-root` / `hermes-codex` / `hatch`，第三把为后续加入）。
- **模板加固（除 SSTI 外）**：拒绝 `.docm` 宏、剥离外部关系（attachedTemplate /
  `TargetMode="External"` / altChunk）防 SSRF、`autoescape=True`。
- **限流**：注册 5/3600s 生效（第 6 次 429）；429 带 Retry-After。

---

## 六、修复优先级

| 优先级 | 事项 | 预计工作量 |
| --- | --- | --- |
| P0 | C-1 模板 SSTI：改用 SandboxedEnvironment + 白名单校验 + 回归测试 | 小 |
| P0 | ~~H-1/H-2 轮换 demo 与 e2e 密码，清理历史测试账号~~ **已完成 2026-11-11** | 小（需改数据） |
| P1 | ~~M-1 refresh 旧令牌失效~~ **已完成 2026-11-12** | 中 |
| P1 | ~~M-2 限流真实 IP + 源站仅允许 Cloudflare~~ **已完成 2026-11-12** | 中（改配置 + 网关） |
| P1 | ~~M-4 安全响应头~~ **API/站点已修（2026-11-12），站点 CSP 待办** | 小 |
| P2 | ~~M-5 依赖升级~~ **2026-11-12 处置完毕**（现查 OSV 只剩 3 个无修复版本的包，chromadb 已移出生产镜像，见 `dependency-risk.md`） | 中 |
| P2 | ~~M-3 登录限流收紧~~ **已完成 2026-11-12**（5/60s + 账号退避） | 小 |
| P3 | L 系列主机加固 | 小 |

---

## 附：本次测试副作用与清理

- 注册限流测试占用了 `ratelimit:3600:auth_register:ip:172.18.0.5` 计数，
  已 `DEL` 清理（核查后 `ratelimit:*` 数量为 0，`quota:*` 未受影响）。
- 未创建任何账号、未上传模板、未修改业务数据。
- 所有 payload 均在本地沙箱验证，未在生产执行命令。

---

## 修复状态（2026-11-11）

### C-1 模板 SSTI —— 已修复并生产验证

**提交**：`5421a98 fix: block template ssti leading to rce`

| 层 | 改动 |
| --- | --- |
| 上传校验 | `inspector.py` 新增 `_validate_jinja_expressions`，白名单只放行 `{{ identifier }}`；`{% ... %}` 控制标签一律拒绝 |
| 渲染 | `renderer.py` 改用 `jinja_env=SandboxedEnvironment(autoescape=True)` 阻断 `__class__` / `__globals__` 逃逸 |
| 测试 | 新增 `tests/test_template_ssti_security.py`（14 条） |

**门禁**：black / ruff / mypy 通过；`pytest --cov-fail-under=80` → **3043 passed, 21 skipped, 89.62%**（903.9s）。
**诚实性验证**：回退两处修复后 10 条测试变红，恢复后 14 条全绿。

**生产验证**（镜像 `bd6cb7ad39f5`，容器 healthy，公网 ready）：

| 攻击载荷 | 结果 |
| --- | --- |
| `{{ 7 * 7 }}` | **400** `含不允许的模板表达式：{{ 7 * 7 }}` |
| 完整逃逸链（`__mro__` → `_wrap_close` → `os.popen`） | **400** 拒绝 |
| `{% if flag %}x{% endif %}` | **400** 拒绝 |

正常模板（`{{ title }}` / `{{ author }}`）**201 创建成功**，占位符识别为 `author`、`title`；测试模板已删除。

**回滚锚点**：`veripaper-backend:shadow-mock`（`79ac91a4374c`）保留。

### 其余项

H-1 / H-2（凭据，Issue #10）**已于 2026-11-11 修复**（见第二节各条下的「已修复」记录，
证据见 `docs/runbooks/ecs-deployment.md` §14）；Medium、Low 仍**未修复**，按 Issue #11 清单继续。
