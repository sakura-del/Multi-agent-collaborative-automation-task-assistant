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
import { computed, ref } from "vue";
import { AnimatePresence, motion, useReducedMotion } from "motion-v";
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

import AnimatedCollapse from "@/components/common/AnimatedCollapse.vue";
import { DURATION, EASE_OUT } from "@/lib/ease";

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
    title: "执行计划",
    planStatus: "ready",
    revision: 1,
    pending: false,
    showActions: true,
  }
);

const emit = defineEmits<{ approve: []; regenerate: []; retry: [stepId: string] }>();

// ===== 卡片折叠 & 行展开 =====
const expanded = ref(true);
const expandedId = ref<string | null>(null);
const reduced = useReducedMotion();

function toggleStep(step: AgentPlanStep) {
  if (step.status !== "failed" && step.status !== "running") return;
  expandedId.value = expandedId.value === step.id ? null : step.id;
}

/** 工具徽章色与中文标签：kind → tailwind 类 */
const KIND_STYLE: Record<string, { pill: string; icon: typeof Search; label: string }> = {
  planner: { pill: "bg-sky-500/10 text-sky-600 dark:text-sky-400", icon: Brain, label: "规划" },
  search: { pill: "bg-amber-500/10 text-amber-600 dark:text-amber-400", icon: Search, label: "检索" },
  extract: { pill: "bg-orange-500/10 text-orange-600 dark:text-orange-400", icon: Wrench, label: "抽取" },
  write: { pill: "bg-orange-500/10 text-orange-600 dark:text-orange-400", icon: Wrench, label: "写作" },
  validate: { pill: "bg-lime-500/10 text-lime-600 dark:text-lime-400", icon: FlaskConical, label: "校验" },
  review: { pill: "bg-teal-500/10 text-teal-600 dark:text-teal-400", icon: FlaskConical, label: "复盘" },
};

function kindStyle(kind: string) {
  return KIND_STYLE[kind] ?? KIND_STYLE.write;
}

const STATUS_BADGE: Record<string, { text: string; cls: string }> = {
  ready: { text: "等待确认", cls: "bg-primary/10 text-primary" },
  running: { text: "执行中", cls: "bg-primary/10 text-primary" },
  completed: { text: "已完成", cls: "bg-success/10 text-success" },
  failed: { text: "需要处理", cls: "bg-warning/15 text-warning" },
  draft: { text: "草稿", cls: "bg-muted text-muted-foreground" },
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
  if (props.planStatus === "failed") return "执行遇阻，请展开查看详情";
  if (completedCount.value === props.steps.length && props.steps.length)
    return `共 ${props.steps.length} 步 · 耗时 ${formatDuration(totalDurationMs.value)}`;
  return `已完成 ${completedCount.value}/${props.steps.length} 步`;
});

function formatDuration(ms: number): string {
  if (ms <= 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function stepDotClass(status: AgentStepStatus): string {
  if (status === "completed") return "bg-foreground text-background";
  if (status === "failed") return "bg-warning/15 text-warning";
  if (status === "running") return "bg-primary/10 text-primary";
  return "bg-muted text-muted-foreground";
}
</script>

<template>
  <section
    data-test="agent-plan"
    class="mx-auto w-full max-w-2xl overflow-clip rounded-2xl border border-border bg-background text-foreground"
  >
    <!-- Header：元信息行 + 标题 + 分段进度条 -->
    <header class="px-4 pb-3 pt-3.5">
      <div class="flex items-center gap-2">
        <span class="flex size-5 items-center justify-center rounded-full bg-muted">
          <Bot class="size-3 text-muted-foreground" />
        </span>
        <span class="text-[11px] text-muted-foreground">智能体 · 执行计划</span>
        <span
          data-test="agent-plan-status"
          class="ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium"
          :class="STATUS_BADGE[planStatus]?.cls"
        >
          {{ STATUS_BADGE[planStatus]?.text ?? planStatus }}
        </span>
        <button
          type="button"
          data-test="agent-plan-collapse"
          :aria-expanded="expanded"
          :aria-label="expanded ? '收起执行计划' : '展开执行计划'"
          class="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          @click="expanded = !expanded"
        >
          <ChevronDown
            class="size-3.5 transition-transform duration-150 motion-reduce:transition-none"
            :class="expanded ? 'rotate-0' : '-rotate-90'"
          />
        </button>
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
            class="h-full origin-left rounded-full bg-primary"
            :initial="{ scaleX: 0 }"
            :animate="{ scaleX: bar.scale }"
            :transition="{ duration: reduced ? 0 : DURATION.normal, ease: EASE_OUT }"
          />
        </div>
      </div>
    </header>

    <AnimatedCollapse :open="expanded" keep-mounted>
      <!-- 步骤列表：竖连线 + 状态圆圈 + 工具徽章 + 时长 -->
      <ol class="px-4 pb-3 pt-1">
        <li v-for="(step, index) in steps" :key="step.id" class="relative min-h-9">
          <!-- 竖连线（最后一个不画） -->
          <span
            v-if="index < steps.length - 1"
            class="absolute left-[9px] top-7 bottom-0 w-px bg-border"
            aria-hidden="true"
          />
          <!-- running/failed 行可点击展开活动区 -->
          <component
            :is="step.status === 'failed' || step.status === 'running' ? 'button' : 'div'"
            :type="step.status === 'failed' || step.status === 'running' ? 'button' : undefined"
            class="flex w-full items-start gap-2.5 rounded-md py-1.5 text-left"
            :class="
              step.status === 'failed' || step.status === 'running'
                ? 'cursor-pointer transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
                : ''
            "
            :aria-expanded="expandedId === step.id ? 'true' : 'false'"
            @click="toggleStep(step)"
          >
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
                <ChevronDown
                  v-if="step.status === 'failed' || step.status === 'running'"
                  class="ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 motion-reduce:transition-none"
                  :class="expandedId === step.id ? 'rotate-0' : '-rotate-90'"
                />
              </div>
              <p
                v-if="step.error && expandedId !== step.id"
                class="mt-1 truncate text-[11px] leading-4 text-destructive"
              >
                {{ step.error }}
              </p>
            </div>
          </component>
          <!-- 活动区：展开时显示完整错误 + 重试 -->
          <AnimatePresence :initial="false">
            <motion.div
              v-if="expandedId === step.id && (step.status === 'failed' || step.status === 'running')"
              :initial="{ opacity: 0, y: reduced ? 0 : -3 }"
              :animate="{ opacity: 1, y: 0 }"
              :exit="{ opacity: 0 }"
              :transition="{ duration: reduced ? 0 : 0.18, ease: EASE_OUT }"
              class="ml-7 mt-1 flex items-start justify-between gap-2 rounded-md bg-muted/40 px-2.5 py-2"
            >
              <p class="min-w-0 break-words text-[11px] leading-4 text-destructive">
                {{ step.error || "节点仍在执行中…" }}
              </p>
              <button
                v-if="step.status === 'failed'"
                type="button"
                data-test="agent-plan-retry"
                class="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg border border-border px-2 text-[11px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                @click.stop="emit('retry', step.id)"
              >
                <RefreshCw class="size-3" />
                重试节点
              </button>
            </motion.div>
          </AnimatePresence>
        </li>
        <li v-if="!steps.length" class="py-3 text-sm text-muted-foreground">暂无执行步骤</li>
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
    </AnimatedCollapse>
  </section>
</template>
