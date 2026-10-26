<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { Activity, Coins, RefreshCw, TriangleAlert } from "lucide-vue-next";

import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import { Badge, type BadgeVariants } from "@/components/ui/badge";
import { getObservabilityOverview } from "@/api/observability";
import type { ObservabilityOverview } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

/**
 * 用量看板（Phase 2 可观测性 · 2026-10-26 重设计）。
 *
 * ## 这一页最容易骗人的地方（也是本轮实测到的缺陷）
 *
 * 1. **「累计 token」曾经永远是 0**：后端 `sum_actual_tokens` 只读
 *    `output["prompt_tokens"]`，而生产载荷把 token 放在
 *    `output["execution_result"]` 里 → `task.used_tokens` 恒为 0。
 *    本页对此**无能为力**（数字本身就是错的），所以修在后端；这里只负责
 *    把「老数据没记录」和「真没用过」区分开，不让 0 冒充健康。
 * 2. **状态分布曾经漏状态**：旧契约只给 4 个固定状态，`waiting_approval`
 *    不在其中 → 「总数 27 / 分布合计 26」，差值用户永远看不见。
 *    现在按后端 `by_status` 全量渲染，并显示合计供用户自己核对。
 * 3. **成功率曾经没有分母**：76.9% 到底是 20/26 还是 20/27，用户无从判断。
 *
 * ## 前端不重算任何比率
 *
 * 成功率的分子分母口径在服务端定义（分母只算「已进入终态的任务」，
 * 等待规划/执行中的不该拉低成功率）。前端重算必然与后端不一致 ——
 * 这里只做格式化与展示。
 *
 * ⚠️ 测试契约（不要改）：`observability-total` / `observability-success-rate` /
 * `observability-success-denominator` / `observability-token-total` /
 * `observability-token-avg` / `observability-token-hint` / `observability-statuses` /
 * `observability-status-sum` / `observability-empty` / `observability-error` /
 * `observability-refresh` / `observability-row-<task_id>`。
 */
const overview = ref<ObservabilityOverview | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const router = useRouter();

/** 状态 → Badge 语义变体（查表字面量，禁止动态拼类名，见 AGENTS §6.5.5）。 */
const STATUS_TONE: Record<string, NonNullable<BadgeVariants["variant"]>> = {
  completed: "success",
  failed: "destructive",
  cancelled: "muted",
  waiting_human: "warning",
  waiting_approval: "warning",
  running: "default",
  reviewing: "default",
  planning: "secondary",
  created: "muted",
  paused: "muted",
  retrying: "warning",
};

/** 状态 → 中文（与 StatusBadge 口径一致，但分布里要短标签）。 */
const STATUS_LABELS: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  waiting_human: "待人工",
  waiting_approval: "待确认",
  running: "执行中",
  reviewing: "复盘中",
  planning: "规划中",
  created: "已创建",
  paused: "已暂停",
  retrying: "重试中",
};

/**
 * 状态分布排序：需要用户行动的状态排最前。
 *
 * 为什么不是按计数排：`waiting_approval` 只有 1 个但它是**唯一**卡住流程的状态，
 * 按计数排会被挤到最后。看板的职责是让人一眼看到「有什么在等我」。
 */
const STATUS_ORDER = [
  "waiting_approval",
  "waiting_human",
  "running",
  "retrying",
  "reviewing",
  "planning",
  "paused",
  "created",
  "completed",
  "failed",
  "cancelled",
];

const isEmpty = computed(() => (overview.value?.tasks.total ?? 0) === 0);

/**
 * 状态分布（只保留计数 > 0 的状态）。
 *
 * 数据源是后端 `by_status`（全状态），不是 `tasks` 那 5 个固定字段 ——
 * 用固定字段会漏掉 `waiting_approval`，产生「合计 ≠ 总数」。
 * 兜底：老后端没有 `by_status` 时退化为 `tasks`（不至于白屏）。
 */
const statusBadges = computed(() => {
  const current = overview.value;
  if (!current) {
    return [];
  }
  const counts: Record<string, number> = current.by_status ?? {};
  const entries = Object.entries(counts).filter(([, count]) => count > 0);
  return entries
    .sort((a, b) => {
      const left = STATUS_ORDER.indexOf(a[0]);
      const right = STATUS_ORDER.indexOf(b[0]);
      // 未知状态排在已知状态之后，但仍在（新状态不该被静默丢弃）
      if (left === -1 && right === -1) return b[1] - a[1];
      if (left === -1) return 1;
      if (right === -1) return -1;
      return left - right;
    })
    .map(([status, count]) => ({ status, count }));
});

