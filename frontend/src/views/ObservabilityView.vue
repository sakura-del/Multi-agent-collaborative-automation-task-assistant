<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Badge, type BadgeVariants } from "@/components/ui/badge";
import { getObservabilityOverview } from "@/api/observability";
import type { ObservabilityOverview } from "@/api/types";

/**
 * 可观测性看板（Phase 2）：我的任务健康与 token 成本。
 *
 * 数据全部来自后端 `/observability/overview`（owner-scoped，只含自己的任务）。
 * **前端不重算任何比率**：成功率的分子分母口径在服务端定义
 * （分母只算「已进入终态的任务」，等待规划/执行中的不该拉低成功率），
 * 前端重算必然与后端不一致。
 *
 * ⚠️ 测试契约（不要改）：`observability-total` / `observability-success-rate` /
 * `observability-token-total` / `observability-token-avg` / `observability-empty` /
 * `observability-error` / `observability-row-<task_id>`。
 */
const overview = ref<ObservabilityOverview | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

/** 任务状态 → Badge 语义变体（与 StatusBadge 口径一致）。 */
const STATUS_TONE: Record<string, NonNullable<BadgeVariants["variant"]>> = {
  completed: "success",
  failed: "destructive",
  cancelled: "muted",
  waiting_human: "warning",
  waiting_approval: "warning",
  running: "default",
  planning: "secondary",
  paused: "muted",
  retrying: "warning",
};

const STATUS_LABELS: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  waiting_human: "待人工",
  waiting_approval: "待确认",
  running: "执行中",
  planning: "规划中",
  paused: "已暂停",
  retrying: "重试中",
};

const isEmpty = computed(() => (overview.value?.tasks.total ?? 0) === 0);

/**
 * 状态分布（只保留计数 > 0 的状态）。
 *
 * 为什么显式排除 `total`：它和后端契约里的状态字段同在一个对象里，
 * 直接 `v-for` 会渲染出一个「total 4」的假状态徽标。
 */
const statusBadges = computed(() => {
  const counts = overview.value?.tasks;
  if (!counts) {
    return [];
  }
  const statuses = ["completed", "failed", "cancelled", "waiting_human"] as const;
  return statuses
    .filter((status) => counts[status] > 0)
    .map((status) => ({ status, count: counts[status] }));
});

/** 成功率显示为百分比（后端给 0~1 的小数）。 */
const successRateText = computed(() => {
  const rate = overview.value?.success_rate ?? 0;
  return `${(rate * 100).toFixed(1)}%`;
});

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
    <header class="mb-4 flex flex-wrap items-start justify-between gap-2">
      <div>
        <h1 class="text-lg font-semibold tracking-tight">用量与健康</h1>
        <p class="mt-0.5 text-sm text-muted-foreground">
          你的任务成功率与 token 消耗；数据来自服务端统计，只包含你自己的任务。
        </p>
      </div>
      <p v-if="loading" class="text-sm text-muted-foreground">加载中…</p>
    </header>

    <ErrorBanner :message="error" testid="observability-error" />

    <EmptyState
      v-if="!loading && isEmpty && !error"
      data-test="observability-empty"
      title="还没有任务数据"
      description="创建一个任务后，这里会显示任务成功率与 token 用量统计。"
    />

    <template v-else-if="overview">
      <!-- 概览卡片 -->
      <div class="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div class="rounded-lg border border-border bg-card px-3 py-2.5">
          <p class="text-xs text-muted-foreground">任务总数</p>
          <p class="text-2xl font-semibold tabular-nums" data-test="observability-total">
            {{ overview.tasks.total }}
          </p>
        </div>
        <div class="rounded-lg border border-border bg-card px-3 py-2.5">
          <p class="text-xs text-muted-foreground">成功率</p>
          <p
            class="text-2xl font-semibold tabular-nums"
            data-test="observability-success-rate"
          >
            {{ successRateText }}
          </p>
        </div>
        <div class="rounded-lg border border-border bg-card px-3 py-2.5">
          <p class="text-xs text-muted-foreground">累计 token</p>
          <p
            class="text-2xl font-semibold tabular-nums"
            data-test="observability-token-total"
          >
            {{ formatTokens(overview.tokens.total_used) }}
          </p>
        </div>
        <div class="rounded-lg border border-border bg-card px-3 py-2.5">
          <p class="text-xs text-muted-foreground">平均每任务 token</p>
          <p
            class="text-2xl font-semibold tabular-nums"
            data-test="observability-token-avg"
          >
            {{ formatTokens(overview.tokens.avg_per_task) }}
          </p>
        </div>
      </div>

      <!-- 状态分布：只列计数 > 0 的状态（0 的不占版面，也不误导） -->
      <div class="mb-4 flex flex-wrap gap-1.5" data-test="observability-statuses">
        <Badge
          v-for="item in statusBadges"
          :key="item.status"
          :variant="STATUS_TONE[item.status] ?? 'muted'"
        >
          {{ STATUS_LABELS[item.status] ?? item.status }} {{ item.count }}
        </Badge>
        <span
          v-if="statusBadges.length === 0"
          class="text-sm text-muted-foreground"
        >
          暂无状态数据
        </span>
      </div>

      <!-- 最近任务 -->
      <section v-if="overview.recent.length" class="rounded-lg border border-border">
        <h2 class="border-b border-border px-3 py-2 text-sm font-medium">最近任务</h2>
        <ul class="divide-y divide-border">
          <li
            v-for="row in overview.recent"
            :key="row.task_id"
            class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
            :data-test="`observability-row-${row.task_id}`"
          >
            <div class="flex min-w-0 items-center gap-2">
              <code class="rounded bg-muted px-1 py-0.5 text-xs">
                {{ shortId(row.task_id) }}
              </code>
              <Badge :variant="STATUS_TONE[row.status] ?? 'muted'">
                {{ STATUS_LABELS[row.status] ?? row.status }}
              </Badge>
            </div>
            <div class="flex items-center gap-3 text-xs text-muted-foreground">
              <span class="tabular-nums">{{ formatTokens(row.used_tokens) }} token</span>
              <span class="tabular-nums">{{ formatDuration(row.duration_seconds) }}</span>
            </div>
          </li>
        </ul>
      </section>
    </template>
  </section>
</template>
