<script setup lang="ts">
/**
 * 设计演示页（不进导航、不接生产数据）。
 *
 * 目的：在动生产页面之前，先把「新配色 + beUI 时间线 + 新表面」的手感
 * 做出来给你确认。配色在这里用局部 token 覆盖，**不影响**其它页面的
 * 现有青绿主题。
 *
 * 路由：`/design/timeline`（public，可直接打开）。
 * 确认后再把 token 提升为全局，并按方案逐页迁移。
 */
import { computed } from "vue";
import { ArrowLeft } from "lucide-vue-next";

import BeTimeline, { type BeTimelineEvent } from "@/components/motion/BeTimeline.vue";

type DemoStatus = "running" | "completed" | "needs-review" | "failed";

interface DemoTask extends BeTimelineEvent {
  kind: string;
  status: DemoStatus;
  progress: number;
  meta: string;
}

const TASKS: DemoTask[] = [
  {
    id: "t-0927",
    date: "2025-09-27",
    title: "知识库重构立项",
    kind: "知识库",
    status: "completed",
    progress: 100,
    meta: "18 篇文献 · 2 轮评审",
  },
  {
    id: "t-1103",
    date: "2025-11-03",
    title: "引用格式导出 v1",
    kind: "引用",
    status: "completed",
    progress: 100,
    meta: "6 种样式 · 全部通过",
  },
  {
    id: "t-0118",
    date: "2026-01-18",
    title: "PRISMA 筛选工作台",
    kind: "筛选",
    status: "completed",
    progress: 100,
    meta: "240 篇 · 双人复核",
  },
  {
    id: "t-0406",
    date: "2026-04-06",
    title: "多智能体评审流水线",
    kind: "评审",
    status: "failed",
    progress: 62,
    meta: "风格检查超时 · 待重试",
  },
  {
    id: "t-0712",
    date: "2026-07-12",
    title: "文献综述 · 多路召回",
    kind: "综述",
    status: "completed",
    progress: 100,
    meta: "42 篇 · 128 条引用",
  },
  {
    id: "t-0930",
    date: "2026-09-30",
    title: "BYOK 自带模型接入",
    kind: "模型",
    status: "needs-review",
    progress: 88,
    meta: "待确认评审意见",
  },
  {
    id: "t-1108",
    date: "2026-11-08",
    title: "引用库批量导入",
    kind: "引用",
    status: "completed",
    progress: 100,
    meta: "128 条 · 0 冲突",
  },
  {
    id: "t-1116",
    date: "2026-11-16",
    title: "文献综述 · Issue #15",
    kind: "综述",
    status: "running",
    progress: 68,
    meta: "已核验 12/18 条证据",
  },
];

const STATUS_LABEL: Record<DemoStatus, string> = {
  running: "运行中",
  completed: "已完成",
  "needs-review": "待确认",
  failed: "失败",
};

const focusedId = computed(() => TASKS[0]?.id ?? "");

function mediaTone(status: DemoStatus): string {
  if (status === "running") return "bg-[#E9EEFF] text-[#2447D6]";
  if (status === "needs-review") return "bg-[#FBF3E4] text-[#8A5A0B]";
  if (status === "failed") return "bg-[#FBE9E7] text-[#A63A2B]";
  return "bg-[#EEF0F3] text-[#4B4E56]";
}

function barColor(status: DemoStatus): string {
  if (status === "running") return "bg-[#2F5BFF]";
  if (status === "needs-review") return "bg-[#C98A12]";
  if (status === "failed") return "bg-[#D2503C]";
  return "bg-[#17181A]";
}

function onSelect(id: string) {
  void id;
}
</script>

