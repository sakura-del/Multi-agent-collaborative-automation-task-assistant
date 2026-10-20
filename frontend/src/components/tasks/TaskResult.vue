<script setup lang="ts">
import { computed, ref } from "vue";
import { Download, FileText } from "lucide-vue-next";

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

// 结果视图：左侧正文（纯文本渲染，不用 v-html），右侧克制的元数据栏
const props = defineProps<{
  taskId: string;
  report: ReviewReport;
  artifacts?: Artifact[];
}>();

const downloading = ref<"markdown" | "docx" | null>(null);
const errorMessage = ref<string | null>(null);

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
  <section class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_16rem]" data-test="task-result">
    <article class="min-w-0">
      <h2 class="mb-2 text-base font-semibold tracking-tight">最终综述</h2>
      <pre
        class="max-h-[70vh] overflow-auto rounded-lg border border-border bg-card p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap"
        data-test="result-markdown"
        >{{ props.report.final_markdown }}</pre
      >
    </article>

    <aside class="flex flex-col gap-4">
      <section>
        <h3 class="mb-1.5 text-sm font-medium">下载</h3>
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

      <section>
        <h3 class="mb-1.5 text-sm font-medium">质量评分</h3>
        <ul class="flex flex-col gap-1 text-sm" data-test="result-scores">
          <li v-for="score in scores" :key="score.key" class="flex justify-between gap-2">
            <span class="text-muted-foreground">{{ score.label }}</span>
            <span class="font-medium tabular-nums">{{ score.percent }}</span>
          </li>
        </ul>
      </section>

      <section v-if="props.report.issues?.length">
        <h3 class="mb-1.5 text-sm font-medium">待改进</h3>
        <ul
          class="list-disc space-y-1 pl-4 text-sm text-muted-foreground"
          data-test="result-issues"
        >
          <li v-for="issue in props.report.issues" :key="issue">{{ issue }}</li>
        </ul>
      </section>

      <section>
        <h3 class="mb-1.5 text-sm font-medium">参考文献</h3>
        <!-- 核验统计 + 逐条结论（逐条徽标由 Panel 渲染，列表项只提供数据） -->
        <ReferenceVerificationPanel :summary="verificationSummary" :entries="bibliography" />
        <ol class="mt-2 flex flex-col gap-1" data-test="result-bibliography">
          <li
            v-for="entry in bibliography"
            :key="entry.index"
            class="flex items-start gap-1.5 text-sm"
            :data-test="`bibliography-item-${entry.index}`"
          >
            <FileText :size="13" class="mt-1 shrink-0 text-muted-foreground" aria-hidden="true" />
            <a
              v-if="entry.url"
              :data-test="`bibliography-link-${entry.index}`"
              :href="entry.url"
              target="_blank"
              rel="noopener noreferrer"
              class="break-words text-primary underline-offset-4 hover:underline"
            >
              {{ entry.title }}
            </a>
            <span v-else class="break-words">{{ entry.title }}</span>
          </li>
        </ol>
      </section>
    </aside>
  </section>
</template>
