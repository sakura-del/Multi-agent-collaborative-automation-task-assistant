<script setup lang="ts">
/**
 * beUI Timeline 的 Vue 移植（https://beui.dev/components/motion/timeline）。
 *
 * 原实现是 React + Framer Motion；这里用已安装的 `motion-v` 复刻几何、
 * 手势与可访问性，不引入 React。
 *
 * 规格（与源码常量一一对应）：
 * - 侧卡 0.72 缩放、遮罩最深 0.45；年份 6%、线缆 14%、刻度 25%/45%；
 * - 拖拽 4px 阈值、橡皮筋 0.3、惯性 0.3、最多连跳 4 张；
 * - 吸附 spring stiffness=120 / damping=24 / mass=1；
 * - ←/→、Home/End、aria-current、focus ring、reduced-motion 降级。
 *
 * 卡片/线缆/刻度/年份各自是子组件：每张卡在自己的 setup 里创建
 * `useTransform`，事件列表动态变化（详情页 WS 事件流）时无需重建。
 * 卡片内容通过 `media` / `meta` slot 注入。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { animate, useMotionValue, useReducedMotion } from "motion-v";

import BeTimelineCard from "./BeTimelineCard.vue";
import BeTimelineCables from "./BeTimelineCables.vue";
import BeTimelineTicks from "./BeTimelineTicks.vue";
import BeTimelineYear from "./BeTimelineYear.vue";
import {
  CARD_CENTER,
  DRAG_THRESHOLD,
  FLICK_MOMENTUM,
  GLIDE_SPRING,
  MAX_FLICK_EVENTS,
  RUBBER_BAND,
  clamp,
  type TimelineGeometry,
} from "./timeline-geometry";

export interface BeTimelineEvent {
  id: string;
  date: string | Date;
  title: string;
}

const props = withDefaults(
  defineProps<{
    events: BeTimelineEvent[];
    itemWidth?: number;
    height?: number;
    years?: boolean;
    ruler?: boolean;
    cables?: boolean;
    ariaLabel?: string;
    /** 初始聚焦第几张卡（详情页默认聚焦最新一条）。 */
    initialIndex?: number;
    formatDate?: (date: Date) => string;
  }>(),
  {
    itemWidth: 340,
    height: 600,
    years: true,
    ruler: true,
    cables: true,
    ariaLabel: "Timeline",
    initialIndex: 0,
    formatDate: (date: Date) =>
      `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
        date.getDate(),
      ).padStart(2, "0")}`,
  },
);

const emit = defineEmits<{
  (event: "change", id: string): void;
}>();

defineSlots<{
  media?: (props: { event: BeTimelineEvent; active: boolean }) => unknown;
  meta?: (props: { event: BeTimelineEvent; active: boolean }) => unknown;
}>();

const stageRef = ref<HTMLElement | null>(null);
const activeIndex = ref(clamp(props.initialIndex, 0, Math.max(0, props.events.length - 1)));
const scroll = useMotionValue(activeIndex.value);
const reducedMotion = useReducedMotion();

const geometry = ref<TimelineGeometry>({
  cardWidth: props.itemWidth,
  gap: clamp(props.itemWidth * 0.22, 40, 72),
  step: props.itemWidth * 0.72 + clamp(props.itemWidth * 0.22, 40, 72),
  near: (props.itemWidth * (1 + 0.72)) / 2 + clamp(props.itemWidth * 0.22, 40, 72),
  halfWidth: 512,
});

function updateGeometry(width: number) {
  const cardWidth = Math.min(props.itemWidth, width * (width < 640 ? 0.58 : 0.36));
  const gap = clamp(cardWidth * 0.22, 40, 72);
  geometry.value = {
    cardWidth,
    gap,
    step: cardWidth * 0.72 + gap,
    near: (cardWidth * (1 + 0.72)) / 2 + gap,
    halfWidth: width / 2,
  };
}

