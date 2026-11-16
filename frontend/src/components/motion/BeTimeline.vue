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
 * 卡片内容通过 `media` / `meta` slot 注入，业务方不需要了解 MotionValue。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { animate, Motion,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from "motion-v";

import {
  CABLE_FADE,
  CABLE_HOLD,
  CABLE_REACH,
  CARD_CENTER,
  clamp,
  FLICK_MOMENTUM,
  GAP_TICKS,
  GAP_TICK_SPAN,
  MAX_FLICK_EVENTS,
  DRAG_THRESHOLD,
  RUBBER_BAND,
  SIDE_DIM,
  SIDE_SCALE,
  TICK_SHORT,
  TICK_TALL,
  YEAR_INSET,
  YEAR_PARALLAX,
  cardScale,
  cardX,
  GLIDE_SPRING,
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
    formatDate?: (date: Date) => string;
  }>(),
  {
    itemWidth: 340,
    height: 600,
    years: true,
    ruler: true,
    cables: true,
    ariaLabel: "Timeline",
    formatDate: (date: Date) =>
      `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
        date.getDate(),
      ).padStart(2, "0")}`,
  },
);

const emit = defineEmits<{
  (event: "change", id: string): void;
}>();

const stageRef = ref<HTMLElement | null>(null);
const stageWidth = ref(1024);
const activeIndex = ref(0);
const scroll = useMotionValue(0);
const reducedMotion = useReducedMotion();

const geometry = ref<TimelineGeometry>({
  cardWidth: 340,
  gap: 72,
  step: 340 * SIDE_SCALE + 72,
  near: (340 * (1 + SIDE_SCALE)) / 2 + 72,
  halfWidth: 512,
});

function updateGeometry(width: number) {
  const cardWidth = Math.min(props.itemWidth, width * (width < 640 ? 0.58 : 0.36));
  const gap = clamp(cardWidth * 0.22, 40, 72);
  geometry.value = {
    cardWidth,
    gap,
    step: cardWidth * SIDE_SCALE + gap,
    near: (cardWidth * (1 + SIDE_SCALE)) / 2 + gap,
    halfWidth: width / 2,
  };
}

// 演示与业务页都是「给定一批事件」；事件数组在组件生命周期内保持稳定。
const cards = props.events.map((event, index) => {
  const offset = useTransform(scroll, (s) => index - s);
  const x = useTransform(offset, (o) => cardX(o, geometry.value));
  const scale = useTransform(offset, cardScale);
  const zIndex = useTransform(offset, (o) => 100 - Math.round(Math.abs(o) * 10));
  const veilOpacity = useTransform(offset, (o) => SIDE_DIM * Math.min(Math.abs(o), 1));
  return { event, index, offset, x, scale, zIndex, veilOpacity };
});

const cableLines = props.events.map((event, index) => {
  const offset = useTransform(scroll, (s) => index - s);
  const reach = (o: number) => (geometry.value.cardWidth / 2) * cardScale(o) * CABLE_REACH;
  const left = useTransform(
    offset,
    (o) => geometry.value.halfWidth + cardX(o, geometry.value) - reach(o),
  );
  const right = useTransform(
    offset,
    (o) => geometry.value.halfWidth + cardX(o, geometry.value) + reach(o),
  );
  const opacity = useTransform(offset, (o) =>
    clamp((CABLE_FADE - Math.abs(o)) / (CABLE_FADE - CABLE_HOLD), 0, 1),
  );
  return { event, index, left, right, opacity };
});

const yearMarks = (() => {
  const seen = new Map<number, number>();
  props.events.forEach((event, index) => {
    const year = new Date(event.date).getFullYear();
    if (!seen.has(year)) {
      seen.set(year, index);
    }
  });
  return [...seen.entries()].map(([year, position]) => {
    const x = useTransform(
      scroll,
      (s) => (position - s) * geometry.value.step * YEAR_PARALLAX,
    );
    return { year, position, x };
  });
})();

