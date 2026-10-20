<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import NodeDetail from "@/components/tasks/NodeDetail.vue";
import PlanPanel from "@/components/tasks/PlanPanel.vue";
import TaskDag from "@/components/tasks/TaskDag.vue";
import TaskLog from "@/components/tasks/TaskLog.vue";
import TaskResult from "@/components/tasks/TaskResult.vue";
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
  <section>
    <header class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-lg font-semibold tracking-tight">
          {{ store.snapshot?.title ?? "任务详情" }}
        </h1>
        <p class="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <StatusBadge :status="store.snapshot?.status ?? 'created'" />
          <span>
            revision {{ store.snapshot?.revision ?? "—" }} · 更新于
            {{ formatDateTime(store.snapshot?.updated_at) }}
          </span>
        </p>
      </div>
    </header>

    <ErrorBanner :message="errorMessage" />

    <!-- 选项卡：下划线式，窄屏可横向滚动 -->
    <nav
      class="mb-4 flex gap-1 overflow-x-auto border-b border-border"
      role="tablist"
    >
      <button
        v-for="tab in TABS"
        :key="tab"
        class="-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors"
        :class="
          activeTab === tab
            ? 'border-primary font-medium text-primary'
            : 'border-transparent text-muted-foreground hover:text-foreground'
        "
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
      class="mb-3"
      :status="store.snapshot?.status ?? 'created'"
      :pending="pending"
      :selected-node-id="selectedNodeId"
      :selected-node-status="selectedSubtask?.status ?? null"
      @command="handleControl"
    />

    <div v-if="activeTab === 'plan'">
      <PlanPanel
        :plan="store.snapshot?.plan ?? null"
        :pending="pending"
        :error="errorMessage"
        :conflict="conflictMessage"
        @approve="handleApprove"
        @regenerate="handleRegenerate"
      />
    </div>

    <div
      v-else-if="activeTab === 'execution'"
      class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_20rem]"
    >
      <TaskDag
        v-if="store.snapshot?.plan"
        :nodes="store.snapshot.plan.nodes"
        :statuses="statuses"
        :selected="selectedNodeId"
        @select="selectedNodeId = $event"
      />
      <p v-else class="text-sm text-muted-foreground">暂无规划，无法展示 DAG。</p>
      <NodeDetail :node="selectedNode" :subtask="selectedSubtask" @close="selectedNodeId = null" />
    </div>

    <div v-else-if="activeTab === 'logs'">
      <TaskLog :events="store.events" />
    </div>

    <div v-else>
      <TaskResult
        v-if="store.snapshot?.review_report"
        :task-id="props.taskId"
        :report="store.snapshot.review_report"
        :artifacts="artifacts"
      />
      <EmptyState
        v-else
        title="任务尚未产出复盘报告"
        description="任务执行完成后，这里会展示复盘结论、引用核验与导出入口。"
      />
    </div>
  </section>
</template>
