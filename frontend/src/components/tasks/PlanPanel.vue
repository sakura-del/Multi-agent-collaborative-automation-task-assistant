<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { CheckCircle2, Circle, Loader2 } from "lucide-vue-next";
import BeAgentPlan from "@/components/motion/BeAgentPlan.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import TaskDag from "@/components/tasks/TaskDag.vue";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { TaskPlan } from "@/api/types";

// 规划确认面板：只提供"重新生成"与"确认规划"两个命令（不可编辑规划内容）
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
//
// 「重新生成」发的是 `replan`（退回规划阶段），不是节点重跑：规划失败时
// 任务还没有任何子任务，`retry_node` 只会 404。
//
// ⚠️ 测试契约（不要改）：data-test="plan-panel" / "plan-conflict" / "plan-error" /
// "plan-regenerate" / "plan-approve"。
const props = withDefaults(
  defineProps<{
    plan: TaskPlan | null;
    pending?: boolean;
    /** 当前是否处于规划生成阶段（status === 'planning' 且尚未出图） */
    planning?: boolean;
    /** 后端推送的最新规划子阶段事件 */
    planningProgress?: { stage: string; message: string } | null;
    /** 规划开始时间戳（ISO 字符串），用于计算实时等待耗时 */
    planningStartedAt?: string | null;
    error?: string | null;
    conflict?: string | null;
    /** 规划被阻断（转人工且没有 plan）：空态要说「没生成」，不是「还没开始」。 */
    blocked?: boolean;
    /** 当前状态是否允许重新规划（与后端白名单一致）；false 时连按钮都不出现。 */
    canReplan?: boolean;
    /**
     * 当前状态是否允许确认规划（只有 waiting_approval）。
     *
     * false 时按钮不渲染并给出原因：`waiting_human` 下旧规划仍在快照里，
     * 若照常渲染，用户点了只会拿到 422（Issue #23）。
     */
    canApprove?: boolean;
    /** 节点步骤（beUI Agent Plan 形态，TaskDetailView 组装）；空 = 不渲染卡片 */
    steps?: import("@/components/motion/BeAgentPlan.vue").AgentPlanStep[];
    /** 规划面板级状态徽章 */
    planStatus?: "ready" | "running" | "completed" | "failed" | "draft";
  }>(),
  {
    pending: false,
    planning: false,
    planningProgress: null,
    planningStartedAt: null,
    error: null,
    conflict: null,
    blocked: false,
    canReplan: true,
    canApprove: true,
  }
);

const PLANNING_STEPS = [
  { key: "binding_model", label: "加载模型与任务配置" },
  { key: "generating_plan", label: "拆解研究问题、检索式与 DAG" },
  { key: "validating_contract", label: "校验契约与依赖无环性" },
  { key: "persisting_plan", label: "保存规划并准备确认" },
] as const;

const STAGE_ORDER: Record<string, number> = {
  binding_model: 0,
  generating_plan: 1,
  retrying_upstream: 1,
  validating_contract: 2,
  repairing_plan: 2,
  persisting_plan: 3,
};

const activeStepIndex = computed(() => {
  const stage = props.planningProgress?.stage ?? "generating_plan";
  return STAGE_ORDER[stage] ?? 1;
});

const elapsedSeconds = ref(0);
let timer: ReturnType<typeof setInterval> | null = null;

function updateElapsed() {
  const base = props.planningStartedAt ? new Date(props.planningStartedAt).getTime() : Date.now();
  elapsedSeconds.value = Number.isFinite(base) ? Math.max(0, Math.floor((Date.now() - base) / 1000)) : 0;
}

