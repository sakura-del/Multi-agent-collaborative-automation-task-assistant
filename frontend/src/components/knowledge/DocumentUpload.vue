<script setup lang="ts">
import { computed, ref } from "vue";
import { FolderUp, Loader2, Trash2, Upload, X } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import FilePicker from "@/components/common/FilePicker.vue";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { toastSuccess } from "@/lib/toast";
import { useKnowledgeStore } from "@/stores/knowledge";
import type { ChunkStrategy } from "@/api/types";
import {
  collectFilesFromDataTransfer,
  defaultTitleFromFile,
  filterSupportedFiles,
  MAX_BATCH_FILES,
  type SkippedFile,
} from "@/utils/file-drop";

// 上传工具：支持多文件选择、文件夹递归导入与拖拽入队，保留单文件既有表单与 E2E 契约
const MAX_BYTES = 30 * 1024 * 1024;
const ACCEPTED = [".pdf", ".docx", ".md", ".markdown", ".txt"] as const;
// 知网 CAJ 是用户最常误传的格式：后端只收 PDF/DOCX/MD/TXT，这里给出可执行的替代路径。
const CAJ_HINT = "知网 CAJ 格式暂不支持，请先在官网下载 PDF 再上传";

type QueueItemStatus = "pending" | "uploading" | "done" | "error";

interface UploadQueueItem {
  id: string;
  file: File;
  title: string;
  status: QueueItemStatus;
  progress: number;
  error?: string;
}

const emit = defineEmits<{
  uploaded: [];
  "drag-settled": [];
}>();
const store = useKnowledgeStore();

const filePicker = ref<{ reset: () => void } | null>(null);
const folderInput = ref<HTMLInputElement | null>(null);
const queue = ref<UploadQueueItem[]>([]);
const skippedFiles = ref<SkippedFile[]>([]);
const singleInvalidFile = ref<File | null>(null);
const title = ref("");
const strategy = ref<ChunkStrategy>("recursive");
const localError = ref<string | null>(null);
const isCardDragOver = ref(false);
let cardDragDepth = 0;
let seq = 0;

const isBatchMode = computed(() => queue.value.length > 1);
const showQueue = computed(
  () => queue.value.length > 1 || queue.value.some((item) => item.status === "error"),
);
const hasPendingOrFailed = computed(() => queue.value.some((item) => item.status !== "done"));

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function singleFileError(file: File): string | null {
  if (extensionOf(file.name) === ".caj") {
    return CAJ_HINT;
  }
  if (!ACCEPTED.includes(extensionOf(file.name) as (typeof ACCEPTED)[number])) {
    return `仅支持 ${ACCEPTED.join(" / ")} 格式`;
  }
  if (file.size > MAX_BYTES) {
    return "文件超过 30 MB 上限";
  }
  return null;
}

/**
 * 接收外部（文件选择器、文件夹、拖拽区、全页拖拽蒙层）传入的文件列表：
 * 过滤非法格式、隐藏/临时文件与超限文件后合并入队列。
 */
