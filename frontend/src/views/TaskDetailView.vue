<script setup lang="ts">
import BeTraceWaterfall, { type TraceSpan } from "@/components/motion/BeTraceWaterfall.vue";
import BeAgentPlan, { type AgentPlanStep } from "@/components/motion/BeAgentPlan.vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ListFilter, MessageSquare, X } from "lucide-vue-next";
import { Motion, useReducedMotion } from "motion-v";

import BeTimeline from "@/components/motion/BeTimeline.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import AnnotationPanel from "@/components/collaboration/AnnotationPanel.vue";
import NodeDetail from "@/components/tasks/NodeDetail.vue";
import PlanPanel from "@/components/tasks/PlanPanel.vue";
import TaskDag from "@/components/tasks/TaskDag.vue";
import TaskRefreshPanel from "@/components/tasks/TaskRefreshPanel.vue";
import TaskLog from "@/components/tasks/TaskLog.vue";
import TaskResult from "@/components/tasks/TaskResult.vue";
import TaskToolbar from "@/components/tasks/TaskToolbar.vue";
import { Button } from "@/components/ui/button";
import { MotionDrawer } from "@/components/ui/drawer";
import { useDelayedLoading } from "@/composables/useDelayedLoading";
import { SPRING_LAYOUT } from "@/lib/ease";
import { useAuthStore } from "@/stores/auth";
import { useTaskStore } from "@/stores/tasks";
import type { PlanNode, SubtaskStatus, TaskControl, TaskEvent } from "@/api/types";
import {
  canApprovePlan,
  formatDateTime,
  needsUserAction,
} from "@/utils/task-status";
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
const tabGroupId = useId();
const reduced = useReducedMotion();
const showSkeleton = useDelayedLoading(() => store.detailLoading && !store.snapshot);

// ===== WS 连接微指示器状态机 =====
const showRecovered = ref(false);
let recoverTimer: ReturnType<typeof setTimeout> | null = null;

function clearRecoverTimer() {
  if (recoverTimer !== null) {
    clearTimeout(recoverTimer);
    recoverTimer = null;
  }
}

watch(
  () => store.connectionState,
  (next, prev) => {
    clearRecoverTimer();
    if ((prev === "reconnecting" || prev === "polling") && next === "connected") {
      showRecovered.value = true;
      recoverTimer = setTimeout(() => {
        showRecovered.value = false;
        recoverTimer = null;
      }, 1500);
    } else {
      showRecovered.value = false;
    }
  },
);

// ===== 执行节点选中脉冲与窄屏定位 =====
const pulsingDetail = ref(false);
let pulseTimer: ReturnType<typeof setTimeout> | null = null;

async function selectExecutionNode(nodeId: string) {
  selectedNodeId.value = nodeId;
  pulsingDetail.value = true;
  if (pulseTimer !== null) clearTimeout(pulseTimer);
  pulseTimer = setTimeout(() => {
    pulsingDetail.value = false;
    pulseTimer = null;
  }, 800);
  if (typeof window !== "undefined" && window.innerWidth < 1280) {
    await nextTick();
    const detailEl = document.querySelector('[data-test="node-detail"]') as HTMLElement | null;
    detailEl?.scrollIntoView?.({
      block: "nearest",
      behavior: reduced.value ? "auto" : "smooth",
    });
  }
}

const activeTab = computed<TabKey>(() => {
  const value = String(route.query.tab ?? "plan");
  return (TABS as readonly string[]).includes(value) ? (value as TabKey) : "plan";
});

const resultAvailable = computed(() => Boolean(store.snapshot?.review_report));

/** 当前状态是否需要用户行动（决定头部是否出现提示）。 */
const needsAction = computed(() => needsUserAction(store.snapshot?.status ?? "created"));

/**
 * 是否允许「重新规划」。
 *
 * 与后端 `_control_replan` 的白名单一一对应：状态不在其中时后端会 422，
 * 按钮就不该出现（而不是让用户点了才报错）。执行中/已完成的任务重规划
 * 会把已经跑完的节点全部作废，属于误操作。
 */
const REPLANNABLE_STATUSES = new Set(["waiting_approval", "waiting_human", "failed"]);
const canReplan = computed(() => REPLANNABLE_STATUSES.has(store.snapshot?.status ?? ""));

/**
 * 是否允许「确认规划」：与后端 `approve_plan` 一致，只有 waiting_approval。
 *
 * 执行失败转 waiting_human 后旧规划仍在快照里，面板照常渲染——
 * 若不禁用按钮，用户点击只会拿到 422（Issue #23 实测）。
 */
