# Cloudflare Pages 部署（前端静态产物 + Tunnel 暴露 API）

> 目标：前端跑在 `https://<project>.pages.dev`（免费、可商用、全球 CDN），
> 后端仍在现有服务器，通过 Cloudflare Tunnel 暴露为 `https://api.<你的域名>`。
>
> 状态（2026-11-04）：**代码与部署脚本已就绪，尚未上线**——
> 卡在两件事：① 缺少域名 / API Token / Tunnel Token；② 服务器当前**出网异常**
> （pypi、github 全部 `http=000`），后端镜像无法重建，而 CORS 改动需要新镜像。

---

## 1. 为什么不能直接把 dist 传上去就完事

| 问题 | 现象 | 原因 |
| --- | --- | --- |
| 接口 404 | 登录、列表全部失败 | axios 原来写死相对 `/api/v1`，Pages 上没有这个路径 |
| 混合内容拦截 | 浏览器控制台报 `Mixed Content` | Pages 是 HTTPS，后端是 `http://10.0.10.2:48080` |
| 外网不可达 | 请求超时 | `10.0.10.2` 是内网地址 |
| 深链 404 | 刷新 `/tasks/<id>` 白屏 | vue-router 用 history 模式，静态站需要回退到 `index.html` |
| 跨域被拒 | 预检 `400` | 后端 CORS 只放行 localhost |
| 下载没文件名 | 产物下载成了 `download` | `Content-Disposition` 默认不对跨域暴露 |
| WS 连不上 | 任务进度不刷新 | WS URL 原来用 `window.location.host`（会打到 Pages 域名上） |

对应改动（已提交 `5d1728e` / `8569b0e`）：`src/config/endpoint.ts`、
`client.ts`、`task-events.ts`、`public/_redirects`、后端 `CORSConfig` 与 `main.py`。

---

## 2. 前置准备（一次性）

### 2.1 域名与 Tunnel

1. 域名托管在 Cloudflare（NS 已指向 Cloudflare）；
2. Zero Trust → Networks → **Tunnels** → Create a tunnel（Cloudflared 类型）；
3. 记下 **Tunnel Token**（形如 `eyJhIjoi...`，只显示一次）；
4. 在 Tunnel 的 Public Hostname 里加：
   - Subdomain `api`，Domain `你的域名`
   - Service `HTTP` → `localhost:48080`（nginx 已处理 `/api` 与 WS Upgrade）

### 2.2 Cloudflare API Token（Page 部���用）

Dashboard → My Profile → API Tokens → Create Token → 自定义权限：

- **Account → Cloudflare Pages → Edit**
- **Account → Account Settings → Read**

同时记下 **Account ID**（Dashboard 右侧栏）。

> ⚠️ 三个凭据（Tunnel Token、API Token、Account ID）只写进 600 权限的文件或进程环境变量，
> **不要**贴进对话、不要提交到仓库。

---

## 3. 后端配置

`.env`（服务器，600）：

```dotenv
# 放行 Pages 生产域名与预览域名（预览域名形如 abc123.<project>.pages.dev）
CORS__ALLOW_ORIGIN_REGEX=^https://([a-z0-9-]+\.)?multi-agent-assistant\.pages\.dev$
```

**坑（本次踩过）**：`docker-compose.yml` 的 `environment` 是**白名单**——
`.env` 里配了但 compose 没透传，容器里读不到，预检直接 `400`。
已在 compose 里加上 `CORS__ALLOW_ORIGIN_REGEX: ${CORS__ALLOW_ORIGIN_REGEX:-}`。

`expose_headers=["Content-Disposition"]` 由代码默认提供，无需额外配置。

---

## 4. 构建与部署

本机**没有 npm/npx**（只有 node.exe），所以用零依赖脚本
`scripts/deploy-pages.mjs`（Pages Direct Upload REST API）。

```powershell
$env:API_BASE_URL   = "https://api.example.com"
$env:CF_API_TOKEN   = "<从 600 文件读，不要写进命令行历史>"
$env:CF_ACCOUNT_ID  = "<account id>"
$env:PAGES_PROJECT  = "multi-agent-assistant"
node scripts/deploy-pages.mjs
```

