<script setup lang="ts">
/**
 * 卡片容器（beUI Motion Card 结构，https://asharca.github.io/ui/components/motion/card/）。
 *
 * ## 间距模型：根管垂直、部件管水平
 *
 * 根负责 `py-*` 与 `gap-*`，各部件负责自己的 `px-*`。这样省略
 * `CardContent` / `CardFooter` 时，剩下的部件边缘不会塌——旧实现把
 * padding 写在每个部件上（`p-4` / `p-4 pt-0`），少一个部件就破坏对齐。
 *
 * ## 交互态只是视觉，不抢焦点
 *
 * `interactive` / `selected` 都不给卡片加 `tabIndex`：真实状态必须由卡内
 * 控件（checkbox / radio / link）承载。按压缩放用 pointer 事件手工驱动，
 * **不用 `whileTap`**——motion 的 whileTap 会给元素塞 `tabIndex=0`，
 * 在真正的控件前多出一个无名的 tab 停靠点。
 */
import { computed, provide, ref } from "vue";
import { animate, useReducedMotion } from "motion-v";
import type { HTMLAttributes } from "vue";

import { PRESS_SCALE, SPRING_PRESS } from "@/lib/ease";
import { cn } from "@/lib/utils";

import { CARD_SIZE_KEY } from "./card-context";

export type CardVariant = "outlined" | "muted";
export type CardSize = "default" | "compact";

const props = withDefaults(
  defineProps<{
    class?: HTMLAttributes["class"];
    /** outlined：白底 + 发丝边框；muted：灰底、无边框（内嵌区块）。 */
    variant?: CardVariant;
    /** default：独立卡；compact：列表/并排卡。 */
    size?: CardSize;
    /** 视觉按压反馈；卡片本身不可聚焦。 */
    interactive?: boolean;
    /** 选中视觉（主色 1px 环）；真实状态由内部控件承载。 */
    selected?: boolean;
  }>(),
  { variant: "outlined", size: "default", interactive: false, selected: false },
);

const VARIANT_CLASS: Record<CardVariant, string> = {
  outlined: "border-border bg-card",
  muted: "border-transparent bg-muted",
};

const ROOT_SIZE_CLASS: Record<CardSize, string> = {
  default: "gap-5 py-5",
  compact: "gap-4 py-4",
};

const reduced = useReducedMotion();
const root = ref<HTMLElement | null>(null);
/** 按下后若指针拖出卡片，松手事件收不到，用 pointerleave 兜底复位。 */
const pressed = ref(false);

// 部件据此决定水平内边距，调用方不用在每个部件上重复写 size
provide(CARD_SIZE_KEY, computed(() => props.size));

const classes = computed(() =>
  cn(
    "relative flex flex-col rounded-2xl border text-card-foreground",
    // 只过渡这三个属性：transform 交给 motion，避免和按压动画打架
    "transition-[border-color,box-shadow,background-color] duration-200",
    VARIANT_CLASS[props.variant],
    ROOT_SIZE_CLASS[props.size],
    props.interactive &&
      "cursor-pointer hover:border-border-strong has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-foreground",
    props.selected && "border-primary shadow-[0_0_0_1px_var(--primary)] hover:border-primary",
    props.class,
  ),
);

function onPointerDown(event: PointerEvent) {
  if (!props.interactive || reduced.value) return;
  // 只响应主指针左键：右键/中键/触控笔多键不应该触发按压
  if (event.button !== 0) return;
  pressed.value = true;
  animate(root.value, { scale: PRESS_SCALE }, SPRING_PRESS);
}

function release() {
  if (!pressed.value) return;
  pressed.value = false;
  animate(root.value, { scale: 1 }, SPRING_PRESS);
}
</script>

<template>
  <div
    ref="root"
    data-slot="card"
    :data-selected="props.selected || undefined"
    :class="classes"
    @pointerdown="onPointerDown"
    @pointerup.capture="release"
    @pointercancel.capture="release"
    @pointerleave="release"
  >
    <slot />
  </div>
</template>
