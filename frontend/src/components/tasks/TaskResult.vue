<script setup lang="ts">
import { computed, ref } from "vue";
import { Download, FileText } from "lucide-vue-next";

import { taskApi } from "@/api/tasks";
import type { Artifact, ReviewReport } from "@/api/types";

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

interface BibliographyEntry {
  index: number;
  title: string;
  url: string | null;
}

/** 只允许 http/https 链接，其余退化为纯文本（避免 javascript:/data: 注入） */
function safeUrl(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  return /^https?:\/\//i.test(value) ? value : null;
}

const bibliography = computed<BibliographyEntry[]>(() =>
  (props.report.bibliography ?? []).map((item, position) => {
    const record = item as Record<string, unknown>;
    const index = Number(record.index ?? position + 1);
    return {
      index,
      title: String(record.title ?? record.source_id ?? `文献 ${index}`),
      url: safeUrl(record.source_url ?? record.url),
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
  <section class="result" data-test="task-result">
    <article class="result__document">
      <h2>最终综述</h2>
      <pre class="result__markdown" data-test="result-markdown">{{ props.report.final_markdown }}</pre>
    </article>

    <aside class="result__sidebar">
      <section>
        <h3>下载</h3>
        <div class="result__downloads">
          <button
            class="btn"
            data-test="download-markdown"
            type="button"
            :disabled="!findArtifact('markdown') || downloading !== null"
            @click="download('markdown')"
          >
            <Download :size="15" /> Markdown
          </button>
          <button
            class="btn"
            data-test="download-docx"
            type="button"
            :disabled="!findArtifact('docx') || downloading !== null"
            @click="download('docx')"
          >
            <Download :size="15" /> DOCX
          </button>
        </div>
        <p v-if="errorMessage" class="banner banner--error">{{ errorMessage }}</p>
      </section>

      <section>
        <h3>质量评分</h3>
        <ul class="result__scores" data-test="result-scores">
          <li v-for="score in scores" :key="score.key">
            <span>{{ score.label }}</span>
            <span class="result__score">{{ score.percent }}</span>
          </li>
        </ul>
      </section>

      <section v-if="props.report.issues?.length">
        <h3>待改进</h3>
        <ul class="result__issues" data-test="result-issues">
          <li v-for="issue in props.report.issues" :key="issue">{{ issue }}</li>
        </ul>
      </section>

      <section>
        <h3>参考文献</h3>
        <ol class="result__bibliography">
          <li
            v-for="entry in bibliography"
            :key="entry.index"
            :data-test="`bibliography-item-${entry.index}`"
          >
            <FileText :size="13" />
            <a
              v-if="entry.url"
              :data-test="`bibliography-link-${entry.index}`"
              :href="entry.url"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ entry.title }}
            </a>
            <span v-else>{{ entry.title }}</span>
          </li>
        </ol>
      </section>
    </aside>
  </section>
</template>

<style scoped>
.result {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: 1.5rem;
  align-items: start;
}

@media (max-width: 900px) {
  .result {
    grid-template-columns: 1fr;
  }
}

.result__document h2,
.result__sidebar h3 {
  font-size: 0.95rem;
  margin-bottom: 0.4rem;
}

.result__markdown {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 1rem;
  font-size: 0.88rem;
  line-height: 1.7;
}

.result__sidebar {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  font-size: 0.85rem;
}

.result__downloads {
  display: flex;
  gap: 0.5rem;
}

.result__scores,
.result__issues,
.result__bibliography {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.result__scores li {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid #f1f5f9;
  padding-bottom: 0.15rem;
}

.result__score {
  font-variant-numeric: tabular-nums;
  color: #475569;
}

.result__issues {
  color: var(--color-warning);
}

.result__bibliography li {
  display: flex;
  gap: 0.35rem;
  align-items: flex-start;
  color: #475569;
}
</style>