function stopTimer() {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

watch(
  () => props.planning && !props.plan,
  (active) => {
    stopTimer();
    if (active) {
      updateElapsed();
      timer = setInterval(updateElapsed, 1000);
    }
  },
  { immediate: true },
);

onBeforeUnmount(stopTimer);

const emit = defineEmits<{
  approve: [version: number];
  regenerate: [];
  retry: [stepId: string];
  dismissError: [];
  dismissConflict: [];
}>();

/** 不可确认规划时按真实执行阶段给出准确说明，避免已完成任务误报「已暂停在执行阶段」（Issue #55）。 */
const approveHint = computed(() => {
  if (props.planStatus === "completed") {
    return "该规划已确认并执行完成，可切到「执行」或「结果」页查看完整产出。";
  }
  if (props.planStatus === "running") {
    return "该规划已确认，任务正在执行中，可切到「执行」页查看实时进度。";
  }
  if (props.planStatus === "failed") {
    return props.canReplan
      ? "该规划已确认，任务已暂停在执行阶段；请到「执行」页重试失败的节点，或点上方「重新生成」换一版规划。"
      : "该规划已确认，任务已暂停在执行阶段；请到「执行」页检查失败的节点。";
  }
  return "该规划已确认，当前任务状态不可重复确认规划。";
});
</script>

<template>
  <section class="flex flex-col gap-4" data-test="plan-panel">
    <ErrorBanner
      :message="props.conflict"
      testid="plan-conflict"
      @dismiss="emit('dismissConflict')"
    />
    <ErrorBanner
      :message="props.error"
      testid="plan-error"
      @dismiss="emit('dismissError')"
    />

    <template v-if="props.plan">
      <header class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 class="text-base font-semibold tracking-tight break-words">
            {{ props.plan.topic }}
          </h2>
          <p class="mt-0.5 text-sm text-muted-foreground">
            规划版本 v{{ props.plan.version }} · {{ props.plan.nodes.length }} 个节点
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <Button
            v-if="props.canReplan"
            variant="outline"
            data-test="plan-regenerate"
            type="button"
            :disabled="props.pending"
            @click="emit('regenerate')"
          >
            重新生成
          </Button>
          <Button
            v-if="props.canApprove"
            data-test="plan-approve"
            type="button"
            :disabled="props.pending"
            @click="emit('approve', props.plan.version)"
          >
            确认规划
          </Button>
        </div>
      </header>

      <!-- 只有 waiting_approval 能确认；其它状态给出真实原因，而不是让用户点了报错 -->
      <p
        v-if="!props.canApprove"
        class="text-sm text-muted-foreground"
        data-test="plan-approve-hint"
      >
        {{ approveHint }}
      </p>

      <div class="grid gap-3 md:grid-cols-3">
        <Card class="gap-1.5 p-3">
          <h3 class="mb-1.5 text-sm font-medium">研究问题</h3>
          <ol class="list-decimal space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="question in props.plan.research_questions" :key="question">
              {{ question }}
            </li>
          </ol>
        </Card>
        <Card class="gap-1.5 p-3">
          <h3 class="mb-1.5 text-sm font-medium">检索式</h3>
          <ul class="list-disc space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="query in props.plan.search_queries" :key="query">{{ query }}</li>
          </ul>
        </Card>
        <Card class="gap-1.5 p-3">
          <h3 class="mb-1.5 text-sm font-medium">验收标准</h3>
          <ul class="list-disc space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="item in props.plan.acceptance_criteria" :key="item">{{ item }}</li>
          </ul>
        </Card>
      </div>

      <section class="flex flex-col gap-2">
        <h3 class="text-sm font-medium">执行计划</h3>
        <BeAgentPlan
          :title="props.plan.topic"
          :steps="props.steps ?? []"
          :plan-status="props.planStatus ?? 'ready'"
          :revision="props.plan.version"
          :pending="props.pending"
          :show-actions="false"
          @retry="emit('retry', $event)"
        />
        <details class="mt-1 rounded-lg border border-border/70 bg-card/50 px-3 py-2">
          <summary class="cursor-pointer text-xs text-muted-foreground">查看经典 DAG 视图</summary>
          <div class="mt-2">
            <TaskDag :nodes="props.plan.nodes" />
          </div>
        </details>
      </section>
    </template>

    <Card
      v-else-if="props.planning"
      class="flex flex-col gap-4 p-5"
      data-test="plan-progress"
      aria-live="polite"
    >
      <div class="flex items-center justify-between gap-3">
        <div class="flex items-center gap-2.5">
          <Loader2
            class="size-4 animate-spin text-primary motion-reduce:animate-none"
            aria-hidden="true"
          />
          <span class="text-sm font-medium text-foreground" data-test="plan-progress-message">
            {{ props.planningProgress?.message ?? "正在规划研究问题、检索式与任务 DAG…" }}
          </span>
        </div>
        <span class="text-xs tabular-nums text-muted-foreground" data-test="plan-progress-elapsed">
          已等待 {{ elapsedSeconds }}s
        </span>
      </div>

      <ol class="grid gap-2 sm:grid-cols-2">
        <li
          v-for="(step, idx) in PLANNING_STEPS"
          :key="step.key"
          :data-test="`plan-progress-step-${step.key}`"
          :data-state="idx < activeStepIndex ? 'done' : idx === activeStepIndex ? 'active' : 'pending'"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-xs"
          :class="
            idx < activeStepIndex
              ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400'
              : idx === activeStepIndex
                ? 'border-primary/40 bg-primary/5 font-medium text-foreground'
                : 'border-border bg-muted/20 text-muted-foreground'
          "
        >
          <CheckCircle2 v-if="idx < activeStepIndex" class="size-3.5 shrink-0" aria-hidden="true" />
          <Loader2
            v-else-if="idx === activeStepIndex"
            class="size-3.5 shrink-0 animate-spin text-primary motion-reduce:animate-none"
            aria-hidden="true"
          />
          <Circle v-else class="size-3.5 shrink-0 opacity-40" aria-hidden="true" />
          <span>{{ step.label }}</span>
        </li>
      </ol>

      <p v-if="elapsedSeconds >= 30" class="text-xs text-muted-foreground">
        复杂主题或模型高峰期规划耗时较长，可离开页面稍后回来，或在左侧继续发起其他任务。
      </p>
    </Card>

    <EmptyState
      v-else
      :title="props.blocked ? '规划没有生成' : '暂无规划'"
      :description="
        props.blocked
          ? '任务在规划阶段停下，具体原因见页面顶部提示。处理后再点「重新规划」。'
          : '任务进入规划阶段后，这里会展示研究问题、检索式与任务 DAG。'
      "
    >
      <!-- 没有规划时唯一能做的事：让后端重新走一遍规划（规划失败时没有任何
           子任务，节点重跑一定 404）。 -->
      <Button
        v-if="props.canReplan"
        variant="outline"
        data-test="plan-regenerate"
        type="button"
        :disabled="props.pending"
        @click="emit('regenerate')"
      >
        {{ props.blocked ? "重新规划" : "重新生成" }}
      </Button>
    </EmptyState>
  </section>
</template>
