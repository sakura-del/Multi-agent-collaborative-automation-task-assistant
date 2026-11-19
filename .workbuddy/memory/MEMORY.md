# 项目长期约定

## 测试在哪跑（2026-11-17 用户最高优先级明令，范围升级）

- **一切测试只在 `10.0.10.2`（`ssh ylin-dev`）跑**。本机 Windows 内存不足；
  美东生产机 `47.253.81.6` 内存也不足。**本机不准运行任何测试进程**：
  pytest、前端 vitest、任何 nodejs/npm 测试都不行。
  本机只做 `black` / `ruff` / `mypy` / `vue-tsc` 与前端 `npm run build`。
  **再犯用户将直接删环境，没有第二次机会**（已写入 AGENTS.md §5 铁律 #0 与 §4.1）。
- 浏览器验收由 GitHub CI 的 `e2e` 作业负责（隔离 Compose 栈，端口 48082）。

## BYOK（Issue #15）关键决策

- 用户凭据只进不出：响应永不返回明文；任务快照保留 model/provider/base_url。
- BYOK 失败**不静默回退**平台模型；401/403 才计数，连续失败达阈值自动 invalid。
- token 日配额对 BYOK 全程不参与；篇数由 `BILLING__BYOK_CONSUMES_REVIEW_QUOTA` 控制（默认 false）。
- 自定义端点：创建期拦 IP 字面量，运行时 `PinnedDNSAsyncHTTPTransport` 解析后只连已校验 IP。
- 探活打 `/models`（不烧 token），未保存配置不落库。

## CI 结构

- push：选片测试（`mode=full` 触发全量）+ lint + migration + smoke + **e2e**（Playwright，仅 model-settings）。
- nightly：全量 + 覆盖率 ≥80%。
- E2E 用一次性注册账号；`SECRETS__ENCRYPTION_KEY` 是测试专用固定值。

## UI 规范（2026-11-17 定稿）

- 圆角：Card 默认 **16px**（`rounded-2xl`）；页面级大卡用 `class="rounded-3xl"`（24px）
  覆盖；密集小块 16px；控件 10px。
- Card 间距模型：**根管 `py` + `gap`，部件各自管 `px`**（`CardHeader/Content/Footer`
  从 `CARD_SIZE_KEY` 注入的 size 取 `px-5` / `px-4`）。省略部件不会塌边。
- `Card` 的 props：`variant(outlined|muted)`、`size(default|compact)`、
  `interactive`、`selected`；`CardTitle as` 控制语义层级。
- `interactive` 用手指驱动的 `scale 0.985`，**不要改成 `whileTap`**——
  会给卡片塞 `tabIndex=0`，在真实控件前多一个无名 tab 停靠点。
- 整卡热区用 `CARD_TARGET_CLASS`（`after:absolute after:inset-0`）铺到唯一控件上；
  卡内其它按钮需 `relative z-10`。
- KPI 小块统一用 `StatCard`（label/value/hint，hint 与 label 支持插槽）。
- 动效常量统一从 `src/lib/ease.ts` 取（PRESS/SWAP/PANEL/LAYOUT/MOUSE/GLIDE +
  MORPH/TIMELINE_GLIDE）。不要在组件里另写字面量。
- `Card` 支持 `as`：放进 `ul/ol` 用 `li`，表单容器用 `form`，节点详情用 `aside`；
  默认 `div`。不要用 `div` 包裹列表项（非法结构）。

## 列表分页契约（2026-11-17）

- `GET /tasks` body 是数组，总数在 `X-Total-Count` 头；**新增响应头必须同步
  加进 `CORS.expose_headers`**，否则跨域前端读不到。
- 列表相关筛选一律下推服务端（`status_group` / `q` + skip/limit），
  不要再在前端对当前页做筛选/搜索/统计——分页后一定会漏数据。
- 顶部统计用 `GET /tasks/counts`（全量 GROUP BY），不用 `items.length`。
- 状态分组定义在后端 `_STATUS_GROUP_STATUSES` 与前端 `task-status.ts` 两处，
  改动必须同步，否则数字与列表对不上。
