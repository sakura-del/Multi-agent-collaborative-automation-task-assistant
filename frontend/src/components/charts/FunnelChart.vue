<script setup lang="ts">
/**
 * 筛选漏斗（PRISMA 阶段可视化，按 beUI `ConversionFlowScene` 的形态实现）。
 *
 * ## 与上一版的差别（2026-11-17 返工）
 *
 * 上一版每段之间有 10px 间隙、统一用 primary 的低透明度，画出来是「五根分开的
 * 竖条」，不是漏斗。这里按原版重做：
 * - **连续梯形**：段与段首尾相接，没有间隙；相邻段共享宽度，形成真正的收窄；
 * - **分段配色**：蓝 → 天蓝 → 青 → 绿，颜色本身表达「越往下越接近结果」；
 * - **点阵画布**：浅灰底 + 点阵，给漏斗一个落点，不然悬在白卡里像表格缺了框；
 * - **粒子**：沿漏斗下落，部分在中途淡出（流失），到底的变绿（纳入）。
 *
 * ## 全 0 数据不画空漏斗
 *
 * 任务还没跑筛选时，后端返回的五个计数全是 0。上一版会画出五根等宽空条，
 * 看起来像「漏斗坏了」。现在 0 数据直接给空态，把「没数据」和「全是 0%」分开。
 */
import { computed } from "vue";
import { useReducedMotion } from "motion-v";

import { cn } from "@/lib/utils";

import { buildFunnel, type FunnelStageInput } from "./build-funnel";

const props = withDefaults(
  defineProps<{
    stages: readonly FunnelStageInput[];
    title?: string;
    /** 右上角的时间/口径说明。 */
    periodLabel?: string;
    /** 单位，用于无障碍文案与数字后缀。 */
    unit?: string;
    class?: string;
  }>(),
  { title: "筛选漏斗", periodLabel: "", unit: "条" },
);

const WIDTH = 200;
const SEGMENT_HEIGHT = 48;
const MIN_WIDTH = 14;

/** 越往下越接近结果：蓝 → 天蓝 → 青 → 绿。 */
const PALETTE = ["#3b82f6", "#0ea5e9", "#14b8a6", "#10b981", "#059669"];

const reducedMotion = useReducedMotion();
const model = computed(() => buildFunnel(props.stages));
const rows = computed(() => model.value.rows);

/** 全 0 或没有阶段时不画图：空漏斗会被误读成「筛选全挂了」。 */
const hasData = computed(() => rows.value.some((row) => row.value > 0));

const height = computed(() => rows.value.length * SEGMENT_HEIGHT);

/** 每段宽度（viewBox 单位）：首段占满，其余按相对首段的比例。 */
const widths = computed(() =>
  rows.value.map((row) => Math.max(MIN_WIDTH, row.proportion * WIDTH)),
);

function colorAt(index: number): string {
  return PALETTE[Math.min(index, PALETTE.length - 1)];
}

/**
 * 第 index 段的梯形路径。
 *
 * 段与段之间**不留间隙**：本段底宽等于下一段顶宽，视觉上才是同一个漏斗。
 * 末段额外收口到自身宽度的 55%，给一个「到底」的收束。
 */
function segmentPath(index: number): string {
  const top = widths.value[index] ?? MIN_WIDTH;
  const next = widths.value[index + 1];
  const bottom = next ?? Math.max(MIN_WIDTH * 0.7, top * 0.55);
  const y = index * SEGMENT_HEIGHT;
  const topLeft = (WIDTH - top) / 2;
  const bottomLeft = (WIDTH - bottom) / 2;
  return [
    `M ${topLeft} ${y}`,
    `H ${WIDTH - topLeft}`,
    `L ${WIDTH - bottomLeft} ${y + SEGMENT_HEIGHT}`,
    `H ${bottomLeft}`,
    "Z",
  ].join(" ");
}

