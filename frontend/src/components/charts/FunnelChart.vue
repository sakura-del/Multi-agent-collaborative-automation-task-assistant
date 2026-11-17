<script setup lang="ts">
/**
 * 筛选漏斗（PRISMA 阶段可视化）。
 *
 * ## 与 beUI `ConversionFlowScene` 的取舍
 *
 * 只保留「逐段收窄 + 阶段转化率」这两个信息内核，去掉粒子动画、点阵背景与
 * 4:3 插画画布：PRISMA 是学术流程，粒子会显得轻浮，且漏斗要嵌进工作台卡片，
 * 不能按营销页比例固定高度。
 *
 * ## 宽度按比例，不按绝对值
 *
 * 识别 165 → 纳入 1 时，若按绝对值映射，末段会细到看不见。这里按
 * `value / 首段` 给宽度，并保证最小宽度，让「几乎全被排除」也看得见。
 */
import { computed } from "vue";

import { cn } from "@/lib/utils";

import { buildFunnel, type FunnelStageInput } from "./build-funnel";

const props = withDefaults(
  defineProps<{
    stages: readonly FunnelStageInput[];
    /** 单位，用于无障碍文案（如「条」「篇」）。 */
    unit?: string;
    class?: string;
  }>(),
  { unit: "条" },
);

const WIDTH = 200;
const SEGMENT_HEIGHT = 52;
const GAP = 10;
const MIN_WIDTH = 10;

const model = computed(() => buildFunnel(props.stages));
const rows = computed(() => model.value.rows);

const height = computed(() =>
  rows.value.length > 0 ? rows.value.length * (SEGMENT_HEIGHT + GAP) - GAP : 0,
);

/** 每段的宽度（像素级 viewBox 单位），首段占满 200。 */
const widths = computed(() => rows.value.map((row) => Math.max(MIN_WIDTH, row.proportion * WIDTH)));

/** 某段的梯形路径：上宽为本段，下宽为下一段（末段再收一次口）。 */
function segmentPath(index: number): string {
  const top = widths.value[index] ?? MIN_WIDTH;
  const next = widths.value[index + 1];
  const bottom = next ?? Math.max(MIN_WIDTH * 0.7, top * 0.55);
  const y = index * (SEGMENT_HEIGHT + GAP);
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

/** 越往下越实：识别最浅、纳入最深，视觉上指向结果。 */
function opacityAt(index: number): number {
  if (rows.value.length <= 1) return 0.28;
  return 0.14 + (0.38 * index) / (rows.value.length - 1);
}

const summary = computed(() =>
  rows.value.map((row) => `${row.label} ${row.value}${props.unit}`).join("，"),
);
</script>

<template>
  <div :class="cn('flex flex-col gap-5 sm:flex-row sm:items-center', props.class)">
    <!-- 漏斗图形：装饰性，语义由右侧列表承担 -->
    <div class="min-w-0 shrink-0 sm:w-[46%]">
      <svg
        v-if="rows.length > 0"
        :viewBox="`0 0 ${WIDTH} ${height}`"
        class="h-auto w-full"
        role="img"
        :aria-label="`筛选漏斗：${summary}`"
      >
        <g v-for="(row, index) in rows" :key="row.id">
          <path
            :d="segmentPath(index)"
            class="text-primary transition-opacity duration-200 hover:opacity-90"
            :fill="index === rows.length - 1 ? 'var(--success)' : 'currentColor'"
            :fill-opacity="opacityAt(index)"
            :stroke="index === rows.length - 1 ? 'var(--success)' : 'currentColor'"
            stroke-opacity="0.18"
            stroke-width="1"
          >
            <title>{{ row.label }}：{{ row.value }} {{ props.unit }}</title>
          </path>
        </g>
      </svg>
      <p v-else class="text-sm text-muted-foreground">还没有可展示的筛选数据。</p>
    </div>

    <!-- 阶段列表：所有数字都在这里，图形只是辅助 -->
    <ol class="min-w-0 flex-1 space-y-1.5" :aria-label="`筛选阶段（${props.unit}）`">
      <li
        v-for="(row, index) in rows"
        :key="row.id"
        class="flex items-baseline justify-between gap-3 border-b border-border/60 pb-1.5 last:border-0 last:pb-0"
      >
        <span class="min-w-0 truncate text-xs text-muted-foreground">{{ row.label }}</span>
        <span class="flex shrink-0 items-baseline gap-2">
          <span
            class="text-sm font-semibold tabular-nums"
            :class="index === rows.length - 1 ? 'text-success' : 'text-foreground'"
          >
            {{ row.value }}
          </span>
          <span
            v-if="index > 0"
            class="w-14 text-right font-mono text-[11px] tabular-nums text-muted-foreground"
            :title="`相对上一阶段：${row.stepConversion}%`"
          >
            ↓{{ row.stepConversion }}%
          </span>
          <span
            v-else
            class="w-14 text-right font-mono text-[11px] text-muted-foreground"
            aria-hidden="true"
          >
            —
          </span>
        </span>
      </li>
      <li class="flex items-baseline justify-between gap-3 pt-1 text-xs text-muted-foreground">
        <span>总体转化（纳入 / 识别）</span>
        <span class="font-mono text-sm font-semibold tabular-nums text-foreground">
          {{ model.overall }}%
        </span>
      </li>
    </ol>
  </div>
</template>
