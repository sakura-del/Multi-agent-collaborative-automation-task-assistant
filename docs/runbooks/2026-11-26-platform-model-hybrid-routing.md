# 平台模型混合降级路由（Qwen 主 + OpenCode Go deepseek-v4.1-flash 备）

> 2026-11-26 实施并上线验证。适用版本：`5710a58`（`26f73c9` + `5710a58` 两个提交）。
> 生产：`root@47.253.81.6:/opt/veripaper`，backend 容器 `vp-backend`。

## 1. 背景与目标

平台模型原来只有 Qwen 一条通道（`https://model.rhdata.cn/v1` +
`qwen3.8-27b-uncensored-fp8`）。Qwen 网关抖动/掉线时，整站规划、校验、复盘、
会话都停摆，而原有降级链配的是 `workbuddy-ai/*-sg` 与 `deepseek/deepseek-chat`
—— 前者在新网关上不可用，后者没有可用 key，等于没有兜底。

目标（用户 2026-11-26 指示）：**Qwen 掉线时自动切到 OpenCode Go 的
`deepseek-v4.1-flash`**，做成混合降级路由，而不是把主模型整个换掉。

## 2. OpenCode Go 接入事实（实测）

| 项 | 值 |
| --- | --- |
| OpenAI 兼容 base_url | `https://opencode.ai/inference/go/openai/v1` |
| 必需请求头 | `x-opencode-session: ses_...`（任意稳定会话 ID；缺头直接 HTTP 400 `MissingSessionID`） |
| 模型 | `deepseek-v4.1-flash`（1M 上下文，支持 reasoning/tools/图片，但平台 `LLM__SUPPORTS_IMAGE` 仍为 false） |
| 兜底候选 | `deepseek-v4-pro`（同端点，实测 200） |

注意：OpenCode Go 是**推理模型**，`completion_tokens` 含思考 token（实测让
「只回复两个字」回了 239 tokens），成本核算不要按字数估算。

## 3. 代码实现

### 3.1 配置项（`backend/app/core/config.py`）

```env
# 全局附加头（单 provider 直接当平台模型时用）
LLM__EXTRA_HEADERS={"x-opencode-session":"ses_veripaper_platform"}
# 按 provider 区分的附加头（混合路由用；命中 provider 时覆盖全局头，不合并）
LLM__PROVIDER_EXTRA_HEADERS={"deepseek":{"x-opencode-session":"ses_veripaper_platform"}}
```

两个字段都是 JSON 对象，走 pydantic-settings 的 `__` 嵌套解析（与
`node_routing` / `routing_targets` 同一套通道）。

### 3.2 透传链路

`LLMConfig` → `LLMManager._apply_config`（读 `extra_headers` /
`provider_extra_headers`）→ `LLMManager._get_or_create`（provider 专属头优先，
否则全局头）→ `create_llm(default_headers=...)` → `OpenAIAdapter`（以及
GLM/Qwen 子类透传）→ `AsyncOpenAI(default_headers=...)` → 每个出站请求。

- 空 dict 传 `None`，保持 SDK 默认头不变；
- **BYOK 路径不传**（`app/llm/byok.py` 直接 `create_llm`），避免把平台会话标识
  发给用户自己的端点；
- 平台与 BYOK 之外的调用方（节点路由 `routing_targets`）也走 `create_llm`，
  如需带头按同样方式传。

### 3.3 降级语义（沿用既有 `LLMManager.chat`）

- 触发降级：**超时、连接错误、429、5xx、404（模型不存在）**，且实例内部重试耗尽；
- 不降级：**401/403/400/422**（换模型也没用，直接抛）；
- `stream_chat` **只走主模型**，不降级（流式切模型会让已输出内容中断）。
  会话流式输出（`/conversations/.../stream`）属于此类；任务式 Agent 走
  `FallbackChatClient` → `LLMManager.chat`，会降级。

## 4. 生产配置（`/opt/veripaper/.env`）

主模型保持 Qwen 不动（`LLM__OPENAI_*` 沿用），OpenCode Go 用 `deepseek`
provider 承载（该 provider 在注册表里复用 OpenAIAdapter，key/base_url 独立）：

```env
LLM__DEFAULT_PROVIDER=openai
LLM__DEFAULT_MODEL=qwen3.8-27b-uncensored-fp8
LLM__OPENAI_BASE_URL=https://model.rhdata.cn/v1

LLM__DEEPSEEK_API_KEY=<OpenCode Go 的 oc_sk_... 不入仓库>
LLM__DEEPSEEK_BASE_URL=https://opencode.ai/inference/go/openai/v1
LLM__PROVIDER_EXTRA_HEADERS={"deepseek":{"x-opencode-session":"ses_veripaper_platform"}}
LLM__FALLBACK_CHAIN=[{"provider":"deepseek","model":"deepseek-v4.1-flash"}]
```

