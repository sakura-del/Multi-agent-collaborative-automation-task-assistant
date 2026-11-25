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
import { computed, onBeforeUnmount, ref } from "vue";
import { motion, useReducedMotion } from "motion-v";
import { Brain, CircleAlert, Pause, Play, Search, Wrench, Clock3 } from "lucide-vue-next";

import { DURATION, EASE_OUT } from "@/lib/ease";

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
  planner: "智能体",
  search: "文献检索",
  extract: "模型生成",
  write: "模型生成",
  validate: "质量校验",
  review: "智能体",
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

const minStartMs = computed(() =>
  props.spans.length ? Math.min(...props.spans.map((span) => span.startMs)) : 0,
);
const totalDurationMs = computed(() => {
  if (!props.spans.length) return 0;
  const start = minStartMs.value;
  const end = Math.max(
    ...props.spans.map((span) => span.startMs + span.durationMs)
  );
  return Math.max(1, end - start);
});
const totalTokensHint = computed(
  () => `共 ${props.spans.length} 个节点 · ${formatDuration(totalDurationMs.value)}`
);

// ===== 播放头（rAF 循环、拖拽 range 实时跟手、越过前 span 变暗）=====
const reducedMotion = useReducedMotion();
const playing = ref(false);
const isScrubbing = ref(false);
/** 播放头位置（相对 run 起点的 ms）；0 = 未播放 */
const playheadMs = ref(0);
let rafId: number | null = null;
const PLAYBACK_MS = 2000;

function stopPlayback() {
  playing.value = false;
  if (rafId !== null) {
    window.cancelAnimationFrame(rafId);
    rafId = null;
  }
}

function replay() {
  stopPlayback();
  playheadMs.value = 0;
  if (reducedMotion.value) {
    playheadMs.value = totalDurationMs.value;
    return;
  }
  playing.value = true;
  const startedAt = performance.now();
  const step = (now: number) => {
    if (!playing.value) return;
    const elapsed = now - startedAt;
    playheadMs.value = Math.min(
      totalDurationMs.value,
      (elapsed / PLAYBACK_MS) * totalDurationMs.value,
    );
    if (playheadMs.value >= totalDurationMs.value) {
      stopPlayback();
      return;
    }
    rafId = window.requestAnimationFrame(step);
  };
  rafId = window.requestAnimationFrame(step);
}

function onScrub(event: Event) {
  isScrubbing.value = true;
  stopPlayback();
  playheadMs.value = Number((event.target as HTMLInputElement).value);
}

const playheadVisible = computed(() => playing.value || playheadMs.value > 0);

onBeforeUnmount(stopPlayback);

/** span 是否已被播放头越过（越过前的 span 变暗） */
function aheadOfPlayhead(span: TraceSpan): boolean {
  return playheadVisible.value && span.startMs > playheadMs.value;
}

/** 播放/拖拽时的生长比例：未开始 0；进行中按进度；已结束 1 */
function spanScale(span: TraceSpan): number {
  if (!playheadVisible.value) return 1;
  if (span.startMs > playheadMs.value) return 0;
  const elapsed = playheadMs.value - span.startMs;
  return Math.min(1, elapsed / Math.max(1, span.durationMs));
}

