<script setup lang="ts">
import { computed, ref } from "vue";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
import { Check, ShieldCheck } from "lucide-vue-next";

import { DURATION, EASE_IN, EASE_OUT, OFFSET } from "@/lib/ease";

/**
 * 认证流共用左侧品牌展示面板（登录 / 注册 / 密码重置统一使用，Issue #39）。
 *
 * `headingTag` 默认为 `"h1"`；当右侧表单页本身将页面标题设为 `<h1>`（如密码重置页
 * E2E / 单测依赖 `h1 = 重置密码`）时，可传入 `"p"` 保持单页唯一主标题语义。
 */
withDefaults(
  defineProps<{
    headingTag?: "h1" | "p";
  }>(),
  {
    headingTag: "h1",
  },
);

const AUTH_PROOFS = {
  1: {
    source: "ACL Findings 2025 · Section 5.1",
    badge: "Crossref DOI 已核验",
    citation:
      "GB/T 7714—2025 · LIN Y, CHEN W, PATEL R. Evaluating faithfulness and citation grounding in retrieval-augmented literature synthesis.",
  },
  2: {
    source: "J. Biomed. Inform. 2025 · Section 3.3",
    badge: "PubMed / Crossref 已核验",
    citation:
      "GB/T 7714—2025 · ZHOU H, MILLER K, SATO T. Automated PRISMA 2020 screening with auditable multi-agent deliberation.",
  },
} as const;

const reduced = useReducedMotion();
const activeProofIndex = ref<1 | 2>(1);
const activeProof = computed(() => AUTH_PROOFS[activeProofIndex.value]);

const TRUST_ITEMS = ["arXiv", "PubMed", "OpenAlex", "PRISMA 2020", "GB/T 7714-2025"] as const;
</script>

<template>
  <!-- 左侧：Elicit 风格学术点阵底纹 + 双层可交互溯源卡（窄屏隐藏） -->
  <section
    data-test="auth-brand-panel"
    class="relative hidden flex-col justify-between overflow-hidden border-r border-[#E5E7EB] bg-[#FAF9F6] px-10 py-8 lg:flex xl:px-14 dark:border-border dark:bg-sidebar"
  >
    <div
      class="pointer-events-none absolute inset-0 bg-[radial-gradient(#d6d3d1_1px,transparent_1px)] [background-size:24px_24px] [mask-image:radial-gradient(ellipse_at_center,black_45%,transparent_100%)] opacity-70 dark:opacity-15"
      aria-hidden="true"
    />

    <RouterLink
      to="/"
      data-test="brand-home"
      aria-label="VeriPaper 首页"
      class="relative flex w-fit items-center gap-2.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[#0F4C3A] dark:focus-visible:ring-ring"
    >
      <img src="/logo.svg" alt="" aria-hidden="true" class="size-7" />
      <span class="text-sm font-semibold tracking-tight text-[#111827] dark:text-foreground">
        VeriPaper
      </span>
    </RouterLink>

    <div class="relative my-auto max-w-lg py-6">
      <component
        :is="headingTag"
        class="font-display text-4xl leading-[1.24] font-semibold tracking-tight xl:text-[2.75rem]"
      >
        <span class="block text-[#111827] dark:text-foreground">每一句论断</span>
        <span class="block text-[#0F4C3A] dark:text-primary">皆有出处</span>
      </component>
      <p class="mt-4 max-w-md text-sm leading-7 text-[#4B5563] dark:text-muted-foreground">
        你负责判断，系统负责把每一条引用锚定回原始文献。
      </p>

      <!-- 双层可交互溯源证据卡 -->
      <div
        data-test="login-evidence-proof"
        class="mt-8 overflow-hidden rounded-xl border border-[#E5E7EB] bg-white shadow-[0_12px_32px_-16px_rgba(15,76,58,0.12)] dark:border-border dark:bg-card"
      >
        <div class="p-4 sm:p-5">
          <div class="flex items-center justify-between gap-2 text-[11px] text-[#6B7280] dark:text-muted-foreground">
            <span class="font-medium">综述正文片段 · 点击角标切换溯源</span>
            <span
              data-test="proof-traceable-tag"
              class="inline-flex items-center gap-1 rounded-full border border-[#E5E7EB] bg-[#FAF9F6] px-2 py-0.5 text-[10px] text-[#4B5563] dark:border-border dark:bg-muted dark:text-muted-foreground"
            >
              <ShieldCheck :size="11" aria-hidden="true" />
              证据可回溯
            </span>
          </div>
          <p class="mt-3 font-display text-sm leading-7 text-[#1F2937] dark:text-card-foreground">
            检索增强生成的评估正从单一答案准确率，转向对忠实度、证据完整性与上下文利用效率的综合判断<button
              type="button"
              data-test="login-proof-marker-1"
              :aria-pressed="activeProofIndex === 1"
              class="mx-0.5 inline-flex items-center rounded px-1.5 py-0.2 align-baseline font-sans text-xs font-semibold transition-colors"
              :class="
                activeProofIndex === 1
                  ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                  : 'bg-[#0F4C3A]/10 text-[#0F4C3A] hover:bg-[#0F4C3A]/20 dark:bg-primary/15 dark:text-primary dark:hover:bg-primary/25'
              "
              @click="activeProofIndex = 1"
            >[1]</button>；而在多智能体筛选阶段，结构化审议协议将人工复核工时降低了 68.2%<button
              type="button"
              data-test="login-proof-marker-2"
              :aria-pressed="activeProofIndex === 2"
              class="mx-0.5 inline-flex items-center rounded px-1.5 py-0.2 align-baseline font-sans text-xs font-semibold transition-colors"
              :class="
                activeProofIndex === 2
                  ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                  : 'bg-[#0F4C3A]/10 text-[#0F4C3A] hover:bg-[#0F4C3A]/20 dark:bg-primary/15 dark:text-primary dark:hover:bg-primary/25'
              "
              @click="activeProofIndex = 2"
            >[2]</button>。
          </p>
        </div>

        <div
          data-test="login-proof-footer"
          class="border-t border-[#E5E7EB] bg-[#F9FAFB] px-4 py-3 text-xs dark:border-border dark:bg-muted/40"
        >
          <AnimatePresence mode="popLayout" :initial="false">
            <Motion
              :key="activeProofIndex"
              :initial="{ opacity: 0, y: reduced ? 0 : OFFSET.microY }"
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
            >
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="font-medium text-[#374151] dark:text-foreground">
                  [{{ activeProofIndex }}] {{ activeProof.source }}
                </span>
                <span class="inline-flex items-center gap-1 rounded-full bg-[#0F4C3A]/10 px-2 py-0.5 text-[11px] font-medium text-[#0F4C3A] dark:bg-success/15 dark:text-success">
                  <Check :size="11" :stroke-width="2.4" aria-hidden="true" />
                  {{ activeProof.badge }}
                </span>
              </div>
              <p class="mt-1.5 font-mono text-[10.5px] leading-relaxed break-words text-[#6B7280] dark:text-muted-foreground">
                {{ activeProof.citation }}
              </p>
            </Motion>
          </AnimatePresence>
        </div>
      </div>
    </div>

    <div class="relative flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-neutral-500 dark:text-muted-foreground">
      <template v-for="(item, idx) in TRUST_ITEMS" :key="item">
        <span>{{ item }}</span>
        <span v-if="idx < TRUST_ITEMS.length - 1" aria-hidden="true">·</span>
      </template>
    </div>
  </section>
</template>