改前已备份：`/opt/veripaper/.env.bak-20261126-131232`。

> `config/default.yaml` 的基线降级链（`workbuddy-ai/*-sg`、`deepseek-chat`）
> 未改：生产以 `.env` 覆盖；但**任何新环境部署都必须显式配上面的 env**，
> 否则沿用 yaml 的旧降级链等于没有兜底。

## 5. 发布记录

1. 本地 `git bundle create vp.bundle --all` → `scp` 到 `/root/vp.bundle`；
2. 服务器 `git fetch origin '+refs/heads/*:refs/remotes/origin/*'` +
   `git merge --ff-only origin/main`（`69c2379 → 5710a58`）；
3. 备份并改 `.env`（见上）；
4. `cd /opt/veripaper/deploy/ecs && docker compose build backend && docker compose up -d backend`；
5. 镜像：`f6fc35ce27ab`（2026-11-26 13:13 +0800）；容器 `vp-backend` healthy。

## 6. 上线验证证据（2026-11-26 14:16 JST，生产容器内只读）

### A. 生产装配

```
primary: ('openai', 'qwen3.8-27b-uncensored-fp8')
fallback_chain: [('deepseek', 'deepseek-v4.1-flash')]
primary.base_url: https://model.rhdata.cn/v1
primary.headers: {}
fallback.base_url: https://opencode.ai/inference/go/openai/v1
fallback.api_key_present: True
fallback.headers: {'x-opencode-session': 'ses_veripaper_platform'}
primary.content: 正常   （真实 Qwen 调用，llm_chat_ok, finish_reason=stop）
```

### B. 模拟主模型掉线（openai 指向不可达端口）

```
llm_chat_failed  error='Connection error.'
llm_fallback_triggered  next_provider=deepseek next_model=deepseek-v4.1-flash
llm_chat_ok  model=deepseek-v4.1-flash
llm_fallback_succeeded
fallback.content: 备用   fallback.model: deepseek-v4.1-flash
```

### C. 健康与日志

- `GET https://api.veripaper.me/api/v1/health/ready` → `200`，
  `{"status":"ready", "llm_credentials":"ok"}`；
- 容器 `Up (healthy)`；`docker logs` 近 20 分钟无 `MissingSessionID`。

## 7. 回归测试

在测试机 10.0.10.2（`~/vp-test`，`./run-tests.sh ... --no-cov`）定向：

```
./run-tests.sh tests/test_llm_adapters.py tests/test_llm_factory.py tests/test_config_priority.py --no-cov
→ 57 passed
```

覆盖点：

- `test_llm_adapters.py::TestDefaultHeadersOnWire`：用 `httpx.MockTransport` 抓
  真实出站请求，断言 `x-opencode-session` 出现、URL 正确；
- `test_llm_factory.py::test_apply_config_hybrid_routing_endpoints_and_headers`：
  主/备实例各自的 `base_url`、`api_key`、请求头（provider 专属头覆盖全局头）；
- `test_config_priority.py`：`LLM__EXTRA_HEADERS` /
  `LLM__PROVIDER_EXTRA_HEADERS` 的 JSON 环境变量覆盖 yaml 基线。

## 8. 回滚

- **只回配置**（不重建镜像）：恢复 `/opt/veripaper/.env.bak-20261126-131232`
  → `cd /opt/veripaper/deploy/ecs && docker compose up -d backend`（env_file
  变化只需重建容器）；
- **回代码**：`git reset --hard 26f73c9^`（或 revert `26f73c9` 与 `5710a58`）
  → 重新 bundle 上传 → `docker compose build backend && up -d backend`。

## 9. 已知边界

- 主模型 401/403（key 失效）不降级——按设计直接报错，需换 key；
- 流式接口不降级；
- 故障切换耗时 = 主模型内部重试预算（当前 `LLM__MAX_RETRIES=6`、
  `LLM__REQUEST_TIMEOUT=180`、`timeout_retries` 默认 1）：**连接拒绝/5xx 快**，
  **超时慢**（180s × 2 才轮到降级）。若要求更快切换，可下调
  `LLM__REQUEST_TIMEOUT` 或 `LLM__MAX_RETRIES`，代价是正常慢请求更易失败。
- `x-opencode-session` 用固定值 `ses_veripaper_platform`（Go 计划要求稳定
  会话 ID 以便路由/缓存）；换值只影响上游路由，不影响功能。
