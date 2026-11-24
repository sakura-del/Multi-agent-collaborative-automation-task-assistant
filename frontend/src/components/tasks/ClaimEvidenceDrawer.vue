<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { X } from "lucide-vue-next";

import type { BibliographyEntry, ClaimCheckResult } from "@/api/types";
import { highlightQuote } from "@/utils/claim-highlight";
import {
  CLAIM_VERDICT_LABELS,
  CLAIM_VERDICT_TONES,
  claimSeverity,
  formatEvidenceLocator,
} from "@/utils/claim-verdict";
import { QUALITY_FLAG_LABELS, parseQualityFlags } from "@/utils/citation-quality";

// 点击正文 [En] 打开的「来源核验」抽屉（Issue #31 Phase 2）。
//
// 只读、不改正文：用户在这里看到「这句话被判定成什么 + 依据是哪段原文」，
// 再回到正文自己改。判定结论缺失时明确说「尚未核验」，不拿空格子冒充通过。
const props = defineProps<{
  open: boolean;
  index: number | null;
  entry: BibliographyEntry | null;
  claims: ClaimCheckResult[];
  /** 证据矩阵格子打开时的自定义标题（不传时沿用「来源核验 · [E#]」） */
  title?: string | null;
  subtitle?: string | null;
}>();

const emit = defineEmits<{ close: [] }>();

const closeButton = ref<HTMLButtonElement | null>(null);

const sortedClaims = computed(() =>
  [...props.claims].sort((a, b) => claimSeverity(a.verdict) - claimSeverity(b.verdict)),
);

const qualityFlags = computed(() => parseQualityFlags(props.entry?.quality_flags));

const safeUrl = computed(() => {
  const url = props.entry?.url ?? null;
  return url && /^https?:\/\//i.test(url) ? url : null;
});