/** 分布合计：用来给用户核对「合计 == 总数」，也是「看板不说谎」的显式证据。 */
const statusSum = computed(() => statusBadges.value.reduce((total, item) => total + item.count, 0));

/** 成功率显示为百分比（后端给 0~1 的小数）。 */
const successRateText = computed(() => {
  const rate = overview.value?.success_rate ?? 0;
  return `${(rate * 100).toFixed(1)}%`;
});

/** 成功率的分子（已完成数）—— 直接从后端字段取，不重算。 */
const successCount = computed(() => overview.value?.tasks.completed ?? 0);
/** 成功率的分母（后端定义的终态数）。 */
const terminalCount = computed(() => overview.value?.terminal_count ?? 0);

/**
 * 「累计 token 为 0 但有历史任务」—— 修复前的任务用量没落库。
 *
 * 必须显式说明，否则 0 会被读成「我还没用过」，而实际是数据缺失。
 */
const tokensMissingForHistory = computed(() => {
  const current = overview.value;
  if (!current) return false;
  return (
    current.tokens.total_used === 0 &&
    current.tasks.total > 0 &&
    recordedTaskCount.value === 0
  );
});

/**
 * 有用量记录的任务数。
 *
 * 单独提成 computed 而不是在模板里直接读 `tokens.recorded_task_count`：
 * 该字段在类型上是可选的（兼容老后端），模板里的 `> 0` 比较会被
 * `vue-tsc` 判为「可能是 undefined」。这里一次归一化，模板就不用再管。
 */
const recordedTaskCount = computed(() => overview.value?.tokens.recorded_task_count ?? 0);

/** token 千分位（看板上这些数字动辄几十万，不分组读不出来）。 */
function formatTokens(value: number): string {
  return value.toLocaleString("zh-CN");
}

/** 秒 → 人类可读（<60s 用秒，否则用「X 分 Y 秒」）。 */
function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

/** 任务 ID 太长，看板只显示前 8 位（完整 id 在任务详情页看）。 */
function shortId(taskId: string): string {
  return taskId.slice(0, 8);
}

