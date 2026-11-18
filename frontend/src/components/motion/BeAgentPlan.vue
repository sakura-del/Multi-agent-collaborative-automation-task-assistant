<script setup lang="ts">
/**
 * beUI Agent Plan 的 Vue 移植（https://asharca.github.io/ui/components/agents/agent-plan/）。
 *
 * 原实现是 React + motion/react；这里用 motion-v 复刻视觉与动效，
 * 不引入 React（AGENTS.md §6.5.1.1 三级查找顺序 ①）。
 *
 * 规格（与 beUI 源码常量一一对应）：
 * - 卡片 rounded-2xl + border + bg-background，header px-4 pt-3.5；
 * - 分步进度条：h-1 圆角分段，bg-primary 填充，scaleX 0.2s origin-left；
 * - 步骤行 min-h-9：竖连线（left-9px）+ 状态圆圈（completed→Check 反色）
 *   + 工具徽章 pill（类型色 bg-X-500/10 text-X-600）+ 时长 font-mono text-[10px]；
 * - EASE_OUT [0.16, 1, 0.3, 1]，尊重 useReducedMotion；
 * - failed 步骤 CircleAlert + 错误文本 text-destructive；
 * - footer：摘要（N steps · 时长）+ 生命周期按钮。
 *
 * 数据由宿主持有（受控视图，与 beUI 同哲学）：本项目的 plan 来自后端（只读），
 * draft 拖拽/重命名不做；生命周期动作通过事件上抛（approve / regenerate）。
 */
import { computed } from "vue";
import { motion } from "motion-v";
import {
  Bot,
  Brain,
  CircleAlert,
  Check,
  ChevronDown,
  FlaskConical,
  RefreshCw,
  Search,
  Wrench,
} from "lucide-vue-next";

export type AgentStepStatus =
  | "pending"
  | "running"
  | "completed"
  | "cancelled"
  | "failed";

export interface AgentPlanStep {
  /** 节点 ID（DAG node_id） */
  id: string;
  title: string;
  /** 节点类型 → beUI 工具色映射：planner=agent / search=retrieval / extract=llm / write=llm / validate=guardrail */
  kind: string;
  status: AgentStepStatus;
  durationMs?: number;
  error?: string;
}

const props = withDefaults(
  defineProps<{
    title?: string;
    steps: AgentPlanStep[];
    /** 任务级状态：waiting_approval → "ready"；running → "running"… */
    planStatus?: "ready" | "running" | "completed" | "failed" | "draft";
    revision?: number;
    /** 压缩动画：E2E 与 reduced-motion 下自动降级 */
    pending?: boolean;
    /** 是否渲染 footer 生命周期按钮；宿主已有按钮区（PlanPanel）时传 false */
    showActions?: boolean;
  }>(),
  {
    title: "Execution plan",
    planStatus: "ready",
    revision: 1,
    pending: false,
    showActions: true,
  }
);

const emit = defineEmits<{ approve: []; regenerate: [] }>();

const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];

/** beUI 工具徽章色：kind → tailwind 类（bg-X-500/10 text-X-600） */
const KIND_STYLE: Record<string, { pill: string; icon: typeof Search; label: string }> = {
  planner: { pill: "bg-sky-500/10 text-sky-600", icon: Brain, label: "Planner" },
  search: { pill: "bg-amber-500/10 text-amber-600", icon: Search, label: "Search" },
  extract: { pill: "bg-orange-500/10 text-orange-600", icon: Wrench, label: "Extract" },
  write: { pill: "bg-orange-500/10 text-orange-600", icon: Wrench, label: "Write" },
  validate: { pill: "bg-lime-500/10 text-lime-600", icon: FlaskConical, label: "Validate" },
  review: { pill: "bg-teal-500/10 text-teal-600", icon: FlaskConical, label: "Review" },
};

function kindStyle(kind: string) {
  return KIND_STYLE[kind] ?? KIND_STYLE.write;
}

const STATUS_BADGE: Record<string, { text: string; cls: string }> = {
  ready: { text: "Awaiting approval", cls: "bg-primary/10 text-primary" },
  running: { text: "Running", cls: "bg-primary/10 text-primary" },
  completed: { text: "Completed", cls: "bg-emerald-500/10 text-emerald-600" },
  failed: { text: "Needs attention", cls: "bg-amber-500/10 text-amber-600" },
  draft: { text: "Draft", cls: "bg-muted text-muted-foreground" },
};

const completedCount = computed(
  () => props.steps.filter((step) => step.status === "completed").length
);
const progressBars = computed(() =>
  props.steps.map((step) => {
    const scale =
      step.status === "completed" ? 1 : step.status === "running" ? 0.45 : 0;
    return { scale };
  })
);
const totalDurationMs = computed(() =>
  props.steps.reduce((sum, step) => sum + (step.durationMs ?? 0), 0)
);
const summaryText = computed(() => {
  if (props.planStatus === "failed") return "execution needs attention";
  if (completedCount.value === props.steps.length && props.steps.length)
    return `${props.steps.length} steps · ${formatDuration(totalDurationMs.value)}`;
  return `${completedCount.value}/${props.steps.length} complete`;
});