function enqueueFiles(rawFiles: File[], replaceWhenSingleEmpty = false) {
  if (!rawFiles.length) return;

  // 单文件兼容：队列为空（或单选重选）且只选了 1 个非法文件时记住该文件并展示校验错误
  if ((queue.value.length === 0 || replaceWhenSingleEmpty) && rawFiles.length === 1) {
    const singleErr = singleFileError(rawFiles[0]);
    if (singleErr) {
      if (replaceWhenSingleEmpty) {
        queue.value = [];
      }
      singleInvalidFile.value = rawFiles[0];
      localError.value = singleErr;
      skippedFiles.value = [];
      return;
    }
  }

  singleInvalidFile.value = null;
  localError.value = null;
  const remainingSlots = Math.max(0, MAX_BATCH_FILES - queue.value.length);
  const { valid, skipped } = filterSupportedFiles(
    rawFiles,
    ACCEPTED,
    MAX_BYTES,
    remainingSlots || MAX_BATCH_FILES,
  );
  // 知网 CAJ 是最常见的「看起来能传其实不行」格式：批量/拖拽路径也给出明确指引，
  // 而不是一句笼统的「格式不支持」。
  skippedFiles.value = skipped.map((item) =>
    extensionOf(item.name) === ".caj" ? { ...item, reason: CAJ_HINT } : item,
  );

  if (!valid.length) {
    if (skipped.length > 0 && queue.value.length === 0) {
      localError.value = `已忽略 ${skipped.length} 个不支持或超限文件`;
    }
    return;
  }

  // 若此前处于单文件选择、且通过原生单/多选触发，允许与已有非重复文件合并或直接初始化
  const existingKeys = new Set(
    queue.value.map((item) => `${item.file.webkitRelativePath || item.file.name}::${item.file.size}`),
  );
  const additions: UploadQueueItem[] = [];
  for (const file of valid) {
    const key = `${file.webkitRelativePath || file.name}::${file.size}`;
    if (!replaceWhenSingleEmpty && existingKeys.has(key)) continue;
    existingKeys.add(key);
    seq += 1;
    additions.push({
      id: `q-${seq}`,
      file,
      title: defaultTitleFromFile(file),
      status: "pending",
      progress: 0,
    });
  }

  if (replaceWhenSingleEmpty && queue.value.length <= 1 && additions.length >= 1) {
    queue.value = additions;
  } else {
    queue.value = [...queue.value, ...additions];
  }

  if (queue.value.length === 1) {
    title.value = title.value.trim() || queue.value[0].title;
  }
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  if (!files.length) return;
  // 从文件选择器重选时，若先前不是批量队列则替换当前选择，避免旧的单个错误文件残留
  const replace = queue.value.length <= 1;
  if (replace && files.length === 1) {
    title.value = "";
  }
  enqueueFiles(files, replace);
}

function onFolderChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  if (files.length) {
    enqueueFiles(files);
  }
  input.value = "";
}

function onCardDragEnter() {
  cardDragDepth += 1;
  isCardDragOver.value = true;
}

function onCardDragLeave() {
  cardDragDepth = Math.max(0, cardDragDepth - 1);
  if (cardDragDepth === 0) {
    isCardDragOver.value = false;
  }
}

async function onDrop(event: DragEvent) {
  cardDragDepth = 0;
  isCardDragOver.value = false;
  emit("drag-settled");
  const files = await collectFilesFromDataTransfer(event.dataTransfer);
  if (files.length) {
    enqueueFiles(files);
  }
}

function removeQueueItem(id: string) {
  queue.value = queue.value.filter((item) => item.id !== id);
  if (queue.value.length === 0) {
    title.value = "";
    filePicker.value?.reset?.();
  } else if (queue.value.length === 1) {
    title.value = queue.value[0].title;
  }
}

function clearQueue() {
  queue.value = [];
  skippedFiles.value = [];
  singleInvalidFile.value = null;
  localError.value = null;
  title.value = "";
  filePicker.value?.reset?.();
}

