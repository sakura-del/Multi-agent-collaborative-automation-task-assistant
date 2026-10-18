<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import NodeDetail from "@/components/tasks/NodeDetail.vue";
import PlanPanel from "@/components/tasks/PlanPanel.vue";
import TaskDag from "@/components/tasks/TaskDag.vue";
import TaskLog from "@/components/tasks/TaskLog.vue";
import TaskToolbar from "@/components/tasks/TaskToolbar.vue";
import { useTaskStore } from "@/stores/tasks";
import type { PlanNode, SubtaskStatus, TaskControl } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

// 任务详情：Tab 由路由 query 驱动（刷新不丢），规划确认与 DAG 联查
const props = defineProps<{ taskId: string }>();

const store = useTaskStore();
const route = useRoute();
const router = useRouter();

const TABS = ["plan", "execution", "logs", "result"] as const;
type TabKey = (typeof TABS)[number];

const pending = ref(false);
const errorMessage = ref<string | null>(null);
const conflictMessage = ref<string | null>(null);
const selectedNodeId = ref<string | null>(null);

const activeTab = computed<TabKey>(() => {
  const value = String(route.query.tab ?? "plan");
  return (TABS as readonly string[]).includes(value) ? (value as TabKey) : "plan";
});

const resultAvailable = computed(() => Boolean(store.snapshot?.review_report));

function selectTab(tab: TabKey) {
  if (tab === "result" && !resultAvailable.value) {
    return;
  }
  void router.replace({ query: { ...route.query, tab } });
}

const statuses = computed<Record<string, SubtaskStatus>>(() => {
  const map: Record<string, SubtaskStatus> = {};
  for (const subtask of store.snapshot?.subtasks ?? []) {
    map[subtask.node_id] = subtask.status;
  }
  return map;
});

const selectedNode = computed<PlanNode | null>(
  () => store.snapshot?.plan?.nodes.find((node) => node.id === selectedNodeId.value) ?? null
);

const selectedSubtask = computed(
  () => store.snapshot?.subtasks.find((subtask) => subtask.node_id === selectedNodeId.value) ?? null
);

const artifacts = computed(() => store.snapshot?.artifacts ?? []);

function artifactHref(artifactId: string): string {
  return `/api/v1/tasks/${props.taskId}/artifacts/${artifactId}`;
}

async function refresh() {
  errorMessage.value = null;
  try {
    await store.load(props.taskId);
  } catch (error) {
    errorMessage.value = (error as Error).message;
  }
}

async function handleApprove(version?: number) {
  pending.value = true;
  errorMessage.value = null;
  conflictMessage.value = null;
  try {
    await store.approve(props.taskId, version ?? store.snapshot?.plan_version ?? 1);
  } catch (error) {
    const message = (error as Error).message;
    // 409 版本冲突：重新拉取快照并提示，而不是静默失败
    if (/revision|版本|conflict|409/i.test(message)) {
      conflictMessage.value = `规划已被更新（${message}），已重新加载最新快照`;
      await refresh();
    } else {
      errorMessage.value = message;
    }
  } finally {
    pending.value = false;
  }
}

async function handleRegenerate() {
  pending.value = true;
  errorMessage.value = null;
  try {
    await store.control(props.taskId, { command: "retry_node", node_id: "n1" });
    await refresh();
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    pending.value = false;
  }
}

async function handleControl(payload: TaskControl) {
  pending.value = true;
  errorMessage.value = null;
  try {
    await store.control(props.taskId, payload);
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    pending.value = false;
  }
}

watch(
  () => props.taskId,
  () => {
    selectedNodeId.value = null;
    void refresh();
  }
);

onMounted(() => {
  void refresh();
  store.watch(props.taskId);
});

onBeforeUnmount(() => {
  store.stopWatching();
});
</script>

<template>
  <section class="page">
    <header class="page__header page__header--row">
      <div>
        <h1>{{ store.snapshot?.title ?? "任务详情" }}</h1>
        <p class="page__hint">
          <StatusBadge :status="store.snapshot?.status ?? 'created'" />
          <span class="detail__meta">
            revision {{ store.snapshot?.revision ?? "—" }} · 更新于
            {{ formatDateTime(store.snapshot?.updated_at) }}
          </span>
        </p>
      </div>
    </header>

    <ErrorBanner :message="errorMessage" />

    <nav class="tabs" role="tablist">
      <button
        v-for="tab in TABS"
        :key="tab"
        class="tabs__item"
        :class="{ active: activeTab === tab }"
        :data-test="`tab-${tab}`"
        role="tab"
        type="button"
        :aria-selected="activeTab === tab"
        :disabled="tab === 'result' && !resultAvailable"
        @click="selectTab(tab)"
      >
        {{ { plan: "规划", execution: "执行", logs: "日志", result: "结果" }[tab] }}
      </button>
    </nav>

    <TaskToolbar
      class="detail__toolbar"
      :status="store.snapshot?.status ?? 'created'"
      :pending="pending"
      :selected-node-id="selectedNodeId"
      :selected-node-status="selectedSubtask?.status ?? null"
      @command="handleControl"
    />

    <div v-if="activeTab === 'plan'" class="detail__panel">
      <PlanPanel
        :plan="store.snapshot?.plan ?? null"
        :pending="pending"
        :error="errorMessage"
        :conflict="conflictMessage"
        @approve="handleApprove"
        @regenerate="handleRegenerate"
      />
    </div>

    <div v-else-if="activeTab === 'execution'" class="detail__split">
      <TaskDag
        v-if="store.snapshot?.plan"
        :nodes="store.snapshot.plan.nodes"
        :statuses="statuses"
        :selected="selectedNodeId"
        @select="selectedNodeId = $event"
      />
      <p v-else class="page__hint">暂无规划，无法展示 DAG。</p>
      <NodeDetail :node="selectedNode" :subtask="selectedSubtask" @close="selectedNodeId = null" />
    </div>

    <div v-else-if="activeTab === 'logs'" class="detail__panel">
      <TaskLog :events="store.events" />
    </div>

    <div v-else class="detail__panel">
      <div v-if="store.snapshot?.review_report" class="result">
        <section class="result__markdown">
          <h2>最终综述</h2>
          <pre data-test="result-markdown">{{ store.snapshot.review_report.final_markdown }}</pre>
        </section>
        <section class="result__artifacts">
          <h2>产物</h2>
          <ul>
            <li v-for="artifact in artifacts" :key="artifact.artifact_id">
              <a :href="artifactHref(artifact.artifact_id)" :download="artifact.download_name">
                {{ artifact.download_name }}
              </a>
              <span class="page__hint"> {{ artifact.kind }} · {{ artifact.size }} B</span>
            </li>
          </ul>
        </section>
      </div>
      <p v-else class="page__hint">任务尚未产出复盘报告。</p>
    </div>
  </section>
</template>

<style scoped>
.page__header--row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.detail__meta {
  margin-left: 0.5rem;
  color: #64748b;
}

.tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 1rem;
}

.tabs__item {
  padding: 0.45rem 0.85rem;
  border: none;
  background: transparent;
  font-size: 0.9rem;
  color: #475569;
  border-bottom: 2px solid transparent;
}

.tabs__item.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
  font-weight: 600;
}

.tabs__item:disabled {
  color: #cbd5e1;
  cursor: not-allowed;
}

.detail__split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 1rem;
  align-items: start;
}

@media (max-width: 900px) {
  .detail__split {
    grid-template-columns: 1fr;
  }
}

.result {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.result__markdown pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 1rem;
  font-size: 0.88rem;
}

.result__artifacts ul {
  list-style: none;
  font-size: 0.9rem;
}
</style>
