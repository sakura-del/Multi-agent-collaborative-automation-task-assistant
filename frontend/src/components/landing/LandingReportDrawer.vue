<script setup lang="ts">
import { ref } from "vue";
import { ArrowRight, CheckCircle2, FileText, ShieldCheck, X } from "lucide-vue-next";

import { Button } from "@/components/ui/button";
import { MotionDrawer } from "@/components/ui/drawer";
import {
  DEMO_CITATIONS,
  DEMO_CSL_OPTIONS,
  DEMO_FULL_REPORT,
  type DemoCslStyle,
} from "./landing-demo-data";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (event: "update:open", value: boolean): void;
  (event: "start"): void;
}>();

const selectedCsl = ref<DemoCslStyle>("gbt7714");

function close() {
  emit("update:open", false);
}
</script>

<template>
  <MotionDrawer
    :open="props.open"
    side="right"
    width-class="w-full max-w-2xl"
    aria-label="真实示范综述报告"
    :backdrop-attrs="{ 'data-test': 'landing-report-backdrop' }"
    :panel-attrs="{ 'data-test': 'landing-report-drawer' }"
    class="border-[#E5E7EB] bg-[#FAF9F6] text-[#111827] dark:border-border dark:bg-background dark:text-foreground"
    @update:open="emit('update:open', $event)"
  >
    <!-- 抽屉顶栏 -->
    <header class="flex items-start justify-between gap-3 border-b border-[#E5E7EB] bg-white px-5 py-4 dark:border-border dark:bg-card">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2 text-[11px] text-[#6B7280] dark:text-muted-foreground">
          <span class="inline-flex items-center gap-1 rounded bg-[#0F4C3A]/10 px-1.5 py-0.5 font-medium text-[#0F4C3A] dark:bg-primary/15 dark:text-primary">
            <FileText :size="11" aria-hidden="true" />
            示范任务快照
          </span>
          <span class="tabular-nums">检索 {{ DEMO_FULL_REPORT.searchedCount }} 篇</span>
          <span>·</span>
          <span class="tabular-nums">纳入 {{ DEMO_FULL_REPORT.includedCount }} 篇</span>
          <span>·</span>
          <span class="tabular-nums">耗时 {{ DEMO_FULL_REPORT.durationText }}</span>
        </div>
        <h2 class="mt-1.5 font-display text-base leading-snug font-semibold text-[#111827] dark:text-foreground">
          {{ DEMO_FULL_REPORT.title }}
        </h2>
      </div>

      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        data-test="close-report-drawer"
        aria-label="关闭示范报告"
        class="shrink-0 text-[#6B7280] hover:bg-black/5 hover:text-[#111827] dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
        @click="close"
      >
        <X :size="15" aria-hidden="true" />
      </Button>
    </header>

        <!-- 主体滚动区 -->
        <div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
          <!-- 四态引用核验统计条 -->
          <div
            data-test="report-verification-summary"
            class="rounded-lg border border-[#E5E7EB] bg-white p-3.5 dark:border-border dark:bg-card"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="inline-flex items-center gap-1.5 text-xs font-semibold text-[#0F4C3A] dark:text-success">
                <ShieldCheck :size="14" aria-hidden="true" />
                Crossref / OpenAlex 四态引文核验报告
              </span>
              <span class="text-[11px] text-[#6B7280] dark:text-muted-foreground">
                {{ DEMO_FULL_REPORT.model }}
              </span>
            </div>
            <div class="mt-2.5 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <div class="rounded border border-[#E5E7EB] bg-[#FAF9F6] px-2.5 py-1.5 dark:border-border dark:bg-muted/40">
                <span class="text-[11px] text-[#6B7280] dark:text-muted-foreground">核验通过</span>
                <p class="mt-0.5 font-semibold text-[#0F4C3A] tabular-nums dark:text-success">
                  {{ DEMO_FULL_REPORT.verification.verified }} 条
                </p>
              </div>
              <div class="rounded border border-[#E5E7EB] bg-[#FAF9F6] px-2.5 py-1.5 dark:border-border dark:bg-muted/40">
                <span class="text-[11px] text-[#6B7280] dark:text-muted-foreground">部分匹配</span>
                <p class="mt-0.5 font-semibold text-[#92400E] tabular-nums dark:text-warning">
                  {{ DEMO_FULL_REPORT.verification.partial }} 条
                </p>
              </div>
              <div class="rounded border border-[#E5E7EB] bg-[#FAF9F6] px-2.5 py-1.5 dark:border-border dark:bg-muted/40">
                <span class="text-[11px] text-[#6B7280] dark:text-muted-foreground">存疑拦截</span>
                <p class="mt-0.5 font-semibold tabular-nums">
                  {{ DEMO_FULL_REPORT.verification.dubious }} 条
                </p>
              </div>
              <div class="rounded border border-[#E5E7EB] bg-[#FAF9F6] px-2.5 py-1.5 dark:border-border dark:bg-muted/40">
                <span class="text-[11px] text-[#6B7280] dark:text-muted-foreground">未核验</span>
                <p class="mt-0.5 font-semibold tabular-nums">
                  {{ DEMO_FULL_REPORT.verification.unverified }} 条
                </p>
              </div>
            </div>
          </div>

          <!-- 综述成稿正文（工作台同款学术排版） -->
          <article class="rounded-lg border border-[#E5E7EB] bg-white p-5 dark:border-border dark:bg-card">
            <div class="md-body font-display">
              <template v-for="section in DEMO_FULL_REPORT.sections" :key="section.heading">
                <h2>{{ section.heading }}</h2>
                <p v-for="(para, idx) in section.paragraphs" :key="idx">
                  {{ para }}
                </p>
              </template>
            </div>

            <!-- 参考文献区块（支持切换 CSL 格式） -->
            <div class="mt-6 border-t border-[#E5E7EB] pt-4 dark:border-border">
              <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h3 class="text-sm font-semibold text-[#111827] dark:text-foreground">
                  参考文献（节选）
                </h3>
                <div class="flex items-center gap-1.5 text-xs">
                  <span class="text-[#6B7280] dark:text-muted-foreground">格式：</span>
                  <select
                    v-model="selectedCsl"
                    data-test="drawer-csl-select"
                    aria-label="切换示范报告引用格式"
                    class="h-7 rounded-md border border-[#E5E7EB] bg-[#FAF9F6] px-2 text-xs font-medium text-[#111827] outline-none focus-visible:border-[#0F4C3A] dark:border-border dark:bg-background dark:text-foreground dark:focus-visible:border-ring"
                  >
                    <option v-for="opt in DEMO_CSL_OPTIONS" :key="opt.value" :value="opt.value">
                      {{ opt.label }}
                    </option>
                  </select>
                </div>
              </div>

              <ol class="space-y-2.5 text-xs leading-relaxed text-[#374151] dark:text-foreground/90">
                <li
                  v-for="item in DEMO_CITATIONS"
                  :key="item.index"
                  class="rounded-md border border-[#E5E7EB] bg-[#FAF9F6] p-2.5 dark:border-border dark:bg-muted/30"
                >
                  <p class="font-mono text-[11px] break-words">{{ item.formatted[selectedCsl] }}</p>
                  <p class="mt-1 inline-flex items-center gap-1 text-[10px] font-medium text-[#0F4C3A] dark:text-success">
                    <CheckCircle2 :size="11" aria-hidden="true" />
                    {{ item.source }} · DOI {{ item.doi }}
                  </p>
                </li>
              </ol>
            </div>
          </article>
        </div>

        <!-- 抽屉底栏 -->
        <footer class="flex flex-wrap items-center justify-between gap-3 border-t border-[#E5E7EB] bg-white px-5 py-3 dark:border-border dark:bg-card">
          <span class="text-xs text-[#6B7280] dark:text-muted-foreground">
            支持导出高校 / 期刊自定义 Word（.docx）模板
          </span>
          <button
            type="button"
            data-test="drawer-start-cta"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-[#0F4C3A] px-3.5 text-xs font-medium text-white transition-colors hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
            @click="
              close();
              emit('start');
            "
          >
            <span>用我的课题开始综述</span>
            <ArrowRight :size="13" aria-hidden="true" />
          </button>
        </footer>
  </MotionDrawer>
</template>
