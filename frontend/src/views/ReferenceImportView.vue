<script setup lang="ts">
import BePromptInput from "@/components/motion/BePromptInput.vue";
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { Motion, useReducedMotion } from "motion-v";
import { CircleCheck, FileInput, FileUp, Loader2, RotateCcw, Save } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { Alert } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { TRANSITION_ENTER, TRANSITION_MICRO } from "@/lib/ease";
import { toastSuccess } from "@/lib/toast";
import { importReferences } from "@/api/references";
import { saveLibraryItems } from "@/api/reference-library";
import type {
  ImportedReferenceItem,
  LibrarySaveResponse,
  ReferenceImportFormat,
  ReferenceImportResponse,
} from "@/api/types";
import { toCslItemPayloads } from "@/utils/reference-payload";
import { MAX_IMPORT_FILE_BYTES, decodeReferenceFile } from "@/utils/reference-file";

// 参考文献导入页（Phase 1 P1-E · 2026-10-26 补上真实出口）。
//
// ## 这页原本的问题（取证得到）
//
// 解析完全正常（BibTeX / RIS 结果一致、错误定位到具体条目），但解析完
// **什么也做不了**：可点元素只有「解析预览」和「重新开始」，后端没有引用库表，
// 任务文献只来自检索源。用户把 Zotero 导出的 .bib 粘进来，看到结果，然后没有下文。
//
// ## 现在的链路（Issue #67 Step 1 扩到 NoteExpress / 本地文件）
//
// 粘贴或选择本地文件（.bib / .ris / .net / .txt） → 解析预览
// → **保存到引用库** → 创建任务时选「我的引用库」→ 任务用这些文献
//
// 本地文件优先按 UTF-8 严格解码，失败自动回退 GB18030 ——
// 知网/维普/万方导出的 .net 文件在 Windows 上常是 GBK 编码，
// 只按 UTF-8 解码会得到一堆替换字符（`�`），用户会以为文件坏了。
const router = useRouter();
const reduced = useReducedMotion();

const content = ref("");
const format = ref<ReferenceImportFormat>("auto");
const result = ref<ReferenceImportResponse | null>(null);
const error = ref<string | null>(null);
const importing = ref(false);
const saving = ref(false);
const saveError = ref<string | null>(null);
const saveResult = ref<LibrarySaveResponse | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const pickedFileName = ref<string | null>(null);

const FORMAT_OPTIONS: { value: ReferenceImportFormat; label: string }[] = [
  { value: "auto", label: "自动识别" },
  { value: "bibtex", label: "BibTeX" },
  { value: "ris", label: "RIS" },
  { value: "noteexpress", label: "NoteExpress" },
];

const FORMAT_LABELS: Record<Exclude<ReferenceImportFormat, "auto">, string> = {
  bibtex: "BibTeX",
  ris: "RIS",
  noteexpress: "NoteExpress",
};

const formatLabel = computed(() =>
  result.value ? FORMAT_LABELS[result.value.format] : "BibTeX"
);
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
  // `merged` 与 `skipped` 是两种不同的去重，分开说：
  // 「合并」意味着库里已有同一篇文献（不同写法），用户可能需要去引用库核对；
  // 「已存在」意味着完全重复，用户不需要做任何事。
  if (value.merged > 0) {
    parts.push(`合并 ${value.merged} 条（库中已有同一篇）`);
  }
  if (value.skipped > 0) {
    parts.push(`已存在 ${value.skipped} 条`);
  }
  if (value.rejected.length > 0) {
    parts.push(`未能入库 ${value.rejected.length} 条`);
  }
  return parts.join(" · ");
});

/** 单文件上限常量在 `utils/reference-file.ts` 里（与解码逻辑同处，便于单测）。 */
function triggerFilePick() {
  fileInput.value?.click();
}

async function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) {
    return;
  }
  if (file.size > MAX_IMPORT_FILE_BYTES) {
    error.value = "文件过大（上限 5 MB），题录文件通常只有几十 KB，请确认选对了文件";
    return;
  }
  try {
    content.value = decodeReferenceFile(await file.arrayBuffer());
    pickedFileName.value = file.name;
    error.value = null;
    result.value = null;
    saveError.value = null;
    saveResult.value = null;
  } catch {
    error.value = "读取文件失败，请确认文件未损坏，或直接粘贴文本内容";
  }
}

async function handleImport() {
  error.value = null;
  saveError.value = null;
  saveResult.value = null;
  if (!content.value.trim()) {
    error.value = "请先选择文件或粘贴 BibTeX / RIS / NoteExpress 内容";
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
    toastSuccess("已保存到引用库");
  } catch (err) {
    saveError.value = (err as Error).message || "保存失败";
  } finally {
    saving.value = false;
  }
}

function goToLibrary() {
  void router.push("/references/library");
}

/** 保存后回建任务页：那里会自动展开表单并恢复未提交的草稿（用户 m00465） */
function goToCreateTask() {
  void router.push("/tasks");
}