/** 12 颗粒子：x 是相对漏斗半宽的偏移（-1..1），exit 决定在第几层侧向流失。 */
const PARTICLES = [
  { x: -0.72, exit: 1 },
  { x: 0.18, exit: 0 },
  { x: 0.78, exit: 1 },
  { x: -0.28, exit: 2 },
  { x: 0.52, exit: 1 },
  { x: -0.58, exit: 3 },
  { x: 0.06, exit: 2 },
  { x: -0.88, exit: 1 },
  { x: 0.62, exit: 3 },
  { x: -0.12, exit: 0 },
  { x: 0.92, exit: 1 },
  { x: -0.42, exit: 2 },
  { x: 0.34, exit: 1 },
  { x: -0.66, exit: 0 },
  { x: 0.12, exit: 2 },
  { x: -0.34, exit: 1 },
].map((particle, index) => ({ ...particle, delay: `${(-0.4 * index).toFixed(1)}s` }));

function formatCompact(value: number): string {
  if (value < 10_000) return String(value);
  return `${(value / 1000).toFixed(1)}K`;
}

const summary = computed(() =>
  rows.value.map((row) => `${row.label} ${row.value}${props.unit}`).join("，"),
);
</script>

<template>
  <div :class="cn('relative isolate overflow-hidden rounded-2xl bg-muted/70 p-3', props.class)">
    <!-- 点阵底：给漏斗一个落点，纯白卡里直接放图形会显得飘 -->
    <div
      aria-hidden="true"
      class="pointer-events-none absolute inset-0 opacity-60 [background-image:radial-gradient(circle,var(--border)_1px,transparent_1px)] [background-size:18px_18px]"
    />

    <div class="relative rounded-xl border border-border bg-background p-4">
      <header class="mb-3 flex items-baseline justify-between gap-2">
        <h3 class="text-sm font-medium">{{ props.title }}</h3>
        <span v-if="props.periodLabel" class="text-[11px] text-muted-foreground">
          {{ props.periodLabel }}
        </span>
      </header>

      <!-- 空态：0 数据不画空漏斗 -->
      <div
        v-if="!hasData"
        class="flex min-h-32 flex-col items-center justify-center gap-1 text-center"
      >
        <p class="text-sm font-medium">还没有筛选数据</p>
        <p class="text-xs text-muted-foreground">
          先执行检索并录入纳入/排除决定，这里会按 PRISMA 阶段展示漏斗。
        </p>
      </div>

      <div v-else class="flex flex-col gap-5 sm:flex-row sm:items-center">
        <!-- 漏斗：连续梯形 + 粒子 -->
        <div class="relative mx-auto w-full max-w-[17.5rem] shrink-0 sm:mx-0 sm:w-[46%]">
          <svg
            :viewBox="`0 0 ${WIDTH} ${height}`"
            class="h-auto w-full"
            role="img"
            :aria-label="`${props.title}：${summary}`"
          >
            <g v-for="(row, index) in rows" :key="row.id">
              <path
                :d="segmentPath(index)"
                :fill="colorAt(index)"
                fill-opacity="0.16"
                :stroke="colorAt(index)"
                stroke-opacity="0.22"
                stroke-width="7"
                stroke-linejoin="round"
              >
                <title>{{ row.label }}：{{ row.value }} {{ props.unit }}</title>
              </path>
            </g>
            <!-- 顶部高亮边：原版的「入口」就是这条线 -->
            <line
              x1="0"
              y1="0"
              :x2="WIDTH"
              y2="0"
              :stroke="colorAt(0)"
              stroke-width="2.5"
              stroke-linecap="round"
            />
          </svg>

          <!-- 粒子层：覆盖漏斗区域，沿收窄的路径下落 -->
          <div
            v-if="!reducedMotion"
            aria-hidden="true"
            class="funnel-particles pointer-events-none absolute inset-0 overflow-hidden"
          >
            <span
              v-for="(particle, index) in PARTICLES"
              :key="index"
              :class="`funnel-particle exit-${particle.exit}`"
              :style="{
                '--x': particle.x,
                '--exit': particle.exit,
                animationDelay: particle.delay,
              }"
            />
          </div>
        </div>

        <!-- 阶段列表：所有数字都在这里，图形只是辅助 -->
        <div class="min-w-0 flex-1">
          <ol class="space-y-1.5" :aria-label="`筛选阶段（${props.unit}）`">
            <li
              v-for="(row, index) in rows"
              :key="row.id"
              class="flex items-center gap-2"
            >
              <span
                aria-hidden="true"
                class="size-1.5 shrink-0 rounded-full"
                :style="{ background: colorAt(index) }"
              />
              <span class="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                {{ row.label }}
              </span>
              <span class="shrink-0 text-base font-semibold tabular-nums text-foreground">
                {{ formatCompact(row.value) }}
              </span>
              <span
                class="w-12 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground"
                :title="index === 0 ? undefined : `相对上一阶段：${row.stepConversion}%`"
              >
                {{ index === 0 ? "—" : `↓${row.stepConversion}%` }}
              </span>
            </li>
          </ol>

          <div class="mt-3 flex items-center justify-between border-t border-border pt-2">
            <span class="text-xs text-muted-foreground">总体转化（纳入 / 识别）</span>
            <span
              class="rounded-full bg-success/10 px-2 py-0.5 font-mono text-xs font-semibold tabular-nums text-success"
            >
              {{ model.overall }}%
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/*
 * 粒子：沿漏斗下落，按 exit 层侧向淡出；exit=0 的走到最底并变绿。
 *
 * ⚠️ 不要用 translateY 的百分比：百分比是相对元素自身高度（3px），
 * 不是容器高度，位移会小到看不见。这里动画 top/left（百分比相对容器）。
 */