function openTask(taskId: string) {
  void router.push({ name: "task-detail", params: { taskId } });
}

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    overview.value = await getObservabilityOverview();
  } catch (err) {
    error.value = (err as Error).message || "用量数据加载失败";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <section>
    <header class="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <Activity :size="18" class="text-muted-foreground" aria-hidden="true" />
          用量与健康
        </h1>
        <p class="mt-1 text-sm text-muted-foreground">
          你的任务成功率与 token 消耗。数据由服务端统计，只包含你自己的任务。
        </p>
      </div>
      <button
        type="button"
        data-test="observability-refresh"
        class="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
        :disabled="loading"
        @click="refresh"
      >
        <RefreshCw
          :size="13"
          :class="loading ? 'animate-spin motion-reduce:animate-none' : ''"
          aria-hidden="true"
        />
        {{ loading ? "刷新中…" : "刷新" }}
      </button>
    </header>

    <ErrorBanner :message="error" testid="observability-error" />

    <EmptyState
      v-if="!loading && isEmpty && !error"
      data-test="observability-empty"
      title="还没有任务数据"
      description="创建一个任务后，这里会显示任务成功率与 token 用量统计。"
    />

    <template v-else-if="overview">
      <!-- 概览：四项。成功率带分母，token 带口径说明 —— 孤零零的数字无法核对。 -->
      <div class="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div class="rounded-lg border border-border bg-card px-3 py-2.5">
          <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            任务总数
          </p>
          <p class="mt-1 text-2xl font-semibold tabular-nums" data-test="observability-total">
            {{ overview.tasks.total }}
          </p>
        </div>

        <div class="rounded-lg border border-border bg-card px-3 py-2.5">
          <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            成功率
          </p>
          <p
            class="mt-1 text-2xl font-semibold tabular-nums"
            data-test="observability-success-rate"
          >
            {{ successRateText }}
          </p>
          <!-- 分母必须露出来：76.9% 是 20/26 还是 20/27，用户要能自己核对 -->
          <p
            class="mt-0.5 text-[11px] text-muted-foreground"
            data-test="observability-success-denominator"
          >
            已完成 {{ successCount }} / {{ terminalCount }} 个终态任务
          </p>
        </div>

        <div class="rounded-lg border border-border bg-card px-3 py-2.5">
          <p class="flex items-center gap-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            <Coins :size="11" aria-hidden="true" /> 累计 token
          </p>
          <p
            class="mt-1 text-2xl font-semibold tabular-nums"
            data-test="observability-token-total"
          >
            {{ formatTokens(overview.tokens.total_used) }}
          </p>
          <!-- 0 不等于「没消耗」：修复前的任务用量没落库。必须说清楚。 -->
          <p
            v-if="tokensMissingForHistory"
            class="mt-0.5 flex items-start gap-1 text-[11px] text-warning"
            data-test="observability-token-hint"
          >
            <TriangleAlert :size="11" class="mt-0.5 shrink-0" aria-hidden="true" />
            <span>历史任务未记录用量，仅新任务开始统计</span>
          </p>
          <p
            v-else-if="recordedTaskCount > 0"
            class="mt-0.5 text-[11px] text-muted-foreground"
            data-test="observability-token-hint"
          >
            {{ recordedTaskCount }} 个任务有用量记录
          </p>
        </div>

        <div class="rounded-lg border border-border bg-card px-3 py-2.5">
          <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            平均每任务 token
          </p>
          <p
            class="mt-1 text-2xl font-semibold tabular-nums"
            data-test="observability-token-avg"
          >
            {{ formatTokens(overview.tokens.avg_per_task) }}
          </p>
          <p class="mt-0.5 text-[11px] text-muted-foreground">仅统计有用量记录的任务</p>
        </div>
      </div>

      <!-- 状态分布：全量状态（含 waiting_approval），并显示合计供核对 -->
      <section class="mb-4 rounded-lg border border-border bg-card px-3 py-2.5">
        <div class="mb-2 flex items-baseline justify-between gap-2">
          <h2 class="text-sm font-medium">状态分布</h2>
          <!-- 合计是「看板自洽」的可见证据：合计 ≠ 总数就说明漏了状态 -->
          <p class="text-[11px] text-muted-foreground" data-test="observability-status-sum">
            合计 {{ statusSum }} / 共 {{ overview.tasks.total }} 个
          </p>
        </div>
        <div class="flex flex-wrap gap-1.5" data-test="observability-statuses">
          <Badge
            v-for="item in statusBadges"
            :key="item.status"
            :variant="STATUS_TONE[item.status] ?? 'muted'"
          >
            {{ STATUS_LABELS[item.status] ?? item.status }} {{ item.count }}
          </Badge>
          <span v-if="statusBadges.length === 0" class="text-sm text-muted-foreground">
            暂无状态数据
          </span>
        </div>
      </section>

      <!-- 最近任务：标题是唯一能认出「哪次运行」的信息，UUID 只作辅助 -->
      <section
        v-if="overview.recent.length"
        class="overflow-hidden rounded-lg border border-border bg-card"
      >
        <h2 class="border-b border-border px-3 py-2 text-sm font-medium">最近任务</h2>
        <ul class="divide-y divide-border">
          <li v-for="row in overview.recent" :key="row.task_id">
            <!-- 用 <button> 而不是给 <li> 加 @click：**键盘可达是无障碍硬要求**
                 （AGENTS §6.5.3）。首版用 div+click，实测 FOCUSABLE 列表里
                 没有这些行 —— 键盘用户完全进不去任务详情。 -->
            <button
              type="button"
              :data-test="`observability-row-${row.task_id}`"
              class="flex w-full flex-wrap items-start justify-between gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/40"
              @click="openTask(row.task_id)"
            >
              <span class="flex min-w-0 flex-1 items-start gap-2">
                <StatusBadge :status="row.status" class="mt-0.5 shrink-0" />
                <span class="min-w-0">
                  <!-- 标题优先：没有标题时才退回 UUID -->
                  <span
                    class="block truncate text-sm font-medium text-foreground"
                    :title="row.title"
                  >
                    {{ row.title || `任务 ${shortId(row.task_id)}` }}
                  </span>
                  <span
                    class="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground"
                  >
                    <code class="rounded bg-muted px-1 py-0.5">{{ shortId(row.task_id) }}</code>
                    <span v-if="row.created_at">{{ formatDateTime(row.created_at) }}</span>
                  </span>
                </span>
              </span>
              <span class="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
                <span class="tabular-nums">{{ formatTokens(row.used_tokens) }} token</span>
                <span class="tabular-nums">{{ formatDuration(row.duration_seconds) }}</span>
              </span>
            </button>
          </li>
        </ul>
      </section>
    </template>
  </section>
</template>
