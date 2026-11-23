<script setup lang="ts">
import { computed, ref } from "vue";

export interface DonutSegment {
  id: string;
  label: string;
  value: number;
  /** CSS 颜色（建议使用 semantic token，如 var(--primary) / var(--success)） */
  color: string;
  hint?: string;
}

const props = withDefaults(
  defineProps<{
    segments: DonutSegment[];
    centerValue?: string;
    centerLabel?: string;
    size?: number;
    strokeWidth?: number;
  }>(),
  {
    size: 132,
    strokeWidth: 14,
  },
);

const hoveredId = ref<string | null>(null);

const total = computed(() =>
  props.segments.reduce((sum, item) => sum + Math.max(0, item.value), 0),
);

const radius = computed(() => (props.size - props.strokeWidth) / 2);
const circumference = computed(() => 2 * Math.PI * radius.value);

const arcs = computed(() => {
  const sum = total.value;
  if (sum <= 0) return [];
  let offset = 0;
  return props.segments
    .filter((item) => item.value > 0)
    .map((item) => {
      const ratio = item.value / sum;
      const length = ratio * circumference.value;
      const arc = {
        ...item,
        ratio,
        percentText: `${(ratio * 100).toFixed(1)}%`,
        dasharray: `${Math.max(0, length - 1.5)} ${circumference.value}`,
        dashoffset: -offset,
      };
      offset += length;
      return arc;
    });
});

const activeArc = computed(() =>
  hoveredId.value ? arcs.value.find((item) => item.id === hoveredId.value) ?? null : null,
);
</script>

<template>
  <div class="flex flex-wrap items-center gap-5">
    <div
      class="relative shrink-0"
      :style="{ width: `${props.size}px`, height: `${props.size}px` }"
    >
      <svg
        :width="props.size"
        :height="props.size"
        :viewBox="`0 0 ${props.size} ${props.size}`"
        class="-rotate-90"
        role="img"
        :aria-label="props.centerLabel ?? '占比分布图'"
      >
        <circle
          :cx="props.size / 2"
          :cy="props.size / 2"
          :r="radius"
          fill="none"
          stroke="var(--muted)"
          :stroke-width="props.strokeWidth"
        />
        <circle
          v-for="arc in arcs"
          :key="arc.id"
          :cx="props.size / 2"
          :cy="props.size / 2"
          :r="radius"
          fill="none"
          :stroke="arc.color"
          :stroke-width="hoveredId === arc.id ? props.strokeWidth + 2 : props.strokeWidth"
          :stroke-dasharray="arc.dasharray"
          :stroke-dashoffset="arc.dashoffset"
          class="cursor-pointer transition-all duration-150"
          :class="hoveredId && hoveredId !== arc.id ? 'opacity-35' : 'opacity-100'"
          @mouseenter="hoveredId = arc.id"
          @mouseleave="hoveredId = null"
        />
      </svg>

      <div class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-3 text-center">
        <span class="text-base font-semibold leading-tight text-foreground tabular-nums">
          {{ activeArc ? activeArc.percentText : (props.centerValue ?? total) }}
        </span>
        <span class="mt-0.5 max-w-full truncate text-[10px] text-muted-foreground">
          {{ activeArc ? activeArc.label : (props.centerLabel ?? "合计") }}
        </span>
      </div>
    </div>

    <ul class="min-w-36 flex-1 space-y-1.5 text-xs">
      <li
        v-for="item in props.segments"
        :key="item.id"
        class="flex items-center justify-between gap-2 rounded px-1.5 py-0.5 transition-colors"
        :class="hoveredId === item.id ? 'bg-muted/70' : 'hover:bg-muted/40'"
        @mouseenter="hoveredId = item.id"
        @mouseleave="hoveredId = null"
      >
        <span class="flex min-w-0 items-center gap-2">
          <span
            class="size-2 shrink-0 rounded-full"
            :style="{ backgroundColor: item.color }"
            aria-hidden="true"
          />
          <span class="truncate text-foreground">{{ item.label }}</span>
        </span>
        <span class="shrink-0 font-medium text-muted-foreground tabular-nums">
          {{ item.hint ?? item.value }}
        </span>
      </li>
    </ul>
  </div>
</template>
