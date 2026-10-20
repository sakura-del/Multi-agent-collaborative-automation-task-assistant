<script setup lang="ts">
import { computed, ref } from "vue";
import { FileInput, RotateCcw } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { importReferences } from "@/api/references";
import type {
  ImportedReferenceItem,
  ReferenceImportFormat,
  ReferenceImportResponse,
} from "@/api/types";

// 参考文献导入页（Phase 1 P1-E）：粘贴 BibTeX/RIS → 预览条目 → 用户确认后自行保存。
// 后端**只解析不落库**，所以这里不提供"保存"按钮（保存路径属后续工作）。
const content = ref("");
const format = ref<ReferenceImportFormat>("auto");
const result = ref<ReferenceImportResponse | null>(null);
const error = ref<string | null>(null);
const importing = ref(false);

const FORMAT_OPTIONS: { value: ReferenceImportFormat; label: string }[] = [
  { value: "auto", label: "自动识别" },
  { value: "bibtex", label: "BibTeX" },
  { value: "ris", label: "RIS" },
];

const formatLabel = computed(() => (result.value?.format === "ris" ? "RIS" : "BibTeX"));
const hasResult = computed(() => result.value !== null);
const isEmpty = computed(() => result.value !== null && result.value.count === 0);

async function handleImport() {
  error.value = null;
  if (!content.value.trim()) {
    error.value = "请先粘贴 BibTeX 或 RIS 内容";
    return;
  }
  importing.value = true;
  try {
    result.value = await importReferences({ content: content.value, format: format.value });
  } catch (err) {
    // 后端 400 的 detail（带定位信息）在这里展示
    error.value = (err as Error).message || "导入失败";
    result.value = null;
  } finally {
    importing.value = false;
  }
}

function handleReset() {
  content.value = "";
  result.value = null;
  error.value = null;
}

function authorText(item: ImportedReferenceItem): string {
  return item.authors.length ? item.authors.join("；") : "（无作者）";
}

function metaText(item: ImportedReferenceItem): string {
  const parts: string[] = [];
  if (item.container_title) {
    parts.push(item.container_title);
  }
  if (item.year) {
    parts.push(String(item.year));
  }
  if (item.volume) {
    parts.push(`第 ${item.volume} 卷`);
  }
  if (item.issue) {
    parts.push(`第 ${item.issue} 期`);
  }
  if (item.page) {
    parts.push(`页 ${item.page}`);
  }
  return parts.join(" · ");
}
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>导入参考文献</h1>
      <p class="page__hint">
        粘贴 BibTeX 或 RIS 文本即可解析预览；支持从 Zotero / EndNote 直接导出。导入仅解析，不会写入引用库。
      </p>
    </header>

    <form class="import" data-test="import-form" @submit.prevent="handleImport">
      <label class="import__field">
        <span>引用文本</span>
        <textarea
          v-model="content"
          data-test="import-content"
          rows="10"
          placeholder="粘贴 BibTeX（@article{...}）或 RIS（TY  - JOUR ... ER  - ）"
        />
      </label>
      <div class="import__row">
        <label class="import__field import__field--inline">
          <span>格式</span>
          <select v-model="format" data-test="import-format">
            <option v-for="option in FORMAT_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <button
          class="btn btn--primary"
          data-test="import-submit"
          type="submit"
          :disabled="importing"
        >
          <FileInput :size="15" />
          <span>{{ importing ? "解析中…" : "解析预览" }}</span>
        </button>
        <button
          v-if="hasResult"
          class="btn btn--ghost"
          data-test="import-reset"
          type="button"
          @click="handleReset"
        >
          <RotateCcw :size="15" />
          <span>重新开始</span>
        </button>
      </div>
    </form>

    <ErrorBanner :message="error" testid="import-error" />

    <p v-if="hasResult" class="banner banner--ok" data-test="import-summary">
      识别为 {{ formatLabel }}，共 {{ result?.count }} 条
    </p>

    <p v-if="isEmpty" class="page__hint" data-test="import-empty">
      未解析出任何条目——请检查内容是否为合法 BibTeX / RIS。
    </p>

    <ul v-if="hasResult && !isEmpty" class="preview" data-test="import-preview">
      <li
        v-for="(item, index) in result?.items"
        :key="item.id ?? index"
        class="preview__row"
        :data-test="`import-item-${index}`"
      >
        <div class="preview__title">{{ item.title || "（无标题）" }}</div>
        <div class="preview__authors">{{ authorText(item) }}</div>
        <div class="preview__meta">
          <span class="badge">{{ item.type }}</span>
          <span v-if="metaText(item)">{{ metaText(item) }}</span>
        </div>
        <div v-if="item.doi" class="preview__doi">
          <code>{{ item.doi }}</code>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.page__header {
  margin-bottom: 1rem;
}

.import {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.import__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
}

.import__field textarea {
  padding: 0.5rem 0.625rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.82rem;
  resize: vertical;
}

.import__row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.75rem;
}

.import__field--inline {
  flex-direction: row;
  align-items: center;
  gap: 0.4rem;
}

.import__field select {
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: 0.85rem;
}

.banner--ok {
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  background: #ecfdf5;
  color: #047857;
  font-size: 0.88rem;
}

.preview {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.preview__row {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.625rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.preview__title {
  font-size: 0.92rem;
  font-weight: 500;
}

.preview__authors {
  font-size: 0.82rem;
  color: #374151;
}

.preview__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
  color: #6b7280;
}

.preview__doi code {
  font-size: 0.75rem;
  color: #6b7280;
}

.badge {
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.72rem;
  white-space: nowrap;
}
</style>
