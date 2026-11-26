<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
import { ArrowRight, BookOpenCheck, Check, ChevronDown, Github, Monitor, Moon, Sun } from "lucide-vue-next";

import LandingReportDrawer from "@/components/landing/LandingReportDrawer.vue";
import LandingShowcase, { type ShowcaseTab } from "@/components/landing/LandingShowcase.vue";
import { TRUST_SOURCES } from "@/components/landing/landing-demo-data";
import { useTheme, type ThemeMode } from "@/composables/useTheme";
import { DURATION, EASE_IN, EASE_OUT, OFFSET } from "@/lib/ease";

const GITHUB_REPO_URL =
  "https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant";

const router = useRouter();
const reduced = useReducedMotion();
const { mode, isDark, setTheme, toggleTheme } = useTheme();

const themeMenuOpen = ref(false);
const themeMenuWrap = ref<HTMLElement | null>(null);
const themeModeTrigger = ref<HTMLButtonElement | null>(null);

const THEME_OPTIONS: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
  { value: "system", label: "跟随系统" },
];

const themeModeLabel = computed(() => {
  if (mode.value === "system") return "跟随系统";
  return mode.value === "dark" ? "深色" : "浅色";
});

const toggleTitle = computed(() => {
  const currentText =
    mode.value === "system"
      ? `跟随系统（当前${isDark.value ? "深色" : "浅色"}）`
      : isDark.value
        ? "深色模式"
        : "浅色模式";
  const nextText = isDark.value ? "切换到浅色模式" : "切换到深色模式";
  return `${currentText} · ${nextText}`;
});

function selectThemeMode(next: ThemeMode) {
  setTheme(next);
  themeMenuOpen.value = false;
  themeModeTrigger.value?.focus({ preventScroll: true });
}

function onDocumentPointerDown(event: Event) {
  if (!themeMenuOpen.value) return;
  const target = event.target as Node | null;
  if (target && themeMenuWrap.value?.contains(target)) return;
  themeMenuOpen.value = false;
}

function onThemeMenuKeydown(event: KeyboardEvent) {
  if (!themeMenuOpen.value || event.isComposing || event.keyCode === 229) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    themeMenuOpen.value = false;
    themeModeTrigger.value?.focus({ preventScroll: true });
  }
}

onMounted(() => {
  if (typeof document !== "undefined") {
    document.addEventListener("pointerdown", onDocumentPointerDown);
    window.addEventListener("keydown", onThemeMenuKeydown);
  }
});

onBeforeUnmount(() => {
  if (typeof document !== "undefined") {
    document.removeEventListener("pointerdown", onDocumentPointerDown);
    window.removeEventListener("keydown", onThemeMenuKeydown);
  }
});

const activeTab = ref<ShowcaseTab>("prisma");
const reportOpen = ref(false);
const showcaseAnchor = ref<HTMLElement | null>(null);

function goRegister() {
  void router.push({ name: "register" });
}

