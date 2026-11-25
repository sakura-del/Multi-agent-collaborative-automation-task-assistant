<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { Badge } from "@/components/ui/badge";
import type { BadgeVariants } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import { Label } from "@/components/ui/label";
import type { TaskEvent } from "@/api/types";
import { formatDateTime, taskStatusLabel } from "@/utils/task-status";

/** 日志级别 → Badge 语义变体（不直接写颜色，保持全站语义色一致）。 */
const LEVEL_TONE: Record<string, NonNullable<BadgeVariants["variant"]>> = {
  info: "muted",
  warning: "warning",
  error: "destructive",
  debug: "muted",
};

const LEVEL_LABELS: Record<string, string> = {
  info: "信息",
  warning: "警告",
  error: "错误",
  debug: "调试",
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  task_created: "任务创建",
  plan_ready: "规划就绪",
  task_status: "任务状态变更",
  task_waiting_human: "等待人工介入",
  task_completed: "任务完成",
  task_failed: "任务失败",
  task_cancelled: "任务已终止",
  subtask_state_transition: "子任务状态流转",
  subtask_status: "子任务状态",
  subtask_started: "子任务开始",
  subtask_completed: "子任务完成",
  subtask_failed: "子任务失败",
  node_completed: "节点完成",
  node_failed: "节点失败",
};

// 结构化日志查看器：按 Agent / 节点 / 级别过滤，默认分页 100 行；一律文本插值（不用 v-html）
const props = withDefaults(
  defineProps<{
    events?: TaskEvent[];
    pageSize?: number;
  }>(),
  { events: () => [], pageSize: 100 }
);

const PAGE_SIZE = computed(() => props.pageSize);

interface LogRow {
  eventId: number;
  occurredAt: string;
  agent: string;
  node: string;
  level: string;
  eventType: string;
  status: string;
  message: string;
}

function pick(payload: Record<string, unknown>, key: string): string {
  const value = payload?.[key];
  return value === undefined || value === null ? "" : String(value);
}

function levelOf(event: TaskEvent): string {
  const explicit = pick(event.payload ?? {}, "level");
  if (explicit) {
    return explicit;
  }
  if (/failed|error|exhausted/.test(event.event_type)) {
    return "error";
  }
  if (/retry|waiting_human|warning/.test(event.event_type)) {
    return "warning";
  }
  return "info";
}

function agentOf(event: TaskEvent): string {
  return pick(event.payload ?? {}, "agent_type") || pick(event.payload ?? {}, "agent") || "—";
}

function nodeOf(event: TaskEvent): string {
  return pick(event.payload ?? {}, "node_id") || event.subtask_id || "—";
}

function messageOf(event: TaskEvent): string {
  const payload = event.payload ?? {};
  return (
    pick(payload, "message") ||
    pick(payload, "error") ||
    pick(payload, "reason") ||
    event.event_type
  );
}

const rows = computed<LogRow[]>(() =>
  props.events.map((event) => ({
    eventId: event.event_id,
    occurredAt: formatDateTime(event.occurred_at),
    agent: agentOf(event),
    node: nodeOf(event),
    level: levelOf(event),
    eventType: event.event_type,
    status: event.status,
    message: messageOf(event),
  }))
);

const agentFilter = ref("");
const nodeFilter = ref("");
const levelFilter = ref("");
const page = ref(1);

const agents = computed(() => [...new Set(rows.value.map((row) => row.agent))].filter((v) => v !== "—"));
const nodes = computed(() => [...new Set(rows.value.map((row) => row.node))].filter((v) => v !== "—"));

const filtered = computed(() =>
  rows.value.filter(
    (row) =>
      (!agentFilter.value || row.agent === agentFilter.value) &&
      (!nodeFilter.value || row.node === nodeFilter.value) &&
      (!levelFilter.value || row.level === levelFilter.value)
  )
);

const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE.value)));
const visible = computed(() =>
  filtered.value.slice((page.value - 1) * PAGE_SIZE.value, page.value * PAGE_SIZE.value)
);

watch([agentFilter, nodeFilter, levelFilter], () => {
  page.value = 1;
});

watch(pageCount, (count) => {
  if (page.value > count) {
    page.value = count;
  }
});

