<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ListFilter, MessageSquare, X } from "lucide-vue-next";

import BeTimeline from "@/components/motion/BeTimeline.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import AnnotationPanel from "@/components/collaboration/AnnotationPanel.vue";
import NodeDetail from "@/components/tasks/NodeDetail.vue";
import PlanPanel from "@/components/tasks/PlanPanel.vue";
import TaskDag from "@/components/tasks/TaskDag.vue";
import TaskLog from "@/components/tasks/TaskLog.vue";
import TaskResult from "@/components/tasks/TaskResult.vue";
import TaskToolbar from "@/components/tasks/TaskToolbar.vue";
import { useAuthStore } from "@/stores/auth";
import { useTaskStore } from "@/stores/tasks";
import type { PlanNode, SubtaskStatus, TaskControl, TaskEvent } from "@/api/types";
import { formatDateTime, needsUserAction } from "@/utils/task-status";
import { adviceForWaitingReason, latestWaitingReason } from "@/utils/waiting-reason";

// 任务详情：Tab 由路由 query 驱动（刷新不丢），规划确认与 DAG 联查。
//
// ## 这次重设计的三个真问题（2026-10-23，截图实测）
//
// 1. **批注面板占满每个 tab 底部**：四个 tab 下面都挂着完整批注面板（含大块
//    空态），把「结果」「日志」挤得很长。批注是协作场景才用的，不该在所有
//    tab 抢占纵向空间 → 改成**侧边抽屉，默认收起**。
// 2. **头部信息偏弱**：只写 `revision 1 · 更新于 …`，没回答「现在要我做什么」。
//    → 「待我确认」状态时在头部显式提示。
// 3. 结果页 Markdown 与执行 tab DAG 的问题分别在 TaskResult / TaskDag 里修。
const props = defineProps<{ taskId: string }>();

const store = useTaskStore();
const auth = useAuthStore();
const route = useRoute();
const router = useRouter();

/** 当前用户 id（批注面板据此决定「删除」按钮显隐；后端仍会校验）。 */
const currentUserId = computed(() => auth.user?.id ?? undefined);

const TABS = ["plan", "execution", "logs", "result"] as const;
type TabKey = (typeof TABS)[number];

const pending = ref(false);
const errorMessage = ref<string | null>(null);
const conflictMessage = ref<string | null>(null);
const selectedNodeId = ref<string | null>(null);
/** 批注抽屉开关（默认收起）。 */
const annotationOpen = ref(false);

const activeTab = computed<TabKey>(() => {
  const value = String(route.query.tab ?? "plan");
  return (TABS as readonly string[]).includes(value) ? (value as TabKey) : "plan";
});

const resultAvailable = computed(() => Boolean(store.snapshot?.review_report));

/** 当前状态是否需要用户行动（决定头部是否出现提示）。 */
const needsAction = computed(() => needsUserAction(store.snapshot?.status ?? "created"));

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

// ===== 需要人工介入时的原因 =====

/**
 * 最近一次「转人工」的原因 + 下一步建议。
 *
 * 后端把失败原因放在 `task_waiting_human` 事件的 payload.reason 里，
 * 页面上此前只显示「任务已暂停，等你介入」——用户不知道要做什么。
 */
const waitingReason = computed(() => latestWaitingReason(store.events));
const waitingAdvice = computed(() => adviceForWaitingReason(waitingReason.value));

// ===== 执行事件时间线（beUI 风格）=====

const TIMELINE_EVENTS = 12;

const timelineEvents = computed(() =>
  store.events.slice(-TIMELINE_EVENTS).map((event) => ({
    id: String(event.event_id),
    date: event.occurred_at,
    title: event.event_type,
  })),
);

function eventById(id: string): TaskEvent | undefined {
  return store.events.find((event) => String(event.event_id) === id);
}

function eventLevel(event: TaskEvent | undefined): string {
  if (!event) return "info";
  const explicit = event.payload?.["level"];
  if (explicit) return String(explicit);
  if (/failed|error|exhausted/.test(event.event_type)) return "error";
  if (/retry|waiting_human|warning/.test(event.event_type)) return "warning";
  return "info";
}

const EVENT_LEVEL_LABEL: Record<string, string> = {
  info: "信息",
  warning: "警告",
  error: "错误",
};

const EVENT_TONE_CLASS: Record<string, string> = {
  info: "bg-muted text-muted-foreground",
  warning: "bg-warning/12 text-warning",
  error: "bg-destructive/10 text-destructive",
};

function eventToneClass(level: string): string {
  return EVENT_TONE_CLASS[level] ?? "bg-muted text-muted-foreground";
}