function jumpToShowcase(tab: ShowcaseTab) {
  activeTab.value = tab;
  const prefersReduced =
    reduced.value ||
    (typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  showcaseAnchor.value?.scrollIntoView({
    behavior: prefersReduced ? "auto" : "smooth",
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
      <div class="mx-auto flex h-14 max-w-[1160px] items-center justify-between gap-2 px-4 sm:gap-4 sm:px-6">
        <RouterLink
          to="/"
          data-test="landing-brand"
          class="flex shrink-0 items-center gap-2.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[#0F4C3A] dark:focus-visible:ring-ring"
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

        <div class="flex shrink-0 items-center gap-1.5 sm:gap-2">
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
            :aria-label="toggleTitle"
            :title="toggleTitle"
            class="inline-flex size-8 items-center justify-center rounded-md text-[#4B5563] transition-colors hover:bg-black/5 hover:text-[#111827] dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
            @click="toggleTheme"
          >
            <AnimatePresence mode="wait" :initial="false">
              <Motion
                v-if="isDark"
                key="sun"
                as="span"
                class="inline-flex items-center justify-center"
                :initial="{ opacity: 0, scale: reduced ? 1 : 0.85 }"
                :animate="{ opacity: 1, scale: 1 }"
                :exit="{ opacity: 0, scale: reduced ? 1 : 0.85 }"
                :transition="{ duration: DURATION.micro, ease: EASE_OUT }"
              >
                <Sun :size="15" aria-hidden="true" />
              </Motion>
              <Motion
                v-else
                key="moon"
                as="span"
                class="inline-flex items-center justify-center"
                :initial="{ opacity: 0, scale: reduced ? 1 : 0.85 }"
                :animate="{ opacity: 1, scale: 1 }"
                :exit="{ opacity: 0, scale: reduced ? 1 : 0.85 }"
                :transition="{ duration: DURATION.micro, ease: EASE_OUT }"
              >
                <Moon :size="15" aria-hidden="true" />
              </Motion>
            </AnimatePresence>
          </button>

          <!-- 三态主题模式选择：浅色 / 深色 / 跟随系统（与 TopBar 同构） -->
          <div ref="themeMenuWrap" class="relative">
            <button
              ref="themeModeTrigger"
              type="button"
              data-test="landing-theme-mode-select"
              aria-haspopup="listbox"
              :aria-expanded="themeMenuOpen"
              :aria-label="`主题模式：${themeModeLabel}`"
              :title="`主题模式：${themeModeLabel}`"
              class="inline-flex h-8 items-center gap-1 rounded-md border border-[#E5E7EB] bg-white/80 px-2 text-[11px] font-medium text-[#4B5563] transition-colors hover:bg-black/5 hover:text-[#111827] dark:border-border dark:bg-muted/40 dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
              @click="themeMenuOpen = !themeMenuOpen"
            >
              <Monitor v-if="mode === 'system'" :size="12" aria-hidden="true" />
              <Moon v-else-if="mode === 'dark'" :size="12" aria-hidden="true" />
              <Sun v-else :size="12" aria-hidden="true" />
              <span class="hidden sm:inline">{{ themeModeLabel }}</span>
              <ChevronDown :size="11" aria-hidden="true" />
            </button>

            <AnimatePresence :initial="false">
              <Motion
                v-if="themeMenuOpen"
                key="landing-theme-menu"
                as="ul"
                role="listbox"
                aria-label="选择主题模式"
                :initial="{ opacity: 0, y: reduced ? 0 : -OFFSET.microY }"
                :animate="{
                  opacity: 1,
                  y: 0,
                  transition: { duration: reduced ? DURATION.micro : DURATION.fast, ease: EASE_OUT },
                }"
                :exit="{
                  opacity: 0,
                  y: reduced ? 0 : -OFFSET.microY,
                  transition: { duration: DURATION.micro, ease: EASE_IN },
                }"
                class="absolute right-0 top-full z-40 mt-1.5 w-32 rounded-lg border border-[#E5E7EB] bg-white p-1 text-[#111827] shadow-raised dark:border-border-strong dark:bg-popover dark:text-popover-foreground"
              >
                <li
                  v-for="option in THEME_OPTIONS"
                  :key="option.value"
                  role="option"
                  :aria-selected="mode === option.value"
                >
                  <button
                    type="button"
                    :data-test="`landing-theme-mode-${option.value}`"
                    class="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors"
                    :class="
                      mode === option.value
                        ? 'bg-[#0F4C3A]/10 font-medium text-[#0F4C3A] dark:bg-accent dark:text-accent-foreground'
                        : 'text-[#111827] hover:bg-black/5 dark:text-foreground dark:hover:bg-muted/70'
                    "
                    @click="selectThemeMode(option.value)"
                  >
                    <span>{{ option.label }}</span>
                    <Check v-if="mode === option.value" :size="12" aria-hidden="true" />
                  </button>
                </li>
              </Motion>
            </AnimatePresence>
          </div>

          <RouterLink
            :to="{ name: 'login' }"
            data-test="landing-login"
            class="inline-flex h-8 shrink-0 items-center whitespace-nowrap rounded-md px-2.5 text-xs font-medium text-[#374151] transition-colors hover:bg-black/5 hover:text-[#111827] sm:px-3 dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground"
          >
            登录
          </RouterLink>

          <RouterLink
            :to="{ name: 'register' }"
            data-test="landing-register"
            class="inline-flex h-8 shrink-0 items-center whitespace-nowrap rounded-md bg-[#0F4C3A] px-3 text-xs font-medium text-white transition-colors hover:bg-[#135D47] sm:px-3.5 dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            免费开始
          </RouterLink>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-[1160px] px-4 pt-12 pb-16 sm:px-6 sm:pt-16 sm:pb-20">
      <!-- ==================== HERO 四段式（定稿文案） ==================== -->
      <section class="mx-auto max-w-3xl text-center">
        <!-- 1. Eyebrow Badge（禁用外层 flex-wrap，避免窄屏下绿点孤立独占首行，Issue #63） -->
        <p
          data-test="landing-eyebrow"
          class="inline-flex max-w-full items-center gap-2 rounded-2xl border border-[#E5E7EB] bg-white px-3.5 py-1.5 text-left text-xs leading-snug font-medium text-[#374151] shadow-2xs sm:rounded-full sm:py-1 sm:text-center dark:border-border dark:bg-card dark:text-muted-foreground"
        >
          <span
            data-test="landing-eyebrow-dot"
            class="size-1.5 shrink-0 rounded-full bg-[#0F4C3A] dark:bg-success"
            aria-hidden="true"
          />
          <span data-test="landing-eyebrow-text" class="min-w-0">
            arXiv · PubMed · OpenAlex · Semantic Scholar · GB/T 7714-2025
          </span>
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
          class="mx-auto mt-5 max-w-3xl text-balance text-sm leading-7 text-[#4B5563] sm:text-base sm:leading-7 dark:text-muted-foreground"
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
        <ul class="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs font-semibold tracking-wide text-[#4B5563] dark:text-muted-foreground">
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