const yearMarks = computed(() => {
  const seen = new Map<number, number>();
  props.events.forEach((event, index) => {
    const year = new Date(event.date).getFullYear();
    if (!seen.has(year)) {
      seen.set(year, index);
    }
  });
  return [...seen.entries()].map(([year, position]) => ({ year, position }));
});

const tickIndices = computed(() =>
  props.events.length > 1 ? props.events.slice(0, -1).map((_, index) => index) : [],
);

let controls: { stop: () => void } | undefined;

function glideTo(index: number, velocity = 0) {
  controls?.stop();
  if (reducedMotion.value) {
    controls = animate(scroll, index, { duration: 0 });
    return;
  }
  controls = animate(scroll, index, {
    type: "spring",
    ...GLIDE_SPRING,
    velocity,
    restDelta: 0.001,
  });
}

function focusButton(index: number) {
  void nextTick(() => {
    stageRef.value
      ?.querySelector<HTMLButtonElement>(`[data-index="${index}"] button`)
      ?.focus();
  });
}

function select(index: number, focus = false) {
  const next = clamp(index, 0, props.events.length - 1);
  activeIndex.value = next;
  glideTo(next);
  const event = props.events[next];
  if (event) {
    emit("change", event.id);
  }
  if (focus) {
    focusButton(next);
  }
}

function onCardKeydown(event: KeyboardEvent, index: number) {
  if (event.key === "ArrowRight") {
    event.preventDefault();
    select(index + 1, true);
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    select(index - 1, true);
  } else if (event.key === "Home") {
    event.preventDefault();
    select(0, true);
  } else if (event.key === "End") {
    event.preventDefault();
    select(props.events.length - 1, true);
  }
}

const drag = {
  pointerId: -1,
  startX: 0,
  startScroll: 0,
  moved: false,
  lastX: 0,
  lastTime: 0,
  velocity: 0,
};

function rubber(value: number): number {
  const max = props.events.length - 1;
  if (value < 0) return value * RUBBER_BAND;
  if (value > max) return max + (value - max) * RUBBER_BAND;
  return value;
}

function onPointerDown(event: PointerEvent) {
  if (props.events.length === 0) return;
  controls?.stop();
  drag.pointerId = event.pointerId;
  drag.startX = event.clientX;
  drag.lastX = event.clientX;
  drag.startScroll = scroll.get();
  drag.lastTime = performance.now();
  drag.velocity = 0;
  drag.moved = false;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}

function onPointerMove(event: PointerEvent) {
  if (drag.pointerId !== event.pointerId) return;
  const deltaX = event.clientX - drag.startX;
  if (!drag.moved && Math.abs(deltaX) < DRAG_THRESHOLD) return;
  drag.moved = true;

  const now = performance.now();
  const dt = now - drag.lastTime;
  if (dt > 0) {
    drag.velocity = -(event.clientX - drag.lastX) / dt;
  }
  drag.lastX = event.clientX;
  drag.lastTime = now;

  scroll.set(rubber(drag.startScroll - deltaX / geometry.value.step));
}

function settle(velocity = 0) {
  const max = props.events.length - 1;
  const projected = scroll.get() + (velocity * FLICK_MOMENTUM * 1000) / geometry.value.step;
  const flick = clamp(projected - scroll.get(), -MAX_FLICK_EVENTS, MAX_FLICK_EVENTS);
  const target = clamp(Math.round(scroll.get() + flick), 0, max);
  activeIndex.value = target;
  glideTo(target, (velocity * 1000) / geometry.value.step);
  const event = props.events[target];
  if (event) emit("change", event.id);
}

function onPointerUp(event: PointerEvent) {
  if (drag.pointerId !== event.pointerId) return;
  drag.pointerId = -1;
  if (!drag.moved) return;
  settle(drag.velocity);
}

let wheelTimer: ReturnType<typeof setTimeout> | null = null;

function onWheel(event: WheelEvent) {
  const horizontal =
    Math.abs(event.deltaX) > Math.abs(event.deltaY) || (event.shiftKey && event.deltaY !== 0);
  if (!horizontal) return;
  const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
  if (delta === 0) return;
  event.preventDefault();
  controls?.stop();
  scroll.set(rubber(scroll.get() + (delta / geometry.value.step) * 0.5));
  if (wheelTimer) clearTimeout(wheelTimer);
  wheelTimer = setTimeout(() => settle(0), 140);
}