const canApprove = computed(() =>
  canApprovePlan((store.snapshot?.status ?? "") as Parameters<typeof canApprovePlan>[0]),
);

/** beUI Agent Plan 形态：plan.nodes × 子任务状态 → 步骤列表（Issue #22 延伸） */
const planSteps = computed<AgentPlanStep[]>(() => {
  const subtasks = store.snapshot?.subtasks ?? [];
  const byNode = new Map(subtasks.map((item) => [item.node_id, item]));
  return (store.snapshot?.plan?.nodes ?? []).map((node) => {
    const subtask = byNode.get(node.id);
    let status: AgentPlanStep["status"] = "pending";
    let durationMs: number | undefined;
    let error: string | undefined;
    if (subtask) {
      status =
        subtask.status === "completed"
          ? "completed"
          : subtask.status === "failed" || subtask.status === "waiting_human"
            ? "failed"
            : subtask.status === "running" || subtask.status === "validating"
              ? "running"
              : "pending";
      if (subtask.started_at && subtask.completed_at) {
        durationMs = Math.max(
          0,
          new Date(subtask.completed_at).getTime() - new Date(subtask.started_at).getTime(),
        );
      }
      error = subtask.error ?? undefined;
    } else if (store.snapshot?.status === "running") {
      status = node.dependencies.every((dep) => byNode.get(dep)?.status === "completed")
        ? "running"
        : "pending";
    }
    return {
      id: node.id,
      title: node.title,
      kind: node.type,
      status,
      durationMs,
      error,
    };
  });
});

/** 任务级规划徽章状态 */
const agentPlanStatus = computed<AgentPlanStep["status"] extends never ? never : "ready" | "running" | "completed" | "failed" | "draft">(() => {
  const status = store.snapshot?.status ?? "";
  if (status === "waiting_approval") return "ready";
  if (status === "running" || status === "reviewing" || status === "planning") return "running";
  if (status === "completed") return "completed";
  if (status === "failed" || status === "waiting_human") return "failed";
  return "draft";
});

/** beUI Trace Waterfall：子任务 started/completed → span 列表（Issue #22 延伸） */
const traceRunName = computed(
  () => `trace_${(store.snapshot?.task_id ?? "").slice(0, 8)}`,
);

const traceSpans = computed<TraceSpan[]>(() => {
  const subtasks = (store.snapshot?.subtasks ?? []).filter(
    (item) => item.started_at !== null,
  );
  if (!subtasks.length) return [];
  const startBase = Math.min(
    ...subtasks.map((item) => new Date(item.started_at as string).getTime()),
  );
  const kindByNode = new Map(
    (store.snapshot?.plan?.nodes ?? []).map((node) => [node.id, node.type]),
  );
  const now = Date.now();
  return subtasks.map((item) => {
    const startMs = new Date(item.started_at as string).getTime() - startBase;
    const endMs = item.completed_at
      ? new Date(item.completed_at).getTime()
      : now;
    const kind = kindByNode.get(item.node_id) ?? "extract";
    return {
      id: item.node_id,
      name: item.title,
      kind: kind === "search" ? "search" : kind === "validate" ? "validate" : "extract",
      startMs: Math.max(0, startMs),
      durationMs: Math.max(250, endMs - new Date(item.started_at as string).getTime()),
      status:
        item.status === "completed"
          ? "completed"
          : item.status === "failed"
            ? "failed"
            : "running",
      error: item.error ?? undefined,
    } satisfies TraceSpan;
  });
});

/** Agent Plan 行内「重试节点」：走 retry_node 命令（与执行页工具栏同路径） */
async function handleRetryStep(stepId: string) {
  if (pending.value) return;
  selectedNodeId.value = stepId;
  const ok = await handleControl({ command: "retry_node", node_id: stepId });
  if (ok) {
    pending.value = true;
    try {
      await refresh();
    } finally {
      pending.value = false;
    }
  }
}

function selectTab(tab: TabKey) {
  if (tab === "result" && !resultAvailable.value) {
    return;
  }
  void router.replace({ query: { ...route.query, tab } });
}

const isPlanning = computed(() => store.snapshot?.status === "planning");

