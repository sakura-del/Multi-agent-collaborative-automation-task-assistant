<script setup lang="ts">
/**
 * beUI 时间线的连接线：从舞台上下中心发散到某张卡的近角。
 *
 * 线只画到卡的垂直中心（藏在卡的不透明表面后面），视觉上正好
 * 「挂」在卡的上下边缘，不需要量卡的实际位置。
 */
import { useTransform, type MotionValue } from "motion-v";

import { CABLE_FADE, CABLE_HOLD, CABLE_REACH, CARD_CENTER, cardScale, cardX, clamp, type TimelineGeometry } from "./timeline-geometry";

const props = defineProps<{
  scroll: MotionValue<number>;
  index: number;
  geometry: TimelineGeometry;
  height: number;
}>();

const offset = useTransform(props.scroll, (s) => props.index - s);
const reach = (o: number) =>
  (props.geometry.cardWidth / 2) * cardScale(o) * CABLE_REACH;
const left = useTransform(
  offset,
  (o) => props.geometry.halfWidth + cardX(o, props.geometry) - reach(o),
);
const right = useTransform(
  offset,
  (o) => props.geometry.halfWidth + cardX(o, props.geometry) + reach(o),
);
const opacity = useTransform(offset, (o) =>
  clamp((CABLE_FADE - Math.abs(o)) / (CABLE_FADE - CABLE_HOLD), 0, 1),
);
</script>

<template>
  <Motion as="g" :style="{ opacity }">
    <line
      :x1="props.geometry.halfWidth"
      :y1="0"
      :x2="(left as unknown as number)"
      :y2="props.height * CARD_CENTER"
    />
    <line
      :x1="props.geometry.halfWidth"
      :y1="0"
      :x2="(right as unknown as number)"
      :y2="props.height * CARD_CENTER"
    />
    <line
      :x1="props.geometry.halfWidth"
      :y1="props.height"
      :x2="(left as unknown as number)"
      :y2="props.height * CARD_CENTER"
    />
    <line
      :x1="props.geometry.halfWidth"
      :y1="props.height"
      :x2="(right as unknown as number)"
      :y2="props.height * CARD_CENTER"
    />
  </Motion>
</template>