function handleReset() {
  content.value = "";
  result.value = null;
  error.value = null;
  saveError.value = null;
  saveResult.value = null;
  pickedFileName.value = null;
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
  <section class="min-w-0">
    <PageHeader
      eyebrow="导入文献"
      title="导入参考文献"
      description="粘贴 BibTeX / RIS / NoteExpress 文本，或选择知识库/维普/万方导出的 .net / .txt 文件；支持从 Zotero / EndNote / 知网 / 维普 / 万方导出。解析后可以保存到引用库，创建任务时选择「我的引用库」就会用这些文献。"
    />

    <Card as="form" class="mb-3 min-w-0 gap-3 p-4" data-test="import-form" @submit.prevent="handleImport">
      <div class="grid min-w-0 gap-1.5">
        <Label for="import-content">引用文本</Label>
        <BePromptInput
          id="import-content"
          v-model="content"
          data-test="import-content"
          placeholder="粘贴 BibTeX（@article{...}）、RIS（TY  - JOUR ... ER  - ）或 NoteExpress（{Reference Type}: ...）"
          hint="支持 @article / @inproceedings 等；RIS 以 TY  - 开头；NoteExpress 为 {Tag}: 值 形式"
          min-height="10rem"
          @submit="handleImport"
        />
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <input
          ref="fileInput"
          type="file"
          accept=".bib,.ris,.net,.txt"
          class="hidden"
          data-test="import-file"
          @change="handleFileChange"
        />
        <Button
          data-test="import-file-button"
          type="button"
          variant="outline"
          @click="triggerFilePick"
        >
          <FileUp :size="15" aria-hidden="true" />
          <span>选择本地文件</span>
        </Button>
        <span v-if="pickedFileName" class="text-xs text-muted-foreground" data-test="import-file-name">
          已读取 {{ pickedFileName }}（UTF-8 / GB18030 自动识别）
        </span>
        <span v-else class="text-xs text-muted-foreground">
          支持 .bib / .ris / .net / .txt；中文导出文件按 UTF-8 → GB18030 自动回退
        </span>
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
          <Loader2 v-if="importing" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <FileInput v-else :size="15" aria-hidden="true" />
          <span>{{ importing ? "解析中…" : "解析预览" }}</span>
        </Button>
        <Button v-if="hasResult" variant="ghost" data-test="import-reset" type="button" @click="handleReset">
          <RotateCcw :size="15" aria-hidden="true" />
          <span>重新开始</span>
        </Button>
      </div>
    </Card>

    <ErrorBanner :message="error" testid="import-error" @dismiss="error = null" />

    <Alert v-if="hasResult" variant="success" class="mb-3" data-test="import-summary">
      <CircleCheck />
      <span>识别为 {{ formatLabel }}，共 {{ result?.count }} 条</span>
    </Alert>

    <!-- 保存到引用库：这是本页的真实出口（此前解析完什么也做不了） -->
    <Card v-if="hasResult && !isEmpty" class="mb-3 flex-row flex-wrap items-center gap-2 px-3 py-2.5">
      <Button data-test="import-save" :disabled="saving || savableCount === 0" @click="handleSave">
        <Loader2 v-if="saving" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <Save v-else :size="15" aria-hidden="true" />
        <span>{{ saving ? "保存中…" : `保存 ${savableCount} 条到引用库` }}</span>
      </Button>
      <span class="text-xs text-muted-foreground">
        保存后可在创建任务时选择「我的引用库」，任务将只用这些文献、不外呼检索。
      </span>
    </Card>

    <ErrorBanner :message="saveError" testid="import-save-error" @dismiss="saveError = null" />

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
        <Button
          type="button"
          variant="link"
          size="sm"
          data-test="import-goto-library"
          class="h-auto w-fit p-0 text-xs"
          @click="goToLibrary"
        >
          去引用库查看 →
        </Button>
        <div class="mt-1 flex flex-wrap items-center gap-3">
          <Button
            type="button"
            size="sm"
            data-test="import-goto-task-create"
            @click="goToCreateTask"
          >
            返回创建任务 →
          </Button>
          <span class="text-xs text-muted-foreground">
            返回后之前的建任务草稿会自动恢复，不用重新填写。
          </span>
        </div>
      </div>
    </Alert>

    <EmptyState
      v-if="isEmpty"
      testid="import-empty"
      title="未解析出任何条目"
      description="请检查内容是否为合法 BibTeX / RIS 格式。"
    />

    <Motion
      v-if="hasResult && !isEmpty"
      as="ul"
      :initial="{ opacity: 0, y: reduced ? 0 : 6 }"
      :animate="{ opacity: 1, y: 0 }"
      :transition="reduced ? TRANSITION_MICRO : TRANSITION_ENTER"
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
          <Badge v-if="item.abstract?.trim()" variant="success" data-test="abstract-badge">
            有摘要
          </Badge>
          <span v-if="metaText(item)">{{ metaText(item) }}</span>
        </div>
        <div v-if="item.doi" class="mt-1">
          <code class="break-all rounded bg-muted px-1 py-0.5 text-xs text-muted-foreground">
            {{ item.doi }}
          </code>
        </div>
      </li>
    </Motion>
  </section>
</template>