const planningProgress = computed<{ stage: string; message: string } | null>(() => {
  const events = store.events ?? [];
  const legacyStageMap: Record<string, string> = {
    planning_llm: "generating_plan",
    planning_repair: "repairing_plan",
    planning_retry: "retrying_upstream",
  };
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const evt = events[i];
    if (evt.event_type === "plan_progress") {
      const payload = (evt.payload ?? {}) as Record<string, unknown>;
      return {
        stage: typeof payload.stage === "string" ? payload.stage : "generating_plan",
        message: typeof payload.message === "string" ? payload.message : "正在生成规划…",
      };
    }
    if (evt.event_type === "stage_progress") {
      const payload = (evt.payload ?? {}) as Record<string, unknown>;
      const rawStage = typeof payload.stage === "string" ? payload.stage : "";
      if (rawStage in legacyStageMap) {
        return {
          stage: legacyStageMap[rawStage],
          message: typeof payload.message === "string" ? payload.message : "正在生成规划…",
        };
      }
    }
  }
  return null;
});

const planningStartedAt = computed(
  () => store.snapshot?.updated_at ?? store.snapshot?.created_at ?? null,
);

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
    // 必须是 replan 而不是 retry_node n1：规划失败时任务还没有任何子任务，
    // 按节点重跑只会 404（生产实测 2026-11-17），用户唯一能点的动作是坏的。
    await store.control(props.taskId, { command: "replan" });
    await refresh();
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    pending.value = false;
  }
}

async function handleControl(payload: TaskControl): Promise<boolean> {
  if (pending.value) return false;
  pending.value = true;
  errorMessage.value = null;
  try {
    await store.control(props.taskId, payload);
    return true;
  } catch (error) {
    errorMessage.value = (error as Error).message;
    return false;
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
const waitingReason = computed(() => {
  const status = store.snapshot?.status ?? "";
  const fromEvents = latestWaitingReason(store.events, status);
  if (fromEvents) return fromEvents;
  if (status === "waiting_human") return store.snapshot?.waiting_reason ?? "";
  if (status === "failed") return store.snapshot?.error ?? "";
  return "";
});
const waitingAdvice = computed(() => adviceForWaitingReason(waitingReason.value));
const failedError = computed(() =>
  store.snapshot?.status === "failed" ? (store.snapshot.error ?? waitingReason.value) : "",
);

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
  clearRecoverTimer();
  if (pulseTimer !== null) clearTimeout(pulseTimer);
  store.stopWatching();
});
</script>

