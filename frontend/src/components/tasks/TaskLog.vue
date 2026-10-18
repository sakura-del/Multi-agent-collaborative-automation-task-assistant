<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { TaskEvent } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

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
  <section class="log" data-test="task-log">
    <div class="log__filters">
      <label>
        Agent
        <select v-model="agentFilter" data-test="log-filter-agent">
          <option value="">全部</option>
          <option v-for="agent in agents" :key="agent" :value="agent">{{ agent }}</option>
        </select>
      </label>
      <label>
        节点
        <select v-model="nodeFilter" data-test="log-filter-node">
          <option value="">全部</option>
          <option v-for="node in nodes" :key="node" :value="node">{{ node }}</option>
        </select>
      </label>
      <label>
        级别
        <select v-model="levelFilter" data-test="log-filter-level">
          <option value="">全部</option>
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="error">error</option>
        </select>
      </label>
      <span class="log__count" data-test="log-count">{{ filtered.length }} 条</span>
    </div>

    <ol class="log__rows">
      <li v-for="row in visible" :key="row.eventId" class="log__row" :data-test="`log-row-${row.eventId}`">
        <span class="log__time">{{ row.occurredAt }}</span>
        <span class="log__level" :class="`log__level--${row.level}`">{{ row.level }}</span>
        <span class="log__type">{{ row.eventType }}</span>
        <span class="log__status">{{ row.status }}</span>
        <span class="log__agent">{{ row.agent }}</span>
        <span class="log__node">{{ row.node }}</span>
        <span class="log__message">{{ row.message }}</span>
      </li>
    </ol>

    <p v-if="!visible.length" class="page__hint">没有匹配的事件。</p>

    <div v-if="pageCount > 1" class="log__pager">
      <button class="btn" type="button" :disabled="page === 1" @click="page -= 1">上一页</button>
      <span data-test="log-page">{{ page }} / {{ pageCount }}</span>
      <button class="btn" type="button" :disabled="page === pageCount" @click="page += 1">下一页</button>
    </div>
  </section>
</template>

<style scoped>
.log {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.log__filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.85rem;
}

.log__filters select {
  margin-left: 0.3rem;
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-card);
}

.log__count {
  color: #64748b;
}

.log__rows {
  list-style: none;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-card);
  max-height: 420px;
  overflow: auto;
}

.log__row {
  display: grid;
  grid-template-columns: 150px 64px 200px 96px 90px 70px minmax(0, 1fr);
  gap: 0.5rem;
  padding: 0.35rem 0.6rem;
  border-bottom: 1px solid #f1f5f9;
  font-size: 0.82rem;
  align-items: center;
}

.log__time,
.log__agent,
.log__node {
  color: #64748b;
}

.log__level {
  text-transform: uppercase;
  font-size: 0.72rem;
  font-weight: 600;
}

.log__level--info {
  color: #1d4ed8;
}

.log__level--warning {
  color: var(--color-warning);
}

.log__level--error {
  color: var(--color-error);
}

.log__message {
  overflow-wrap: anywhere;
}

.log__pager {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
}
</style>
