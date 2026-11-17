<script setup lang="ts">
/**
 * Arc Picker 的单个选项（beUI 移植）。
 *
 * 每项在自己的 setup 里创建 `useTransform`，拖动/滚轮时只更新 MotionValue，
 * 不触发 Vue 重渲染。几何公式与原实现逐行对应。
 */
import { useTransform, Motion, type MotionValue } from "motion-v";

import { clamp } from "@/components/motion/timeline-geometry";
import type { ArcPickerSide } from "./arc-picker-types";

const props = defineProps<{
  option: { value: string; label: string; disabled?: boolean };
  index: number;
  position: MotionValue<number>;
  verticalAnchor: number | string;
  radius: number;
  itemHeight: number;
  visibleCount: number;
  side: ArcPickerSide;
  selected: boolean;
  disabled: boolean;
  labelClass: string;
}>();

const emit = defineEmits<{
  (event: "select", index: number): void;
}>();

const horizontal = props.side === "top" || props.side === "bottom";

const transform = useTransform(props.position, (at) => {
  const angle = clamp(((props.index - at) * props.itemHeight) / props.radius, -1.5, 1.5);
  const bend = props.radius * (1 - Math.cos(angle));
  const along = props.radius * Math.sin(angle);
  const x = horizontal ? along : props.side === "left" ? bend : -bend;
  const y = horizontal ? (props.side === "top" ? bend : -bend) : along;
  const rotation = ((props.side === "left" || props.side === "bottom" ? -angle : angle) * 180) / Math.PI;
  const anchor = horizontal ? "-50%" : props.side === "left" ? "-100%" : "0%";
  return `translate3d(${x}px, ${y}px, 0) translate(${anchor}, -50%) rotate(${rotation}deg)`;
});

const opacity = useTransform(props.position, (at) => {
  const distance = Math.abs(props.index - at);
  return (
    clamp((props.visibleCount / 2 + 0.5 - distance) / 1.5, 0, 1) *
    (props.disabled || props.option.disabled ? 0.35 : 1)
  );
});

const pointerEvents = useTransform(props.position, (at) =>
  Math.abs(props.index - at) < props.visibleCount / 2 ? "auto" : "none",
);
</script>

<template>
  <Motion
    as="button"
    type="button"
    role="radio"
    :aria-checked="props.selected"
    :disabled="props.disabled || props.option.disabled"
    :tabindex="props.selected && !props.disabled && !props.option.disabled ? 0 : -1"
    data-slot="arc-picker-option"
    :data-value="props.option.value"
    :data-selected="props.selected || undefined"
    :class="[
      'absolute rounded-md px-2 py-1 whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-35',
      props.labelClass,
      horizontal && 'text-center',
      !horizontal && (props.side === 'left' ? 'text-right' : 'text-left'),
      props.selected ? 'text-primary' : 'text-muted-foreground',
    ]"
    :style="{
      top: horizontal ? (props.side === 'top' ? '36px' : 'calc(100% - 36px)') : '50%',
      left: horizontal ? '50%' : props.verticalAnchor,
      transformOrigin: horizontal
        ? 'center'
        : props.side === 'left'
          ? 'right center'
          : 'left center',
      maxWidth: horizontal ? 'calc(100% - 4rem)' : 'calc(100% - 3rem)',
      transform,
      opacity,
      pointerEvents,
    }"
    @click="emit('select', props.index)"
  >
    <span data-slot="arc-picker-label" class="block overflow-hidden text-ellipsis">
      {{ props.option.label }}
    </span>
  </Motion>
</template>
