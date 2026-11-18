<script setup lang="ts">
/**
 * beUI Agent Traces（TraceWaterfall）的 Vue 移植
 * （https://asharca.github.io/ui/components/agents/agent-traces/）。
 *
 * 原实现是 React + motion/react；本项目裁剪为**节点级瀑布**（DAG 节点平铺，
 * 无父子树/播放头拖拽），保留：Header 统计（Duration/Spans/Tokens）、
 * 类型过滤（kind 圆点按钮，选中变暗）、左名称右时间轴、条形定位
 * （left/width 按占比）、失败 span 斜条纹。
 *
 * 规格：max-w-6xl rounded-3xl；span 条 h-3 圆角胶囊，bg 按 kind
 * （agent=sky / llm=orange / retrieval=amber / guardrail=lime / tool=teal）；
 * 失败 = rose + repeating-linear-gradient；数据 tabular-nums；
 * startMs 相对首个节点开始时刻；时长 <1s 显示 ms。
 */
import { computed, ref } from "vue";
import { motion } from "motion-v";
import { Brain, CircleAlert, Search, Wrench, Clock3 } from "lucide-vue-next";

export interface TraceSpan {
  id: string;
  name: string;
  /** agent | retrieval | llm | guardrail */
  kind: string;
  startMs: number;
  durationMs: number;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  error?: string;
}

const props = defineProps<{
  runName: string;
  spans: TraceSpan[];
}>();

const KIND_DOT: Record<string, string> = {
  planner: "bg-sky-500",
  search: "bg-amber-500",
  extract: "bg-orange-500",
  write: "bg-orange-500",
  validate: "bg-lime-500",
  review: "bg-teal-500",
};
const KIND_ICON: Record<string, typeof Search> = {
  planner: Brain,
  search: Search,
  extract: Wrench,
  write: Wrench,
  validate: Clock3,
  review: Clock3,
};
const KIND_LABEL: Record<string, string> = {
  planner: "Agent",
  search: "Retrieval",
  extract: "LLM",
  write: "LLM",
  validate: "Guardrail",
  review: "Agent",
};

/** 过滤：点击类型按钮 → 该类行变暗（beUI 的 dimmed 语义） */
const dimmed = ref<Set<string>>(new Set());
const kinds = computed(() => [...new Set(props.spans.map((span) => span.kind))]);

function toggleKind(kind: string) {
  const next = new Set(dimmed.value);
  if (next.has(kind)) next.delete(kind);
  else next.add(kind);
  dimmed.value = next;
}

const totalDurationMs = computed(() => {
  if (!props.spans.length) return 0;
  const start = Math.min(...props.spans.map((span) => span.startMs));
  const end = Math.max(
    ...props.spans.map((span) => span.startMs + span.durationMs)
  );
  return Math.max(1, end - start);
});
const totalTokensHint = computed(
  () => `${props.spans.length} spans · ${formatDuration(totalDurationMs.value)}`
);

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function spanStyle(span: TraceSpan) {
  const start = Math.min(...props.spans.map((s) => s.startMs));
  const left = ((span.startMs - start) / totalDurationMs.value) * 100;
  const width = Math.max(1.5, (span.durationMs / totalDurationMs.value) * 100);
  return { left: `${left}%`, width: `${width}%` };
}

function spanClass(span: TraceSpan): string {
  if (span.status === "failed")
    return "bg-rose-500 [background-image:repeating-linear-gradient(45deg,rgba(255,255,255,.35)_0_6px,transparent_6px_12px)]";
  return KIND_DOT[span.kind] ?? "bg-primary";
}

function isDimmed(span: TraceSpan): boolean {
  return dimmed.value.has(span.kind);
}

function spanIcon(kind: string) {
  return KIND_ICON[kind] ?? Wrench;
}
</script>

