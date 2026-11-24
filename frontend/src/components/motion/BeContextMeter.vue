<script setup lang="ts">
/**
 * beUI Context Window（ContextMeter）的 Vue 移植
 * （https://asharca.github.io/ui/components/agents/context-window/）。
 *
 * 本项目语义改编（AGENTS.md §6.5.1.1 三级顺序 ① 的「照设计复刻」）：
 * 原组件显示上下文窗口占用；这里显示**节点路由覆盖率**——
 * capacity = 可路由节点总数，categories = 已配置路由的节点。
 *
 * 规格：rounded-3xl 容器 + rounded-2xl 内卡；分段条 h-2.5 圆角胶囊
 * （分段间 gap-1，颜色循环 slate/sky/emerald/violet/amber）；
 * header 右侧大号百分比 tabular-nums；未选中分段 opacity-30；
 * 分类列表行（色点+名称+token 数→这里改为「已配置」徽章）；
 * 动效 0.25s EASE_OUT [0.16,1,0.3,1]。
 */
import { computed } from "vue";
import { motion } from "motion-v";
import { Layers } from "@/components/icons";

export interface RoutingCategory {
  /** 节点类型（extract 等） */
  id: string;
  label: string;
  /** 已路由的模型名（空 = 跟随任务勾选） */
  model: string;
}

const props = defineProps<{
  /** 可路由节点总数 */
  capacity: number;
  categories: RoutingCategory[];
  model?: string;
}>();

const PALETTE = ["bg-slate-400", "bg-sky-500", "bg-emerald-500", "bg-violet-500", "bg-amber-400"];

const configured = computed(() => props.categories.filter((c) => c.model !== "").length);
const percent = computed(() =>
  props.capacity ? Math.round((configured.value / props.capacity) * 100) : 0
);

/** 分段条：capacity 个等宽段，已配置段按调色板着色 + 生长动效，未配置 bg-muted */
const segments = computed(() =>
  Array.from({ length: props.capacity }, (_, index) => {
    const category = props.categories[index];
    const isConfigured = Boolean(category && category.model);
    return {
      id: category?.id ?? `slot-${index}`,
      configured: isConfigured,
      color: PALETTE[index % PALETTE.length],
      width: `${100 / props.capacity}%`,
      label: category?.label ?? "",
      model: category?.model ?? "",
    };
  })
);

const summary = computed(() => {
  const configuredModels = props.categories.filter((c) => c.model).map((c) => c.model);
  if (!configuredModels.length) return "全部节点跟随任务勾选的模型";
  return `已路由：${configuredModels.join(" · ")}`;
});
</script>

<template>
  <section
    data-test="context-meter"
    class="w-full max-w-md rounded-3xl border border-border bg-card p-1.5"
    aria-label="节点路由覆盖率"
  >
    <!-- 内卡 -->
    <div class="rounded-2xl border border-border bg-background px-4 py-3">
      <!-- Header -->
      <div class="flex items-center gap-2">
        <Layers class="size-4 text-muted-foreground" />
        <div class="min-w-0">
          <h3 class="text-sm font-medium leading-5">Routing coverage</h3>
          <p v-if="model" class="text-[10px] text-muted-foreground">{{ model }}</p>
        </div>
        <span
          class="ml-auto text-xl font-medium tabular-nums"
          :class="percent >= 100 ? 'text-emerald-600' : 'text-foreground'"
        >
          {{ percent }}%
        </span>
      </div>

      <!-- 分段条 -->
      <div class="mt-3">
        <div class="flex items-baseline justify-between">
          <span class="font-mono text-xs tabular-nums">
            {{ configured }} of {{ capacity }} nodes routed
          </span>
          <span class="font-mono text-xs tabular-nums text-muted-foreground">
            {{ capacity - configured }} follow task
          </span>
        </div>
        <div class="mt-2 flex h-2.5 gap-1" role="meter" :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100">
          <motion.div
            v-for="seg in segments"
            :key="seg.id"
            class="h-full rounded-full"
            :class="seg.configured ? seg.color : 'bg-muted'"
            :style="{ width: seg.width }"
            :initial="{ scaleX: 0 }"
            :animate="{ scaleX: 1 }"
            :transition="{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }"
            style="transform-origin: left center"
            :title="`${seg.label}${seg.model ? ` → ${seg.model}` : ' → 跟随任务'}`"
          />
        </div>
        <p class="mt-2 truncate text-[11px] leading-4 text-muted-foreground" :title="summary">
          {{ summary }}
        </p>
      </div>

      <!-- 分类列表：已配置的节点行 -->
      <ul v-if="configured" class="mt-3 flex flex-col gap-1.5 border-t border-border pt-3">
        <li
          v-for="cat in categories.filter((c) => c.model)"
          :key="cat.id"
          class="flex items-center gap-2 text-xs"
        >
          <span
            class="size-2 rounded-full"
            :class="PALETTE[props.categories.findIndex((c) => c.id === cat.id) % PALETTE.length]"
          />
          <span class="font-medium">{{ cat.label }}</span>
          <span class="ml-auto truncate font-mono text-[10px] tabular-nums text-muted-foreground">
            {{ cat.model }}
          </span>
        </li>
      </ul>
    </div>
  </section>
</template>