const playheadRatio = computed(() =>
  totalDurationMs.value > 0 ? Math.min(1, playheadMs.value / totalDurationMs.value) : 0,
);

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function spanStyle(span: TraceSpan) {
  const start = minStartMs.value;
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
        <h3 class="text-xl font-medium leading-6">节点执行轨迹</h3>
      </div>
      <dl class="flex gap-4 text-right">
        <div>
          <dt class="text-[10px] tracking-wide text-muted-foreground">总耗时</dt>
          <dd class="font-mono text-sm tabular-nums">{{ formatDuration(totalDurationMs) }}</dd>
        </div>
        <div>
          <dt class="text-[10px] tracking-wide text-muted-foreground">节点数</dt>
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

    <!-- 播放工具栏：X / 总时长 + Replay / Pause + 拖拽 scrub -->
    <div class="flex items-center gap-2 border-b border-border px-4 py-2">
      <span class="font-mono text-[10px] tabular-nums text-muted-foreground">
        {{ formatDuration(Math.min(playheadMs, totalDurationMs)) }} / {{ formatDuration(totalDurationMs) }}
      </span>
      <input
        type="range"
        class="h-1.5 flex-1 cursor-col-resize accent-primary"
        :max="totalDurationMs"
        :value="Math.min(playheadMs, totalDurationMs)"
        step="50"
        aria-label="拖拽播放头"
        @input="onScrub"
        @change="isScrubbing = false"
      />
      <button
        type="button"
        data-test="trace-replay"
        class="inline-flex size-7 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :aria-label="playing ? '暂停回放' : '回放'"
        @click="playing ? stopPlayback() : replay()"
      >
        <Pause v-if="playing" class="size-3.5" />
        <Play v-else class="size-3.5" />
      </button>
    </div>

    <!-- 瀑布主体：393px 移动端双行紧凑，sm 以上双列网格 -->
    <div class="px-4 py-3">
      <div class="relative [--trace-col-offset:12.75rem] md:[--trace-col-offset:14.75rem]">
        <!-- 时间轴刻度 -->
        <div class="hidden sm:grid sm:grid-cols-[12rem_minmax(0,1fr)] md:grid-cols-[14rem_minmax(0,1fr)] sm:items-center sm:gap-3 pb-1">
          <span class="text-[10px] tracking-wide text-muted-foreground">节点</span>
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
          class="flex flex-col gap-1 py-1.5 sm:grid sm:grid-cols-[12rem_minmax(0,1fr)] md:grid-cols-[14rem_minmax(0,1fr)] sm:items-center sm:gap-3 sm:py-1"
          :class="isDimmed(span) ? 'opacity-30' : ''"
        >
          <div class="flex min-w-0 items-center gap-1.5">
            <component :is="spanIcon(span.kind)" class="size-3 shrink-0 text-muted-foreground" />
            <span class="truncate text-xs leading-5">{{ span.name }}</span>
            <CircleAlert
              v-if="span.status === 'failed'"
              class="size-3 shrink-0 text-rose-500"
              aria-label="失败"
            />
          </div>
          <div class="relative h-6">
            <!-- 轨道 -->
            <div class="absolute inset-x-0 top-1/2 h-3 -translate-y-1/2 rounded-full bg-muted/40" />
            <!-- span 条：播放/拖拽时 scaleX 按 playhead 生长 -->
            <motion.div
              class="absolute top-1/2 h-3 -translate-y-1/2 rounded-full"
              :class="[spanClass(span), isDimmed(span) || aheadOfPlayhead(span) ? 'opacity-30' : '']"
              :style="spanStyle(span)"
              :initial="{ scaleX: 0 }"
              :animate="{ scaleX: spanScale(span) }"
              :transition="{ duration: reducedMotion || isScrubbing ? 0 : DURATION.fast, ease: EASE_OUT }"
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
        <!-- 播放头竖线（播放或拖拽中均保持可见；轨道定位层按 --trace-col-offset 对齐右侧时间轴起点） -->
        <div
          v-if="playheadVisible"
          data-test="trace-playhead-track"
          class="pointer-events-none hidden sm:block absolute inset-y-0 right-0 left-[var(--trace-col-offset,12.75rem)]"
          aria-hidden="true"
        >
          <div
            data-test="trace-playhead-line"
            class="absolute top-0 bottom-0 w-px bg-foreground/50"
            :style="{
              '--playhead-ratio': String(playheadRatio),
              left: `${playheadRatio * 100}%`,
            }"
          />
        </div>
      </div>
    </div>
  </section>
</template>