脚本做的事：

1. `node <vue-tsc> -b` 类型检查 → `node <vite> build`（注入 `VITE_API_BASE_URL` / `VITE_WS_BASE_URL`）；
2. 校验 `dist/_redirects` 存在（SPA 深链依赖）；
3. 项目不存在则创建；按文件 SHA-256 组装 `manifest` + multipart 直传；
4. 打印部署 URL 与别名。

**`--dry-run`**：只构建不上传，用来验证「变量是否内联进产物」

```powershell
$env:API_BASE_URL = "https://api.example.com"; node scripts/deploy-pages.mjs --dry-run
# 然后确认产物里确实有该域名：
Select-String -Path frontend\dist\assets\*.js -Pattern "api\.example\.com"
```

> ⚠️ `VITE_` 变量是**构建期内联**：换域名必须重新构建，改运行期配置无效。

---

## 5. 验收清单（缺一不可）

| # | 检查 | 命令 / 方式 |
| --- | --- | --- |
| 1 | API 公网可达且 ready | `curl https://api.<域名>/api/v1/health/ready` |
| 2 | CORS 预检放行 | `curl -i -X OPTIONS https://api.<域名>/api/v1/tasks -H "Origin: https://<project>.pages.dev" -H "Access-Control-Request-Method: GET"` → 200 且 `access-control-allow-origin` 命中 |
| 3 | 异域仍被拒 | 换成 `https://evil.example.com` → **无** `access-control-allow-origin` |
| 4 | 下载头可读 | 实际 GET 响应含 `access-control-expose-headers: Content-Disposition` |
| 5 | 深链刷新 | 直接打开并刷新 `https://<project>.pages.dev/tasks/<id>` 不 404 |
| 6 | 登录 + 列表 + 检索 | `E2E_BASE_URL=https://<project>.pages.dev` 跑 Playwright（每文件只登录一次，避开 5 次/小时限流） |
| 7 | WS 实时进度 | 任务详情页状态变为「已连接」，执行中能看到事件推进（`wss://api.<域名>`） |
| 8 | 同源部署零回归 | 不注入 env 重新构建一次，nginx 下功能照旧 |

---

## 6. 回滚

| 层面 | 动作 |
| --- | --- |
| Pages 版本 | Dashboard → Deployments → 选上一版本 Promote to production |
| 隧道 | `sudo cloudflared service uninstall`（前端可回退到 nginx 同源访问） |
| 前端代码 | `git revert 5d1728e`（默认行为本就未变，风险极低） |
| CORS | 删掉 `.env` 里的 `CORS__ALLOW_ORIGIN_REGEX` 并重建容器 |

---

## 7. 限额与商业使用

| 项 | 免费版 |
| --- | --- |
| 商业使用 | **允许** |
| 静态请求 / 带宽 | 不限量 |
| 构建次数 | 500 次/月（本项目用本地构建+直传，不消耗 CI 构建） |
| 单文件大小 | 25 MB |
| 站点文件数 | 20,000 |
| Cloudflare Tunnel | 免费，无 SLA；上传大文件受 100MB 请求体上限约束 |

---

## 8. 已知风险

- **服务器出网不稳**：2026-11-04 实测服务器访问 pypi / github 均 `http=000`（TLS 握手即失败），
  导致 `docker compose build` 卡在 `pip install torch`。**镜像重建需要等出网恢复**。
- 服务器到境外单次往返约 0.48s：页面静态资源走 CDN 会很快，但**接口响应受后端链路限制**。
- Pages 的 `*.pages.dev` 在国内可达性一般，必要时绑自定义域名并走优选 IP。
- 免费档 Supabase 闲置会 pause（与本部署无关，但会影响接口可用性）。

---

## 9. 待办（上线前）

1. 拿到域名 + Tunnel Token → 服务器安装 `cloudflared service install`；
2. 出网恢复后重建后端镜像（CORS 改动才生效）；
3. 拿 API Token + Account ID → `node scripts/deploy-pages.mjs`；
4. 按 §5 逐条验收，记录证据到本文档。
