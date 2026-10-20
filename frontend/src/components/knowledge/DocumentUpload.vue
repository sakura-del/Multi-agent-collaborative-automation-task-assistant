<script setup lang="ts">
import { ref } from "vue";
import { Loader2, Upload } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useKnowledgeStore } from "@/stores/knowledge";
import type { ChunkStrategy } from "@/api/types";

// 上传工具：客户端先做扩展名与 30MB 限制，避免无意义的网络往返
const MAX_BYTES = 30 * 1024 * 1024;
const ACCEPTED = [".pdf", ".docx", ".md", ".markdown", ".txt"];

const emit = defineEmits<{ uploaded: [] }>();
const store = useKnowledgeStore();

const selectedFile = ref<File | null>(null);
const title = ref("");
const strategy = ref<ChunkStrategy>("recursive");
const localError = ref<string | null>(null);

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function fileError(file: File | null): string | null {
  if (!file) {
    return "请先选择文件";
  }
  if (!ACCEPTED.includes(extensionOf(file.name))) {
    return `仅支持 ${ACCEPTED.join(" / ")} 格式`;
  }
  if (file.size > MAX_BYTES) {
    return "文件超过 30 MB 上限";
  }
  return null;
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  selectedFile.value = file;
  localError.value = fileError(file);
  if (!file || localError.value) {
    return;
  }
  if (!title.value) {
    title.value = file.name.replace(/\.[^.]+$/, "");
  }
}

async function handleSubmit() {
  // 提交前重新校验文件，避免"扩展名/大小错误"被后续校验覆盖
  const invalid = fileError(selectedFile.value);
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
    await store.upload(selectedFile.value as File, title.value.trim(), strategy.value);
    selectedFile.value = null;
    title.value = "";
    emit("uploaded");
  } catch (caught) {
    // 就近展示错误信息（不含栈）
    localError.value = (caught as Error).message || "上传失败";
  }
}
</script>

<template>
  <form
    class="mb-4 flex flex-col gap-3 rounded-lg border border-border bg-card p-4"
    data-test="document-upload"
    @submit.prevent="handleSubmit"
  >
    <div class="flex flex-wrap items-end gap-3">
      <div class="grid gap-1.5">
        <Label for="document-file">文件</Label>
        <input
          id="document-file"
          data-test="document-file"
          type="file"
          :accept="ACCEPTED.join(',')"
          class="block w-full text-xs text-muted-foreground file:mr-2 file:rounded-md file:border file:border-border file:bg-background file:px-2 file:py-1 file:text-xs file:font-medium file:text-foreground hover:file:bg-muted"
          @change="onFileChange"
        />
      </div>
      <div class="grid min-w-48 flex-1 gap-1.5">
        <Label for="document-title">标题</Label>
        <Input
          id="document-title"
          v-model="title"
          data-test="document-title"
          type="text"
          maxlength="256"
          placeholder="例如：RAG 评估综述"
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
          <option value="recursive">recursive</option>
          <option value="fixed">fixed</option>
          <option value="semantic">semantic</option>
        </select>
      </div>
      <Button data-test="document-submit" type="submit" :disabled="store.uploading">
        <Loader2 v-if="store.uploading" class="animate-spin" aria-hidden="true" />
        <Upload v-else :size="15" aria-hidden="true" />
        <span>{{ store.uploading ? `上传中 ${store.uploadProgress}%` : "上传" }}</span>
      </Button>
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
        class="h-full rounded-full bg-primary transition-[width] duration-200"
        :style="{ width: `${store.uploadProgress}%` }"
      />
    </div>

    <ErrorBanner :message="localError" testid="upload-error" />
    <ErrorBanner v-if="!localError" :message="store.error" testid="upload-error" />
  </form>
</template>
