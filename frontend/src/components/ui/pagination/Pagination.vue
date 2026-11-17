<script setup lang="ts">
/**
 * 分页（beUI Motion Pagination 移植）。
 *
 * 三个动效点，缺一个就不像：
 * 1. 当前页是**一个**共享胶囊（`layoutId`），翻页时从旧位置滑到新位置；
 * 2. 页码窗口变化时数字用 `layout="position"` 平滑让位，不重建整行；
 * 3. 省略号悬停/聚焦时 `…` 缩掉、双向箭头浮出，点击一次跳 `jump` 页。
 */
import { computed, useId } from "vue";
import { AnimatePresence, LayoutGroup, Motion, useReducedMotion } from "motion-v";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-vue-next";

import { EASE_OUT, SPRING_LAYOUT } from "@/lib/ease";
import { cn } from "@/lib/utils";

import { paginationRange } from "./pagination-range";

const props = withDefaults(
  defineProps<{
    /** 总页数（至少 1）。 */
    totalPages: number;
    /** 当前页（v-model），从 1 开始。 */
    modelValue: number;
    /** 当前页两侧各显示几页。 */
    siblings?: number;
    /** 首尾固定显示几页。 */
    boundaries?: number;
    /** 点省略号一次跨几页。 */
    jump?: number;
    ariaLabel?: string;
    class?: string;
  }>(),
  {
    siblings: 1,
    boundaries: 1,
    jump: 5,
    ariaLabel: "分页",
  },
);

const emit = defineEmits<{
  (event: "update:modelValue", page: number): void;
  (event: "change", page: number): void;
}>();

const reduced = useReducedMotion();
const groupId = useId();

/** 所有控件共用：32/36px 高、胶囊圆角、等宽数字、按下 0.94。 */
const CONTROL =
  "relative inline-flex h-8 min-w-8 items-center justify-center rounded-full px-2 text-sm tabular-nums outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none sm:h-9 sm:min-w-9";

const total = computed(() => Math.max(1, Math.floor(props.totalPages)));
const current = computed(() => Math.min(total.value, Math.max(1, Math.round(props.modelValue))));
const items = computed(() =>
  paginationRange(current.value, total.value, props.siblings, props.boundaries),
);

const fade = computed(() => ({
  duration: reduced.value ? 0.12 : 0.16,
  ease: EASE_OUT,
}));

function go(page: number) {
  const target = Math.min(total.value, Math.max(1, Math.round(page)));
  if (target === current.value) return;
  emit("update:modelValue", target);
  emit("change", target);
}

function isCurrent(item: number | string): boolean {
  return item === current.value;
}
</script>

<template>
  <nav :aria-label="props.ariaLabel" :class="cn('flex', props.class)">
    <LayoutGroup :id="groupId">
      <ul class="relative flex items-center gap-0.5 sm:gap-1">
        <li>
          <button
            type="button"
            :class="
              cn(
                CONTROL,
                'text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-40',
              )
            "
            aria-label="上一页"
            :disabled="current === 1"
            @click="go(current - 1)"
          >
            <ChevronLeft class="size-4" aria-hidden="true" />
          </button>
        </li>

        <AnimatePresence mode="popLayout" :initial="false">
          <Motion
            v-for="item in items"
            :key="String(item)"
            as="li"
            :layout="reduced ? false : 'position'"
            :initial="{ opacity: 0, scale: reduced ? 1 : 0.8 }"
            :animate="{ opacity: 1, scale: 1 }"
            :exit="{ opacity: 0, scale: reduced ? 1 : 0.8 }"
            :transition="{ layout: SPRING_LAYOUT, opacity: fade, scale: SPRING_LAYOUT }"
          >
            <!-- 省略号：点击一次跨 jump 页 -->
            <button
              v-if="typeof item === 'string'"
              type="button"
              :class="
                cn(
                  CONTROL,
                  'group text-muted-foreground hover:bg-foreground/5 hover:text-foreground',
                )
              "
              :aria-label="
                item === 'ellipsis-start' ? `向前 ${props.jump} 页` : `向后 ${props.jump} 页`
              "
              @click="go(item === 'ellipsis-start' ? current - props.jump : current + props.jump)"
            >
              <span
                aria-hidden="true"
                class="transition-[opacity,transform] duration-150 ease-out group-hover:scale-75 group-hover:opacity-0 group-focus-visible:scale-75 group-focus-visible:opacity-0 motion-reduce:transform-none"
              >
                …
              </span>
              <ChevronsLeft
                v-if="item === 'ellipsis-start'"
                aria-hidden="true"
                class="absolute size-4 scale-75 opacity-0 transition-[opacity,transform] duration-150 ease-out group-hover:scale-100 group-hover:opacity-100 group-focus-visible:scale-100 group-focus-visible:opacity-100 motion-reduce:transform-none"
              />
              <ChevronsRight
                v-else
                aria-hidden="true"
                class="absolute size-4 scale-75 opacity-0 transition-[opacity,transform] duration-150 ease-out group-hover:scale-100 group-hover:opacity-100 group-focus-visible:scale-100 group-focus-visible:opacity-100 motion-reduce:transform-none"
              />
            </button>

            <!-- 页码 -->
            <button
              v-else
              type="button"
              :class="cn(CONTROL, isCurrent(item) ? 'font-medium text-background' : 'text-muted-foreground hover:bg-foreground/5 hover:text-foreground')"
              :aria-current="isCurrent(item) ? 'page' : undefined"
              :aria-label="`第 ${item} 页`"
              @click="go(item)"
            >
              <!-- 共享胶囊：整条分页里只有一个，翻页时滑过去 -->
              <Motion
                v-if="isCurrent(item)"
                :layout-id="reduced ? undefined : 'pagination-current'"
                :transition="SPRING_LAYOUT"
                aria-hidden="true"
                class="absolute inset-0 rounded-full bg-foreground"
              />
              <span class="relative">{{ item }}</span>
            </button>
          </Motion>
        </AnimatePresence>

        <li>
          <button
            type="button"
            :class="
              cn(
                CONTROL,
                'text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-40',
              )
            "
            aria-label="下一页"
            :disabled="current === total"
            @click="go(current + 1)"
          >
            <ChevronRight class="size-4" aria-hidden="true" />
          </button>
        </li>
      </ul>
    </LayoutGroup>
  </nav>
</template>
