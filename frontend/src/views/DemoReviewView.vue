<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Database,
  Eye,
  FileText,
  Quote,
  ShieldCheck,
  Sparkles,
  Table2,
} from "lucide-vue-next";

import { trackEvent } from "@/api/growth";

import {
  DEMO_CITATIONS,
  DEMO_CSL_OPTIONS,
  DEMO_FULL_REPORT,
  DEMO_MATRIX_ROWS,
  DEMO_SCREENING_PAPERS,
  type DemoCslStyle,
} from "@/components/landing/landing-demo-data";

type DemoSectionTab = "report" | "prisma" | "matrix";

const activeSection = ref<DemoSectionTab>("report");
const selectedCsl = ref<DemoCslStyle>("gbt7714");
const activeCitationIndex = ref<number>(1);

const activeCitation = computed(
  () => DEMO_CITATIONS.find((item) => item.index === activeCitationIndex.value) ?? DEMO_CITATIONS[0],
);

const VERDICT_LABEL: Record<string, string> = {
  include: "已纳入",
  maybe: "待定复核",
  exclude: "已排除",
};

onMounted(() => {
  trackEvent("demo_view");
});
</script>

<template>
  <div class="min-h-dvh bg-[#FAF9F6] text-[#111827] dark:bg-background dark:text-foreground">
    <!-- 顶部极简导航 -->
    <header
      class="sticky top-0 z-30 border-b border-[#E5E7EB]/90 bg-[#FAF9F6]/90 backdrop-blur-sm dark:border-border dark:bg-background/90"
    >
      <div class="mx-auto flex h-14 max-w-[1160px] items-center justify-between gap-2 px-4 sm:px-6">
        <div class="flex min-w-0 items-center gap-3">
          <RouterLink
            to="/"
            data-test="demo-back-home"
            class="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-[#4B5563] transition-colors hover:bg-black/5 hover:text-[#111827] dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
          >
            <ArrowLeft :size="14" aria-hidden="true" />
            <span class="hidden sm:inline">返回首页</span>
          </RouterLink>
          <span class="h-4 w-px bg-[#E5E7EB] dark:bg-border" aria-hidden="true" />
          <RouterLink to="/" class="flex shrink-0 items-center gap-2">
            <img src="/logo.svg" alt="" aria-hidden="true" class="size-6" />
            <span class="text-sm font-semibold tracking-tight">VeriPaper</span>
          </RouterLink>
          <span
            data-test="demo-readonly-badge"
            class="hidden items-center gap-1 rounded-full border border-[#0F4C3A]/20 bg-[#0F4C3A]/10 px-2 py-0.5 text-[11px] font-medium text-[#0F4C3A] sm:inline-flex dark:border-primary/30 dark:bg-primary/15 dark:text-primary"
          >
            <Eye :size="11" aria-hidden="true" />
            免登录只读示例
          </span>
        </div>

        <div class="flex shrink-0 items-center gap-2">
          <RouterLink
            :to="{ name: 'login' }"
            class="inline-flex h-8 items-center whitespace-nowrap rounded-md px-2.5 text-xs font-medium text-[#374151] hover:bg-black/5 dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
          >
            登录
          </RouterLink>
          <RouterLink
            :to="{ name: 'register' }"
            data-test="demo-top-cta"
            class="inline-flex h-8 items-center gap-1 whitespace-nowrap rounded-md bg-[#0F4C3A] px-3 text-xs font-medium text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            <span>开始免费综述</span>
            <ArrowRight :size="12" aria-hidden="true" />
          </RouterLink>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-[1160px] space-y-5 px-4 py-6 sm:px-6 sm:py-8">
      <!-- 示范说明横幅 -->
      <section
        data-test="demo-banner"
        class="flex flex-col justify-between gap-3 rounded-xl border border-[#0F4C3A]/20 bg-[#0F4C3A]/[0.05] px-4 py-3.5 sm:flex-row sm:items-center dark:border-primary/30 dark:bg-primary/10"
      >
        <div class="min-w-0 text-xs leading-relaxed">
          <p class="font-semibold text-[#0F4C3A] dark:text-primary">
            完整成品示例（只读预览 · 免注册查看）
          </p>
          <p class="mt-0.5 text-[#4B5563] dark:text-muted-foreground">
            以下展示了 VeriPaper 从多库检索、PRISMA 初筛、矩阵提取到句级引文核验的真实成品结构，注册后新号每月赠送 3 次免费额度。
          </p>
        </div>
        <RouterLink
          :to="{ name: 'register' }"
          data-test="demo-banner-cta"
          class="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-[#0F4C3A] px-4 text-xs font-medium text-white transition-colors hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
        >
          <span>用我的课题生成</span>
          <ArrowRight :size="13" aria-hidden="true" />
        </RouterLink>
      </section>

      <!-- 任务概览卡 -->
      <section class="rounded-xl border border-[#E5E7EB] bg-white p-5 dark:border-border dark:bg-card">
        <div class="flex flex-wrap items-center gap-2 text-xs text-[#6B7280] dark:text-muted-foreground">
          <span class="inline-flex items-center gap-1 rounded bg-[#0F4C3A]/10 px-2 py-0.5 font-medium text-[#0F4C3A] dark:bg-primary/15 dark:text-primary">
            <FileText :size="12" aria-hidden="true" />
            已完成综述示范
          </span>
          <span>任务 ID：{{ DEMO_FULL_REPORT.taskId }}</span>
          <span>·</span>
          <span>模型：{{ DEMO_FULL_REPORT.model }}</span>
          <span>·</span>
          <span class="tabular-nums">耗时 {{ DEMO_FULL_REPORT.durationText }}</span>
        </div>

        <h1
          data-test="demo-report-title"
          class="mt-2 font-display text-xl leading-snug font-semibold text-[#111827] sm:text-2xl dark:text-foreground"
        >
          {{ DEMO_FULL_REPORT.title }}
        </h1>

        <div class="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <div class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] px-3 py-2 dark:border-border dark:bg-muted/30">
            <p class="text-[11px] text-[#6B7280] dark:text-muted-foreground">多库检索 / 去重</p>
            <p class="mt-0.5 text-sm font-semibold tabular-nums">
              {{ DEMO_FULL_REPORT.searchedCount }} → 892 篇
            </p>
          </div>
          <div class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] px-3 py-2 dark:border-border dark:bg-muted/30">
            <p class="text-[11px] text-[#6B7280] dark:text-muted-foreground">PRISMA 最终纳入</p>
            <p class="mt-0.5 text-sm font-semibold text-[#0F4C3A] tabular-nums dark:text-success">
              {{ DEMO_FULL_REPORT.includedCount }} 篇
            </p>
          </div>
          <div class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] px-3 py-2 dark:border-border dark:bg-muted/30">
            <p class="text-[11px] text-[#6B7280] dark:text-muted-foreground">引文核验通过</p>
            <p class="mt-0.5 text-sm font-semibold text-[#0F4C3A] tabular-nums dark:text-success">
              {{ DEMO_FULL_REPORT.verification.verified }} / {{ DEMO_FULL_REPORT.verification.verified + DEMO_FULL_REPORT.verification.partial }} 条
            </p>
          </div>
          <div class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] px-3 py-2 dark:border-border dark:bg-muted/30">
            <p class="text-[11px] text-[#6B7280] dark:text-muted-foreground">存疑 / 幻觉拦截</p>
            <p class="mt-0.5 text-sm font-semibold tabular-nums">
              {{ DEMO_FULL_REPORT.verification.dubious }} 条
            </p>
          </div>
        </div>
      </section>

      <!-- 三视角切换栏 -->
      <div
        role="tablist"
        aria-label="示范任务视角切换"
        class="inline-flex flex-wrap gap-1 rounded-lg border border-[#E5E7EB] bg-white p-1 dark:border-border dark:bg-card"
      >
        <button
          type="button"
          role="tab"
          data-test="demo-tab-report"
          :aria-selected="activeSection === 'report'"
          class="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
          :class="
            activeSection === 'report'
              ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
              : 'text-[#4B5563] hover:text-[#111827] dark:text-muted-foreground dark:hover:text-foreground'
          "
          @click="activeSection = 'report'"
        >
          <FileText :size="13" aria-hidden="true" />
          综述正文与引文溯源
        </button>
        <button
          type="button"
          role="tab"
          data-test="demo-tab-prisma"
          :aria-selected="activeSection === 'prisma'"
          class="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
          :class="
            activeSection === 'prisma'
              ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
              : 'text-[#4B5563] hover:text-[#111827] dark:text-muted-foreground dark:hover:text-foreground'
          "
          @click="activeSection = 'prisma'"
        >
          <Sparkles :size="13" aria-hidden="true" />
          PRISMA 初筛记录
        </button>
        <button
          type="button"
          role="tab"
          data-test="demo-tab-matrix"
          :aria-selected="activeSection === 'matrix'"
          class="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
          :class="
            activeSection === 'matrix'
              ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
              : 'text-[#4B5563] hover:text-[#111827] dark:text-muted-foreground dark:hover:text-foreground'
          "
          @click="activeSection = 'matrix'"
        >
          <Table2 :size="13" aria-hidden="true" />
          结构化提取矩阵
        </button>
      </div>

      <!-- 视角 1：综述正文 + 右侧句级引文溯源 -->
      <section
        v-if="activeSection === 'report'"
        data-test="demo-panel-report"
        class="grid gap-5 lg:grid-cols-[1.8fr_1fr] lg:items-start"
      >
        <article class="rounded-xl border border-[#E5E7EB] bg-white p-5 sm:p-7 dark:border-border dark:bg-card">
          <div class="md-body font-display">
            <template v-for="section in DEMO_FULL_REPORT.sections" :key="section.heading">
              <h2>{{ section.heading }}</h2>
              <p v-for="(para, idx) in section.paragraphs" :key="idx">
                {{ para }}
              </p>
            </template>
          </div>

          <!-- 交互式引文锚点试听段 -->
          <div class="mt-5 rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] p-4 dark:border-border dark:bg-muted/30">
            <p class="text-xs font-medium text-[#4B5563] dark:text-muted-foreground">
              点击角标验证句级出处锚定：
            </p>
            <p class="mt-2 font-display text-sm leading-7 text-[#1F2937] dark:text-foreground">
              引入后置 Crossref 跨库核验与句级证据绑定后，不可追溯引用率从 18.6% 下降至 1.2%
              <button
                type="button"
                data-test="demo-page-marker-1"
                :aria-pressed="activeCitationIndex === 1"
                class="mx-0.5 inline-flex items-center rounded px-1.5 py-0.2 font-sans text-xs font-semibold transition-colors"
                :class="
                  activeCitationIndex === 1
                    ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                    : 'bg-[#0F4C3A]/10 text-[#0F4C3A] hover:bg-[#0F4C3A]/20 dark:bg-primary/15 dark:text-primary'
                "
                @click="activeCitationIndex = 1"
              >[1]</button>；
              多智能体辩论筛选在保持 97.4% 召回率下降低 68.2% 人工审阅量
              <button
                type="button"
                data-test="demo-page-marker-2"
                :aria-pressed="activeCitationIndex === 2"
                class="mx-0.5 inline-flex items-center rounded px-1.5 py-0.2 font-sans text-xs font-semibold transition-colors"
                :class="
                  activeCitationIndex === 2
                    ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                    : 'bg-[#0F4C3A]/10 text-[#0F4C3A] hover:bg-[#0F4C3A]/20 dark:bg-primary/15 dark:text-primary'
                "
                @click="activeCitationIndex = 2"
              >[2]</button>。
            </p>
          </div>

          <!-- 参考文献区块 -->
          <div class="mt-6 border-t border-[#E5E7EB] pt-5 dark:border-border">
            <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 class="text-sm font-semibold text-[#111827] dark:text-foreground">
                参考文献（支持一键切换 CSL 1.0 格式）
              </h2>
              <div class="flex items-center gap-1.5 text-xs">
                <label for="demo-page-csl" class="text-[#6B7280] dark:text-muted-foreground">样式：</label>
                <select
                  id="demo-page-csl"
                  v-model="selectedCsl"
                  data-test="demo-page-csl-select"
                  class="h-8 rounded-md border border-[#E5E7EB] bg-[#FAF9F6] px-2 text-xs font-medium text-[#111827] outline-none focus-visible:border-[#0F4C3A] dark:border-border dark:bg-background dark:text-foreground dark:focus-visible:border-ring"
                >
                  <option v-for="opt in DEMO_CSL_OPTIONS" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                  </option>
                </select>
              </div>
            </div>

            <ol class="space-y-2.5 text-xs">
              <li
                v-for="item in DEMO_CITATIONS"
                :key="item.index"
                class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] p-3 dark:border-border dark:bg-muted/30"
              >
                <p data-test="demo-page-citation-text" class="font-mono text-[11px] leading-relaxed break-words">
                  {{ item.formatted[selectedCsl] }}
                </p>
                <p class="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-[#0F4C3A] dark:text-success">
                  <CheckCircle2 :size="12" aria-hidden="true" />
                  {{ item.source }} · DOI {{ item.doi }}
                </p>
              </li>
            </ol>
          </div>
        </article>

        <!-- 右侧固定溯源面板 -->
        <aside
          data-test="demo-page-verifier"
          class="rounded-xl border border-[#E5E7EB] bg-white p-5 lg:sticky lg:top-20 dark:border-border dark:bg-card"
        >
          <div class="flex items-center justify-between gap-2 border-b border-[#E5E7EB] pb-3 dark:border-border">
            <span class="inline-flex items-center gap-1.5 text-xs font-semibold text-[#0F4C3A] dark:text-success">
              <ShieldCheck :size="14" aria-hidden="true" />
              原文证据与核验详情
            </span>
            <span class="rounded-full bg-[#0F4C3A]/10 px-2 py-0.5 text-[11px] font-semibold text-[#0F4C3A] tabular-nums dark:bg-primary/15 dark:text-primary">
              [{{ activeCitation.index }}]
            </span>
          </div>

          <div class="mt-3.5 space-y-3 text-xs">
            <div>
              <p class="font-semibold leading-snug text-[#111827] dark:text-foreground">
                {{ activeCitation.title }}
              </p>
              <p class="mt-1 text-[11px] text-[#6B7280] dark:text-muted-foreground">
                {{ activeCitation.authors }} · {{ activeCitation.venue }} ({{ activeCitation.year }})
              </p>
              <p class="mt-1 font-mono text-[10.5px] text-[#0F4C3A] dark:text-primary">
                DOI: {{ activeCitation.doi }}
              </p>
            </div>

            <div class="rounded-lg border border-[#FDE68A] bg-[#FEF9C3]/75 p-3 dark:border-warning/30 dark:bg-warning/15">
              <p class="flex items-center gap-1 text-[11px] font-semibold text-[#92400E] dark:text-warning">
                <Quote :size="11" aria-hidden="true" />
                原文支撑片段（{{ activeCitation.locator }}）
              </p>
              <p class="mt-1.5 leading-relaxed text-[#1F2937] dark:text-foreground">
                {{ activeCitation.quote }}
              </p>
            </div>

            <div class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] p-3 text-[11px] text-[#4B5563] dark:border-border dark:bg-muted/30 dark:text-muted-foreground">
              <p class="flex items-center gap-1.5 font-medium text-[#111827] dark:text-foreground">
                <Database :size="12" aria-hidden="true" />
                可导出格式
              </p>
              <p class="mt-1">
                注册后支持导出完整 Markdown 与高校 / 期刊自定义 Word（.docx）模板。
              </p>
            </div>
          </div>
        </aside>
      </section>

      <!-- 视角 2：PRISMA 初筛记录 -->
      <section
        v-else-if="activeSection === 'prisma'"
        data-test="demo-panel-prisma"
        class="rounded-xl border border-[#E5E7EB] bg-white p-5 dark:border-border dark:bg-card"
      >
        <h2 class="text-sm font-semibold">PRISMA 2020 候选文献初筛记录（节选）</h2>
        <ul class="mt-3 divide-y divide-[#E5E7EB] rounded-lg border border-[#E5E7EB] dark:divide-border dark:border-border">
          <li
            v-for="paper in DEMO_SCREENING_PAPERS"
            :key="paper.id"
            class="flex flex-col justify-between gap-2 px-4 py-3 sm:flex-row sm:items-center"
          >
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="rounded bg-[#0F4C3A]/10 px-1.5 py-0.5 text-[11px] font-semibold text-[#0F4C3A] tabular-nums dark:bg-primary/15 dark:text-primary">
                  {{ paper.score }}%
                </span>
                <span class="text-sm font-medium">{{ paper.title }}</span>
              </div>
              <p class="mt-1 text-xs text-[#6B7280] dark:text-muted-foreground">
                {{ paper.year }} · {{ paper.venue }} · {{ paper.source }}
              </p>
              <p class="mt-1 text-xs text-[#4B5563] dark:text-muted-foreground">
                {{ paper.reason }}
              </p>
            </div>
            <span
              class="inline-flex w-fit shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium"
              :class="
                paper.initialVerdict === 'include'
                  ? 'bg-[#0F4C3A]/10 text-[#0F4C3A] dark:bg-success/15 dark:text-success'
                  : paper.initialVerdict === 'maybe'
                    ? 'bg-[#FEF3C7] text-[#92400E] dark:bg-warning/20 dark:text-warning'
                    : 'bg-[#FFF1F2] text-[#BE123C] dark:bg-destructive/15 dark:text-rose-300'
              "
            >
              <Check v-if="paper.initialVerdict === 'include'" :size="12" aria-hidden="true" />
              {{ VERDICT_LABEL[paper.initialVerdict] }}
            </span>
          </li>
        </ul>
      </section>

      <!-- 视角 3：结构化提取矩阵 -->
      <section
        v-else
        data-test="demo-panel-matrix"
        class="rounded-xl border border-[#E5E7EB] bg-white p-5 dark:border-border dark:bg-card"
      >
        <h2 class="text-sm font-semibold">结构化文献对比矩阵（节选）</h2>
        <div class="mt-3 overflow-x-auto rounded-lg border border-[#E5E7EB] dark:border-border">
          <table class="w-full min-w-[760px] border-collapse text-left text-xs">
            <thead>
              <tr class="border-b border-[#E5E7EB] bg-[#F8F9FA] text-[#4B5563] dark:border-border dark:bg-muted/40 dark:text-muted-foreground">
                <th class="w-56 px-3.5 py-2.5 font-semibold">文献</th>
                <th class="px-3.5 py-2.5 font-semibold">核心方法</th>
                <th class="px-3.5 py-2.5 font-semibold">样本 / 数据集</th>
                <th class="px-3.5 py-2.5 font-semibold">主要结论</th>
                <th class="px-3.5 py-2.5 font-semibold">局限性</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-[#E5E7EB] dark:divide-border">
              <tr v-for="row in DEMO_MATRIX_ROWS" :key="row.id">
                <td class="px-3.5 py-3 align-top">
                  <p class="font-semibold">{{ row.title }}</p>
                  <p class="mt-1 text-[11px] text-[#6B7280] dark:text-muted-foreground">
                    {{ row.source }} · {{ row.year }}
                  </p>
                </td>
                <td class="px-3.5 py-3 align-top">{{ row.method.text }}</td>
                <td class="px-3.5 py-3 align-top">{{ row.sample.text }}</td>
                <td class="px-3.5 py-3 align-top">{{ row.finding.text }}</td>
                <td class="px-3.5 py-3 align-top">{{ row.limitation.text }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>

    <footer class="border-t border-[#E5E7EB]/80 py-6 dark:border-border">
      <div class="mx-auto flex max-w-[1160px] flex-wrap items-center justify-between gap-3 px-4 text-xs text-[#6B7280] sm:px-6 dark:text-muted-foreground">
        <span>VeriPaper · 证据驱动的文献综述工作台</span>
        <div class="flex flex-wrap items-center gap-4">
          <RouterLink to="/" class="hover:text-[#111827] dark:hover:text-foreground">首页</RouterLink>
          <RouterLink :to="{ name: 'privacy' }" class="hover:text-[#111827] dark:hover:text-foreground">隐私政策</RouterLink>
          <RouterLink :to="{ name: 'terms' }" class="hover:text-[#111827] dark:hover:text-foreground">用户协议</RouterLink>
        </div>
      </div>
    </footer>
  </div>
</template>
