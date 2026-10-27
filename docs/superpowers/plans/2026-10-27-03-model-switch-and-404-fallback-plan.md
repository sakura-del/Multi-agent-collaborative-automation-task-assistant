# 切换模型端点 + 修复 404 不降级缺陷

> 日期：2026-10-27 ｜ 状态：**已实施**
> 触发：用户提供新密钥与模型提示（`qoder/qfmodel`）

---

## 1. 端点实测：21 个模型只有 3 个可用

`https://model.rhzy.ai/v1` 用新密钥列出 21 个模型，逐个实测（timeout=30s 并发）：

| 模型 | 结果 | 耗时 |
| --- | --- | --- |
| **`workbuddy-ai/deepseek-v4.1-flash`** | ✅ 200 | **1.5s** |
| **`workbuddy-ai/deepseek-v4.1-flash-sg`** | ✅ 200 | **1.6s** |
| `workbuddy-ai/hy3` | ⚠️ 200 但**内容在 `reasoning_content`** | 2.0s |
| `workbuddy-ai/step-5-preview` | ❌ 400 service info not found | 4.0s |
| `qoder/qfmodel` | ❌ **超时**（180s 无响应） | — |
| `qoder/dfmodel` | ❌ **超时**（180s 无响应） | — |
| `group/never-stuck` | ❌ 超时 | — |
| `opencode-zen-free/*`（14 个） | ❌ **502 upstream_error** | 0.1s |

### 1.1 用户提示的 `qoder/qfmodel` 不可用

**如实说明**：用户提示使用 `qoder/qfmodel`，但实测它在 180 秒内**无任何响应**。
不是「配置错」，也不是「key 无效」（`/models` 列表里有它，且 key 能列出模型），
而是该模型的上游**没有返回**。

所以改用 `workbuddy-ai/deepseek-v4.1-flash`（1.5s，严格 JSON 输出正常）。

### 1.2 为什么排除 `hy3`

`hy3` 返回 200 且很快，但**正文在 `reasoning_content` 字段里，`content` 为空**。
项目里 `LLMResponse.content` 直接读 `content` → 拿到空字符串 →
下游会认为「模型什么都没说」。**不能用**（不是慢，是结构不兼容）。

### 1.3 用真实业务提示词验证，不是「say ok」

| 模型 | 延迟 | 严格 JSON |
| --- | --- | --- |
| `workbuddy-ai/deepseek-v4.1-flash` | 1.9s | ✅ |
| `workbuddy-ai/deepseek-v4.1-flash-sg` | 1.8s | ✅ |
| `workbuddy-ai/hy3` | 6.1s | ⚠️ `content` 为空 |

判定用项目里校验 Agent 的真实提示词（要求只输出 JSON），
**`say ok` 通了不等于能承担结构化输出任务**。

---

## 2. 配置变更

### 2.1 `.env`（未入库，本地 + 服务器各改一次）

| 键 | 旧值 | 新值 |
| --- | --- | --- |
| `LLM__OPENAI_API_KEY` | 旧 key | **新 key** |
| `LLM__DEFAULT_MODEL` | `gpt-6-luna` | `workbuddy-ai/deepseek-v4.1-flash` |
| `LLM_DEFAULT_MODEL` | `gpt-4o-mini` | 同上（旧变量名保持一致） |

`LLM__OPENAI_BASE_URL` 本来就是 `https://model.rhzy.ai/v1`，未改。

**服务器改前已备份**：`.env.bak-20261027-124039`。

### 2.2 `config/default.yaml`

```yaml
default_model: "workbuddy-ai/deepseek-v4.1-flash"
fallback_chain:
  - provider: "openai"      # 同端点同族，共用 key，最不易整链失效
    model: "workbuddy-ai/deepseek-v4.1-flash-sg"
  - provider: "deepseek"    # 实测可用（实际路由到 deepseek-flash）
    model: "deepseek-chat"
```

### 2.3 降级链为什么删掉 glm / qwen

逐个实测（容器内直调各 provider）：

| 候选 | 结果 |
| --- | --- |
| `deepseek/deepseek-chat` | ✅ 可用 |
| `glm/glm-4-flash` | ❌ **401 令牌已过期** |
| `qwen/qwen-turbo` | ❌ **401 Incorrect API key** |

