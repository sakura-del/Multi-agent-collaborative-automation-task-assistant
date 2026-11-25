<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useAttrs, watch } from "vue";
import type { HTMLAttributes } from "vue";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";

import { DURATION, EASE_IN, EASE_OUT, TRANSITION_DRAWER_ENTER, TRANSITION_DRAWER_EXIT } from "@/lib/ease";
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
 * 统一侧滑/底滑抽屉原语：
 * - Teleport 至 body，支持 right / bottom 方向与分级宽度；
 * - 复用引用计数滚动锁、顶层 Esc 拦截与 Tab 焦点陷阱；
 * - 支持透传 backdropAttrs 与 panelAttrs（保留各业务抽屉 data-test 契约）；
 * - 完整尊重 prefers-reduced-motion。
 */
const props = withDefaults(
  defineProps<{
    open: boolean;
    side?: "right" | "bottom";
    widthClass?: string;
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
    side: "right",
    widthClass: undefined,
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
const overlayToken = Symbol("MotionDrawer");
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

const resolvedWidthClass = computed(() => {
  if (props.widthClass) return props.widthClass;
  return props.side === "right" ? "w-full max-w-md" : "w-full max-h-[85vh]";
});

const mergedBackdropAttrs = computed(() => omitClass(props.backdropAttrs));
const mergedPanelAttrs = computed(() => ({
  ...omitClass(attrs as Record<string, unknown>),
  ...omitClass(props.panelAttrs),
}));

const panelInitial = computed(() => {
  if (reduced.value) {
    return { opacity: 0, x: 0, y: 0 };
  }
  return props.side === "right"
    ? { opacity: 1, x: "100%", y: 0 }
    : { opacity: 1, x: 0, y: "100%" };
});

const panelExit = computed(() => {
  if (reduced.value) {
    return {
      opacity: 0,
      x: 0,
      y: 0,
      transition: { duration: DURATION.micro, ease: EASE_IN },
    };
  }
  return props.side === "right"
    ? { opacity: 1, x: "100%", y: 0, transition: TRANSITION_DRAWER_EXIT }
    : { opacity: 1, x: 0, y: "100%", transition: TRANSITION_DRAWER_EXIT };
});

const panelTransition = computed(() =>
  reduced.value ? { duration: DURATION.micro, ease: EASE_OUT } : TRANSITION_DRAWER_ENTER,
);

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
        key="motion-drawer-backdrop"
        v-bind="mergedBackdropAttrs"
        :initial="{ opacity: 0 }"
        :animate="{ opacity: 1 }"
        :exit="{ opacity: 0, transition: { duration: reduced ? DURATION.micro : 0.18, ease: EASE_IN } }"
        :transition="{ duration: reduced ? DURATION.micro : DURATION.normal, ease: EASE_OUT }"
        :class="
          cn(
            'fixed inset-0 z-50 flex bg-black/40 backdrop-blur-[1px]',
            props.side === 'right' ? 'justify-end' : 'items-end justify-center',
            props.backdropClass,
            props.backdropAttrs?.class as HTMLAttributes['class'],
          )
        "
        @click="handleBackdropClick"
      >
        <Motion
          :ref="setPanelRef"
          as="aside"
          v-bind="mergedPanelAttrs"
          role="dialog"
          aria-modal="true"
          :aria-label="props.ariaLabel"
          :aria-labelledby="props.ariaLabelledby"
          tabindex="-1"
          :initial="panelInitial"
          :animate="{ opacity: 1, x: 0, y: 0 }"
          :exit="panelExit"
          :transition="panelTransition"
          :class="
            cn(
              'flex flex-col bg-background text-foreground shadow-raised outline-none',
              props.side === 'right'
                ? 'h-full border-l border-border'
                : 'max-h-[85vh] w-full rounded-t-xl border-t border-border',
              resolvedWidthClass,
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
