<script setup lang="ts">
import { computed, ref } from "vue";
import { Download } from "lucide-vue-next";

import { taskApi } from "@/api/tasks";
import ReferenceVerificationPanel from "@/components/tasks/ReferenceVerificationPanel.vue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type {
  Artifact,
  BibliographyEntry,
  ReferenceVerificationSummary,
  ReviewReport,
  VerificationStatus,
} from "@/api/types";
import { renderMarkdown } from "@/utils/markdown";

// 结果视图：左侧正文（Markdown 渲染成排版 + 可切源码），右侧克制的元数据栏。
//
// ## 为什么不再用纯文本
//
// 原先用 `<pre>{{ final_markdown }}</pre>`，注释写的原因是「不用 v-html」
// （安全考虑）。但用户看到的是一篇**带井号和星号的综述** —— 这是核心产出页，
// 可读性影响最大。
//
// 现在改为 marked + DOMPurify 两段式（见 utils/markdown.ts）：
// 渲染成排版，同时消毒；环境不支持可靠消毒时降级为转义纯文本。
// 这样既保住安全底线，又不是把 Markdown 原样摆在用户面前。
const props = defineProps<{
  taskId: string;
  report: ReviewReport;
  artifacts?: Artifact[];
}>();

const downloading = ref<"markdown" | "docx" | null>(null);
const errorMessage = ref<string | null>(null);
/** 源码视图开关（默认渲染视图）。 */
const showSource = ref(false);

/** 消毒后的正文 HTML（`v-html` 用；安全性由 renderMarkdown 保证）。 */
const renderedMarkdown = computed(() => renderMarkdown(props.report.final_markdown));

const SCORE_LABELS: Record<string, string> = {
  citation_traceability: "引用可追溯性",
  quality: "质量",
  coherence: "连贯性",
  coverage: "覆盖面",
};

const scores = computed(() =>
  Object.entries(props.report.scores ?? {}).map(([key, value]) => ({
    key,
    label: SCORE_LABELS[key] ?? key,
    percent: Math.round(Number(value) * 100),
  }))
);

/** 只允许 http/https 链接，其余退化为纯文本（避免 javascript:/data: 注入） */
function safeUrl(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  return /^https?:\/\//i.test(value) ? value : null;
}

// ===== 引用核验（Issue #3 遗留项）：stats 是 Record<string, unknown>，必须安全解析 =====

const VERIFICATION_STATUSES: readonly VerificationStatus[] = [
  "verified",
  "metadata_mismatch",
  "not_found",
  "unverified",
];

const SUMMARY_COUNT_KEYS = [
  "total",
  "verified",
  "metadata_mismatch",
  "not_found",
  "unverified",
  "cached_hits",
  "dropped",
] as const;

/** 非法值 → null（不渲染徽标，也不猜测结论） */
function parseVerificationStatus(value: unknown): VerificationStatus | null {
  return typeof value === "string" && (VERIFICATION_STATUSES as readonly string[]).includes(value)
    ? (value as VerificationStatus)
    : null;
}

/**
 * 安全解析 `stats.reference_verification`：缺键 / 类型不符 → null。
 *
 * 不抛错、也不臆造 0 值——离线任务默认不核验，展示层必须能区分「未核验」与「核验 0 条」。
 */
function parseVerificationSummary(value: unknown): ReferenceVerificationSummary | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const counts: Record<string, number> = {};
  for (const key of SUMMARY_COUNT_KEYS) {
    const raw = record[key];
    if (typeof raw !== "number" || !Number.isFinite(raw)) {
      return null;
    }
    counts[key] = raw;
  }
  if (typeof record.intercepted !== "boolean") {
    return null;
  }
  return {
    total: counts.total,
    verified: counts.verified,
    metadata_mismatch: counts.metadata_mismatch,
    not_found: counts.not_found,
    unverified: counts.unverified,
    cached_hits: counts.cached_hits,
    dropped: counts.dropped,
    intercepted: record.intercepted,
  };
}

const verificationSummary = computed<ReferenceVerificationSummary | null>(() =>
  parseVerificationSummary(props.report.stats?.reference_verification)
);

const bibliography = computed<BibliographyEntry[]>(() =>
  (props.report.bibliography ?? []).map((item, position) => {
    const record = item as Record<string, unknown>;
    const index = Number(record.index ?? position + 1);
    return {
      index,
      title: String(record.title ?? record.source_id ?? `文献 ${index}`),
      url: safeUrl(record.source_url ?? record.url),
      verification_status: parseVerificationStatus(record.verification_status),
    };
  })
);

function findArtifact(kind: "markdown" | "docx"): Artifact | undefined {
  return (props.artifacts ?? []).find((item) => item.kind === kind);
}

async function download(kind: "markdown" | "docx") {
  const artifact = findArtifact(kind);
  if (!artifact) {
    return;
  }
  downloading.value = kind;
  errorMessage.value = null;
  let objectUrl: string | null = null;
  try {
    const { blob, filename } = await taskApi.downloadArtifact(props.taskId, artifact.artifact_id);
    objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename ?? artifact.download_name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } catch (error) {
    errorMessage.value = (error as Error).message || "下载失败";
  } finally {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl); // 触发后立即释放，避免内存泄漏
    }
    downloading.value = null;
  }
}
</script>

