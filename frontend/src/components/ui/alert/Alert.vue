<script setup lang="ts">
/**
 * Animated Alert（beUI 移植，https://beui.dev/components/motion/alert）。
 *
 * 保持旧 `Alert` 的 props 契约（`variant` + 默认插槽）不变，所有既有页面
 * 自动获得新外观与动效：
 * - 变体：default / info / success / warning / destructive / muted；
 * - 入场淡入 + 上移 6px；布局变化弹簧；退出 120ms 后卸载；
 * - 图标随变体交叉淡入；关闭按钮 hover 变深、按下 0.94；
 * - warning/destructive 用 role="alert"，其余 role="status"；
 * - prefers-reduced-motion 下只淡入淡出，不做位移/弹簧。
 *
 * 新写法可选 `title` / `description` / `action` 插槽；旧写法
 * `<Alert><Icon/><span>文案</span></Alert>` 仍按行内布局渲染。
 */
import { computed, ref, watch } from "vue";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
import {
  AlertCircle,
  CheckCircle2,
  Info,
  TriangleAlert,
  X,
} from "lucide-vue-next";
import type { Component, HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";
import { DURATION, EASE_IN, EASE_OUT, OFFSET } from "@/lib/ease";
import { type AlertVariants, alertVariants } from "./variants";

const props = withDefaults(
  defineProps<{
    variant?: AlertVariants["variant"];
    class?: HTMLAttributes["class"];
    /** 结构化内容；不传则把默认插槽按旧版行内布局渲染。 */
    title?: string;
    description?: string;
    /** 是否显示关闭按钮（beUI 默认有）。 */
    dismissible?: boolean;
    /** 受控开关；不传则组件内部管理。 */
    open?: boolean;
    /** 关闭后把焦点还给这个元素（若焦点在提示条内）。 */
    returnFocus?: HTMLElement | null;
    labels?: { dismiss?: string };
    icon?: boolean;
  }>(),
  {
    variant: "default",
    class: undefined,
    title: undefined,
    description: undefined,
    dismissible: true,
    open: undefined,
    returnFocus: null,
    labels: () => ({ dismiss: "关闭提示" }),
    icon: undefined,
  },
);

const emit = defineEmits<{
  (event: "update:open", open: boolean): void;
}>();

defineOptions({ inheritAttrs: false });

const reduced = useReducedMotion();
const internalOpen = ref(true);
const surface = ref<HTMLElement | null>(null);

/** Motion 的模板 ref 给的是组件实例，取它的 $el；普通元素直接返回。 */
function setSurface(el: unknown) {
  if (el && typeof el === "object" && "$el" in el) {
    surface.value = (el as { $el?: HTMLElement }).$el ?? null;
  } else {
    surface.value = (el as HTMLElement | null) ?? null;
  }
}

const isOpen = computed(() => (props.open === undefined ? internalOpen.value : props.open));

watch(
  () => props.open,
  (value) => {
    if (value !== undefined) internalOpen.value = value;
  },
);

const VARIANT_ICON: Record<string, Component> = {
  default: Info,
  info: Info,
  success: CheckCircle2,
  warning: TriangleAlert,
  destructive: AlertCircle,
  muted: Info,
};

/** 结构化内容自动配图标；旧版默认插槽通常自带图标，不再重复。 */
const showAutoIcon = computed(() => props.icon ?? (props.title !== undefined || props.description !== undefined));
const autoIcon = computed(() => VARIANT_ICON[props.variant ?? "default"] ?? Info);
const role = computed(() =>
  props.variant === "warning" || props.variant === "destructive" ? "alert" : "status",
);

function close() {
  const hadFocus = surface.value?.contains(document.activeElement) ?? false;
  internalOpen.value = false;
  emit("update:open", false);
  if (hadFocus) {
    props.returnFocus?.focus({ preventScroll: true });
  }
}
</script>

<template>
  <AnimatePresence :initial="false">
    <Motion
      v-if="isOpen"
      key="alert"
      v-bind="$attrs"
      :ref="setSurface"
      :role="role"
      :data-variant="props.variant"
      :initial="{ opacity: 0, y: reduced ? 0 : OFFSET.alertY }"
      :animate="{ opacity: 1, y: 0 }"
      :exit="{
        opacity: 0,
        y: reduced ? 0 : OFFSET.alertY,
        transition: { duration: reduced ? DURATION.micro : DURATION.fast, ease: EASE_IN },
      }"
      :transition="{
        opacity: { duration: reduced ? DURATION.micro : DURATION.normal, ease: EASE_OUT },
        y: { duration: DURATION.normal, ease: EASE_OUT },
      }"
      :layout="!reduced"
      :class="cn(alertVariants({ variant: props.variant }), props.class)"
      :style="{ originY: 0 }"
    >
      <div class="flex min-w-0 items-start gap-3">
        <span
          v-if="showAutoIcon"
          aria-hidden="true"
          class="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center"
        >
          <AnimatePresence :initial="false" mode="popLayout">
            <Motion
              :key="String(props.variant)"
              :initial="{ opacity: 0, y: reduced ? 0 : OFFSET.microY, scale: reduced ? 1 : 0.9 }"
              :animate="{ opacity: 1, y: 0, scale: 1 }"
              :exit="{
                opacity: 0,
                y: reduced ? 0 : -OFFSET.microY,
                scale: reduced ? 1 : 0.9,
                transition: { duration: DURATION.micro, ease: EASE_IN },
              }"
              :transition="{ duration: reduced ? DURATION.micro : DURATION.normal, ease: EASE_OUT }"
              class="inline-flex [&>svg]:size-4"
            >
              <component :is="autoIcon" />
            </Motion>
          </AnimatePresence>
        </span>

        <div class="min-w-0 flex-1 space-y-1">
          <p v-if="props.title" class="font-medium leading-5 text-pretty">
            {{ props.title }}
          </p>
          <p v-if="props.description" class="leading-relaxed text-pretty text-muted-foreground">
            {{ props.description }}
          </p>
          <!-- 旧版调用：图标与文案是兄弟节点，保持行内布局 -->
          <div v-if="$slots.default" class="flex min-w-0 items-start gap-2 [&>svg]:size-4 [&>svg]:shrink-0">
            <slot />
          </div>
          <div v-if="$slots.action" class="flex flex-wrap items-center gap-2 pt-2">
            <slot name="action" />
          </div>
        </div>

        <button
          v-if="props.dismissible"
          type="button"
          :aria-label="props.labels?.dismiss ?? '关闭提示'"
          class="-m-1.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground outline-none transition-colors hover:bg-foreground/5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring active:scale-[0.94]"
          @click="close"
        >
          <X aria-hidden="true" class="size-4" />
        </button>
      </div>
    </Motion>
  </AnimatePresence>
</template>
