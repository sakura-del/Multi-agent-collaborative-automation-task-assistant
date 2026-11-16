<script setup lang="ts">
/**
 * beUI 时间线的连接线：从舞台上下中心发散到某张卡的近角。
 *
 * 为什么不用 MotionValue 直接绑到 `<line>`：motion-v 的 `Motion` 只接管
 * HTML 标签；SVG `<line>` 是普通元素，Vue 会把 MotionValue 对象原样
 * 绑到属性上（`[object Object]`）。所以这里用
 * `useMotionValueEvent` 把 scroll 变化写进响应式数字，由 Vue 正常渲染。
 */
import { ref, watch } from "vue";
import { useMotionValueEvent, type MotionValue } from "motion-v";

import {
  CABLE_FADE,
  CABLE_HOLD,
  CABLE_REACH,
  CARD_CENTER,
  cardScale,
  cardX,
  clamp,
  type TimelineGeometry,
} from "./timeline-geometry";

const props = defineProps<{
  scroll: MotionValue<number>;
  index: number;
  geometry: TimelineGeometry;
  height: number;
}>();

const left = ref(0);
const right = ref(0);
const opacity = ref(0);

function update(scrollValue: number) {
  const g = props.geometry;
  const o = props.index - scrollValue;
  const reach = (g.cardWidth / 2) * cardScale(o) * CABLE_REACH;
  left.value = g.halfWidth + cardX(o, g) - reach;
  right.value = g.halfWidth + cardX(o, g) + reach;
  opacity.value = clamp((CABLE_FADE - Math.abs(o)) / (CABLE_FADE - CABLE_HOLD), 0, 1);
}

useMotionValueEvent(props.scroll, "change", update);
// 几何尺寸变化（resize）与首次挂载都要重算
watch(
  () => [props.geometry, props.index, props.height],
  () => update(props.scroll.get()),
  { immediate: true },
);
</script>

<template>
  <g :style="{ opacity }">
    <line
      :x1="props.geometry.halfWidth"
      :y1="0"
      :x2="left"
      :y2="props.height * CARD_CENTER"
    />
    <line
      :x1="props.geometry.halfWidth"
      :y1="0"
      :x2="right"
      :y2="props.height * CARD_CENTER"
    />
    <line
      :x1="props.geometry.halfWidth"
      :y1="props.height"
      :x2="left"
      :y2="props.height * CARD_CENTER"
    />
    <line
      :x1="props.geometry.halfWidth"
      :y1="props.height"
      :x2="right"
      :y2="props.height * CARD_CENTER"
    />
  </g>
</template>
