<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  Circle,
  Download,
  FileCheck2,
  HelpCircle,
  Loader2,
  Pause,
  Play,
  Quote,
  Sparkles,
  Table2,
  X,
} from "lucide-vue-next";

import {
  DEMO_AGENT_STAGES,
  DEMO_CITATIONS,
  DEMO_CSL_OPTIONS,
  DEMO_MATRIX_ROWS,
  DEMO_SCREENING_PAPERS,
  type DemoCslStyle,
  type DemoVerdict,
} from "./landing-demo-data";

export type ShowcaseTab = "prisma" | "matrix" | "review";

const props = withDefaults(
  defineProps<{
    activeTab?: ShowcaseTab;
  }>(),
  {
    activeTab: "prisma",
  },
);

const emit = defineEmits<{
  (event: "update:activeTab", value: ShowcaseTab): void;
  (event: "start"): void;
}>();

const currentTab = computed({
  get: () => props.activeTab,
  set: (value: ShowcaseTab) => emit("update:activeTab", value),
});

const TABS: { id: ShowcaseTab; label: string; icon: typeof FileCheck2 }[] = [
  { id: "prisma", label: "PRISMA 2020 智能筛选", icon: Sparkles },
  { id: "matrix", label: "文献提取表格 & 多智能体编排", icon: Table2 },
  { id: "review", label: "可溯源综述 & 模板导出", icon: FileCheck2 },
];

// ===== Tab 1：PRISMA 2020 实时交互状态 =====
// 基础背景已纳入 16 篇 + 示范列表中初始纳入的 2 篇 = 默认展示「最终纳入 18 篇」
const BASE_INCLUDED_OFFSET = 16;
const verdicts = ref<Record<string, DemoVerdict>>(
  Object.fromEntries(DEMO_SCREENING_PAPERS.map((paper) => [paper.id, paper.initialVerdict])),
);

const includedCount = computed(() => {
  const inDemo = Object.values(verdicts.value).filter((v) => v === "include").length;
  return BASE_INCLUDED_OFFSET + inDemo;
});

function setVerdict(paperId: string, next: DemoVerdict) {
  stopReplay();
  verdicts.value = { ...verdicts.value, [paperId]: next };
}

// ===== Tab 2：矩阵单元格原文定位状态 =====
interface SelectedCell {
  paperTitle: string;
  columnLabel: string;
  section: string;
  quote: string;
}
const selectedCell = ref<SelectedCell | null>(null);

function inspectCell(paperTitle: string, columnLabel: string, cell: { section: string; quote: string }) {
  stopReplay();
  selectedCell.value = {
    paperTitle,
    columnLabel,
    section: cell.section,
    quote: cell.quote,
  };
}

// ===== Tab 3：可溯源综述 & CSL 样式实时切换 =====
const selectedCsl = ref<DemoCslStyle>("gbt7714");
const activeCitationIndex = ref<number>(1);
const exportNotice = ref(false);

const activeCitation = computed(
  () => DEMO_CITATIONS.find((item) => item.index === activeCitationIndex.value) ?? DEMO_CITATIONS[0],
);

function triggerExportHint() {
  stopReplay();
  exportNotice.value = true;
}

// ===== 自动流程回放（真实研究流程 4 步演示，用户随时可接管） =====
const replaying = ref(false);
const replayStep = ref(0);
let replayTimer: ReturnType<typeof setInterval> | null = null;

const REPLAY_STEPS: { label: string; apply: () => void }[] = [
  {
    label: "1/4 · PRISMA 多库初筛纳入文献",
    apply: () => {
      currentTab.value = "prisma";
      verdicts.value = {
        ...verdicts.value,
        p3: "include",
      };
    },
  },
  {
    label: "2/4 · 四阶段 Agent 提取结构化证据",
    apply: () => {
      currentTab.value = "matrix";
      const row = DEMO_MATRIX_ROWS[0];
      selectedCell.value = {
        paperTitle: row.title,
        columnLabel: "主要结论",
        section: row.finding.section,
        quote: row.finding.quote,
      };
    },
  },
  {
    label: "3/4 · 句级引用绑定与 Crossref 核验",
    apply: () => {
      currentTab.value = "review";
      activeCitationIndex.value = 1;
      selectedCsl.value = "gbt7714";
    },
  },
  {
    label: "4/4 · 切换引用标准并锚定第 [2] 条证据",
    apply: () => {
      currentTab.value = "review";
      activeCitationIndex.value = 2;
      selectedCsl.value = "apa";
    },
  },
];

