<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { HTMLAttributes } from "vue";
import { AlertTriangle, Loader2 } from "lucide-vue-next";

import AnimatedCollapse from "@/components/common/AnimatedCollapse.vue";
import { Button } from "@/components/ui/button";
import { isTopOverlay, pushOverlay, removeOverlay } from "@/lib/overlay";
import { cn } from "@/lib/utils";

/**
 * 统一破坏性二次确认条（RECHECK-B1 & R7）：
 * - 支持外层 `v-if` 配合默认 `open=true` 挂载，或通过 `:open` 受控展开收起；
 * - 明确标题与不可逆后果说明（consequence prop / slot）；
 * - 统一采用 shadcn `<Button>`（`destructive`/`default` + `outline`），透传 `confirmTestid`/`cancelTestid`；
 * - 支持 `Esc` 取消（并阻止冒泡误关外层弹层）与防连点锁。
 */
const props = withDefaults(
  defineProps<{
    open?: boolean;
    title: string;
    consequence?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    confirmTestid?: string;
    cancelTestid?: string;
    loading?: boolean;
    variant?: "destructive" | "warning";
    class?: HTMLAttributes["class"];
  }>(),
  {
    open: true,
    consequence: undefined,
    confirmLabel: "确认",
    cancelLabel: "取消",
    confirmTestid: undefined,
    cancelTestid: undefined,
    loading: false,
    variant: "destructive",
    class: undefined,
  },
);

const emit = defineEmits<{
  (event: "confirm"): void;
  (event: "cancel"): void;
}>();

const barToken = Symbol("ConfirmBar");
const alertRef = ref<HTMLElement | null>(null);

const clickLocked = ref(false);
let lockTimer: ReturnType<typeof setTimeout> | null = null;
let keyListenerBound = false;
let isMounted = false;

const isBusy = computed(() => props.loading || clickLocked.value);

function clearLockTimer() {
  if (lockTimer !== null) {
    clearTimeout(lockTimer);
    lockTimer = null;
  }
}

watch(
  () => props.loading,
  (loading) => {
    if (!loading) {
      clearLockTimer();
      clickLocked.value = false;
    }
  },
);

function handleConfirm() {
  if (isBusy.value) return;
  clickLocked.value = true;
  clearLockTimer();
  lockTimer = setTimeout(() => {
    lockTimer = null;
    if (!props.loading) {
      clickLocked.value = false;
    }
  }, 150);
  emit("confirm");
}

function handleCancel() {
  if (isBusy.value) return;
  emit("cancel");
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.open || event.isComposing || event.keyCode === 229) return;
  // 仅当 ConfirmBar 处于全局 overlayStack 栈顶时消费 Esc，避免穿透顶层模态框或与多栈失步死锁
  if (!isTopOverlay(barToken)) return;
  if (event.key !== "Escape") return;
  event.preventDefault();
  event.stopImmediatePropagation();
  event.stopPropagation();
  if (isBusy.value) return;
  emit("cancel");
}

function syncKeyListener(active: boolean) {
  if (typeof window === "undefined") return;
  if (active && !keyListenerBound) {
    pushOverlay(barToken);
    window.addEventListener("keydown", handleKeydown, { capture: true });
    keyListenerBound = true;
  } else if (!active && keyListenerBound) {
    removeOverlay(barToken);
    window.removeEventListener("keydown", handleKeydown, { capture: true });
    keyListenerBound = false;
  }
}

onMounted(() => {
  isMounted = true;
  syncKeyListener(props.open);
  // 若 ConfirmBar 与包裹它的祖先 Dialog/Drawer 同时初始 open=true 挂载，
  // Vue 会先执行子组件 onMounted 再执行父组件 onMounted，导致祖先随后将自己压到栈顶；
  // 需在 nextTick 后确认若栈顶仍是该祖先弹层，将 ConfirmBar 恢复至其上方。
  if (props.open && typeof document !== "undefined") {
    const ancestorDialog = alertRef.value?.closest('[role="dialog"]');
    if (ancestorDialog) {
      void nextTick(() => {
        if (!isMounted || !props.open || !keyListenerBound) return;
        const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
        if (dialogs[dialogs.length - 1] === ancestorDialog) {
          pushOverlay(barToken);
        }
      });
    }
  }
});

watch(
  () => props.open,
  (open) => {
    syncKeyListener(open);
  },
);

onBeforeUnmount(() => {
  isMounted = false;
  clearLockTimer();
  syncKeyListener(false);
});
</script>

<template>
  <AnimatedCollapse :open="props.open">
    <div
      ref="alertRef"
      role="alertdialog"
      aria-live="assertive"
      :class="
        cn(
          'flex flex-col gap-2.5 rounded-lg border px-3 py-2.5 text-sm sm:flex-row sm:items-center sm:justify-between',
          props.variant === 'warning'
            ? 'border-warning/40 bg-warning/8 text-foreground'
            : 'border-destructive/35 bg-destructive/8 text-foreground',
          props.class,
        )
      "
    >
      <div class="flex min-w-0 items-start gap-2">
        <AlertTriangle
          aria-hidden="true"
          :class="
            cn(
              'mt-0.5 size-4 shrink-0',
              props.variant === 'warning' ? 'text-warning' : 'text-destructive',
            )
          "
        />
        <div class="min-w-0 space-y-0.5">
          <p class="font-medium leading-5 text-pretty">{{ props.title }}</p>
          <p v-if="props.consequence || $slots.consequence" class="text-xs leading-relaxed text-muted-foreground">
            <slot name="consequence">{{ props.consequence }}</slot>
          </p>
          <div v-if="$slots.default" class="text-xs text-muted-foreground">
            <slot />
          </div>
        </div>
      </div>

      <div class="flex shrink-0 items-center justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          :data-test="props.cancelTestid"
          :disabled="isBusy"
          @click="handleCancel"
        >
          {{ props.cancelLabel }}
        </Button>
        <Button
          type="button"
          :variant="props.variant === 'warning' ? 'default' : 'destructive'"
          size="sm"
          :data-test="props.confirmTestid"
          :disabled="isBusy"
          @click="handleConfirm"
        >
          <Loader2
            v-if="props.loading"
            aria-hidden="true"
            class="size-3.5 animate-spin motion-reduce:animate-none"
          />
          <span>{{ props.confirmLabel }}</span>
        </Button>
      </div>
    </div>
  </AnimatedCollapse>
</template>
