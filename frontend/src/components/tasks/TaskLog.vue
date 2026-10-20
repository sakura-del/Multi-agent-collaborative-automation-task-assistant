<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { Badge } from "@/components/ui/badge";
import type { BadgeVariants } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import type { TaskEvent } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

/** 日志级别 → Badge 语义变体（不直接写颜色，保持全站语义色一致）。 */
const LEVEL_TONE: Record<string, NonNullable<BadgeVariants["variant"]>> = {
  info: "muted",
  warning: "warning",
  error: "destructive",
  debug: "muted",
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
</script>

<template>
  <section class="flex flex-col gap-2.5" data-test="task-log">
    <div class="flex flex-wrap items-end gap-3">
      <div class="grid gap-1">
        <Label for="log-filter-agent">Agent</Label>
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
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="error">error</option>
        </select>
      </div>
      <span class="ml-auto text-xs text-muted-foreground" data-test="log-count">
        {{ filtered.length }} 条
      </span>
    </div>

    <ol class="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
      <li
        v-for="row in visible"
        :key="row.eventId"
        class="grid grid-cols-[auto_auto_auto_auto_auto_auto_1fr] items-center gap-2 px-2.5 py-1.5 text-xs"
        :data-test="`log-row-${row.eventId}`"
      >
        <span class="tabular-nums text-muted-foreground">{{ row.occurredAt }}</span>
        <Badge :variant="LEVEL_TONE[row.level] ?? 'muted'">{{ row.level }}</Badge>
        <span class="text-muted-foreground">{{ row.eventType }}</span>
        <span class="text-muted-foreground">{{ row.status }}</span>
        <span>{{ row.agent }}</span>
        <span class="text-muted-foreground">{{ row.node }}</span>
        <span class="truncate" :title="row.message">{{ row.message }}</span>
      </li>
    </ol>

    <p v-if="!visible.length" class="text-sm text-muted-foreground">没有匹配的事件。</p>

    <div v-if="pageCount > 1" class="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        type="button"
        data-test="log-prev"
        :disabled="page === 1"
        @click="page -= 1"
      >
        上一页
      </Button>
      <span class="text-xs text-muted-foreground" data-test="log-page">
        {{ page }} / {{ pageCount }}
      </span>
      <Button
        variant="outline"
        size="sm"
        type="button"
        data-test="log-next"
        :disabled="page === pageCount"
        @click="page += 1"
      >
        下一页
      </Button>
    </div>
  </section>
</template>