`.env` 里 `GLM_API_KEY=your-glm-...`、`QWEN_API_KEY=your-qwen-...` 是**占位符**。

**留着无效项不是"多一层兜底"**：降级时会先浪费一次 401 往返才轮到下一个候选 ——
更慢，且日志里全是噪声。配了真 key 再加回来。

---

## 3. 顺带发现的真实缺陷：404 不触发降级

### 3.1 怎么发现的

改完配置后想验证降级链真的能兜住（AGENTS §5 #34：不靠推理靠实测）。
方法：把主模型名故意写成 `definitely-not-a-real-model-xyz`，看是否降级。

**结果：直接抛 404，降级链完全没走。**

```
llm_chat_failed attempt=1 error='404 - Model "definitely-not-a-real-model-xyz"
is not supported by any configured account in this group'
❌ 降级也失败: NotFoundError
```

### 3.2 根因

`_is_retryable()` 的 docstring 把 404 归为「参数错误（400/422），重试与降级都无意义」：

```python
if isinstance(status, int):
    return status >= 500 or status == 429   # ← 404 落到 False
```

**这个归类错了**：

| 状态 | 含义 | 该不该降级 |
| --- | --- | --- |
| 401/403 | key 无效 | ❌ 换模型也没用（key 是同一个） |
| 400/422 | **请求**不合法 | ❌ 换模型也一样不合法 |
| **404** | **模型在本账号/分组不存在** | ✅ **正是该换模型的场景** |
| 429/5xx | 限流/上游故障 | ✅ 已支持 |

### 3.3 后果（真实的）

`model.rhzy.ai` 这次把 `gpt-6-luna` 下架（旧 key 对 `qoder/*` 返回
`model_not_found`）—— 如果主模型返回 404 不降级，**整个应用直接不可用**，
而配置里明明有可用的备选模型。

这与「400 是请求不合法」有本质区别：400 换谁都失败，404 换一个就行。

### 3.4 修法

`_is_retryable` 增加 404 判定，三重识别：

```python
if "notfound" in name:                      # 类名（NotFoundError）
    return True
if isinstance(status, int):
    return status >= 500 or status in (404, 429)   # 状态码
# 消息兜底（不同厂商/网关写法不同）
"model_not_found" / "not supported by any configured account" / "is not supported" / "model not found"
```

### 3.5 为什么 401/400/422 保持不变

这三类是「换模型也没用」的错误，降级只会浪费一次往返并掩盖真实原因
（比如 key 过期时，降级链里的项用同一个 key 也全会 401，最终报错反而更难懂）。
测试 `test_401_is_still_not_retryable` 等 4 条锁住这一点。

---

## 4. 验收标准

| # | 标准 | 验证 |
| --- | --- | --- |
| A1 | 404（带 status_code）触发降级 | `test_404_model_not_found_is_retryable` |
| A2 | 401/403/400/422 **不**触发降级 | 4 条 `test_*_is_still_not_retryable` |
| A3 | 429/5xx 仍触发降级（不回归） | `test_transient_statuses_still_retryable`（5 个参数） |
| A4 | 无 status_code 时靠类名识别 404 | `test_404_without_status_code_by_class_name` |
| A5 | 无 status_code 时靠消息识别 404 | `test_404_by_message_when_no_status` |
| A6 | 降级链非空且每项完整 | `test_fallback_chain_first_entry_is_usable` |
| A7 | 降级链**不含**占位 key 厂商 | 同上 |
| A8 | 真实任务能跑通 | 见 §5 |

---

## 5. 实施结果

### 5.1 交付

| 文件 | 改动 |
| --- | --- |
| `config/default.yaml` | 默认模型换成 `workbuddy-ai/deepseek-v4.1-flash`；降级链改为 flash-sg + deepseek-chat（删掉 401 的 glm/qwen） |
| `.env`（本地 + 服务器，未入库） | 新 key + 新默认模型；服务器改前备份 `.env.bak-20261027-124039` |
| `app/llm/base.py` | `_is_retryable` 增加 404 判定（三重识别） |
| `tests/test_llm_fallback_404.py` | **新增 12 条**测试 |
| `tests/test_config.py` / `test_llm_factory.py` / `test_config_priority.py` | 4 条断言从「写死 deepseek」改为「链条非空且每项完整 + 不含占位 key 厂商」 |