/** 每条论断的原文高亮结果：quote 不在片段里时不标红，并给出明确提示。 */
const renderedClaims = computed(() =>
  sortedClaims.value.map((claim) => {
    const passage = claim.evidence_passage ?? "";
    const highlighted = passage
      ? highlightQuote(passage, claim.evidence_quote)
      : { html: "", matched: false };
    return {
      claim,
      label: CLAIM_VERDICT_LABELS[claim.verdict],
      tone: CLAIM_VERDICT_TONES[claim.verdict],
      confidence: Math.round(claim.confidence * 100),
      passageHtml: highlighted.html,
      quoteMissing: Boolean(claim.evidence_quote) && !highlighted.matched,
      locatorLabel: formatEvidenceLocator(claim.locator),
    };
  }),
);

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && props.open) {
    event.preventDefault();
    emit("close");
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      return;
    }
    await nextTick();
    closeButton.value?.focus();
  },
);

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex justify-end bg-black/30"
      data-test="claim-drawer"
      @click.self="emit('close')"
    >
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="来源核验"
        class="flex h-full w-[26rem] max-w-full flex-col overflow-y-auto border-l border-border bg-card shadow-xl"
        @click.stop
      >
        <header class="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div class="min-w-0">
            <p class="text-[9px] font-semibold tracking-[0.16em] text-primary uppercase">
              Source check
            </p>
            <h2 class="mt-1 truncate text-sm font-semibold">
              {{ title || `来源核验 · [E${index ?? "?"}]` }}
            </h2>
          </div>
          <button
            ref="closeButton"
            type="button"
            class="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            data-test="claim-drawer-close"
            aria-label="关闭来源核验"
            @click="emit('close')"
          >
            <X class="size-4" />
          </button>
        </header>

        <div class="flex flex-col gap-4 px-4 py-4">
          <!-- 文献信息 + 来源质量 -->
          <section class="flex flex-col gap-2">
            <p class="text-sm font-medium leading-snug" data-test="claim-source-title">
              {{
                subtitle ||
                entry?.title ||
                "未找到这条引用的参考文献条目"
              }}
            </p>
            <a
              v-if="safeUrl"
              :href="safeUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="truncate text-xs text-primary underline-offset-2 hover:underline"
            >
              {{ safeUrl }}
            </a>
            <div v-if="qualityFlags.length" class="flex flex-wrap gap-1.5">
              <span
                v-for="flag in qualityFlags"
                :key="flag"
                class="rounded-full border px-2 py-0.5 text-[10px]"
                :class="
                  flag === 'retracted'
                    ? 'border-destructive/30 bg-destructive/10 text-destructive'
                    : flag === 'preprint'
                      ? 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400'
                      : 'border-border bg-muted text-muted-foreground'
                "
              >
                {{ QUALITY_FLAG_LABELS[flag] }}
              </span>
            </div>
            <p
              v-if="entry?.retraction_reason"
              class="rounded border border-destructive/30 bg-destructive/5 px-2 py-1 text-[11px] leading-relaxed text-destructive"
            >
              撤稿原因：{{ entry.retraction_reason }}
            </p>
          </section>

          <!-- 论断核验 -->
          <section class="flex flex-col gap-3">
            <h3 class="text-xs font-semibold tracking-wide text-muted-foreground">
              论断核验（{{ renderedClaims.length }}）
            </h3>

            <p
              v-if="!renderedClaims.length"
              class="rounded border border-dashed border-border px-3 py-4 text-xs leading-relaxed text-muted-foreground"
              data-test="claim-empty"
            >
              这条引用还没有论断级核验结论（旧任务，或生成时未启用 LLM 核验）。这不代表它已被验证通过。
            </p>

            <article
              v-for="item in renderedClaims"
              :key="item.claim.claim_id"
              class="flex flex-col gap-2 rounded-lg border border-border p-3"
              data-test="claim-card"
            >
              <div class="flex items-center justify-between gap-2">
                <span
                  class="rounded-full border px-2 py-0.5 text-[10px] font-medium"
                  :class="item.tone"
                  data-test="claim-verdict"
                >
                  {{ item.label }}
                </span>
                <span class="text-[10px] text-muted-foreground">把握 {{ item.confidence }}%</span>
              </div>

              <p class="text-xs leading-relaxed text-foreground" data-test="claim-sentence">
                {{ item.claim.sentence_text }}
              </p>

              <p
                v-if="item.claim.evidence_scope === 'abstract_only'"
                class="text-[10px] text-amber-700 dark:text-amber-400"
              >
                仅有题录/摘要，未读全文：摘要未提及的细节只能判「无法核验」。
              </p>

              <p class="text-[11px] leading-relaxed text-muted-foreground" data-test="claim-rationale">
                {{ item.claim.rationale || "模型未给出理由" }}
              </p>

              <p
                v-if="item.claim.suggested_fix"
                class="rounded bg-muted px-2 py-1.5 text-[11px] leading-relaxed text-foreground"
                data-test="claim-fix"
              >
                建议：{{ item.claim.suggested_fix }}
              </p>

              <p
                v-if="item.locatorLabel"
                class="inline-flex w-fit items-center rounded border border-border bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                data-test="claim-locator"
              >
                出处定位：{{ item.locatorLabel }}
              </p>

              <div v-if="item.passageHtml" class="flex flex-col gap-1">
                <p class="text-[10px] font-medium tracking-wide text-muted-foreground">原文片段</p>
                <!-- highlightQuote 内部转义全部文本，只插入固定 class 的 <mark> -->
                <p
                  class="rounded bg-muted/60 px-2 py-1.5 text-[11px] leading-relaxed"
                  data-test="claim-passage"
                  v-html="item.passageHtml"
                />
                <p v-if="item.quoteMissing" class="text-[10px] text-muted-foreground">
                  未在片段中定位到引文（可能被模型改写，未做高亮）。
                </p>
              </div>
            </article>
          </section>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
