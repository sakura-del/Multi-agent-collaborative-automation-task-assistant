<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { CircleCheck, FileInput, Loader2, RotateCcw, Save } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { importReferences } from "@/api/references";
import { saveLibraryItems } from "@/api/reference-library";
import type {
  ImportedReferenceItem,
  LibrarySaveResponse,
  ReferenceImportFormat,
  ReferenceImportResponse,
} from "@/api/types";
import { toCslItemPayloads } from "@/utils/reference-payload";

// 参考文献导入页（Phase 1 P1-E · 2026-10-26 补上真实出口）。
//
// ## 这页原本的问题（取证得到）
//
// 解析完全正常（BibTeX / RIS 结果一致、错误定位到具体条目），但解析完
// **什么也做不了**：可点元素只有「解析预览」和「重新开始」，后端没有引用库表，
// 任务文献只来自检索源。用户把 Zotero 导出的 .bib 粘进来，看到结果，然后没有下文。
//
// ## 现在的链路
//
// 粘贴 → 解析预览 → **保存到引用库** → 创建任务时选「我的引用库」→ 任务用这些文献
//
// 保存是**部分成功**的：能进的进，不能进的带原因逐条展示
// （缺年份/缺 URL 的条目不能当文献源用，后端会拒）。
const router = useRouter();

const content = ref("");
const format = ref<ReferenceImportFormat>("auto");
const result = ref<ReferenceImportResponse | null>(null);
const error = ref<string | null>(null);
const importing = ref(false);
const saving = ref(false);
const saveError = ref<string | null>(null);
const saveResult = ref<LibrarySaveResponse | null>(null);

const FORMAT_OPTIONS: { value: ReferenceImportFormat; label: string }[] = [
  { value: "auto", label: "自动识别" },
  { value: "bibtex", label: "BibTeX" },
  { value: "ris", label: "RIS" },
];

const formatLabel = computed(() => (result.value?.format === "ris" ? "RIS" : "BibTeX"));
const hasResult = computed(() => result.value !== null);
const isEmpty = computed(() => result.value !== null && result.value.count === 0);

/** 可保存的条目数（无题名的会被过滤，CslItem.title 必填）。 */
const savableCount = computed(() =>
  result.value ? toCslItemPayloads(result.value.items).length : 0
);

const saveSummary = computed(() => {
  const value = saveResult.value;
  if (!value) {
    return "";
  }
  const parts = [`新增 ${value.created} 条`];
  if (value.skipped > 0) {
    parts.push(`已存在 ${value.skipped} 条`);
  }
  if (value.rejected.length > 0) {
    parts.push(`未能入库 ${value.rejected.length} 条`);
  }
  return parts.join(" · ");
});

async function handleImport() {
  error.value = null;
  saveError.value = null;
  saveResult.value = null;
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

/**
 * 保存到引用库。
 *
 * 只发**用户已经看过并确认**的结构化条目 —— 不再传原始文本重新解析，
 * 否则可能出现「看到的和存进去的不一致」。
 */
async function handleSave() {
  if (!result.value) {
    return;
  }
  saveError.value = null;
  saveResult.value = null;
  const payloads = toCslItemPayloads(result.value.items);
  if (payloads.length === 0) {
    saveError.value = "没有可保存的条目（缺题名的条目无法入库）";
    return;
  }
  saving.value = true;
  try {
    saveResult.value = await saveLibraryItems(payloads);
  } catch (err) {
    saveError.value = (err as Error).message || "保存失败";
  } finally {
    saving.value = false;
  }
}

function goToLibrary() {
  void router.push("/references/library");
}

function handleReset() {
  content.value = "";
  result.value = null;
  error.value = null;
  saveError.value = null;
  saveResult.value = null;
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
        粘贴 BibTeX 或 RIS 文本即可解析预览；支持从 Zotero / EndNote 直接导出。
        解析后可以<strong>保存到引用库</strong>，创建任务时选择「我的引用库」就会用这些文献。
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

    <!-- 保存到引用库：这是本页的真实出口（此前解析完什么也做不了） -->
    <div
      v-if="hasResult && !isEmpty"
      class="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2.5"
    >
      <Button data-test="import-save" :disabled="saving || savableCount === 0" @click="handleSave">
        <Loader2 v-if="saving" class="animate-spin" aria-hidden="true" />
        <Save v-else :size="15" aria-hidden="true" />
        <span>{{ saving ? "保存中…" : `保存 ${savableCount} 条到引用库` }}</span>
      </Button>
      <span class="text-xs text-muted-foreground">
        保存后可在创建任务时选择「我的引用库」，任务将只用这些文献、不外呼检索。
      </span>
    </div>

    <ErrorBanner :message="saveError" testid="import-save-error" />

    <Alert v-if="saveResult" variant="muted" class="mb-3" data-test="import-save-result">
      <CircleCheck />
      <div class="grid gap-1">
        <span>{{ saveSummary }}</span>
        <!-- 逐条拒绝原因必须展示：用户要知道哪些没进去、为什么 -->
        <ul v-if="saveResult.rejected.length" class="ml-4 list-disc text-xs">
          <li v-for="item in saveResult.rejected" :key="item.ref_id">
            {{ item.title || item.ref_id }}：{{ item.reason }}
          </li>
        </ul>
        <button
          type="button"
          data-test="import-goto-library"
          class="w-fit text-xs font-medium text-primary underline-offset-2 hover:underline"
          @click="goToLibrary"
        >
          去引用库查看 →
        </button>
      </div>
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
