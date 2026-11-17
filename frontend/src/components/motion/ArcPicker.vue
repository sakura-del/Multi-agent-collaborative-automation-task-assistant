<script setup lang="ts">
/**
 * Arc Picker（beUI 移植，https://beui.dev/components/motion/arc-picker）。
 *
 * 用途：侧栏折叠后的页面切换器。沿弧线排列选项，滚轮 / 拖拽 / 方向键
 * 切换；只在**停稳或明确选择**时上报，避免滚动过程中连续跳页。
 *
 * 与 React 原实现的差异（有意为之）：
 * - 不承担表单 hidden input（导航场景不需要）；
 * - 滚轮/拖拽期间只移动视觉位置，settle 后才 emit；
 * - 受控值不接受时不做 reconcile（父级是路由，天然幂等）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  animate,
  Motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "motion-v";

import { clamp } from "@/components/motion/timeline-geometry";
import ArcPickerOption from "./ArcPickerOption.vue";
import type { ArcPickerOption as Option, ArcPickerSide } from "./arc-picker-types";

const props = withDefaults(
  defineProps<{
    options: readonly Option[];
    modelValue?: string;
    side?: ArcPickerSide;
    radius?: number;
    itemHeight?: number;
    visibleCount?: number;
    disabled?: boolean;
    ariaLabel?: string;
    labelClass?: string;
  }>(),
  {
    modelValue: undefined,
    side: "right",
    radius: 260,
    itemHeight: 44,
    visibleCount: 7,
    disabled: false,
    ariaLabel: "选择页面",
    labelClass: "text-lg font-medium",
  },
);

const emit = defineEmits<{
  (event: "update:modelValue", value: string): void;
}>();

const WHEEL_SETTLE_MS = 140;
const DRAG_THRESHOLD = 6;
const COAST_SECONDS = 0.18;
const SPRING_LAYOUT = { stiffness: 320, damping: 32, mass: 0.8 };

const root = ref<HTMLElement | null>(null);
const reduced = useReducedMotion();
const position = useMotionValue(0);
const activity = useMotionValue(0);
const expansion = useSpring(activity, { stiffness: 260, damping: 30 });
const frameTarget = useMotionValue(0);
const frameWidth = useSpring(frameTarget, { stiffness: 400, damping: 36 });

const measurements = ref({ width: 0, labelWidth: 0 });
const horizontal = computed(() => props.side === "top" || props.side === "bottom");

const enabledOptions = computed(() =>
  props.options
    .map((option, index) => ({ option, index }))
    .filter(({ option }) => !option.disabled),
);

const selectedValue = computed(() => {
  const candidate = props.modelValue;
  if (candidate && props.options.some((option) => option.value === candidate && !option.disabled)) {
    return candidate;
  }
  return enabledOptions.value[0]?.option.value;
});

const selectedIndex = computed(() =>
  Math.max(
    0,
    props.options.findIndex((option) => option.value === selectedValue.value),
  ),
);

const rowHeight = computed(() => Math.max(32, props.itemHeight));
const count = computed(() => Math.max(3, Math.floor(props.visibleCount)));
const spacing = computed(() =>
  horizontal.value
    ? Math.max(rowHeight.value, measurements.value.labelWidth + 48)
    : rowHeight.value,
);
const verticalInset = computed(() =>
  Math.max(
    24,
    Math.min(
      measurements.value.width * 0.42,
      measurements.value.width - measurements.value.labelWidth - 24,
    ),
  ),
);
const verticalAnchor = computed(() => {
  if (!measurements.value.width) {
    return props.side === "left" ? "58%" : "42%";
  }
  return props.side === "left"
    ? measurements.value.width - verticalInset.value
    : verticalInset.value;
});
const curveRadius = computed(() =>
  horizontal.value ? Math.max(props.radius, spacing.value * 2) : Math.max(120, props.radius),
);
const shownCount = computed(() =>
  horizontal.value
    ? Math.min(
        count.value,
        Math.max(1, (measurements.value.width || count.value * spacing.value) / spacing.value),
      )
    : count.value,
);
const rootHeight = computed(() => {
  if (!horizontal.value) return count.value * rowHeight.value;
  return Math.ceil(
    Math.max(
      rowHeight.value * 2,
      curveRadius.value *
        (1 - Math.cos(Math.min(1.35, (shownCount.value * spacing.value) / (2 * curveRadius.value)))) +
        rowHeight.value +
        24,
    ),
  );
});

const leading = useTransform<number, string>([frameWidth, expansion], ([size = 0, amount = 0]) => {
  const extent = horizontal.value ? size / 2 : props.side === "left" ? size : 0;
  const gap = 4 + clamp(amount, 0, 1) * 6;
  return `translate3d(${-extent - gap}px, -50%, 0) scaleY(${1 - clamp(amount, 0, 1) * 0.08})`;
});
const trailing = useTransform<number, string>([frameWidth, expansion], ([size = 0, amount = 0]) => {
  const extent = horizontal.value ? size / 2 : props.side === "right" ? size : 0;
  const gap = 4 + clamp(amount, 0, 1) * 6;
  return `translate3d(${extent + gap}px, -50%, 0) scaleY(${1 - clamp(amount, 0, 1) * 0.08})`;
});
const frameOpacity = useTransform(frameWidth, (size) => (size > 0 ? 0.8 : 0));

let tracking = false;
let wheelTarget: number | null = null;
let wheelTimer: ReturnType<typeof setTimeout> | null = null;
let animation: { stop: () => void } | null = null;
let suppressClick = false;
let frameMeasured = false;

interface DragState {
  id: number;
  coordinate: number;
  start: number;
  last: number;
  time: number;
  velocity: number;
  moved: boolean;
}

let drag: DragState | null = null;

function measure() {
  const element = root.value;
  if (!element) return;
  const width = element.clientWidth;
  const buttons = [...element.querySelectorAll<HTMLButtonElement>('[data-slot="arc-picker-option"]')];
  const labelWidth = Math.max(0, ...buttons.map((button) => button.offsetWidth));
  if (measurements.value.width !== width || measurements.value.labelWidth !== labelWidth) {
    measurements.value = { width, labelWidth };
  }
  const selected = element.querySelector<HTMLButtonElement>('[data-selected="true"]');
  const size = selected?.offsetWidth ?? 0;
  frameTarget.set(size);
  if (!frameMeasured || reduced.value) frameWidth.jump(size);
  frameMeasured = true;
}

function settle(index: number, immediate = false, commit = false) {
  animation?.stop();
  const done = () => {
    tracking = false;
    animation = null;
    wheelTarget = null;
    activity.set(0);
    if (reduced.value) expansion.jump(0);
  };
  if (immediate || reduced.value) {
    position.jump(index);
    done();
  } else {
    animation = animate(position, index, {
      ...SPRING_LAYOUT,
      onComplete: done,
    });
  }
  if (commit) {
    const option = props.options[index];
    if (option && option.value !== selectedValue.value) emit("update:modelValue", option.value);
  }
}

function commitAt(index: number) {
  const option = props.options[index];
  if (!option || option.disabled || props.disabled) return;
  settle(index, true, true);
  void nextTick(() => {
    root.value
      ?.querySelector<HTMLButtonElement>(`[data-value="${option.value}"]`)
      ?.focus({ preventScroll: true });
  });
}

function nearestEnabled(at: number): number {
  let nearest = -1;
  let distance = Infinity;
  props.options.forEach((option, index) => {
    if (!option.disabled && Math.abs(index - at) < distance) {
      nearest = index;
      distance = Math.abs(index - at);
    }
  });
  return nearest;
}

function select(index: number) {
  if (suppressClick) {
    suppressClick = false;
    return;
  }
  commitAt(index);
}

function onWheel(event: WheelEvent) {
  const element = root.value;
  if (!element || props.disabled || event.ctrlKey) return;
  if (!enabledOptions.value.length) return;
  const useX = Math.abs(event.deltaX) > Math.abs(event.deltaY);
  if (!horizontal.value && useX) return;
  const delta =
    (horizontal.value && useX ? event.deltaX : event.deltaY) *
    (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1);
  if (!delta) return;

  const first = enabledOptions.value[0]?.index ?? 0;
  const last = enabledOptions.value[enabledOptions.value.length - 1]?.index ?? first;
  const from = wheelTarget ?? position.get();
  // 到两端就交还滚轮给页面，不把页面滚动锁死
  if ((from <= first && delta < 0) || (from >= last && delta > 0)) return;
  event.preventDefault();

  if (wheelTimer) clearTimeout(wheelTimer);
  if (reduced.value) {
    const current = enabledOptions.value.findIndex(({ option }) => option.value === selectedValue.value);
    const next =
      enabledOptions.value[clamp(current + Math.sign(delta), 0, enabledOptions.value.length - 1)];
    if (next) commitAt(next.index);
    return;
  }

  animation?.stop();
  animation = null;
  tracking = true;
  activity.set(1);
  const next = clamp(from + delta / spacing.value, first, last);
  wheelTarget = next;
  position.set(next);
  wheelTimer = setTimeout(() => {
    wheelTimer = null;
    wheelTarget = null;
    settle(nearestEnabled(next), false, true);
  }, WHEEL_SETTLE_MS);
}

function onPointerDown(event: PointerEvent) {
  if (props.disabled || event.button !== 0 || !event.isPrimary) return;
  animation?.stop();
  tracking = false;
  suppressClick = false;
  const coordinate = horizontal.value ? event.clientX : event.clientY;
  drag = {
    id: event.pointerId,
    coordinate,
    start: position.get(),
    last: coordinate,
    time: performance.now(),
    velocity: 0,
    moved: false,
  };
}

function onPointerMove(event: PointerEvent) {
  const current = drag;
  if (!current || current.id !== event.pointerId) return;
  const coordinate = horizontal.value ? event.clientX : event.clientY;
  const delta = coordinate - current.coordinate;
  if (!current.moved && Math.abs(delta) < DRAG_THRESHOLD) return;
  current.moved = true;
  tracking = true;
  activity.set(1);
  if (!root.value?.hasPointerCapture(event.pointerId)) {
    root.value?.setPointerCapture(event.pointerId);
  }
  const now = performance.now();
  const elapsed = Math.max(8, now - current.time);
  current.velocity =
    current.velocity * 0.35 + ((current.last - coordinate) / spacing.value / elapsed) * 1000 * 0.65;
  current.last = coordinate;
  current.time = now;
  const next = clamp(current.start - delta / spacing.value, 0, Math.max(0, props.options.length - 1));
  position.set(reduced.value ? Math.round(next) : next);
}

function release(event: PointerEvent, cancelled: boolean) {
  const current = drag;
  if (!current || current.id !== event.pointerId) return;
  drag = null;
  if (root.value?.hasPointerCapture(event.pointerId)) {
    root.value.releasePointerCapture(event.pointerId);
  }
  if (!current.moved) return;
  suppressClick = true;
  if (cancelled) {
    settle(nearestEnabled(position.get()), false, true);
    return;
  }
  const velocity = performance.now() - current.time > 100 ? 0 : current.velocity;
  const target = nearestEnabled(position.get() + (reduced.value ? 0 : velocity * COAST_SECONDS));
  settle(target, false, true);
}

function onKeydown(event: KeyboardEvent) {
  if (props.disabled) return;
  const enabled = enabledOptions.value;
  if (!enabled.length) return;
  const current = Math.max(
    0,
    enabled.findIndex(({ option }) => option.value === selectedValue.value),
  );
  let next: number;
  switch (event.key) {
    case "ArrowDown":
    case "ArrowRight":
      next = current + (event.shiftKey ? 5 : 1);
      break;
    case "ArrowUp":
    case "ArrowLeft":
      next = current - (event.shiftKey ? 5 : 1);
      break;
    case "Home":
      next = 0;
      break;
    case "End":
      next = enabled.length - 1;
      break;
    case "PageDown":
      next = current + 5;
      break;
    case "PageUp":
      next = current - 5;
      break;
    default:
      return;
  }
  event.preventDefault();
  const option = enabled[clamp(next, 0, enabled.length - 1)];
  if (option) commitAt(option.index);
}

let observer: ResizeObserver | undefined;

function observeButtons() {
  const element = root.value;
  if (!element || !observer) return;
  observer.disconnect();
  observer.observe(element);
  element
    .querySelectorAll<HTMLButtonElement>('[data-slot="arc-picker-option"]')
    .forEach((button) => observer?.observe(button));
}

onMounted(() => {
  position.jump(selectedIndex.value);
  if (typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(() => measure());
  } else {
    window.addEventListener("resize", measure);
  }
  observeButtons();
  measure();
  root.value?.addEventListener("wheel", onWheel, { passive: false });
});

onBeforeUnmount(() => {
  animation?.stop();
  observer?.disconnect();
  window.removeEventListener("resize", measure);
  root.value?.removeEventListener("wheel", onWheel);
  if (wheelTimer) clearTimeout(wheelTimer);
});

watch(
  () => [selectedIndex.value, props.options.length, props.side, spacing.value],
  async () => {
    await nextTick();
    observeButtons();
    if (!tracking) {
      settle(selectedIndex.value, true);
    }
    measure();
  },
  { immediate: false },
);
</script>

<template>
  <div
    ref="root"
    role="radiogroup"
    :aria-label="props.ariaLabel"
    aria-orientation="vertical"
    :aria-disabled="props.disabled || undefined"
    data-slot="arc-picker"
    :data-side="props.side"
    :class="[
      'relative w-full min-w-0 cursor-grab touch-pan-x overflow-hidden select-none active:cursor-grabbing',
      props.disabled && 'cursor-default opacity-50',
    ]"
    :style="{
      touchAction: horizontal ? 'pan-y pinch-zoom' : 'pan-x pinch-zoom',
      height: `${rootHeight}px`,
      maskImage: `linear-gradient(to ${horizontal ? 'right' : 'bottom'}, transparent, black 12%, black 88%, transparent)`,
    }"
    @keydown="onKeydown"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="release($event, false)"
    @pointercancel="release($event, true)"
  >
    <span class="sr-only">
      拖拽或滚动选择页面；方向键逐项移动，Home/End 跳到首尾。
    </span>

    <ArcPickerOption
      v-for="(option, index) in props.options"
      :key="option.value"
      :option="option"
      :index="index"
      :position="position"
      :vertical-anchor="verticalAnchor"
      :radius="curveRadius"
      :item-height="spacing"
      :visible-count="shownCount"
      :side="props.side"
      :selected="option.value === selectedValue"
      :disabled="props.disabled"
      :label-class="props.labelClass"
      @select="select"
    />

    <div
      aria-hidden="true"
      class="pointer-events-none absolute h-0 w-0 text-primary"
      :class="props.labelClass"
      :style="{
        top: horizontal ? (props.side === 'top' ? '36px' : 'calc(100% - 36px)') : '50%',
        left: horizontal ? '50%' : verticalAnchor,
      }"
    >
      <Motion
        as="span"
        data-slot="arc-picker-bracket"
        data-edge="leading"
        class="absolute right-0"
        :style="{ transform: leading, opacity: frameOpacity }"
      >
        [
      </Motion>
      <Motion
        as="span"
        data-slot="arc-picker-bracket"
        data-edge="trailing"
        class="absolute left-0"
        :style="{ transform: trailing, opacity: frameOpacity }"
      >
        ]
      </Motion>
    </div>
  </div>
</template>
