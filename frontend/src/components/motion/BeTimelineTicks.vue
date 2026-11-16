<script setup lang="ts">
/**
 * beUI 时间线的间隙刻度：位于卡 index 与下一张卡之间的中点。
 * 间隙宽度在拖动中保持不变（见 cardX），所以刻度组只做平移。
 */
import { useTransform, type MotionValue } from "motion-v";

import { GAP_TICKS, GAP_TICK_SPAN, TICK_SHORT, TICK_TALL, cardScale, cardX, type TimelineGeometry } from "./timeline-geometry";

const CARD_CENTER = 0.5;

const props = defineProps<{
  scroll: MotionValue<number>;
  index: number;
  geometry: TimelineGeometry;
}>();

const x = useTransform(props.scroll, (s) => {
  const g = props.geometry;
  const o = props.index - s;
  const right = cardX(o, g) + (g.cardWidth * cardScale(o)) / 2;
  const left = cardX(o + 1, g) - (g.cardWidth * cardScale(o + 1)) / 2;
  return (right + left) / 2;
});
</script>

<template>
  <Motion
    aria-hidden="true"
    class="absolute left-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center"
    :style="{
      x,
      top: `${CARD_CENTER * 100}%`,
      gap: `${(props.geometry.gap * GAP_TICK_SPAN) / (GAP_TICKS - 1) - 1}px`,
    }"
  >
    <span
      v-for="i in GAP_TICKS"
      :key="i"
      class="block w-px rounded-full"
      :class="i === (GAP_TICKS + 1) / 2 ? 'bg-foreground/45' : 'bg-foreground/25'"
      :style="{
        height: `${props.geometry.cardWidth * (i === (GAP_TICKS + 1) / 2 ? TICK_TALL : TICK_SHORT)}px`,
      }"
    />
  </Motion>
</template>