let resizeObserver: ResizeObserver | undefined;

function onWindowResize() {
  const element = stageRef.value;
  if (element) updateGeometry(element.clientWidth || 1024);
}

onMounted(() => {
  const element = stageRef.value;
  if (!element) return;
  updateGeometry(element.clientWidth || 1024);
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => onWindowResize());
    resizeObserver.observe(element);
  } else {
    window.addEventListener("resize", onWindowResize);
  }
  element.addEventListener("wheel", onWheel, { passive: false });
  // 初始聚焦直接定位，不做入场动画（详情页默认聚焦最新一条）。
  if (activeIndex.value > 0) {
    scroll.set(activeIndex.value);
  }
});

onBeforeUnmount(() => {
  controls?.stop();
  resizeObserver?.disconnect();
  window.removeEventListener("resize", onWindowResize);
  stageRef.value?.removeEventListener("wheel", onWheel);
  if (wheelTimer) clearTimeout(wheelTimer);
});

watch(
  () => props.events.length,
  (length) => {
    if (activeIndex.value > length - 1) {
      activeIndex.value = Math.max(0, length - 1);
      glideTo(activeIndex.value);
    }
  },
);

function dateLabel(date: string | Date): string {
  return props.formatDate(new Date(date));
}

const hintId = `be-timeline-hint-${Math.random().toString(36).slice(2, 9)}`;
</script>

<template>
  <section
    ref="stageRef"
    data-test="be-timeline"
    class="relative w-full cursor-grab touch-pan-y overscroll-x-contain select-none [clip-path:inset(0)] [-webkit-touch-callout:none] active:cursor-grabbing [mask-image:linear-gradient(to_right,transparent,black_6%,black_94%,transparent)]"
    :style="{ height: `${height}px` }"
    :aria-label="ariaLabel"
    :aria-describedby="hintId"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <p :id="hintId" class="sr-only">使用左右方向键在事件之间移动。</p>

    <template v-if="years">
      <BeTimelineYear
        v-for="mark in yearMarks"
        :key="mark.year"
        :scroll="scroll"
        :position="mark.position"
        :step="geometry.step"
        :label="String(mark.year)"
        :font-size="height * 0.2"
      />
    </template>

    <svg
      v-if="cables"
      aria-hidden="true"
      class="pointer-events-none absolute inset-0 size-full stroke-foreground/[0.14] [mask-image:linear-gradient(to_bottom,transparent,black_6%,black_94%,transparent)]"
      fill="none"
      stroke-width="1"
    >
      <BeTimelineCables
        v-for="(event, index) in events"
        :key="event.id"
        :scroll="scroll"
        :index="index"
        :geometry="geometry"
        :height="height"
      />
    </svg>

    <div
      aria-hidden="true"
      class="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 text-foreground/80"
      :style="{
        backgroundImage:
          'linear-gradient(to bottom, transparent, currentColor 6%, currentColor 94%, transparent)',
      }"
    />

    <template v-if="ruler">
      <BeTimelineTicks
        v-for="index in tickIndices"
        :key="`ticks-${index}`"
        :scroll="scroll"
        :index="index"
        :geometry="geometry"
      />
    </template>

    <ul class="absolute inset-0">
      <BeTimelineCard
        v-for="(event, index) in events"
        :key="event.id"
        :scroll="scroll"
        :index="index"
        :geometry="geometry"
        :event="event"
        :date-label="dateLabel(event.date)"
        :active="index === activeIndex"
        @select="select($event)"
        @keydown="onCardKeydown"
      >
        <template #media>
          <slot name="media" :event="event" :active="index === activeIndex" />
        </template>
        <template #meta>
          <slot name="meta" :event="event" :active="index === activeIndex" />
        </template>
      </BeTimelineCard>
    </ul>
  </section>
</template>