/** 点击时间线卡片 → 定位到下方对应日志行（尊重减少动效偏好）。 */
function scrollToLogEvent(id: string) {
  const row = document.querySelector(`[data-test="log-row-${id}"]`);
  if (!row) return;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  row.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
}

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
      <div class="min-w-0">
        <p class="mb-1.5 text-[9px] font-semibold tracking-[0.16em] text-primary uppercase">
          Research task
        </p>
        <h1 class="font-display text-2xl leading-snug font-medium tracking-[-0.025em]">
          {{ store.snapshot?.title ?? "任务详情" }}
        </h1>
        <p class="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <StatusBadge :status="store.snapshot?.status ?? 'created'" />
          <span class="text-xs">
            修订 {{ store.snapshot?.revision ?? "—" }} · 更新于
            {{ formatDateTime(store.snapshot?.updated_at) }}
          </span>
          <!-- 模型快照（Issue #15 BYOK）：凭据删除后仍能看到当时用的模型 -->
          <span v-if="store.snapshot" class="text-xs" data-test="task-model">
            {{
              store.snapshot.model
                ? `模型：${store.snapshot.model.name}（${store.snapshot.model.model}）`
                : "模型：平台默认"
            }}
          </span>
        </p>
      </div>

      <div class="flex shrink-0 items-center gap-2">
        <!-- 批注入口：抽屉默认收起，用计数提示有没有待看意见 -->
        <button
          type="button"
          data-test="toggle-annotation"
          :aria-expanded="annotationOpen"
          class="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium transition-colors hover:bg-muted"
          @click="annotationOpen = !annotationOpen"
        >
          <MessageSquare :size="15" aria-hidden="true" />
          <span>批注</span>
        </button>

        <!-- PRISMA 筛选工作台入口（Phase 2）：筛选数据跟着任务走 -->
        <RouterLink
          :to="{ name: 'task-screening', params: { taskId: props.taskId } }"
          data-test="open-screening"
          class="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium transition-colors hover:bg-muted"
        >
          <ListFilter :size="15" aria-hidden="true" />
          <span>PRISMA 筛选</span>
        </RouterLink>
      </div>
    </header>

    <!-- 需要用户行动时显式提示：这是本页唯一「你必须做点什么」的信息，
         以前只靠状态徽标暗示，容易被忽略。 -->
    <div
      v-if="needsAction"
      data-test="action-hint"
      class="mb-4 flex flex-col gap-1 rounded-lg border border-warning/30 bg-warning/8 px-3 py-2 text-sm"
    >
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-medium text-warning">
          {{
            store.snapshot?.status === "waiting_approval"
              ? "规划已生成，等你确认后开始执行"
              : "任务已暂停，等你介入"
          }}
        </span>
        <button
          v-if="activeTab !== 'plan' && store.snapshot?.status === 'waiting_approval'"
          type="button"
          class="text-xs text-warning underline underline-offset-2"
          @click="selectTab('plan')"
        >
          去确认规划 →
        </button>
      </div>
      <!-- 后端给的原因原文：不翻译，方便贴给管理员排查 -->
      <p
        v-if="waitingReason"
        data-test="waiting-reason"
        class="font-mono text-xs leading-5 break-words text-warning/90"
      >
        {{ waitingReason }}
      </p>
      <p
        v-if="waitingAdvice"
        data-test="waiting-advice"
        class="text-xs leading-5 text-muted-foreground"
      >
        {{ waitingAdvice }}
      </p>
    </div>

    <ErrorBanner :message="errorMessage" />

    <!-- 选项卡：下划线式，窄屏可横向滚动 -->
    <nav class="mb-4 flex gap-1 overflow-x-auto border-b border-border" role="tablist">
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
        :blocked="store.snapshot?.status === 'waiting_human' && !store.snapshot?.plan"
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

    <div v-else-if="activeTab === 'logs'" class="space-y-4">
      <!-- beUI 时间线：最近 12 条执行事件，默认聚焦最新一条；点击定位下方日志行 -->
      <div
        v-if="timelineEvents.length"
        data-test="event-timeline"
        class="rounded-3xl border border-border bg-card p-2"
      >
        <BeTimeline
          :events="timelineEvents"
          :height="360"
          :item-width="300"
          :initial-index="timelineEvents.length - 1"
          aria-label="执行事件时间线"
          @activate="scrollToLogEvent"
        >
          <template #media="{ event }">
            <div
              class="flex h-full w-full flex-col justify-between p-3"
              :class="eventToneClass(eventLevel(eventById(event.id)))"
            >
              <div class="flex items-center justify-between text-xs font-medium">
                <span>{{ EVENT_LEVEL_LABEL[eventLevel(eventById(event.id))] ?? "信息" }}</span>
                <span class="tabular-nums">#{{ eventById(event.id)?.event_id }}</span>
              </div>
              <p class="truncate text-[11px] opacity-80">
                {{ eventById(event.id)?.event_type }}
              </p>
            </div>
          </template>
          <template #meta="{ event }">
            <span>{{ formatDateTime(eventById(event.id)?.occurred_at) }}</span>
          </template>
        </BeTimeline>
        <p class="px-4 pb-2 text-xs text-muted-foreground">
          最近 {{ timelineEvents.length }} 条事件 · 点击卡片定位下方日志
        </p>
      </div>
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

    <!-- 导师批注（Phase 2）：**侧边抽屉**，默认收起。
         旧版把它常驻在四个 tab 底部（含大块空态），把结果/日志挤得很长；
         批注属于协作场景，不该在所有 tab 抢占纵向空间。 -->
    <Teleport to="body">
      <div
        v-if="annotationOpen"
        class="fixed inset-0 z-40 bg-foreground/20"
        data-test="annotation-backdrop"
        @click="annotationOpen = false"
      />
      <aside
        v-if="annotationOpen"
        data-test="annotation-drawer"
        role="dialog"
        aria-label="导师批注"
        class="fixed top-0 right-0 z-50 flex h-full w-full max-w-md flex-col border-l border-border bg-card shadow-raised"
      >
        <div class="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 class="text-sm font-semibold">导师批注</h2>
          <button
            type="button"
            data-test="close-annotation"
            aria-label="关闭批注"
            class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            @click="annotationOpen = false"
          >
            <X :size="16" aria-hidden="true" />
          </button>
        </div>
        <div class="min-h-0 flex-1 overflow-y-auto p-4">
          <AnnotationPanel :task-id="props.taskId" :current-user-id="currentUserId" bare />
        </div>
      </aside>
    </Teleport>
  </section>
</template>
