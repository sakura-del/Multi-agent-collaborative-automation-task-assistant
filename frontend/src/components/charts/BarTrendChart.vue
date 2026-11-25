<script setup lang="ts">
import { computed, ref } from "vue";

export interface BarTrendItem {
  key: string;
  label: string;
  total: number;
  /** 可选的高亮子集（如 BYOK 占总量的部分）；不传则整柱为主色 */
  primary?: number;
  meta?: string;
  testid?: string;
}

const props = withDefaults(
  defineProps<{
    items: BarTrendItem[];
    height?: number;
    totalLabel?: string;
    primaryLabel?: string;
    clickable?: boolean;
  }>(),
  {
    height: 168,
    totalLabel: "总量",
    primaryLabel: "核心",
    clickable: false,
  },
);

const emit = defineEmits<{
  (event: "select", key: string): void;
}>();

const hoveredKey = ref<string | null>(null);

function onBarClick(key: string) {
  if (props.clickable) {
    emit("select", key);
  } else {
    hoveredKey.value = hoveredKey.value === key ? null : key;
  }
}

const maxTotal = computed(() =>
  Math.max(1, ...props.items.map((item) => Math.max(0, item.total))),
);

const hasPrimarySeries = computed(() =>
  props.items.some((item) => typeof item.primary === "number"),
);

const chartWidth = 560;
const padLeft = 44;
const padRight = 6;
const padTop = 10;
const padBottom = 22;

const plotHeight = computed(() => props.height - padTop - padBottom);
/** Y 轴三档参考刻度（顶 / 中 / 0），为用量看板与 BYOK 趋势图提供清晰数值参照（Issue #57）。 */
const yTicks = computed(() => [
  { ratio: 0, value: maxTotal.value, label: formatCompact(maxTotal.value) },
  {
    ratio: 0.5,
    value: Math.round(maxTotal.value / 2),
    label: formatCompact(Math.round(maxTotal.value / 2)),
  },
  { ratio: 1, value: 0, label: "0" },
]);
const stepWidth = computed(() =>
  props.items.length > 0 ? (chartWidth - padLeft - padRight) / props.items.length : 0,
);
const barWidth = computed(() => Math.min(28, Math.max(8, stepWidth.value * 0.56)));

const bars = computed(() =>
  props.items.map((item, index) => {
    const x = padLeft + index * stepWidth.value + (stepWidth.value - barWidth.value) / 2;
    const totalH = item.total > 0 ? Math.max(3, (item.total / maxTotal.value) * plotHeight.value) : 0;
    const primaryVal = typeof item.primary === "number" ? Math.min(item.total, item.primary) : item.total;
    const primaryH =
      primaryVal > 0 ? Math.max(3, (primaryVal / maxTotal.value) * plotHeight.value) : 0;
    const showLabel =
      props.items.length <= 10 || index === 0 || index === props.items.length - 1 || index % 3 === 0;
    return {
      ...item,
      x,
      centerX: x + barWidth.value / 2,
      totalY: padTop + plotHeight.value - totalH,
      totalH,
      primaryY: padTop + plotHeight.value - primaryH,
      primaryH,
      showLabel,
    };
  }),
);

const hoveredBar = computed(() =>
  hoveredKey.value ? bars.value.find((bar) => bar.key === hoveredKey.value) ?? null : null,
);

function formatCompact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}
</script>

<template>
  <div class="relative">
    <!-- 图例与悬停读数 -->
    <div class="mb-2 flex min-h-5 flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
      <div class="flex items-center gap-3">
        <span class="inline-flex items-center gap-1.5">
          <span class="size-2 rounded-xs bg-primary" aria-hidden="true" />
          {{ hasPrimarySeries ? props.primaryLabel : props.totalLabel }}
        </span>
        <span v-if="hasPrimarySeries" class="inline-flex items-center gap-1.5">
          <span class="size-2 rounded-xs bg-primary/25" aria-hidden="true" />
          {{ props.totalLabel }}
        </span>
      </div>
      <div v-if="hoveredBar" class="font-medium text-foreground tabular-nums">
        {{ hoveredBar.label }} · {{ formatCompact(hoveredBar.total) }}
        <template v-if="hasPrimarySeries && typeof hoveredBar.primary === 'number'">
          ({{ props.primaryLabel }} {{ formatCompact(hoveredBar.primary) }})
        </template>
        <template v-if="hoveredBar.meta"> · {{ hoveredBar.meta }}</template>
      </div>
    </div>

    <svg
      :viewBox="`0 0 ${chartWidth} ${props.height}`"
      class="w-full overflow-visible"
      role="img"
      :aria-label="props.totalLabel"
    >
      <!-- 水平参考线与 Y 轴刻度 -->
      <g v-for="tick in yTicks" :key="tick.ratio">
        <line
          :x1="padLeft"
          :x2="chartWidth - padRight"
          :y1="padTop + plotHeight * tick.ratio"
          :y2="padTop + plotHeight * tick.ratio"
          stroke="var(--border)"
          stroke-dasharray="3 3"
        />
        <text
          :x="padLeft - 6"
          :y="padTop + plotHeight * tick.ratio + 3"
          text-anchor="end"
          fill="var(--muted-foreground)"
          font-size="10"
          data-test="bar-y-tick"
          class="tabular-nums"
        >
          {{ tick.label }}
        </text>
      </g>

      <g
        v-for="bar in bars"
        :key="bar.key"
        tabindex="0"
        :role="props.clickable ? 'button' : 'graphics-symbol'"
        :aria-label="`${bar.label} ${formatCompact(bar.total)}`"
        :data-test="bar.testid"
        class="outline-none focus-visible:outline-2 focus-visible:outline-ring"
        :class="props.clickable ? 'cursor-pointer' : ''"
        @mouseenter="hoveredKey = bar.key"
        @mouseleave="hoveredKey = null"
        @focus="hoveredKey = bar.key"
        @blur="hoveredKey = null"
        @click="onBarClick(bar.key)"
        @keydown.enter.prevent="onBarClick(bar.key)"
        @keydown.space.prevent="onBarClick(bar.key)"
      >
        <!-- 透明命中热区，方便小柱子悬停与点击 -->
        <rect
          :x="bar.x - 4"
          :y="padTop"
          :width="barWidth + 8"
          :height="plotHeight"
          fill="transparent"
        />

        <!-- 外层总量柱 -->
        <rect
          v-if="hasPrimarySeries && bar.totalH > 0"
          :x="bar.x"
          :y="bar.totalY"
          :width="barWidth"
          :height="bar.totalH"
          rx="3"
          fill="var(--primary)"
          class="transition-opacity duration-150 motion-reduce:transition-none"
          :class="hoveredKey === bar.key ? 'opacity-35' : 'opacity-20'"
        />

        <!-- 内层主柱 -->
        <rect
          v-if="bar.primaryH > 0"
          :x="bar.x"
          :y="bar.primaryY"
          :width="barWidth"
          :height="bar.primaryH"
          rx="3"
          fill="var(--primary)"
          class="transition-opacity duration-150 motion-reduce:transition-none"
          :class="hoveredKey && hoveredKey !== bar.key ? 'opacity-50' : 'opacity-100'"
        />

        <!-- X 轴标签 -->
        <text
          v-if="bar.showLabel"
          :x="bar.centerX"
          :y="props.height - 4"
          text-anchor="middle"
          fill="var(--muted-foreground)"
          font-size="10"
          class="tabular-nums"
        >
          {{ bar.label }}
        </text>
      </g>
    </svg>
  </div>
</template>