<template>
  <section class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_17rem]" data-test="task-result">
    <!-- 正文是核心交付物：纸页式排版，不再把长文关进 70vh 的内滚动卡片。 -->
    <article class="min-w-0" data-test="result-paper">
      <div class="mb-3 flex items-center justify-between gap-3">
        <div>
          <p class="text-[9px] font-semibold tracking-[0.16em] text-primary uppercase">Final review</p>
          <h2 class="mt-1 text-base font-semibold tracking-tight">最终综述</h2>
        </div>
        <!-- 源码/预览切换仍保留，核对引用标记与复制时可查看原始 Markdown。 -->
        <button
          type="button"
          data-test="toggle-md-source"
          class="rounded px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          @click="showSource = !showSource"
        >
          {{ showSource ? "渲染视图" : "源码" }}
        </button>
      </div>

      <!-- renderMarkdown 继续负责 marked + DOMPurify 两段式安全链，不在本层另做 HTML 拼接。 -->
      <div
        v-if="!showSource"
        data-test="result-markdown"
        class="md-body max-h-none overflow-visible rounded-lg border border-border bg-card px-5 py-6 font-display sm:px-8 sm:py-8 lg:px-10"
        v-html="renderedMarkdown"
      />

      <pre
        v-else
        data-test="result-markdown-source"
        class="max-h-[70vh] overflow-auto rounded-lg border border-border bg-card p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap"
        >{{ props.report.final_markdown }}</pre
      >
    </article>

    <!-- 证据栏始终在正文之后：桌面并列，移动端正文优先。所有数字都来自真实报告。 -->
    <aside class="flex flex-col gap-5" data-test="evidence-rail">
      <section class="border-b border-border pb-5">
        <ReferenceVerificationPanel :summary="verificationSummary" :entries="bibliography" />
      </section>

      <section v-if="scores.length" class="border-b border-border pb-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h3 class="text-sm font-medium">质量检查</h3>
          <span class="text-[9px] tracking-wider text-muted-foreground">SCORE / 100</span>
        </div>
        <ul class="flex flex-col gap-2.5" data-test="result-scores">
          <li
            v-for="score in scores"
            :key="score.key"
            class="grid grid-cols-[5rem_1fr_2rem] items-center gap-2 text-xs"
          >
            <span class="text-muted-foreground">{{ score.label }}</span>
            <span
              class="h-1 overflow-hidden rounded-full bg-muted"
              role="meter"
              :aria-label="score.label"
              :aria-valuenow="score.percent"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <span
                class="block h-full rounded-full bg-primary"
                :style="{ width: `${score.percent}%` }"
              />
            </span>
            <span class="text-right font-medium tabular-nums">{{ score.percent }}</span>
          </li>
        </ul>
      </section>

      <section class="border-b border-border pb-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h3 class="text-sm font-medium">参考文献</h3>
          <span class="text-[9px] tracking-wider text-muted-foreground">
            {{ bibliography.length }} ENTRIES
          </span>
        </div>
        <ol class="flex flex-col gap-3" data-test="result-bibliography">
          <li
            v-for="entry in bibliography"
            :key="entry.index"
            class="flex items-start gap-2 text-sm"
            :data-test="`bibliography-item-${entry.index}`"
          >
            <span class="mt-0.5 w-5 shrink-0 font-mono text-[10px] text-primary">
              {{ String(entry.index).padStart(2, "0") }}
            </span>
            <a
              v-if="entry.url"
              :data-test="`bibliography-link-${entry.index}`"
              :href="entry.url"
              target="_blank"
              rel="noopener noreferrer"
              class="min-w-0 font-display text-xs leading-5 break-words text-foreground underline-offset-4 hover:text-primary hover:underline"
            >
              {{ entry.title }}
            </a>
            <span v-else class="min-w-0 font-display text-xs leading-5 break-words">{{ entry.title }}</span>
          </li>
        </ol>
      </section>

      <section v-if="props.report.issues?.length" class="border-b border-border pb-5">
        <h3 class="mb-2 text-sm font-medium">待改进</h3>
        <ul class="list-disc space-y-1 pl-4 text-xs leading-5 text-muted-foreground" data-test="result-issues">
          <li v-for="issue in props.report.issues" :key="issue">{{ issue }}</li>
        </ul>
      </section>

      <section>
        <h3 class="mb-2 text-sm font-medium">导出与交付</h3>
        <div class="flex flex-wrap gap-1.5">
          <Button
            variant="outline"
            size="sm"
            data-test="download-markdown"
            type="button"
            :disabled="!findArtifact('markdown') || downloading !== null"
            @click="download('markdown')"
          >
            <Download :size="15" aria-hidden="true" /> Markdown
          </Button>
          <Button
            variant="outline"
            size="sm"
            data-test="download-docx"
            type="button"
            :disabled="!findArtifact('docx') || downloading !== null"
            @click="download('docx')"
          >
            <Download :size="15" aria-hidden="true" /> DOCX
          </Button>
        </div>
        <Alert v-if="errorMessage" variant="destructive" class="mt-2">
          <span>{{ errorMessage }}</span>
        </Alert>
      </section>
    </aside>
  </section>
</template>