<template>
  <div
    class="design-demo min-h-screen overflow-x-hidden bg-background px-5 py-10 text-foreground sm:px-8"
  >
    <div class="mx-auto w-full max-w-6xl">
      <header class="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <p class="mb-2 text-[10px] font-semibold tracking-[0.16em] text-primary uppercase">
            Design preview · beUI
          </p>
          <h1 class="font-display text-3xl font-medium tracking-[-0.035em]">研究时间线</h1>
          <p class="mt-2 max-w-xl text-sm text-muted-foreground">
            新配色（纯白 + 钴蓝）、beUI 时间线与新表面的设计演示。拖拽、滚轮或方向键浏览；
            生产页面暂未改动。
          </p>
        </div>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
          @click="$router.back()"
        >
          <ArrowLeft :size="13" aria-hidden="true" />
          返回
        </button>
      </header>

      <!--
        演示页局部覆盖设计 token：确认后这套值会替换全局 main.css 的
        青绿主题，这里先用 scope 限定，避免影响生产页面。
      -->
      <div
        class="rounded-3xl border border-border bg-card p-2 [--primary:#2F5BFF] [--ring:#2F5BFF]"
      >
        <BeTimeline
          :events="TASKS"
          :height="560"
          :item-width="340"
          aria-label="研究任务时间线演示"
          @change="onSelect"
        >
          <template #media="{ event }">
            <div
              class="flex h-full w-full flex-col justify-between p-3"
              :class="mediaTone((event as DemoTask).status)"
            >
              <div class="flex items-center justify-between text-xs font-medium">
                <span>{{ (event as DemoTask).kind }}</span>
                <span class="tabular-nums">{{ (event as DemoTask).progress }}%</span>
              </div>
              <div>
                <p class="text-[11px] font-medium tracking-wide uppercase">
                  {{ STATUS_LABEL[(event as DemoTask).status] }}
                </p>
                <div class="mt-1.5 h-1.5 rounded-full bg-foreground/10">
                  <div
                    class="h-full rounded-full"
                    :class="barColor((event as DemoTask).status)"
                    :style="{ width: `${(event as DemoTask).progress}%` }"
                  />
                </div>
              </div>
            </div>
          </template>
          <template #meta="{ event }">
            <span>{{ (event as DemoTask).meta }}</span>
          </template>
        </BeTimeline>
        <p class="px-4 pb-2 text-xs text-muted-foreground">
          聚焦任务：{{ focusedId === TASKS[0]?.id ? "跟随拖拽更新" : "跟随拖拽更新" }} ·
          键盘 ←/→ 也可切换
        </p>
      </div>

      <!-- 新表面：统计卡、按钮与色板，确认后推广到其余页面 -->
      <section class="mt-8 grid gap-4 lg:grid-cols-3">
        <div class="rounded-3xl border border-border bg-card p-5">
          <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            本月任务
          </p>
          <p class="mt-1 font-display text-4xl leading-none tabular-nums">86</p>
          <p class="mt-2 text-xs text-muted-foreground">较上月 +31%，全部走证据链</p>
        </div>
        <div class="rounded-3xl border border-border bg-card p-5">
          <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            BYOK 占比
          </p>
          <p class="mt-1 font-display text-4xl leading-none tabular-nums">12.4%</p>
          <p class="mt-2 text-xs text-muted-foreground">用户自带模型 · 8 人配置</p>
        </div>
        <div class="rounded-3xl border border-border bg-card p-5">
          <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            估算成本
          </p>
          <p class="mt-1 font-display text-4xl leading-none tabular-nums">$0.63</p>
          <p class="mt-2 text-xs text-muted-foreground">按公开标价 · 不含折扣</p>
        </div>
      </section>

      <section class="mt-6 flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-transform active:scale-[0.98]"
        >
          新建任务
        </button>
        <button
          type="button"
          class="rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
        >
          次要操作
        </button>
        <span class="inline-flex items-center gap-1.5 rounded-full bg-[#E9EEFF] px-2.5 py-1 text-xs font-medium text-[#2447D6]">
          运行中
        </span>
        <span class="inline-flex items-center gap-1.5 rounded-full bg-[#EEF0F3] px-2.5 py-1 text-xs font-medium text-[#4B4E56]">
          已完成
        </span>
      </section>

      <section class="mt-8">
        <p class="mb-2 text-xs text-muted-foreground">配色（候选 A：纯白 + 钴蓝，卡片靠发丝边框分层）</p>
        <div class="flex flex-wrap gap-2">
          <div
            v-for="swatch in [
              { name: '底色', value: '#FFFFFF' },
              { name: '墨色', value: '#17181A' },
              { name: '强调', value: '#2F5BFF' },
              { name: '浅强调', value: '#E9EEFF' },
            ]"
            :key="swatch.name"
            class="w-28 rounded-xl border border-border bg-card p-2"
          >
            <div
              class="h-10 rounded-lg border border-border"
              :style="{ background: swatch.value }"
            />
            <p class="mt-1.5 text-xs text-muted-foreground">
              {{ swatch.name }} {{ swatch.value }}
            </p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* 演示页专属 token 覆盖：确认后迁移到全局 main.css */
.design-demo {
  --background: #ffffff;
  --foreground: #17181a;
  --card: #ffffff;
  --card-foreground: #17181a;
  --muted: #f5f5f6;
  --muted-foreground: #5f6066;
  --border: rgba(23, 24, 26, 0.12);
  --border-strong: rgba(23, 24, 26, 0.18);
  --primary: #2f5bff;
  --primary-foreground: #ffffff;
  --accent: #e9eeff;
  --accent-foreground: #2447d6;
  --ring: #2f5bff;
}
</style>