function stopReplay() {
  if (!replaying.value) return;
  replaying.value = false;
  if (replayTimer) {
    clearInterval(replayTimer);
    replayTimer = null;
  }
}

function toggleReplay() {
  if (replaying.value) {
    stopReplay();
    return;
  }
  replaying.value = true;
  replayStep.value = 0;
  REPLAY_STEPS[0].apply();
  replayTimer = setInterval(() => {
    const next = (replayStep.value + 1) % REPLAY_STEPS.length;
    replayStep.value = next;
    REPLAY_STEPS[next].apply();
  }, 2800);
}

function selectTabManual(tab: ShowcaseTab) {
  stopReplay();
  currentTab.value = tab;
}

onBeforeUnmount(() => {
  if (replayTimer) {
    clearInterval(replayTimer);
    replayTimer = null;
  }
});
</script>

<template>
  <div
    data-test="landing-showcase"
    class="mx-auto w-full max-w-[1120px] overflow-hidden rounded-xl border border-[#E5E7EB] bg-white text-left shadow-[0_18px_48px_-18px_rgba(15,76,58,0.12)] dark:border-border dark:bg-card"
  >
    <!-- 模拟工作台浅灰顶栏 + 三胶囊 Tab -->
    <div
      class="flex flex-wrap items-center justify-between gap-3 border-b border-[#E5E7EB] bg-[#F5F4F0] px-4 py-2.5 dark:border-border dark:bg-muted/50"
    >
      <div class="flex items-center gap-2">
        <span class="flex items-center gap-1.5" aria-hidden="true">
          <span class="size-2.5 rounded-full bg-[#D6D3CD] dark:bg-border-strong" />
          <span class="size-2.5 rounded-full bg-[#D6D3CD] dark:bg-border-strong" />
          <span class="size-2.5 rounded-full bg-[#D6D3CD] dark:bg-border-strong" />
        </span>
        <span class="ml-1 text-xs font-medium text-[#4B5563] dark:text-muted-foreground">
          VeriPaper 工作台 · 交互示范
        </span>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-test="showcase-replay-toggle"
          :aria-pressed="replaying"
          class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors"
          :class="
            replaying
              ? 'border-[#0F4C3A] bg-[#0F4C3A]/10 text-[#0F4C3A] dark:border-primary dark:bg-primary/15 dark:text-primary'
              : 'border-[#E5E7EB] bg-white text-[#374151] hover:border-[#0F4C3A]/40 hover:text-[#0F4C3A] dark:border-border dark:bg-background dark:text-foreground'
          "
          @click="toggleReplay"
        >
          <Pause v-if="replaying" :size="12" aria-hidden="true" />
          <Play v-else :size="12" aria-hidden="true" />
          <span>{{ replaying ? REPLAY_STEPS[replayStep].label : "播放流程演示" }}</span>
        </button>

        <div
          class="flex flex-wrap items-center gap-1 rounded-full border border-[#E5E7EB] bg-white/80 p-1 dark:border-border dark:bg-background/80"
          role="tablist"
          aria-label="产品能力交互预览"
        >
          <button
            v-for="tab in TABS"
            :key="tab.id"
            type="button"
            role="tab"
            :data-test="`showcase-tab-${tab.id}`"
            :aria-selected="currentTab === tab.id"
            class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors"
            :class="
              currentTab === tab.id
                ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                : 'text-[#4B5563] hover:text-[#111827] dark:text-muted-foreground dark:hover:text-foreground'
            "
            @click="selectTabManual(tab.id)"
          >
            <component :is="tab.icon" :size="13" aria-hidden="true" />
            <span>{{ tab.label }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- ==================== TAB 1：PRISMA 2020 智能筛选 ==================== -->
    <div v-if="currentTab === 'prisma'" data-test="showcase-panel-prisma" class="p-4 sm:p-6">
      <!-- 横向精简版 PRISMA 漏斗进度 -->
      <div
        class="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] px-3.5 py-2.5 text-xs dark:border-border dark:bg-muted/30"
      >
        <span class="text-[#4B5563] dark:text-muted-foreground">
          多库检索 <strong class="font-semibold text-[#111827] tabular-nums dark:text-foreground">1,248</strong> 篇
        </span>
        <ArrowRight :size="12" class="text-[#9CA3AF]" aria-hidden="true" />
        <span class="text-[#4B5563] dark:text-muted-foreground">
          自动去重 <strong class="font-semibold text-[#111827] tabular-nums dark:text-foreground">892</strong> 篇
        </span>
        <ArrowRight :size="12" class="text-[#9CA3AF]" aria-hidden="true" />
        <span class="text-[#4B5563] dark:text-muted-foreground">
          标题/摘要初筛 <strong class="font-semibold text-[#111827] tabular-nums dark:text-foreground">64</strong> 篇
        </span>
        <ArrowRight :size="12" class="text-[#9CA3AF]" aria-hidden="true" />
        <span
          data-test="prisma-included-counter"
          class="inline-flex items-center gap-1 rounded-full bg-[#0F4C3A]/10 px-2.5 py-0.5 font-semibold text-[#0F4C3A] tabular-nums dark:bg-success/15 dark:text-success"
        >
          最终纳入 {{ includedCount }} 篇
        </span>
        <span class="ml-auto hidden text-[11px] text-[#6B7280] sm:inline dark:text-muted-foreground">
          试着点击右侧「纳入 / 排除」按钮，观察计数实时联动
        </span>
      </div>

      <!-- 文献筛选列表 -->
      <ul class="divide-y divide-[#E5E7EB] overflow-hidden rounded-lg border border-[#E5E7EB] bg-white dark:divide-border dark:border-border dark:bg-card">
        <li
          v-for="paper in DEMO_SCREENING_PAPERS"
          :key="paper.id"
          :data-test="`demo-paper-${paper.id}`"
          class="flex flex-col justify-between gap-3 px-4 py-3 transition-colors hover:bg-[#FAF9F6] sm:flex-row sm:items-center dark:hover:bg-muted/25"
        >
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span
                class="inline-flex items-center rounded bg-[#0F4C3A]/10 px-1.5 py-0.5 text-[11px] font-semibold text-[#0F4C3A] tabular-nums dark:bg-primary/15 dark:text-primary"
              >
                优先级 {{ paper.score }}%
              </span>
              <span class="text-sm font-semibold text-[#111827] dark:text-foreground">
                {{ paper.title }}
              </span>
            </div>
            <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-[#6B7280] dark:text-muted-foreground">
              <span class="tabular-nums">{{ paper.year }}</span>
              <span>·</span>
              <span>{{ paper.venue }}</span>
              <span class="rounded border border-[#E5E7EB] bg-[#F8F9FA] px-1.5 py-0.2 text-[10px] font-medium dark:border-border dark:bg-muted">
                {{ paper.source }}
              </span>
            </div>
            <p class="mt-1.5 text-xs leading-relaxed text-[#4B5563] dark:text-muted-foreground">
              <span class="font-medium text-[#0F4C3A] dark:text-primary">AI 初筛理由：</span>{{ paper.reason }}
            </p>
          </div>

          <div class="flex shrink-0 items-center gap-1.5" role="group" :aria-label="`筛选决策：${paper.title}`">
            <button
              type="button"
              :data-test="`demo-verdict-${paper.id}-include`"
              :aria-pressed="verdicts[paper.id] === 'include'"
              class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors"
              :class="
                verdicts[paper.id] === 'include'
                  ? 'border-[#0F4C3A] bg-[#0F4C3A] text-white dark:border-success dark:bg-success dark:text-success-foreground'
                  : 'border-[#E5E7EB] bg-white text-[#4B5563] hover:border-[#0F4C3A]/40 hover:text-[#0F4C3A] dark:border-border dark:bg-background dark:text-muted-foreground'
              "
              @click="setVerdict(paper.id, 'include')"
            >
              <Check :size="12" aria-hidden="true" />
              纳入
            </button>
            <button
              type="button"
              :data-test="`demo-verdict-${paper.id}-maybe`"
              :aria-pressed="verdicts[paper.id] === 'maybe'"
              class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors"
              :class="
                verdicts[paper.id] === 'maybe'
                  ? 'border-[#D97706]/40 bg-[#FEF3C7] text-[#92400E] dark:border-warning/40 dark:bg-warning/20 dark:text-warning'
                  : 'border-[#E5E7EB] bg-white text-[#4B5563] hover:border-[#D97706]/40 hover:text-[#92400E] dark:border-border dark:bg-background dark:text-muted-foreground'
              "
              @click="setVerdict(paper.id, 'maybe')"
            >
              <HelpCircle :size="12" aria-hidden="true" />
              待定
            </button>
            <button
              type="button"
              :data-test="`demo-verdict-${paper.id}-exclude`"
              :aria-pressed="verdicts[paper.id] === 'exclude'"
              class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors"
              :class="
                verdicts[paper.id] === 'exclude'
                  ? 'border-[#E11D48]/30 bg-[#FFF1F2] text-[#BE123C] dark:border-destructive/40 dark:bg-destructive/15 dark:text-destructive'
                  : 'border-[#E5E7EB] bg-white text-[#4B5563] hover:border-[#E11D48]/30 hover:text-[#BE123C] dark:border-border dark:bg-background dark:text-muted-foreground'
              "
              @click="setVerdict(paper.id, 'exclude')"
            >
              <X :size="12" aria-hidden="true" />
              排除
            </button>
          </div>
        </li>
      </ul>
    </div>

    <!-- ==================== TAB 2：文献提取表格 & 多智能体编排 ==================== -->
    <div v-else-if="currentTab === 'matrix'" data-test="showcase-panel-matrix" class="p-4 sm:p-6">
      <!-- 四阶段协作编排条 -->
      <div
        class="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] px-3.5 py-2.5 text-xs dark:border-border dark:bg-muted/30"
      >
        <template v-for="(stage, idx) in DEMO_AGENT_STAGES" :key="stage.id">
          <span
            class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-medium"
            :class="
              stage.status === 'done'
                ? 'bg-[#0F4C3A]/10 text-[#0F4C3A] dark:bg-success/15 dark:text-success'
                : stage.status === 'active'
                  ? 'bg-[#FEF3C7] text-[#92400E] dark:bg-warning/15 dark:text-warning'
                  : 'bg-white text-[#6B7280] dark:bg-background dark:text-muted-foreground'
            "
          >
            <CheckCircle2 v-if="stage.status === 'done'" :size="12" aria-hidden="true" />
            <Loader2
              v-else-if="stage.status === 'active'"
              :size="12"
              class="animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
            <Circle v-else :size="11" aria-hidden="true" />
            {{ stage.label }}
          </span>
          <ArrowRight
            v-if="idx < DEMO_AGENT_STAGES.length - 1"
            :size="12"
            class="text-[#9CA3AF]"
            aria-hidden="true"
          />
        </template>
      </div>

      <!-- Elicit 标志性结构化矩阵表格 -->
      <div class="overflow-x-auto rounded-lg border border-[#E5E7EB] bg-white dark:border-border dark:bg-card">
        <table class="w-full min-w-[780px] border-collapse text-left text-xs">
          <thead>
            <tr class="border-b border-[#E5E7EB] bg-[#F8F9FA] text-[#4B5563] dark:border-border dark:bg-muted/40 dark:text-muted-foreground">
              <th class="w-60 px-3.5 py-2.5 font-semibold">文献标题 &amp; 来源库</th>
              <th class="px-3.5 py-2.5 font-semibold">核心方法</th>
              <th class="px-3.5 py-2.5 font-semibold">样本 / 数据集</th>
              <th class="px-3.5 py-2.5 font-semibold">主要结论</th>
              <th class="px-3.5 py-2.5 font-semibold">局限性</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[#E5E7EB] dark:divide-border">
            <tr v-for="row in DEMO_MATRIX_ROWS" :key="row.id">
              <td class="px-3.5 py-3 align-top">
                <p class="font-semibold text-[#111827] dark:text-foreground">{{ row.title }}</p>
                <div class="mt-1 flex items-center gap-1.5 text-[11px] text-[#6B7280] dark:text-muted-foreground">
                  <span class="rounded border border-[#E5E7EB] bg-[#FAF9F6] px-1.5 py-0.2 font-medium dark:border-border dark:bg-muted">
                    {{ row.source }}
                  </span>
                  <span class="tabular-nums">{{ row.year }}</span>
                </div>
              </td>

              <td
                v-for="col in [
                  { key: 'method', label: '核心方法', data: row.method },
                  { key: 'sample', label: '样本 / 数据集', data: row.sample },
                  { key: 'finding', label: '主要结论', data: row.finding },
                  { key: 'limitation', label: '局限性', data: row.limitation },
                ] as const"
                :key="col.key"
                class="group relative px-3.5 py-3 align-top"
              >
                <button
                  type="button"
                  :data-test="`matrix-cell-${row.id}-${col.key}`"
                  class="block w-full rounded-md p-1.5 text-left leading-relaxed text-[#374151] transition-colors hover:bg-[#0F4C3A]/6 focus-visible:outline-2 focus-visible:outline-[#0F4C3A] dark:text-foreground/90 dark:hover:bg-primary/10"
                  @click="inspectCell(row.title, col.label, col.data)"
                >
                  {{ col.data.text }}
                  <span
                    class="pointer-events-none mt-1.5 inline-flex items-center gap-1 rounded bg-[#111827] px-1.5 py-0.5 text-[10px] font-medium text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 dark:bg-foreground dark:text-background"
                  >
                    点击定位至原文片段
                  </span>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 点击单元格后展示的原文定位证据条 -->
      <div
        v-if="selectedCell"
        data-test="matrix-quote-panel"
        class="mt-3 flex items-start justify-between gap-3 rounded-lg border border-[#0F4C3A]/25 bg-[#0F4C3A]/[0.04] px-3.5 py-2.5 text-xs dark:border-primary/30 dark:bg-primary/10"
      >
        <div class="min-w-0">
          <p class="font-semibold text-[#0F4C3A] dark:text-primary">
            原文定位 · {{ selectedCell.paperTitle }}（{{ selectedCell.columnLabel }} · {{ selectedCell.section }}）
          </p>
          <p class="mt-1 rounded bg-[#FEF9C3]/80 px-2 py-1 font-mono text-[11px] leading-relaxed text-[#1F2937] dark:bg-warning/20 dark:text-foreground">
            “{{ selectedCell.quote }}”
          </p>
        </div>
        <button
          type="button"
          aria-label="关闭原文片段"
          class="rounded p-1 text-[#6B7280] hover:bg-black/5 hover:text-[#111827] dark:text-muted-foreground"
          @click="selectedCell = null"
        >
          <X :size="14" aria-hidden="true" />
        </button>
      </div>
    </div>

    <!-- ==================== TAB 3：可溯源综述 & 模板导出 ==================== -->
    <div v-else data-test="showcase-panel-review" class="p-4 sm:p-6">
      <!-- 综述顶部工具栏：CSL 样式切换 + Word 模板导出 -->
      <div
        class="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] px-3.5 py-2 dark:border-border dark:bg-muted/30"
      >
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <label for="landing-csl-select" class="font-medium text-[#4B5563] dark:text-muted-foreground">
            引用格式：
          </label>
          <select
            id="landing-csl-select"
            v-model="selectedCsl"
            data-test="demo-csl-select"
            class="h-7 rounded-md border border-[#E5E7EB] bg-white px-2 text-xs font-medium text-[#111827] outline-none focus-visible:border-[#0F4C3A] dark:border-border dark:bg-background dark:text-foreground"
          >
            <option v-for="opt in DEMO_CSL_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </div>

        <button
          type="button"
          data-test="demo-export-docx"
          class="inline-flex h-7 items-center gap-1.5 rounded-md border border-[#0F4C3A]/25 bg-white px-2.5 text-xs font-medium text-[#0F4C3A] transition-colors hover:bg-[#0F4C3A] hover:text-white dark:border-primary/40 dark:bg-background dark:text-primary dark:hover:bg-primary dark:hover:text-primary-foreground"
          @click="triggerExportHint"
        >
          <Download :size="13" aria-hidden="true" />
          导出学校 .docx 模板
        </button>
      </div>

      <div
        v-if="exportNotice"
        data-test="demo-export-notice"
        class="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#0F4C3A]/25 bg-[#0F4C3A]/[0.05] px-3.5 py-2 text-xs text-[#0F4C3A] dark:border-primary/30 dark:bg-primary/10 dark:text-primary"
      >
        <span>
          已按 <strong>{{ DEMO_CSL_OPTIONS.find((o) => o.value === selectedCsl)?.label }}</strong> 样式就绪。免费注册后可上传任意高校/期刊 Word 模板并一键导出成稿。
        </span>
        <button
          type="button"
          class="font-semibold underline underline-offset-2"
          @click="emit('start')"
        >
          去免费体验 →
        </button>
      </div>

      <!-- 左 65% 正文预览 + 右 35% 溯源核验侧边栏 -->
      <div class="grid gap-4 lg:grid-cols-[1.85fr_1fr] lg:items-start">
        <article
          class="rounded-lg border border-[#E5E7EB] bg-white p-5 dark:border-border dark:bg-background"
        >
          <p class="text-[11px] font-medium tracking-wider text-[#6B7280] uppercase dark:text-muted-foreground">
            3.2 证据约束与多智能体初筛进展 · 示范片段
          </p>
          <h3 class="mt-1.5 font-display text-lg font-semibold text-[#111827] dark:text-foreground">
            检索增强文献综述中的引用忠实度与流程自动化
          </h3>

          <div class="mt-3 space-y-3 font-display text-[14.5px] leading-8 text-[#1F2937] dark:text-foreground/90">
            <p>
              近年来，基于大语言模型的自动化文献综述在生成流畅度上取得显著进展，但缺乏证据约束的自由生成仍面临严重的引文幻觉问题。实证评估显示，将双阶段混合检索与句级证据绑定相结合，并在成稿前接入 Crossref 与 OpenAlex 外部题录核验后，不可追溯引用率可由 18.6% 显著压降至 1.2%<button
                type="button"
                data-test="citation-marker-1"
                :aria-pressed="activeCitationIndex === 1"
                class="mx-0.5 inline-flex items-center rounded px-1.5 py-0.2 align-baseline font-sans text-xs font-semibold transition-colors"
                :class="
                  activeCitationIndex === 1
                    ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                    : 'bg-[#0F4C3A]/10 text-[#0F4C3A] hover:bg-[#0F4C3A]/20 dark:bg-primary/15 dark:text-primary'
                "
                @click="activeCitationIndex = 1"
              >[1]</button>。
            </p>
            <p>
              在文献筛选阶段，遵循 PRISMA 2020 规范的透明纳排流程同样是学术合规的核心前提。针对 12 项临床系统综述共 14,380 条候选记录的回溯实验表明，采用多智能体分工辩论与主动学习优先排序策略，可在维持 97.4% 高召回率的同时将人工审阅工作量降低 68.2%<button
                type="button"
                data-test="citation-marker-2"
                :aria-pressed="activeCitationIndex === 2"
                class="mx-0.5 inline-flex items-center rounded px-1.5 py-0.2 align-baseline font-sans text-xs font-semibold transition-colors"
                :class="
                  activeCitationIndex === 2
                    ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                    : 'bg-[#0F4C3A]/10 text-[#0F4C3A] hover:bg-[#0F4C3A]/20 dark:bg-primary/15 dark:text-primary'
                "
                @click="activeCitationIndex = 2"
              >[2]</button>，为高质量综述的规模化产出提供了可复现的工程路径。
            </p>
          </div>

          <p class="mt-4 border-t border-[#E5E7EB] pt-2.5 font-sans text-[11px] text-[#6B7280] dark:border-border dark:text-muted-foreground">
            提示：点击正文中的 <span class="font-semibold text-[#0F4C3A] dark:text-primary">[1]</span> 或 <span class="font-semibold text-[#0F4C3A] dark:text-primary">[2]</span> 角标，右侧将即时切换原始证据与核验结果。
          </p>
        </article>

        <!-- 右侧：来源溯源核验侧边栏 -->
        <aside
          data-test="citation-verifier-panel"
          class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] p-4 dark:border-border dark:bg-muted/30"
        >
          <div class="flex items-center justify-between gap-2 border-b border-[#E5E7EB] pb-2.5 dark:border-border">
            <span class="inline-flex items-center gap-1 rounded-full bg-[#0F4C3A]/10 px-2 py-0.5 text-[11px] font-semibold text-[#0F4C3A] dark:bg-success/15 dark:text-success">
              <Check :size="12" :stroke-width="2.4" aria-hidden="true" />
              DOI / Crossref 真实性核验通过
            </span>
            <span class="text-[11px] font-semibold text-[#6B7280] tabular-nums dark:text-muted-foreground">
              [{{ activeCitation.index }}]
            </span>
          </div>

          <div class="mt-3 space-y-2.5 text-xs">
            <div>
              <p class="font-semibold leading-snug text-[#111827] dark:text-foreground">
                {{ activeCitation.title }}
              </p>
              <p class="mt-1 text-[11px] text-[#6B7280] dark:text-muted-foreground">
                {{ activeCitation.authors }} · {{ activeCitation.venue }} ({{ activeCitation.year }})
              </p>
              <p class="mt-0.5 font-mono text-[10px] text-[#0F4C3A] dark:text-primary">
                DOI: {{ activeCitation.doi }}
              </p>
            </div>

            <div class="rounded-md border border-[#FDE68A] bg-[#FEF9C3]/75 p-2.5 dark:border-warning/30 dark:bg-warning/15">
              <p class="flex items-center gap-1 text-[10px] font-semibold text-[#92400E] dark:text-warning">
                <Quote :size="11" aria-hidden="true" />
                原文支撑句（{{ activeCitation.locator }}）
              </p>
              <p class="mt-1 leading-relaxed text-[#1F2937] dark:text-foreground">
                {{ activeCitation.quote }}
              </p>
            </div>

            <div class="border-t border-[#E5E7EB] pt-2.5 dark:border-border">
              <p class="text-[10px] font-semibold tracking-wide text-[#6B7280] uppercase dark:text-muted-foreground">
                CSL 实时渲染条目（{{ DEMO_CSL_OPTIONS.find((o) => o.value === selectedCsl)?.label }}）
              </p>
              <p
                data-test="demo-csl-output"
                class="mt-1.5 rounded border border-[#E5E7EB] bg-white p-2 font-mono text-[11px] leading-relaxed break-words text-[#374151] dark:border-border dark:bg-background dark:text-foreground/90"
              >
                {{ activeCitation.formatted[selectedCsl] }}
              </p>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>