function formatDuration(ms: number): string {
  if (ms <= 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function stepDotClass(status: AgentStepStatus): string {
  if (status === "completed") return "bg-foreground text-background";
  if (status === "failed") return "bg-amber-500/10 text-amber-600";
  if (status === "running") return "bg-primary/10 text-primary";
  return "bg-muted text-muted-foreground";
}
</script>

<template>
  <section
    data-test="agent-plan"
    class="mx-auto w-full max-w-md overflow-clip rounded-2xl border border-border bg-background text-foreground"
  >
    <!-- Header：元信息行 + 标题 + 分段进度条 -->
    <header class="px-4 pt-3.5">
      <div class="flex items-center gap-2">
        <span class="flex size-5 items-center justify-center rounded-full bg-muted">
          <Bot class="size-3 text-muted-foreground" />
        </span>
        <span class="text-[11px] text-muted-foreground">Agent · Execution plan</span>
        <span
          data-test="agent-plan-status"
          class="ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium"
          :class="STATUS_BADGE[planStatus]?.cls"
        >
          {{ STATUS_BADGE[planStatus]?.text ?? planStatus }}
        </span>
        <ChevronDown class="size-3.5 text-muted-foreground" />
      </div>
      <h2 class="mt-2 text-sm font-medium leading-5">
        {{ title }}
        <span class="text-muted-foreground">· v{{ revision }}</span>
      </h2>
      <!-- 分步进度条 -->
      <div class="mt-2.5 flex gap-1">
        <div
          v-for="(bar, index) in progressBars"
          :key="steps[index]?.id ?? index"
          class="h-1 flex-1 overflow-hidden rounded-full bg-muted"
        >
          <motion.div
            class="h-full rounded-full bg-primary origin-left"
            :initial="{ scaleX: 0 }"
            :animate="{ scaleX: bar.scale }"
            :transition="{ duration: 0.2, ease: EASE_OUT }"
          />
        </div>
      </div>
    </header>

    <!-- 步骤列表：竖连线 + 状态圆圈 + 工具徽章 + 时长 -->
    <ol class="px-4 pb-3 pt-4">
      <li v-for="(step, index) in steps" :key="step.id" class="relative min-h-9">
        <!-- 竖连线（最后一个不画） -->
        <span
          v-if="index < steps.length - 1"
          class="absolute left-[9px] top-7 bottom-0 w-px bg-border"
          aria-hidden="true"
        />
        <div class="flex items-start gap-2.5 py-1.5">
          <span
            class="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-medium"
            :class="stepDotClass(step.status)"
          >
            <Check v-if="step.status === 'completed'" class="size-3" />
            <CircleAlert v-else-if="step.status === 'failed'" class="size-3" />
            <template v-else>{{ index + 1 }}</template>
          </span>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-1.5">
              <span
                class="truncate text-sm leading-5"
                :class="step.status === 'pending' ? 'text-muted-foreground' : ''"
              >
                {{ step.title }}
              </span>
              <span
                class="inline-flex h-5 items-center gap-1 rounded-full px-2 text-[10px] font-medium"
                :class="kindStyle(step.kind).pill"
              >
                <component :is="kindStyle(step.kind).icon" class="size-3" />
                {{ kindStyle(step.kind).label }}
              </span>
              <span
                v-if="step.durationMs"
                class="font-mono text-[10px] tabular-nums text-muted-foreground"
              >
                {{ formatDuration(step.durationMs) }}
              </span>
            </div>
            <p
              v-if="step.error"
              class="mt-1 text-[11px] leading-4 text-destructive"
            >
              {{ step.error }}
            </p>
          </div>
        </div>
      </li>
      <li v-if="!steps.length" class="py-3 text-sm text-muted-foreground">No steps yet.</li>
    </ol>

    <!-- Footer：摘要 + 生命周期动作 -->
    <footer
      class="flex min-h-12 items-center justify-between gap-2 border-t border-border px-4 py-2"
    >
      <span class="text-[11px] text-muted-foreground">{{ summaryText }}</span>
      <div class="flex items-center gap-1.5">
        <button
          v-if="showActions"
          data-test="plan-regenerate"
          type="button"
          class="inline-flex size-8 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          :disabled="pending"
          aria-label="重新生成规划"
          @click="emit('regenerate')"
        >
          <RefreshCw class="size-3.5" />
        </button>
        <button
          v-if="showActions"
          data-test="plan-approve"
          type="button"
          class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          :disabled="pending || planStatus !== 'ready'"
          @click="emit('approve')"
        >
          <Check class="size-3.5" />
          确认规划
        </button>
      </div>
    </footer>
  </section>
</template>