const hasActiveFilter = computed(() =>
  Boolean(agentFilter.value || nodeFilter.value || levelFilter.value),
);

function resetFilters() {
  agentFilter.value = "";
  nodeFilter.value = "";
  levelFilter.value = "";
}
</script>

<template>
  <section class="flex flex-col gap-2.5" data-test="task-log">
    <div class="flex flex-wrap items-end gap-3">
      <div class="grid gap-1">
        <Label for="log-filter-agent">执行角色</Label>
        <select
          id="log-filter-agent"
          v-model="agentFilter"
          data-test="log-filter-agent"
          class="flex h-8 rounded-md border border-input bg-background px-2 text-xs shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
        >
          <option value="">全部</option>
          <option v-for="agent in agents" :key="agent" :value="agent">{{ agent }}</option>
        </select>
      </div>
      <div class="grid gap-1">
        <Label for="log-filter-node">节点</Label>
        <select
          id="log-filter-node"
          v-model="nodeFilter"
          data-test="log-filter-node"
          class="flex h-8 rounded-md border border-input bg-background px-2 text-xs shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
        >
          <option value="">全部</option>
          <option v-for="node in nodes" :key="node" :value="node">{{ node }}</option>
        </select>
      </div>
      <div class="grid gap-1">
        <Label for="log-filter-level">级别</Label>
        <select
          id="log-filter-level"
          v-model="levelFilter"
          data-test="log-filter-level"
          class="flex h-8 rounded-md border border-input bg-background px-2 text-xs shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
        >
          <option value="">全部</option>
          <option value="info">信息</option>
          <option value="warning">警告</option>
          <option value="error">错误</option>
        </select>
      </div>
      <span class="ml-auto text-xs text-muted-foreground" data-test="log-count">
        {{ filtered.length }} 条
      </span>
    </div>

    <ol
      v-if="visible.length"
      class="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card"
    >
      <li
        v-for="row in visible"
        :key="row.eventId"
        class="flex flex-col gap-1 px-2.5 py-2 text-xs [content-visibility:auto] [contain-intrinsic-size:auto_48px] sm:grid sm:grid-cols-[auto_auto_auto_auto_auto_auto_1fr] sm:items-center sm:gap-2 sm:py-1.5"
        :data-test="`log-row-${row.eventId}`"
      >
        <div class="flex flex-wrap items-center gap-2 sm:contents">
          <span class="tabular-nums text-muted-foreground">{{ row.occurredAt }}</span>
          <Badge :variant="LEVEL_TONE[row.level] ?? 'muted'">
            {{ LEVEL_LABELS[row.level] ?? row.level }}
          </Badge>
          <span class="text-muted-foreground">
            {{ EVENT_TYPE_LABELS[row.eventType] ?? row.eventType }}
          </span>
          <span class="text-muted-foreground">{{ taskStatusLabel(row.status) }}</span>
          <span>{{ row.agent }}</span>
          <span class="text-muted-foreground">{{ row.node }}</span>
        </div>
        <span class="truncate" :title="row.message">{{ row.message }}</span>
      </li>
    </ol>

    <div
      v-else
      class="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-card px-4 py-6 text-center"
    >
      <p class="text-sm font-medium text-foreground">没有匹配的事件</p>
      <p class="text-xs text-muted-foreground">
        {{
          hasActiveFilter
            ? "当前筛选条件下暂无日志，可以清除筛选条件查看全部执行事件。"
            : "任务推进后，实时执行事件会展示在这里。"
        }}
      </p>
      <Button
        v-if="hasActiveFilter"
        type="button"
        variant="outline"
        size="sm"
        data-test="log-reset-filters"
        @click="resetFilters"
      >
        清除筛选
      </Button>
    </div>

    <!-- 日志可能上百页：只给「上一页/下一页」翻不到中间，换成可跳页的分页 -->
    <div
      v-if="pageCount > 1"
      class="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3"
    >
      <span class="text-xs text-muted-foreground" data-test="log-page">
        {{ page }} / {{ pageCount }}
      </span>
      <Pagination v-model="page" :total-pages="pageCount" aria-label="日志分页" />
    </div>
  </section>
</template>
