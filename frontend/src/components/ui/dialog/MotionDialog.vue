<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useAttrs, watch } from "vue";
import type { HTMLAttributes } from "vue";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";

import { DURATION, EASE_IN, EASE_OUT, OFFSET } from "@/lib/ease";
import {
  acquireScrollLock,
  consumeDeferredFocusRestore,
  deferFocusRestore,
  getFocusableElements,
  hasActiveOverlays,
  isTopOverlay,
  pushOverlay,
  releaseScrollLock,
  removeOverlay,
} from "@/lib/overlay";
import { cn } from "@/lib/utils";

/**
 * 统一居中弹窗原语：
 * - Teleport 至 body 并提供居中/顶偏遮罩（可经 backdropClass 覆盖如 CommandPalette）；
 * - Esc 与遮罩点击关闭（支持独立禁用）；
 * - 焦点管理：打开聚焦首交互控件，Tab/Shift+Tab 循环陷阱，关闭还原原焦点；
 * - 引用计数滚动锁，多层弹层叠加不冲突；
 * - 完整尊重 prefers-reduced-motion。
 */
const props = withDefaults(
  defineProps<{
    open: boolean;
    title?: string;
    ariaLabel?: string;
    ariaLabelledby?: string;
    closeOnBackdrop?: boolean;
    closeOnEsc?: boolean;
    initialFocus?: HTMLElement | string | null;
    backdropClass?: HTMLAttributes["class"];
    class?: HTMLAttributes["class"];
    backdropAttrs?: Record<string, unknown>;
    panelAttrs?: Record<string, unknown>;
  }>(),
  {
    title: undefined,
    ariaLabel: undefined,
    ariaLabelledby: undefined,
    closeOnBackdrop: true,
    closeOnEsc: true,
    initialFocus: null,
    backdropClass: undefined,
    class: undefined,
    backdropAttrs: undefined,
    panelAttrs: undefined,
  },
);

const emit = defineEmits<{
  (event: "update:open", open: boolean): void;
  (event: "close"): void;
}>();

defineOptions({ inheritAttrs: false });

const attrs = useAttrs();
const reduced = useReducedMotion();
const panelRef = ref<HTMLElement | null>(null);
const overlayToken = Symbol("MotionDialog");
let isActive = false;
let previousFocus: HTMLElement | null = null;

function omitClass(record?: Record<string, unknown>): Record<string, unknown> {
  if (!record) return {};
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (key !== "class") result[key] = value;
  }
  return result;
}

const mergedBackdropAttrs = computed(() => omitClass(props.backdropAttrs));
const mergedPanelAttrs = computed(() => ({
  ...omitClass(attrs as Record<string, unknown>),
  ...omitClass(props.panelAttrs),
}));

function setPanelRef(el: unknown) {
  if (el && typeof el === "object" && "$el" in el) {
    panelRef.value = (el as { $el?: HTMLElement | null }).$el ?? null;
  } else {
    panelRef.value = (el as HTMLElement | null) ?? null;
  }
}

function requestClose() {
  emit("update:open", false);
  emit("close");
}

function handleBackdropClick(event: MouseEvent) {
  if (!props.closeOnBackdrop) return;
  if (event.target !== event.currentTarget) return;
  requestClose();
}

function handleKeydown(event: KeyboardEvent) {
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
  if (!props.open || !isTopOverlay(overlayToken)) return;

  if (event.key === "Escape") {
    if (!props.closeOnEsc) return;
    event.preventDefault();
    event.stopPropagation();
    requestClose();
    return;
  }

  if (event.key === "Tab" && panelRef.value) {
    const focusables = getFocusableElements(panelRef.value);
    if (focusables.length === 0) {
      event.preventDefault();
      panelRef.value.focus({ preventScroll: true });
      return;
    }

    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement as HTMLElement | null;

    if (event.shiftKey) {
      if (active === first || !panelRef.value.contains(active)) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      }
    } else if (active === last || !panelRef.value.contains(active)) {
      event.preventDefault();
      first.focus({ preventScroll: true });
    }
  }
}

