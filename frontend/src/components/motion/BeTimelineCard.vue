<script setup lang="ts">
/**
 * beUI 时间线的单张卡片。
 *
 * 独立成子组件的原因：每张卡的 `useTransform` 必须在自己的 setup 里创建；
 * 事件列表动态变化（任务详情的 WS 事件流）时，新卡挂载即有自己的变换，
 * 旧卡卸载即清理 —— 父组件不需要为「事件数组变了」做任何重建。
 */
import { Motion, useTransform, type MotionValue } from "motion-v";

import { SIDE_DIM, cardScale, cardX, type TimelineGeometry } from "./timeline-geometry";

const CARD_CENTER = 0.5;

const props = defineProps<{
  scroll: MotionValue<number>;
  index: number;
  geometry: TimelineGeometry;
  event: { id: string; title: string };
  dateLabel: string;
  active: boolean;
}>();

const emit = defineEmits<{
  (e: "select", index: number): void;
  (e: "keydown", event: KeyboardEvent, index: number): void;
}>();

const offset = useTransform(props.scroll, (s) => props.index - s);
const x = useTransform(offset, (o) => cardX(o, props.geometry));
const scale = useTransform(offset, cardScale);
const zIndex = useTransform(offset, (o) => 100 - Math.round(Math.abs(o) * 10));
const veilOpacity = useTransform(offset, (o) => SIDE_DIM * Math.min(Math.abs(o), 1));
</script>

<template>
  <Motion
    as="li"
    :data-index="props.index"
    :data-test="`be-timeline-card-${props.event.id}`"
    :data-active="props.active || undefined"
    class="absolute left-1/2"
    :style="{
      top: `${CARD_CENTER * 100}%`,
      x,
      scale,
      zIndex,
      width: `${props.geometry.cardWidth}px`,
      marginLeft: `${-props.geometry.cardWidth / 2}px`,
    }"
  >
    <div class="relative -translate-y-1/2">
      <div class="relative overflow-hidden rounded-lg border border-border bg-card">
        <div class="px-3.5 pt-3 pb-2">
          <p class="text-xs tabular-nums text-muted-foreground">{{ props.dateLabel }}</p>
          <h3 class="truncate text-base font-medium tracking-tight text-foreground">
            {{ props.event.title }}
          </h3>
        </div>
        <div class="px-2">
          <div class="aspect-[16/10] overflow-hidden rounded-md border border-border bg-background">
            <slot name="media" />
          </div>
        </div>
        <div
          class="flex min-h-10 items-center gap-1.5 px-3.5 py-2.5 text-sm tabular-nums text-muted-foreground"
        >
          <slot name="meta" />
        </div>
        <Motion
          aria-hidden="true"
          class="pointer-events-none absolute inset-0 bg-background"
          :style="{ opacity: veilOpacity }"
        />
      </div>
      <button
        type="button"
        class="absolute inset-0 cursor-pointer rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-foreground"
        :tabindex="props.active ? 0 : -1"
        :aria-current="props.active ? 'step' : undefined"
        :aria-label="`${props.dateLabel} ${props.event.title}`"
        @click="emit('select', props.index)"
        @keydown="emit('keydown', $event, props.index)"
      />
    </div>
  </Motion>
</template>
