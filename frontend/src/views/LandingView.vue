<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { ArrowRight, BookOpenCheck, Github, Moon, Sun } from "lucide-vue-next";

import LandingReportDrawer from "@/components/landing/LandingReportDrawer.vue";
import LandingShowcase, { type ShowcaseTab } from "@/components/landing/LandingShowcase.vue";
import { TRUST_SOURCES } from "@/components/landing/landing-demo-data";
import { useTheme } from "@/composables/useTheme";

const GITHUB_REPO_URL =
  "https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant";

const router = useRouter();
const { isDark, toggleTheme } = useTheme();

const activeTab = ref<ShowcaseTab>("prisma");
const reportOpen = ref(false);
const showcaseAnchor = ref<HTMLElement | null>(null);

function goRegister() {
  void router.push({ name: "register" });
}

function jumpToShowcase(tab: ShowcaseTab) {
  activeTab.value = tab;
  const reduced =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  showcaseAnchor.value?.scrollIntoView({
    behavior: reduced ? "auto" : "smooth",
    block: "center",
  });
}
</script>

<template>
  <div class="min-h-dvh bg-[#FAF9F6] text-[#111827] dark:bg-background dark:text-foreground">
    <!-- 顶部极简学术导航栏 -->
    <header
      class="sticky top-0 z-30 border-b border-[#E5E7EB]/90 bg-[#FAF9F6]/90 backdrop-blur-sm dark:border-border dark:bg-background/90"
    >
      <div class="mx-auto flex h-14 max-w-[1160px] items-center justify-between gap-4 px-4 sm:px-6">
        <RouterLink
          to="/"
          data-test="landing-brand"
          class="flex items-center gap-2.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[#0F4C3A]"
        >
          <img src="/logo.svg" alt="" aria-hidden="true" class="size-7" />
          <span class="text-sm font-semibold tracking-tight text-[#111827] dark:text-foreground">
            VeriPaper
          </span>
        </RouterLink>

        <nav aria-label="产品特性快速预览" class="hidden items-center gap-6 text-xs font-medium text-[#4B5563] md:flex dark:text-muted-foreground">
          <button
            type="button"
            class="transition-colors hover:text-[#0F4C3A] dark:hover:text-foreground"
            @click="jumpToShowcase('prisma')"
          >
            PRISMA 筛选
          </button>
          <button
            type="button"
            class="transition-colors hover:text-[#0F4C3A] dark:hover:text-foreground"
            @click="jumpToShowcase('matrix')"
          >
            结构化矩阵提取
          </button>
          <button
            type="button"
            class="transition-colors hover:text-[#0F4C3A] dark:hover:text-foreground"
            @click="jumpToShowcase('review')"
          >
            可溯源综述与导出
          </button>
        </nav>

        <div class="flex items-center gap-2">
          <a
            :href="GITHUB_REPO_URL"
            target="_blank"
            rel="noopener noreferrer"
            data-test="landing-github"
            aria-label="GitHub 开源仓库"
            title="GitHub 开源仓库"
            class="inline-flex size-8 items-center justify-center rounded-md text-[#4B5563] transition-colors hover:bg-black/5 hover:text-[#111827] dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
          >
            <Github :size="15" aria-hidden="true" />
          </a>

          <button
            type="button"
            data-test="landing-theme-toggle"
            :aria-label="isDark ? '切换到浅色模式' : '切换到深色模式'"
            class="inline-flex size-8 items-center justify-center rounded-md text-[#4B5563] transition-colors hover:bg-black/5 hover:text-[#111827] dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
            @click="toggleTheme"
          >
            <Sun v-if="isDark" :size="15" aria-hidden="true" />
            <Moon v-else :size="15" aria-hidden="true" />
          </button>

          <RouterLink
            :to="{ name: 'login' }"
            data-test="landing-login"
            class="inline-flex h-8 items-center rounded-md px-3 text-xs font-medium text-[#374151] transition-colors hover:bg-black/5 hover:text-[#111827] dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
          >
            登录
          </RouterLink>

          <RouterLink
            :to="{ name: 'register' }"
            data-test="landing-register"
            class="inline-flex h-8 items-center rounded-md bg-[#0F4C3A] px-3.5 text-xs font-medium text-white transition-colors hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            免费开始
          </RouterLink>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-[1160px] px-4 pt-12 pb-16 sm:px-6 sm:pt-16 sm:pb-20">
      <!-- ==================== HERO 四段式（定稿文案） ==================== -->
      <section class="mx-auto max-w-3xl text-center">
        <!-- 1. Eyebrow Badge -->
        <p
          data-test="landing-eyebrow"
          class="inline-flex flex-wrap items-center justify-center gap-1.5 rounded-full border border-[#E5E7EB] bg-white px-3.5 py-1 text-xs font-medium text-[#374151] shadow-2xs dark:border-border dark:bg-card dark:text-muted-foreground"
        >
          <span class="size-1.5 rounded-full bg-[#0F4C3A] dark:bg-success" aria-hidden="true" />
          arXiv · PubMed · OpenAlex · Semantic Scholar · GB/T 7714-2025
        </p>

        <!-- 2. 主标题（思源宋体，无标点，两行双色） -->
        <h1
          data-test="landing-title"
          class="mt-6 font-display text-4xl leading-[1.24] font-semibold tracking-tight sm:text-5xl sm:leading-[1.2]"
        >
          <span class="block text-[#111827] dark:text-foreground">每一句综述</span>
          <span class="block text-[#0F4C3A] dark:text-primary">皆有出处</span>
        </h1>

        <!-- 3. 副标题 -->
        <p
          class="mx-auto mt-5 max-w-2xl text-sm leading-7 text-[#4B5563] sm:text-base sm:leading-7 dark:text-muted-foreground"
        >
          面向严肃研究的文献综述工作台。从多库检索、PRISMA 初筛到模板成稿，每一处引用均锚定原文。
        </p>

        <!-- 4. CTA + 信任小字 -->
        <div class="mt-7 flex flex-wrap items-center justify-center gap-3">
          <RouterLink
            :to="{ name: 'register' }"
            data-test="landing-cta-primary"
            class="inline-flex h-10 items-center gap-2 rounded-lg bg-[#0F4C3A] px-5 text-sm font-medium text-white shadow-xs transition-colors hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            <span>开始综述</span>
            <ArrowRight :size="15" aria-hidden="true" />
          </RouterLink>

          <button
            type="button"
            data-test="landing-cta-demo"
            class="inline-flex h-10 items-center gap-2 rounded-lg border border-[#E5E7EB] bg-white px-5 text-sm font-medium text-[#111827] transition-colors hover:border-[#D1D5DB] hover:bg-[#F8F9FA] dark:border-border dark:bg-card dark:text-foreground dark:hover:bg-muted"
            @click="reportOpen = true"
          >
            <BookOpenCheck :size="15" class="text-[#0F4C3A] dark:text-primary" aria-hidden="true" />
            <span>阅读示范报告</span>
          </button>
        </div>

        <p class="mt-3.5 text-xs text-[#6B7280] dark:text-muted-foreground">
          免费试用 · 支持自带 API Key · 全文引文防幻觉核验
        </p>
      </section>

      <!-- ==================== 首屏核心：可交互工作台预览窗 ==================== -->
      <section ref="showcaseAnchor" class="mt-10 sm:mt-12" aria-label="可交互产品工作台预览">
        <LandingShowcase v-model:active-tab="activeTab" @start="goRegister" />
      </section>

      <!-- ==================== 学术信任条（64px 留白） ==================== -->
      <section
        data-test="landing-trust-bar"
        aria-label="聚合数据源与引文标准"
        class="mt-16 flex flex-col items-center justify-between gap-4 border-y border-[#E5E7EB] py-5 lg:flex-row dark:border-border"
      >
        <p class="shrink-0 text-xs font-medium text-[#6B7280] dark:text-muted-foreground">
          聚合全球主流学术数据源与引文标准
        </p>
        <ul class="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs font-semibold tracking-wide text-[#4B5563]/80 dark:text-muted-foreground">
          <li v-for="source in TRUST_SOURCES" :key="source" class="select-none">
            {{ source }}
          </li>
        </ul>
      </section>
    </main>

    <!-- 极简页脚 -->
    <footer class="border-t border-[#E5E7EB]/80 py-6 dark:border-border">
      <div class="mx-auto flex max-w-[1160px] flex-wrap items-center justify-between gap-3 px-4 text-xs text-[#6B7280] sm:px-6 dark:text-muted-foreground">
        <span>VeriPaper · 证据驱动的文献综述工作台</span>
        <div class="flex items-center gap-4">
          <a
            :href="GITHUB_REPO_URL"
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex items-center gap-1 hover:text-[#111827] dark:hover:text-foreground"
          >
            <Github :size="13" aria-hidden="true" />
            <span>GitHub</span>
          </a>
          <RouterLink :to="{ name: 'login' }" class="hover:text-[#111827] dark:hover:text-foreground">
            登录账号
          </RouterLink>
          <RouterLink :to="{ name: 'register' }" class="hover:text-[#111827] dark:hover:text-foreground">
            免费注册
          </RouterLink>
        </div>
      </div>
    </footer>

    <LandingReportDrawer v-model:open="reportOpen" @start="goRegister" />
  </div>
</template>
