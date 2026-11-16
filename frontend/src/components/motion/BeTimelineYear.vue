<script setup lang="ts">
/**
 * beUI 时间线的背景年份数字（视差层）。
 * 年份移动速度是卡片的 0.5 倍（YEAR_PARALLAX），产生纵深。
 */
import { Motion, useTransform, type MotionValue } from "motion-v";

import { YEAR_INSET, YEAR_PARALLAX } from "./timeline-geometry";

const props = defineProps<{
  scroll: MotionValue<number>;
  position: number;
  step: number;
  label: string;
  fontSize: number;
}>();

const x = useTransform(
  props.scroll,
  (s) => (props.position - s) * props.step * YEAR_PARALLAX,
);
</script>

<template>
  <Motion
    aria-hidden="true"
    class="absolute left-1/2"
    :style="{ x, top: `${props.fontSize * YEAR_INSET}px` }"
  >
    <span
      class="block -translate-x-1/2 font-medium leading-none tracking-tighter tabular-nums text-foreground/[0.06]"
      :style="{ fontSize: `${props.fontSize}px` }"
    >
      {{ props.label }}
    </span>
  </Motion>
</template>