### 5.2 真实任务验证

**简化任务**（单篇文献）：

```
任务 f6c0f61b → completed
final_output 长度 = 3788
失败检查项 = 无
```

日志确认真的用了新模型、且**没降级**：

```json
{"provider": "openai", "model": "workbuddy-ai/deepseek-v4.1-flash",
 "attempt": 1, "prompt_tokens": 10971, "completion_tokens": 394,
 "finish_reason": "stop", "event": "llm_chat_ok"}
```

`attempt: 1` = 一次成功，没有走 fallback。

**复杂任务**（两篇文献）→ `waiting_human`：

```
n3 retry=2  error=校验未通过  score=90.0
✗ semantic_consistency: [E1] 未在上下文中有明确对应的证据描述…
```

**判断**：这不是配置问题（模型在工作、能生成内容、能被语义校验），
而是**新模型的生成质量在该任务上不足以稳定通过语义校验**。
如实记录，不掩盖。

### 5.3 404 降级的实测验证（真实服务器）

修好后在服务器上重跑「主模型名故意写错」的探针：

```
真实配置: primary=workbuddy-ai/deepseek-v4.1-flash
真实降级链: [('openai', 'workbuddy-ai/deepseek-v4.1-flash-sg'), ('deepseek', 'deepseek-chat')]
测试配置: primary=definitely-not-a-real-model-xyz（故意写错）

llm_fallback_triggered  error='404 - Model "definitely-not-a-real-model-xyz"
                        is not supported by any configured account in this group'
                        next_model=workbuddy-ai/deepseek-v4.1-flash-sg
llm_fallback_succeeded  fallback_model=workbuddy-ai/deepseek-v4.1-flash-sg
✅ 降级成功: model='workbuddy-ai/deepseek-v4.1-flash-sg' content='ok'
```

**修复前这里会直接抛 404**（见 §3.1 的对照输出）。

### 5.4 `qoder/*` 复查：不是临时故障

隔了约 50 分钟再测一次：

```
FAIL qoder/qfmodel    status=  0  90.1s  TimeoutError
FAIL qoder/dfmodel    status=  0  90.1s  TimeoutError
OK   workbuddy-ai/deepseek-v4.1-flash  status=200  1.7s
```

**结论：`qoder/*` 持续不可用**，不是一次性抖动。具体原因（端点限流 / 上游故障 /
需要额外参数）**无法判定** —— 没有任何响应体可查。

### 5.5 门禁

```
black：1 file reformatted
ruff：All checks passed
mypy --no-incremental：0 错误（207 文件）
pytest 全量：2821 passed / 8 skipped（+12）
```

**首次全量遗留 1 条失败**：`test_config_priority.py::test_repository_yaml_is_loaded`
—— 又一处「写死 `fallback_chain[0].provider == "deepseek"`」。

这暴露了我的疏漏：**改配置里的约定值后，应该 grep 全部同类断言**，
而不是改完眼前这一处就交（AGENTS §5 #35 讲的就是这个，我又犯了）。

修正后 grep 全仓确认：只剩 `test_config_priority.py:114` 一处，
那是 `monkeypatch` 注入 `deepseek` 的场景，**断言合法**（环境变量里明确写了 deepseek）。

### 5.6 诚实性验证

回退 404 判定（`status in (404, 429)` → `status == 429`，
并去掉类名判定）→ **1 条变红**：`test_404_model_not_found_is_retryable`。

---

## 6. 验证限制

- **`qoder/qfmodel` 未验证成功**：180s 超时，无法判断是端点限流、上游故障、
  还是该模型需要额外参数。**没有结论**，只是「当前不可用」；
- `hy3` 只测了 chat 兼容性，**没测流式**（项目当前不用流式）；
- 切模型后**只跑了一个简化任务**（`completed`，3788 字符）+ 一个复杂任务
  （`waiting_human`，`semantic_consistency` 失败）；
  **新模型的生成质量与 gpt-6-luna 的对比没有系统评估** ——
  实验矩阵若要用新模型，需要重新标定；
- 模型切换影响**所有** LLM 调用（规划/执行/校验/复盘），
  只做了主链路冒烟，**未逐一验证每条链路**。
