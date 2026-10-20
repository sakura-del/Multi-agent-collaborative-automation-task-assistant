<script setup lang="ts">
import { computed, ref } from "vue";
import { CircleCheck, FileInput, Loader2, RotateCcw } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
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
  <section>
    <header class="mb-4">
      <h1 class="text-lg font-semibold tracking-tight">导入参考文献</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        粘贴 BibTeX 或 RIS 文本即可解析预览；支持从 Zotero / EndNote 直接导出。导入仅解析，不会写入引用库。
      </p>
    </header>

    <form
      class="mb-3 flex flex-col gap-3 rounded-lg border border-border bg-card p-4"
      data-test="import-form"
      @submit.prevent="handleImport"
    >
      <div class="grid gap-1.5">
        <Label for="import-content">引用文本</Label>
        <textarea
          id="import-content"
          v-model="content"
          data-test="import-content"
          rows="10"
          class="w-full resize-y rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
          placeholder="粘贴 BibTeX（@article{...}）或 RIS（TY  - JOUR ... ER  - ）"
        />
      </div>
      <div class="flex flex-wrap items-end gap-3">
        <div class="grid gap-1.5">
          <Label for="import-format">格式</Label>
          <select
            id="import-format"
            v-model="format"
            data-test="import-format"
            class="flex h-9 rounded-md border border-input bg-background px-3 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
          >
            <option v-for="option in FORMAT_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </div>
        <Button data-test="import-submit" type="submit" :disabled="importing">
          <Loader2 v-if="importing" class="animate-spin" aria-hidden="true" />
          <FileInput v-else :size="15" aria-hidden="true" />
          <span>{{ importing ? "解析中…" : "解析预览" }}</span>
        </Button>
        <Button v-if="hasResult" variant="ghost" data-test="import-reset" type="button" @click="handleReset">
          <RotateCcw :size="15" aria-hidden="true" />
          <span>重新开始</span>
        </Button>
      </div>
    </form>

    <ErrorBanner :message="error" testid="import-error" />

    <Alert v-if="hasResult" variant="success" class="mb-3" data-test="import-summary">
      <CircleCheck />
      <span>识别为 {{ formatLabel }}，共 {{ result?.count }} 条</span>
    </Alert>

    <EmptyState
      v-if="isEmpty"
      testid="import-empty"
      title="未解析出任何条目"
      description="请检查内容是否为合法 BibTeX / RIS 格式。"
    />

    <ul
      v-if="hasResult && !isEmpty"
      class="space-y-2"
      data-test="import-preview"
    >
      <li
        v-for="(item, index) in result?.items"
        :key="item.id ?? index"
        class="rounded-lg border border-border bg-card px-3 py-2.5"
        :data-test="`import-item-${index}`"
      >
        <div class="text-sm font-medium">{{ item.title || "（无标题）" }}</div>
        <div class="mt-0.5 text-xs text-muted-foreground">{{ authorText(item) }}</div>
        <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="muted">{{ item.type }}</Badge>
          <span v-if="metaText(item)">{{ metaText(item) }}</span>
        </div>
        <div v-if="item.doi" class="mt-1">
          <code class="rounded bg-muted px-1 py-0.5 text-xs text-muted-foreground">
            {{ item.doi }}
          </code>
        </div>
      </li>
    </ul>
  </section>
</template>