<template>
  <section class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_17.5rem] lg:items-start">
    <!-- 左侧主工作区：标题、行动提示、Tab 与内容 -->
    <div class="min-w-0">
      <header class="mb-4 min-h-[1.75rem]">
        <div
          v-if="!store.snapshot && !errorMessage"
          class="h-7 w-72 rounded-md"
          :class="showSkeleton ? 'animate-pulse bg-muted/60 motion-reduce:animate-none' : ''"
        />
        <h1 v-else class="text-xl leading-snug font-semibold tracking-tight text-foreground">
          {{ store.snapshot?.title ?? "任务详情" }}
        </h1>
      </header>

      <!-- 需要用户行动时显式提示 -->
      <div
        v-if="needsAction"
        data-test="action-hint"
        class="mb-4 flex flex-col gap-1 rounded-md border border-warning/30 bg-warning/8 px-3 py-2 text-sm"
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
            v-if="activeTab !== 'plan' && canReplan"
            type="button"
            class="text-xs text-warning underline underline-offset-2"
            @click="selectTab('plan')"
          >
            {{ store.snapshot?.status === "waiting_approval" ? "去确认规划" : "去重新规划" }} →
          </button>
        </div>
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

      <div
        v-else-if="failedError"
        data-test="failed-reason"
        class="mb-4 flex flex-col gap-1 rounded-md border border-destructive/30 bg-destructive/8 px-3 py-2 text-sm"
      >
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-medium text-destructive">任务执行失败</span>
          <button
            v-if="activeTab !== 'plan' && canReplan"
            type="button"
            class="text-xs text-destructive underline underline-offset-2"
            @click="selectTab('plan')"
          >
            去重新规划 →
          </button>
        </div>
        <p class="font-mono text-xs leading-5 break-words text-destructive/90">
          {{ failedError }}
        </p>
      </div>

      <ErrorBanner :message="errorMessage" @dismiss="errorMessage = null" />

      <!-- 选项卡：共享 layoutId 滑块（useId() 实例隔离），显式 overflow-y-hidden 防止窄屏纵向滚动箭头（Issue #57） -->
      <nav
        class="mb-4 flex gap-1 overflow-x-auto overflow-y-hidden border-b border-border"
        role="tablist"
      >
        <button
          v-for="tab in TABS"
          :key="tab"
          class="relative shrink-0 whitespace-nowrap px-3 py-2 text-xs font-medium transition-colors"
          :class="
            activeTab === tab
              ? 'text-foreground'
              : 'text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50'
          "
          :data-test="`tab-${tab}`"
          role="tab"
          type="button"
          :aria-selected="activeTab === tab"
          :disabled="tab === 'result' && !resultAvailable"
          :title="tab === 'result' && !resultAvailable ? '任务完成复盘后可用' : undefined"
          @click="selectTab(tab)"
        >
          {{ { plan: "规划", execution: "执行", logs: "日志", result: "结果" }[tab] }}
          <Motion
            v-if="activeTab === tab"
            :layout-id="reduced ? undefined : `tab-pill-${tabGroupId}`"
            :transition="SPRING_LAYOUT"
            class="pointer-events-none absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-primary"
          />
        </button>
      </nav>

      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <TaskToolbar
          :status="store.snapshot?.status ?? 'created'"
          :pending="pending"
          :selected-node-id="selectedNodeId"
          :selected-node-status="selectedSubtask?.status ?? null"
          @command="handleControl"
        />
        <span
          v-if="store.connectionState !== 'connected' || showRecovered"
          data-test="ws-connection-indicator"
          class="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-0.5 text-[11px] text-muted-foreground"
        >
          <span
            class="size-1.5 rounded-full"
            :class="
              store.connectionState === 'reconnecting'
                ? 'animate-pulse bg-warning motion-reduce:animate-none'
                : store.connectionState === 'polling'
                  ? 'bg-muted-foreground'
                  : 'bg-success'
            "
            aria-hidden="true"
          />
          <span v-if="store.connectionState === 'reconnecting'">实时连接重连中…</span>
          <span v-else-if="store.connectionState === 'polling'">已切换为定时同步</span>
          <span v-else>实时连接已恢复</span>
        </span>
      </div>

      <div v-if="!store.snapshot && !errorMessage" class="min-h-[24rem] space-y-3" aria-busy="true">
        <template v-if="showSkeleton">
          <div class="h-24 animate-pulse rounded-lg border border-border bg-muted/40 motion-reduce:animate-none" />
          <div class="h-36 animate-pulse rounded-lg border border-border bg-muted/40 motion-reduce:animate-none" />
          <div class="h-24 animate-pulse rounded-lg border border-border bg-muted/40 motion-reduce:animate-none" />
        </template>
      </div>

      <div v-else-if="activeTab === 'plan'">
        <PlanPanel
          :plan="store.snapshot?.plan ?? null"
          :pending="pending"
          :planning="isPlanning"
          :planning-progress="planningProgress"
          :planning-started-at="planningStartedAt"
          :blocked="store.snapshot?.status === 'waiting_human' && !store.snapshot?.plan"
          :can-replan="canReplan"
          :can-approve="canApprove"
          :steps="planSteps"
          :plan-status="agentPlanStatus"
          :error="errorMessage"
          :conflict="conflictMessage"
          @approve="handleApprove"
          @regenerate="handleRegenerate"
          @retry="handleRetryStep"
          @dismiss-error="errorMessage = null"
          @dismiss-conflict="conflictMessage = null"
        />
      </div>

      <div
        v-else-if="activeTab === 'execution'"
        class="grid gap-3 xl:grid-cols-[minmax(0,1fr)_18rem]"
      >
        <div class="min-w-0">
          <TaskDag
            v-if="store.snapshot?.plan"
            :nodes="store.snapshot.plan.nodes"
            :statuses="statuses"
            :selected="selectedNodeId"
            @select="selectExecutionNode"
          />
          <p v-else class="text-sm text-muted-foreground">暂无规划，无法展示 DAG。</p>
          <BeTraceWaterfall
            v-if="traceSpans.length"
            :run-name="traceRunName"
            :spans="traceSpans"
            class="mt-3"
          />
        </div>
        <NodeDetail
          v-if="selectedNode"
          :node="selectedNode"
          :subtask="selectedSubtask"
          :siblings="store.snapshot?.subtasks ?? []"
          :class="
            pulsingDetail
              ? 'border-primary ring-2 ring-primary/40 transition-colors duration-150'
              : ''
          "
          @close="selectedNodeId = null"
        />
        <div
          v-else
          class="flex h-fit items-center justify-center rounded-2xl border border-dashed border-border bg-card/40 px-4 py-6 text-center text-xs text-muted-foreground"
        >
          点击左侧节点查看执行耗时与输出详情
        </div>
      </div>

      <div v-else-if="activeTab === 'logs'" class="space-y-4">
        <div
          v-if="timelineEvents.length"
          data-test="event-timeline"
          class="rounded-lg border border-border bg-card p-2"
        >
          <BeTimeline
            :events="timelineEvents"
            :height="340"
            :item-width="290"
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
          <p class="px-3 pb-1 text-xs text-muted-foreground">
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
          @refresh="refresh"
        />
        <EmptyState
          v-else
          title="任务尚未产出复盘报告"
          description="任务执行完成后，这里会展示复盘结论、引用核验与导出入口。"
        />
      </div>

      <!-- 持续更新（Issue #27）：放在主体内容区下方，避免异步加载完成后将 Tab 与内容区向下推挤导致 CLS -->
      <TaskRefreshPanel
        class="mt-6"
        :task-id="props.taskId"
        :status="store.snapshot?.status ?? 'created'"
      />
    </div>

    <!-- 右侧属性检查器（Linear Inspector）：窄屏置顶紧凑展示，宽屏吸顶固定 -->
    <aside
      aria-label="任务属性与快捷操作"
      class="flex flex-col gap-3 rounded-lg border border-border bg-card p-3.5 max-lg:order-first lg:sticky lg:top-16"
    >
      <div class="flex items-center justify-between border-b border-border pb-2.5">
        <span class="text-[11px] font-medium tracking-wider text-muted-foreground uppercase">
          任务属性
        </span>
        <StatusBadge :status="store.snapshot?.status ?? 'created'" />
      </div>

      <dl class="grid gap-2.5 text-xs">
        <div class="flex items-baseline justify-between gap-2">
          <dt class="text-muted-foreground">修订</dt>
          <dd class="font-medium tabular-nums">v{{ store.snapshot?.revision ?? "—" }}</dd>
        </div>
        <div class="flex items-baseline justify-between gap-2">
          <dt class="text-muted-foreground">更新于</dt>
          <dd class="text-right tabular-nums">{{ formatDateTime(store.snapshot?.updated_at) }}</dd>
        </div>
        <div class="flex flex-col gap-1 border-t border-border pt-2.5">
          <dt class="text-muted-foreground">使用模型</dt>
          <dd class="font-medium break-words" data-test="task-model">
            <span v-if="!store.snapshot" class="inline-block h-4 w-24 animate-pulse rounded bg-muted/60 motion-reduce:animate-none" />
            <template v-else>
              {{
                store.snapshot.model
                  ? `${store.snapshot.model.name}（${store.snapshot.model.model}）`
                  : "平台默认"
              }}
            </template>
          </dd>
        </div>
      </dl>

      <div class="grid grid-cols-2 gap-2 border-t border-border pt-3 lg:grid-cols-1">
        <button
          type="button"
          data-test="toggle-annotation"
          :aria-expanded="annotationOpen"
          class="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs font-medium transition-colors hover:bg-muted"
          @click="annotationOpen = !annotationOpen"
        >
          <MessageSquare :size="13" aria-hidden="true" />
          <span>批注</span>
        </button>

        <RouterLink
          :to="{ name: 'task-screening', params: { taskId: props.taskId } }"
          data-test="open-screening"
          class="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs font-medium transition-colors hover:bg-muted"
        >
          <ListFilter :size="13" aria-hidden="true" />
          <span>PRISMA 筛选</span>
        </RouterLink>
      </div>

      <!-- 平台回退标注（Issue #28） -->
      <div
        v-if="store.snapshot?.actual_provider"
        data-test="task-platform-fallback"
        class="rounded-md border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-[11px] leading-snug text-warning"
      >
        已回退平台模型（{{ store.snapshot.actual_model ?? "平台" }}）· 本次消耗平台额度
      </div>
    </aside>

    <!-- 导师批注（Phase 2）：**侧边抽屉**，默认收起。
         旧版把它常驻在四个 tab 底部（含大块空态），把结果/日志挤得很长；
         批注属于协作场景，不该在所有 tab 抢占纵向空间。 -->
    <MotionDrawer
      v-model:open="annotationOpen"
      side="right"
      width-class="w-full max-w-md"
      aria-label="导师批注"
      :backdrop-attrs="{ 'data-test': 'annotation-backdrop' }"
      :panel-attrs="{ 'data-test': 'annotation-drawer' }"
    >
      <div class="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 class="text-sm font-semibold">导师批注</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          data-test="close-annotation"
          aria-label="关闭批注"
          @click="annotationOpen = false"
        >
          <X :size="16" aria-hidden="true" />
        </Button>
      </div>
      <div class="min-h-0 flex-1 overflow-y-auto p-4">
        <AnnotationPanel :task-id="props.taskId" :current-user-id="currentUserId" bare />
      </div>
    </MotionDrawer>
  </section>
</template>
