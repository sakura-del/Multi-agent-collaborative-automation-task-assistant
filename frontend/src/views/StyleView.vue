<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { CircleCheck, Loader2, Trash2, Upload } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import FilePicker from "@/components/common/FilePicker.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  deleteCitationStyle,
  listCitationStyles,
  uploadCitationStyle,
} from "@/api/citation-styles";
import type { CitationStyleOut } from "@/api/types";

// 引用样式管理页（Issue #4 M2 §1.5）：列表（预置 / 自定义）+ 上传 .csl + 删除自定义
// 上传失败必须把后端 `detail` 展示出来，绝不静默失败
const ACCEPTED = [".csl"];

const styles = ref<CitationStyleOut[]>([]);
const defaultStyleId = ref("");
const loading = ref(false);
const loadError = ref<string | null>(null);
const uploadError = ref<string | null>(null);
const successMessage = ref<string | null>(null);
const uploading = ref(false);
const deletingId = ref<string | null>(null);
const selectedFile = ref<File | null>(null);
const filePicker = ref<{ reset: () => void } | null>(null);

const defaultStyleTitle = computed(
  () => styles.value.find((style) => style.style_id === defaultStyleId.value)?.title ?? ""
);
const customCount = computed(
  () => styles.value.filter((style) => style.source === "custom").length
);
const hint = computed(() => {
  if (!styles.value.length) {
    return "上传 .csl 文件即可在创建任务时选用；系统预置样式不可删除";
  }
  const base = `${styles.value.length} 种样式（自定义 ${customCount.value} 种）`;
  return defaultStyleTitle.value ? `${base} · 默认：${defaultStyleTitle.value}` : base;
});

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  loadError.value = null;
  try {
    const data = await listCitationStyles();
    styles.value = data.styles;
    defaultStyleId.value = data.default_style_id;
  } catch (error) {
    loadError.value = (error as Error).message || "样式列表加载失败";
  } finally {
    loading.value = false;
  }
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function fileError(file: File | null): string | null {
  if (!file) {
    return "请先选择文件";
  }
  if (!ACCEPTED.includes(extensionOf(file.name))) {
    return `仅支持 ${ACCEPTED.join(" / ")} 样式文件`;
  }
  return null;
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  selectedFile.value = file;
  successMessage.value = null;
  uploadError.value = fileError(file);
}

async function handleUpload() {
  successMessage.value = null;
  const invalid = fileError(selectedFile.value);
  if (invalid) {
    uploadError.value = invalid;
    return;
  }
  uploadError.value = null;
  uploading.value = true;
  try {
    const created = await uploadCitationStyle(selectedFile.value as File);
    successMessage.value = `已上传样式「${created.title}」（${created.style_id}）`;
    selectedFile.value = null;
    filePicker.value?.reset();
    await refresh();
  } catch (error) {
    // 后端 400 的 detail（含逐条 reasons）在这里展示
    uploadError.value = (error as Error).message || "样式上传失败";
  } finally {
    uploading.value = false;
  }
}

async function handleDelete(styleId: string) {
  uploadError.value = null;
  successMessage.value = null;
  deletingId.value = styleId;
  try {
    await deleteCitationStyle(styleId);
    successMessage.value = `已删除自定义样式 ${styleId}`;
    await refresh();
  } catch (error) {
    uploadError.value = (error as Error).message || "样式删除失败";
  } finally {
    deletingId.value = null;
  }
}
</script>

<template>
  <section>
    <header class="mb-4">
      <h1 class="text-lg font-semibold tracking-tight">引用样式</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">{{ hint }}</p>
    </header>

    <ErrorBanner :message="loadError" testid="style-load-error" />

    <form
      class="mb-3 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-card p-4"
      data-test="style-upload-form"
      @submit.prevent="handleUpload"
    >
      <div class="grid gap-1.5">
        <Label for="style-file">样式文件</Label>
        <FilePicker
          id="style-file"
          ref="filePicker"
          data-test="style-file"
          :accept="ACCEPTED.join(',')"
          placeholder="选择 .csl 文件"
          @change="onFileChange"
        />
      </div>
      <Button data-test="style-upload-submit" type="submit" :disabled="uploading">
        <Loader2 v-if="uploading" class="animate-spin" aria-hidden="true" />
        <Upload v-else :size="15" aria-hidden="true" />
        <span>{{ uploading ? "上传中…" : "上传样式" }}</span>
      </Button>
    </form>

    <ErrorBanner :message="uploadError" testid="style-upload-error" />
    <Alert
      v-if="successMessage"
      variant="success"
      class="mb-3"
      data-test="style-upload-success"
    >
      <CircleCheck />
      <span>{{ successMessage }}</span>
    </Alert>

    <p v-if="loading && !styles.length" class="text-sm text-muted-foreground">加载中…</p>
    <EmptyState
      v-else-if="!styles.length"
      testid="style-empty"
      title="还没有可用样式"
      description="上传一个 .csl 文件即可作为自定义引用样式使用。"
    />
    <ul v-else class="space-y-2">
      <li
        v-for="style in styles"
        :key="style.style_id"
        class="flex items-start justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5"
        :data-test="`style-row-${style.style_id}`"
      >
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="text-sm font-medium">{{ style.title }}</span>
            <Badge :variant="style.source === 'custom' ? 'outline' : 'muted'">
              {{ style.source === "custom" ? "自定义" : "预置" }}
            </Badge>
            <Badge v-if="style.style_id === defaultStyleId" variant="default">默认</Badge>
          </div>
          <div class="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <code class="rounded bg-muted px-1 py-0.5">{{ style.style_id }}</code>
            <span>{{ style.numbering }} · {{ style.ordering }} · {{ style.default_locale }}</span>
          </div>
        </div>
        <Button
          v-if="style.source === 'custom'"
          variant="ghost"
          size="icon-sm"
          :data-test="`style-delete-${style.style_id}`"
          :disabled="deletingId === style.style_id"
          :title="`删除 ${style.title}`"
          :aria-label="`删除 ${style.title}`"
          @click="handleDelete(style.style_id)"
        >
          <Trash2 :size="15" />
        </Button>
      </li>
    </ul>
  </section>
</template>