<template>
  <section
    data-test="trace-waterfall"
    class="w-full rounded-3xl border border-border bg-card"
    aria-label="节点执行瀑布图"
  >
    <!-- Header：运行名 + 统计 -->
    <div class="flex flex-wrap items-end justify-between gap-3 border-b border-border px-4 py-3">
      <div>
        <p class="font-mono text-[10px] text-muted-foreground">{{ runName }}</p>
        <h3 class="text-xl font-medium leading-6">Node execution trace</h3>
      </div>
      <dl class="flex gap-4 text-right">
        <div>
          <dt class="text-[10px] uppercase tracking-wide text-muted-foreground">Duration</dt>
          <dd class="font-mono text-sm tabular-nums">{{ formatDuration(totalDurationMs) }}</dd>
        </div>
        <div>
          <dt class="text-[10px] uppercase tracking-wide text-muted-foreground">Spans</dt>
          <dd class="font-mono text-sm tabular-nums">{{ spans.length }}</dd>
        </div>
      </dl>
    </div>

    <!-- 类型过滤 -->
    <div class="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
      <button
        v-for="kind in kinds"
        :key="kind"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[11px] transition-opacity"
        :class="dimmed.has(kind) ? 'opacity-30' : ''"
        :aria-pressed="!dimmed.has(kind)"
        @click="toggleKind(kind)"
      >
        <span class="size-2 rounded-full" :class="KIND_DOT[kind] ?? 'bg-primary'" />
        {{ KIND_LABEL[kind] ?? kind }}
      </button>
      <span class="ml-auto inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
        <Clock3 class="size-3" />
        {{ totalTokensHint }}
      </span>
    </div>

    <!-- 瀑布主体：左名称 / 右时间轴 -->
    <div class="overflow-x-auto px-4 py-3">
      <div class="min-w-[576px]">
        <!-- 时间轴刻度 -->
        <div class="grid grid-cols-[14rem_minmax(0,1fr)] items-center gap-3 pb-1">
          <span class="text-[10px] uppercase tracking-wide text-muted-foreground">Node</span>
          <div class="relative h-3">
            <span
              v-for="tick in 5"
              :key="tick"
              class="absolute top-0 h-full w-px bg-border/60"
              :style="{ left: `${((tick - 1) / 4) * 100}%` }"
            />
          </div>
        </div>
        <!-- 每行：名称 + 条 -->
        <div
          v-for="span in spans"
          :key="span.id"
          class="grid grid-cols-[14rem_minmax(0,1fr)] items-center gap-3 py-1"
          :class="isDimmed(span) ? 'opacity-30' : ''"
        >
          <div class="flex min-w-0 items-center gap-1.5">
            <component :is="spanIcon(span.kind)" class="size-3 shrink-0 text-muted-foreground" />
            <span class="truncate text-xs leading-5">{{ span.name }}</span>
            <CircleAlert
              v-if="span.status === 'failed'"
              class="size-3 shrink-0 text-rose-500"
              aria-label="failed"
            />
          </div>
          <div class="relative h-6">
            <!-- 轨道 -->
            <div class="absolute inset-x-0 top-1/2 h-3 -translate-y-1/2 rounded-full bg-muted/40" />
            <!-- span 条（motion 生长动效，0.15s EASE_OUT）-->
            <motion.div
              class="absolute top-1/2 h-3 -translate-y-1/2 rounded-full"
              :class="[spanClass(span), isDimmed(span) ? 'opacity-30' : '']"
              :style="spanStyle(span)"
              :initial="{ scaleX: 0 }"
              :animate="{ scaleX: 1 }"
              :transition="{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }"
              style="transform-origin: left center"
            />
            <span
              class="pointer-events-none absolute top-1/2 -translate-y-1/2 pl-1.5 font-mono text-[10px] tabular-nums text-muted-foreground"
              :style="spanStyle(span)"
            >
              {{ formatDuration(span.durationMs) }}
            </span>
          </div>
        </div>
        <p v-if="!spans.length" class="py-4 text-sm text-muted-foreground">暂无执行数据。</p>
      </div>
    </div>
  </section>
</template>