async function handleSubmit() {
  if (!queue.value.length) {
    localError.value = singleInvalidFile.value
      ? singleFileError(singleInvalidFile.value) ?? "请先选择文件"
      : "请先选择文件";
    return;
  }

  // 单文件分支：100% 沿用 store.upload，兼容原有单文件校验与进度提示
  if (queue.value.length === 1) {
    const single = queue.value[0];
    const invalid = singleFileError(single.file);
    if (invalid) {
      localError.value = invalid;
      return;
    }
    if (!title.value.trim()) {
      localError.value = "请填写标题";
      return;
    }
    localError.value = null;
    try {
      await store.upload(single.file, title.value.trim(), strategy.value);
      clearQueue();
      toastSuccess("文档已上传，正在建立索引");
      emit("uploaded");
    } catch (caught) {
      localError.value = (caught as Error).message || "上传失败";
    }
    return;
  }

  // 批量分支：仅提交未完成的项，并发受 store.uploadBatch 控制，保留失败项供重试
  const pendingItems = queue.value.filter((item) => item.status !== "done");
  if (!pendingItems.length) return;

  localError.value = null;
  for (const item of pendingItems) {
    item.status = "uploading";
    item.progress = 0;
    item.error = undefined;
  }

  const { succeeded, failed } = await store.uploadBatch(
    pendingItems.map((item) => ({ id: item.id, file: item.file, title: item.title })),
    strategy.value,
    {
      onProgress: (id, percent) => {
        const target = queue.value.find((item) => item.id === id);
        if (target) {
          target.progress = percent;
        }
      },
      onItemDone: (id, ok, errorMessage) => {
        const target = queue.value.find((item) => item.id === id);
        if (!target) return;
        target.status = ok ? "done" : "error";
        target.progress = ok ? 100 : target.progress;
        target.error = errorMessage;
      },
    },
  );

  // 移除成功条目，仅保留失败条目方便用户重试
  queue.value = queue.value.filter((item) => item.status !== "done");
  if (queue.value.length === 0) {
    filePicker.value?.reset?.();
    title.value = "";
  } else if (queue.value.length === 1) {
    title.value = queue.value[0].title;
  }

  if (succeeded > 0) {
    toastSuccess(`已成功上传 ${succeeded} 篇文档并建立索引`);
    emit("uploaded");
  }
  if (failed > 0) {
    localError.value = `${failed} 篇文档上传失败，可查看下方原因后点击上传重试`;
  }
}

defineExpose({
  enqueueFiles,
});
</script>