async function activateOverlay() {
  if (isActive) return;
  isActive = true;

  if (typeof document !== "undefined") {
    previousFocus = document.activeElement as HTMLElement | null;
  }
  acquireScrollLock(overlayToken);
  pushOverlay(overlayToken);
  if (typeof window !== "undefined") {
    window.addEventListener("keydown", handleKeydown);
  }

  await nextTick();
  if (!props.open || !panelRef.value) return;

  let target: HTMLElement | null = null;
  if (typeof props.initialFocus === "string") {
    target = panelRef.value.querySelector<HTMLElement>(props.initialFocus);
  } else if (props.initialFocus instanceof HTMLElement) {
    target = props.initialFocus;
  }
  if (!target) {
    target = getFocusableElements(panelRef.value)[0] ?? panelRef.value;
  }
  target.focus({ preventScroll: true });
}

function deactivateOverlay() {
  if (!isActive) return;
  isActive = false;

  removeOverlay(overlayToken);
  releaseScrollLock(overlayToken);
  if (typeof window !== "undefined") {
    window.removeEventListener("keydown", handleKeydown);
  }

  const toRestore = previousFocus;
  previousFocus = null;
  if (!hasActiveOverlays()) {
    const deferred = consumeDeferredFocusRestore();
    const target = toRestore && toRestore.isConnected ? toRestore : deferred;
    if (target && target.isConnected) {
      target.focus({ preventScroll: true });
    }
  } else if (toRestore && toRestore.isConnected) {
    deferFocusRestore(toRestore);
  }
}

onMounted(() => {
  if (props.open) {
    void activateOverlay();
  }
});

watch(
  () => props.open,
  (open) => {
    if (open) {
      void activateOverlay();
    } else {
      deactivateOverlay();
    }
  },
);

onBeforeUnmount(() => {
  deactivateOverlay();
});
</script>

<template>
  <Teleport to="body">
    <AnimatePresence>
      <Motion
        v-if="props.open"
        key="motion-dialog-backdrop"
        v-bind="mergedBackdropAttrs"
        :initial="{ opacity: 0 }"
        :animate="{ opacity: 1 }"
        :exit="{ opacity: 0, transition: { duration: reduced ? DURATION.micro : DURATION.fast, ease: EASE_IN } }"
        :transition="{ duration: reduced ? DURATION.micro : 0.18, ease: EASE_OUT }"
        :class="
          cn(
            'fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[1px]',
            props.backdropClass,
            props.backdropAttrs?.class as HTMLAttributes['class'],
          )
        "
        @click="handleBackdropClick"
      >
        <Motion
          :ref="setPanelRef"
          v-bind="mergedPanelAttrs"
          role="dialog"
          aria-modal="true"
          :aria-label="props.ariaLabel ?? props.title"
          :aria-labelledby="props.ariaLabelledby"
          tabindex="-1"
          :initial="{
            opacity: 0,
            scale: reduced ? 1 : OFFSET.dialogScale,
            y: reduced ? 0 : OFFSET.dialogY,
          }"
          :animate="{ opacity: 1, scale: 1, y: 0 }"
          :exit="{
            opacity: 0,
            scale: reduced ? 1 : 0.97,
            y: reduced ? 0 : OFFSET.microY,
            transition: { duration: reduced ? DURATION.micro : DURATION.fast, ease: EASE_IN },
          }"
          :transition="{ duration: reduced ? DURATION.micro : DURATION.normal, ease: EASE_OUT }"
          :class="
            cn(
              'w-full max-w-lg rounded-lg border border-border bg-card text-card-foreground shadow-raised outline-none',
              props.class,
              props.panelAttrs?.class as HTMLAttributes['class'],
            )
          "
          @click.stop
        >
          <slot />
        </Motion>
      </Motion>
    </AnimatePresence>
  </Teleport>
</template>
