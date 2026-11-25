<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import type { HTMLAttributes } from "vue";
import { useReducedMotion } from "motion-v";

import { EASE_IN_CSS, EASE_OUT_CSS } from "@/lib/ease";
import { cn } from "@/lib/utils";

/**
 * 基于 CSS Grid `0fr ↔ 1fr` 的平滑折叠原语：
 * - 遵守 R5：展开过渡结束后将内层切换为 `overflow-visible`，防止裁切内部控件焦点环；
 *   收起时同步立刻切回 `overflow-hidden`；
 * - 遵守 RECHECK-B1：支持 `keepMounted` 常驻 DOM（收起稳态加 `invisible pointer-events-none` 防 Tab 误入）；
 * - 完整尊重 `prefers-reduced-motion`。
 */
const props = withDefaults(
  defineProps<{
    open: boolean;
    keepMounted?: boolean;
    class?: HTMLAttributes["class"];
  }>(),
  {
    keepMounted: false,
    class: undefined,
  },
);

const reduced = useReducedMotion();
const gridRef = ref<HTMLElement | null>(null);
const settledOpen = ref(props.open);
const shouldRender = ref(props.open);
const isAnimating = ref(false);
let timer: ReturnType<typeof setTimeout> | null = null;

function clearTimer() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

function finalizeState(open: boolean) {
  clearTimer();
  isAnimating.value = false;
  if (open) {
    settledOpen.value = true;
  } else {
    settledOpen.value = false;
    shouldRender.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    clearTimer();
    if (open) {
      shouldRender.value = true;
      if (reduced.value) {
        settledOpen.value = true;
        isAnimating.value = false;
        return;
      }
      isAnimating.value = true;
      timer = setTimeout(() => {
        finalizeState(true);
      }, 220);
    } else {
      // 收起时同步立刻恢复 overflow-hidden，防止内容溢出
      settledOpen.value = false;
      if (reduced.value) {
        isAnimating.value = false;
        shouldRender.value = false;
        return;
      }
      isAnimating.value = true;
      timer = setTimeout(() => {
        finalizeState(false);
      }, 180);
    }
  },
);

function onTransitionEnd(event: TransitionEvent) {
  if (event.target !== gridRef.value || event.propertyName !== "grid-template-rows") return;
  finalizeState(props.open);
}

onBeforeUnmount(() => {
  clearTimer();
});
</script>

<template>
  <div
    ref="gridRef"
    :class="
      cn(
        'grid transition-[grid-template-rows,opacity] motion-reduce:transition-none',
        props.open ? 'grid-rows-[1fr] opacity-100 duration-200' : 'grid-rows-[0fr] opacity-0 duration-150',
        props.class,
      )
    "
    :style="{ transitionTimingFunction: props.open ? EASE_OUT_CSS : EASE_IN_CSS }"
    @transitionend="onTransitionEnd"
  >
    <div
      v-if="props.keepMounted || shouldRender"
      :aria-hidden="!props.open || undefined"
      :class="
        cn(
          'min-h-0',
          props.open || isAnimating ? 'p-0.5 -m-0.5' : '',
          settledOpen ? 'overflow-visible' : 'overflow-hidden',
          !props.open && !isAnimating ? 'invisible pointer-events-none' : '',
        )
      "
    >
      <slot />
    </div>
  </div>
</template>