const tickGroups = props.events.slice(0, -1).map((event, index) => {
  const x = useTransform(scroll, (s) => {
    const g = geometry.value;
    const o = index - s;
    const right = cardX(o, g) + (g.cardWidth * cardScale(o)) / 2;
    const left = cardX(o + 1, g) - (g.cardWidth * cardScale(o + 1)) / 2;
    return (right + left) / 2;
  });
  return { event, index, x };
});

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
      <Motion
        v-for="mark in yearMarks"
        :key="mark.year"
        aria-hidden="true"
        class="absolute left-1/2"
        :style="{ x: mark.x, top: `${height * 0.2 * YEAR_INSET}px` }"
      >
        <span
          class="block -translate-x-1/2 font-medium leading-none tracking-tighter tabular-nums text-foreground/[0.06]"
          :style="{ fontSize: `${height * 0.2}px` }"
        >
          {{ mark.year }}
        </span>
      </Motion>
    </template>

    <svg
      v-if="cables"
      aria-hidden="true"
      class="pointer-events-none absolute inset-0 size-full stroke-foreground/[0.14] [mask-image:linear-gradient(to_bottom,transparent,black_6%,black_94%,transparent)]"
      fill="none"
      stroke-width="1"
    >
      <Motion
        v-for="line in cableLines"
        :key="line.event.id"
        as="g"
        :style="{ opacity: line.opacity }"
      >
        <line
          :x1="geometry.halfWidth"
          :y1="0"
          :x2="(line.left as unknown as number)"
          :y2="height * CARD_CENTER"
        />
        <line
          :x1="geometry.halfWidth"
          :y1="0"
          :x2="(line.right as unknown as number)"
          :y2="height * CARD_CENTER"
        />
        <line
          :x1="geometry.halfWidth"
          :y1="height"
          :x2="(line.left as unknown as number)"
          :y2="height * CARD_CENTER"
        />
        <line
          :x1="geometry.halfWidth"
          :y1="height"
          :x2="(line.right as unknown as number)"
          :y2="height * CARD_CENTER"
        />
      </Motion>
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
      <Motion
        v-for="group in tickGroups"
        :key="group.event.id"
        aria-hidden="true"
        class="absolute left-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center"
        :style="{
          x: group.x,
          top: `${CARD_CENTER * 100}%`,
          gap: `${(geometry.gap * GAP_TICK_SPAN) / (GAP_TICKS - 1) - 1}px`,
        }"
      >
        <span
          v-for="i in GAP_TICKS"
          :key="i"
          class="block w-px rounded-full"
          :class="i === (GAP_TICKS + 1) / 2 ? 'bg-foreground/45' : 'bg-foreground/25'"
          :style="{
            height: `${geometry.cardWidth * (i === (GAP_TICKS + 1) / 2 ? TICK_TALL : TICK_SHORT)}px`,
          }"
        />
      </Motion>
    </template>

    <ul class="absolute inset-0">
      <Motion
        v-for="card in cards"
        :key="card.event.id"
        as="li"
        :data-index="card.index"
        :data-test="`be-timeline-card-${card.event.id}`"
        :data-active="card.index === activeIndex || undefined"
        class="absolute left-1/2"
        :style="{
          top: `${CARD_CENTER * 100}%`,
          x: card.x,
          scale: card.scale,
          zIndex: card.zIndex,
          width: `${geometry.cardWidth}px`,
          marginLeft: `${-geometry.cardWidth / 2}px`,
        }"
      >
        <div class="relative -translate-y-1/2">
          <div class="relative overflow-hidden rounded-3xl border border-border bg-card">
            <div class="px-3.5 pt-3 pb-2">
              <p class="text-xs tabular-nums text-muted-foreground">
                {{ dateLabel(card.event.date) }}
              </p>
              <h3 class="truncate text-base font-medium tracking-tight text-foreground">
                {{ card.event.title }}
              </h3>
            </div>
            <div class="px-2">
              <div class="aspect-[16/10] overflow-hidden rounded-2xl border border-border bg-background">
                <slot name="media" :event="card.event" :active="card.index === activeIndex" />
              </div>
            </div>
            <div class="flex items-center gap-1.5 px-3.5 py-2.5 text-sm tabular-nums text-muted-foreground">
              <slot name="meta" :event="card.event" :active="card.index === activeIndex" />
            </div>
            <Motion
              aria-hidden="true"
              class="pointer-events-none absolute inset-0 bg-background"
              :style="{ opacity: card.veilOpacity }"
            />
          </div>
          <button
            type="button"
            class="absolute inset-0 cursor-pointer rounded-3xl outline-none focus-visible:ring-2 focus-visible:ring-foreground"
            :tabindex="card.index === activeIndex ? 0 : -1"
            :aria-current="card.index === activeIndex ? 'step' : undefined"
            :aria-label="`${dateLabel(card.event.date)} ${card.event.title}`"
            @click="select(card.index)"
            @keydown="onCardKeydown($event, card.index)"
          />
        </div>
      </Motion>
    </ul>
  </section>
</template>