.funnel-particle {
  position: absolute;
  top: 0;
  left: 50%;
  width: 3.5px;
  height: 3.5px;
  border-radius: 999px;
  background: var(--foreground);
  opacity: 0;
  transform: translateX(-50%);
  animation: funnel-drop 5.2s linear infinite;
}

@keyframes funnel-drop {
  0% {
    top: -2%;
    left: calc(50% + var(--x) * 18%);
    background: var(--foreground);
    opacity: 0;
  }
  6% {
    opacity: 0.55;
  }
  22% {
    top: 20%;
    left: calc(50% + var(--x) * 13.5%);
    opacity: 0.55;
  }
  44% {
    top: 42%;
    left: calc(50% + var(--x) * 8.5%);
    opacity: 0.45;
  }
  66% {
    top: 64%;
    left: calc(50% + var(--x) * 5%);
    opacity: 0.45;
  }
  84% {
    top: 82%;
    left: calc(50% + var(--x) * 2.6%);
    background: var(--success);
    opacity: 0.9;
  }
  100% {
    top: 99%;
    left: calc(50% + var(--x) * 1.6%);
    background: var(--success);
    opacity: 0;
  }
}

/* exit=1：第一层（去重后/标题摘要之间）侧向流出 */
@keyframes funnel-drop-exit-1 {
  0% {
    top: -2%;
    left: calc(50% + var(--x) * 18%);
    opacity: 0;
  }
  6% {
    opacity: 0.55;
  }
  22% {
    top: 20%;
    left: calc(50% + var(--x) * 13.5%);
    opacity: 0.5;
  }
  34% {
    top: 25%;
    left: calc(50% + var(--x) * 40%);
    opacity: 0;
  }
  100% {
    top: 25%;
    left: calc(50% + var(--x) * 40%);
    opacity: 0;
  }
}

/* exit=2：第二层流出 */
@keyframes funnel-drop-exit-2 {
  0% {
    top: -2%;
    left: calc(50% + var(--x) * 18%);
    opacity: 0;
  }
  6% {
    opacity: 0.55;
  }
  44% {
    top: 42%;
    left: calc(50% + var(--x) * 8.5%);
    opacity: 0.45;
  }
  56% {
    top: 47%;
    left: calc(50% + var(--x) * 30%);
    opacity: 0;
  }
  100% {
    top: 47%;
    left: calc(50% + var(--x) * 30%);
    opacity: 0;
  }
}

/* exit=3：第三层流出 */
@keyframes funnel-drop-exit-3 {
  0% {
    top: -2%;
    left: calc(50% + var(--x) * 18%);
    opacity: 0;
  }
  6% {
    opacity: 0.5;
  }
  66% {
    top: 64%;
    left: calc(50% + var(--x) * 5%);
    opacity: 0.45;
  }
  78% {
    top: 69%;
    left: calc(50% + var(--x) * 22%);
    opacity: 0;
  }
  100% {
    top: 69%;
    left: calc(50% + var(--x) * 22%);
    opacity: 0;
  }
}

.exit-1 {
  animation-name: funnel-drop-exit-1;
}
.exit-2 {
  animation-name: funnel-drop-exit-2;
}
.exit-3 {
  animation-name: funnel-drop-exit-3;
}

@media (prefers-reduced-motion: reduce) {
  .funnel-particles {
    display: none;
  }
}
</style>