<template>
  <Card
    as="form"
    :class="[
      'mb-4 gap-3 p-4 transition-colors',
      isCardDragOver ? 'border-primary bg-primary/5' : '',
    ]"
    data-test="document-upload"
    @submit.prevent="handleSubmit"
    @dragenter.prevent="onCardDragEnter"
    @dragover.prevent
    @dragleave="onCardDragLeave"
    @drop.stop.prevent="onDrop"
  >
    <div class="flex flex-wrap items-end gap-3">
      <div class="grid gap-1.5">
        <Label for="document-file">文件（支持多选 / 拖拽）</Label>
        <div class="flex flex-wrap items-center gap-2">
          <FilePicker
            id="document-file"
            ref="filePicker"
            data-test="document-file"
            :accept="ACCEPTED.join(',')"
            :multiple="true"
            placeholder="选择 pdf / docx / md / txt"
            @change="onFileChange"
          />
          <input
            ref="folderInput"
            type="file"
            webkitdirectory
            directory
            multiple
            class="hidden"
            data-test="document-folder-input"
            @change="onFolderChange"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-test="document-folder"
            :disabled="store.uploading"
            @click="folderInput?.click()"
          >
            <FolderUp :size="14" aria-hidden="true" />
            <span>选择文件夹</span>
          </Button>
        </div>
      </div>

      <div class="grid min-w-48 flex-1 gap-1.5">
        <Label for="document-title">标题</Label>
        <Input
          v-if="!isBatchMode"
          id="document-title"
          v-model="title"
          data-test="document-title"
          type="text"
          maxlength="256"
          placeholder="例如：RAG 评估综述"
        />
        <Input
          v-else
          id="document-title"
          data-test="document-title"
          type="text"
          disabled
          :model-value="`批量模式：共 ${queue.length} 个文件，将按文件名自动命名`"
        />
      </div>

      <div class="grid gap-1.5">
        <Label for="document-strategy">分块策略</Label>
        <select
          id="document-strategy"
          v-model="strategy"
          data-test="document-strategy"
          class="flex h-9 rounded-md border border-input bg-background px-3 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
        >
          <option value="recursive">智能分段（推荐）</option>
          <option value="fixed">固定长度分段</option>
          <option value="semantic">按语义分段</option>
        </select>
      </div>

      <Button
        data-test="document-submit"
        type="submit"
        :disabled="store.uploading || (isBatchMode && !hasPendingOrFailed)"
      >
        <Loader2 v-if="store.uploading" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <Upload v-else :size="15" aria-hidden="true" />
        <span>
          {{
            store.uploading
              ? isBatchMode
                ? `批量上传 ${store.uploadProgress}%`
                : store.uploadProgress >= 100
                  ? "正在解析与索引…"
                  : `上传中 ${store.uploadProgress}%`
              : isBatchMode
                ? `上传全部（${queue.length}）`
                : "上传"
          }}
        </span>
      </Button>
    </div>

    <!-- 多文件待上传队列（批量留存失败项时仅剩 1 篇也保留展示原因） -->
    <div
      v-if="showQueue"
      data-test="upload-queue"
      class="rounded-md border border-border bg-muted/30 p-2.5"
    >
      <div class="mb-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>待上传队列（{{ queue.length }} 篇，最多 {{ MAX_BATCH_FILES }} 篇）</span>
        <button
          v-if="!store.uploading"
          type="button"
          class="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          @click="clearQueue"
        >
          <Trash2 :size="12" aria-hidden="true" />
          清空队列
        </button>
      </div>
      <ul class="max-h-48 space-y-1.5 overflow-y-auto pr-1">
        <li
          v-for="item in queue"
          :key="item.id"
          class="flex items-center justify-between gap-2 rounded bg-background px-2.5 py-1.5 text-xs"
        >
          <div class="min-w-0 flex-1">
            <p class="truncate font-medium text-foreground">{{ item.file.name }}</p>
            <p v-if="item.error" class="truncate text-[11px] text-destructive">
              {{ item.error }}
            </p>
          </div>
          <span class="shrink-0 text-[11px] text-muted-foreground tabular-nums">
            {{ formatSize(item.file.size) }}
          </span>
          <span
            :class="[
              'shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums',
              item.status === 'error'
                ? 'bg-destructive/10 text-destructive'
                : item.status === 'done'
                  ? 'bg-success/10 text-success'
                  : item.status === 'uploading'
                    ? 'bg-primary/10 text-primary'
                    : 'bg-secondary text-secondary-foreground',
            ]"
          >
            {{
              item.status === "uploading"
                ? item.progress >= 100
                  ? "索引中"
                  : `${item.progress}%`
                : item.status === "done"
                  ? "已完成"
                  : item.status === "error"
                    ? "失败"
                    : "等待中"
            }}
          </span>
          <button
            v-if="!store.uploading"
            type="button"
            aria-label="移除该文件"
            class="shrink-0 text-muted-foreground hover:text-foreground"
            @click="removeQueueItem(item.id)"
          >
            <X :size="13" aria-hidden="true" />
          </button>
        </li>
      </ul>
    </div>

    <!-- 忽略/过滤文件温和提示 -->
    <div
      v-if="skippedFiles.length > 0"
      data-test="upload-skipped"
      class="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground"
    >
      <span class="truncate">
        已自动忽略 {{ skippedFiles.length }} 个不支持或超限文件（如 {{ skippedFiles[0].name }}：{{
          skippedFiles[0].reason
        }}）
      </span>
      <button
        type="button"
        aria-label="关闭忽略提示"
        class="shrink-0 hover:text-foreground"
        @click="skippedFiles = []"
      >
        <X :size="13" aria-hidden="true" />
      </button>
    </div>

    <div
      v-if="store.uploading"
      class="h-1.5 max-w-md overflow-hidden rounded-full bg-secondary"
      data-test="document-progress"
      role="progressbar"
      :aria-valuenow="store.uploadProgress"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div
        class="h-full w-full origin-left rounded-full bg-primary transition-transform duration-200 ease-out motion-reduce:transition-none"
        :style="{ transform: `scaleX(${Math.max(0, Math.min(100, store.uploadProgress)) / 100})` }"
      />
    </div>

    <ErrorBanner :message="localError" testid="upload-error" @dismiss="localError = null" />
    <ErrorBanner
      v-if="!localError && !isBatchMode"
      :message="store.error"
      testid="upload-error"
      @dismiss="store.error = null"
    />
  </Card>
</template>
