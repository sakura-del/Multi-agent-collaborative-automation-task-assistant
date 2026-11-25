<script setup lang="ts">
/**
 * 设计演示页（不进导航、不接生产数据）。
 *
 * 目的：在动生产页面之前，先把「新配色 + beUI 时间线 + 新表面」的手感
 * 做出来给你确认。配色在这里用局部 token 覆盖，**不影响**其它页面的
 * 现有青绿主题。
 *
 * 路由：`/design/timeline`（requiresAdmin，仅管理员可访问，Issue #62）。
 * 确认后再把 token 提升为全局，并按方案逐页迁移。
 */
import { ref } from "vue";
import { ArrowLeft } from "lucide-vue-next";

import BeTimeline, { type BeTimelineEvent } from "@/components/motion/BeTimeline.vue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

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
    title: "PRISMA 筛选队列",
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

const ALERTS = [
  { variant: "info", title: "正在保存", description: "更改会自动保存，无需手动提交。" },
  { variant: "success", title: "已保存", description: "你的更改已保存到云端。" },
  {
    variant: "warning",
    title: "接近存储上限",
    description: "空间即将用尽，上传新文献前请先清理旧文件。",
  },
  {
    variant: "destructive",
    title: "保存失败",
    description: "网络中断，更改尚未保存。请重试。",
  },
] as const;

const alertVersion = ref(0);
/** 演示 selected 视觉：点击卡片切换。 */
const cardSelected = ref(true);
/** 分页演示：20 页足够看到两组省略号。 */
const demoPage = ref(1);

const STATUS_LABEL: Record<DemoStatus, string> = {
  running: "运行中",
  completed: "已完成",
  "needs-review": "待确认",
  failed: "失败",
};

function mediaTone(status: DemoStatus): string {
  if (status === "running") return "bg-primary/10 text-primary";
  if (status === "needs-review") return "bg-warning/15 text-warning";
  if (status === "failed") return "bg-destructive/15 text-destructive";
  return "bg-muted text-muted-foreground";
}

function barColor(status: DemoStatus): string {
  if (status === "running") return "bg-primary";
  if (status === "needs-review") return "bg-warning";
  if (status === "failed") return "bg-destructive";
  return "bg-foreground";
}

function onSelect(id: string) {
  void id;
}
</script>

<template>
  <div
    class="min-h-screen overflow-x-hidden bg-background px-5 py-10 text-foreground sm:px-8"
  >
    <div class="mx-auto w-full max-w-6xl">
      <header class="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <p class="mb-2 text-[10px] font-semibold tracking-[0.16em] text-primary uppercase">
            设计预览 · 时间线
          </p>
          <h1 class="font-display text-3xl font-medium tracking-[-0.035em]">研究时间线</h1>
          <p class="mt-2 max-w-xl text-sm text-muted-foreground">
            全局双主题 token、时间线与新版表面组件设计演示。拖拽、滚轮或方向键浏览。
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          @click="$router.back()"
        >
          <ArrowLeft :size="13" aria-hidden="true" />
          返回
        </Button>
      </header>

      <div class="rounded-3xl border border-border bg-card p-2">
        <BeTimeline
          :events="TASKS"
          :height="560"
          :item-width="340"
          aria-label="研究任务时间线演示"
          @activate="onSelect"
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
                <div class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-foreground/10">
                  <div
                    class="h-full w-full origin-left rounded-full transition-transform duration-200 ease-out motion-reduce:transition-none"
                    :class="barColor((event as DemoTask).status)"
                    :style="{ transform: `scaleX(${(event as DemoTask).progress / 100})` }"
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
          拖拽 / 滚轮 / ←→ 只浏览；点击卡片才打开
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
        <Button type="button" class="rounded-xl">
          新建任务
        </Button>
        <Button type="button" variant="outline" class="rounded-xl">
          次要操作
        </Button>
        <span class="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">
          运行中
        </span>
        <span class="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
          已完成
        </span>
      </section>

      <section class="mt-10">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p class="text-xs text-muted-foreground">动画警报（Animated Alert）</p>
            <p class="mt-1 text-sm text-muted-foreground">
              淡入 + 上移 6px；关闭有退出动画；warning/destructive 用 alert 角色。
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            @click="alertVersion += 1"
          >
            重新展示
          </Button>
        </div>
        <div class="grid gap-2">
          <Alert
            v-for="item in ALERTS"
            :key="`${item.variant}-${alertVersion}`"
            :variant="item.variant"
            :title="item.title"
            :description="item.description"
          />
        </div>
      </section>

      <section class="mt-10">
        <p class="mb-2 text-xs text-muted-foreground">分页（Motion Pagination）</p>
        <p class="text-xs text-muted-foreground">
          当前页是一个共享胶囊，翻页时滑过去；省略号悬停变双箭头，点一次跳 5 页。
        </p>
        <div class="mt-3 flex flex-col items-start gap-2">
          <Pagination v-model="demoPage" :total-pages="20" aria-label="演示分页" />
          <p class="text-xs tabular-nums text-muted-foreground">
            第 {{ demoPage }} 页 / 共 20 页
          </p>
        </div>
      </section>

      <section class="mt-10">
        <p class="mb-2 text-xs text-muted-foreground">
          卡片（Motion Card 结构：部件自管水平内边距、可选交互态）
        </p>
        <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle as="h3">默认卡片</CardTitle>
              <CardDescription>标题 + 描述双行网格，操作自动占右上角。</CardDescription>
              <CardAction>
                <span
                  class="rounded-md bg-accent px-1.5 py-0.5 text-[10px] font-medium text-accent-foreground"
                >
                  新
                </span>
              </CardAction>
            </CardHeader>
            <CardContent class="text-muted-foreground">
              根容器只管垂直间距，每个部件自己管左右内边距——少放一个部件也不会破坏边缘。
            </CardContent>
            <CardFooter class="justify-end">
              <Button type="button" variant="outline" size="sm">
                取消
              </Button>
              <Button type="button" size="sm">
                确认
              </Button>
            </CardFooter>
          </Card>

          <Card
            variant="muted"
            size="compact"
            interactive
            :selected="cardSelected"
            data-test="design-card-interactive"
            @click="cardSelected = !cardSelected"
          >
            <CardHeader>
              <CardTitle as="h3">可选卡片</CardTitle>
              <CardDescription>点击切换选中：主色 1px 环 + 按下缩放 0.985。</CardDescription>
            </CardHeader>
            <CardContent class="text-muted-foreground">
              {{ cardSelected ? "已选中" : "未选中" }} · 按压缩放由 pointer 事件手工驱动，
              卡片本身不获取焦点。
            </CardContent>
          </Card>

          <Card variant="muted" size="compact">
            <CardHeader>
              <CardTitle as="h3">紧凑灰底</CardTitle>
              <CardDescription>用于卡片内的次级区块，不再手写 bg-muted。</CardDescription>
            </CardHeader>
            <CardContent class="text-muted-foreground">
              compact 档：水平 16px、垂直 16px，适合并排与列表。
            </CardContent>
          </Card>
        </div>
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
