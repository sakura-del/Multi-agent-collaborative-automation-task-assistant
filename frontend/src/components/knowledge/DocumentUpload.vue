<script setup lang="ts">
import { ref } from "vue";
import { Upload } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
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
  <form class="upload" data-test="document-upload" @submit.prevent="handleSubmit">
    <div class="upload__row">
      <label class="upload__field">
        <span>文件</span>
        <input
          data-test="document-file"
          type="file"
          :accept="ACCEPTED.join(',')"
          @change="onFileChange"
        />
      </label>
      <label class="upload__field">
        <span>标题</span>
        <input
          v-model="title"
          data-test="document-title"
          class="input"
          type="text"
          maxlength="256"
          placeholder="例如：RAG 评估综述"
        />
      </label>
      <label class="upload__field">
        <span>分块策略</span>
        <select v-model="strategy" data-test="document-strategy" class="input">
          <option value="recursive">recursive</option>
          <option value="fixed">fixed</option>
          <option value="semantic">semantic</option>
        </select>
      </label>
      <button class="btn btn--primary" data-test="document-submit" type="submit" :disabled="store.uploading">
        <Upload :size="15" />
        <span>{{ store.uploading ? `上传中 ${store.uploadProgress}%` : "上传" }}</span>
      </button>
    </div>

    <div v-if="store.uploading" class="upload__progress" data-test="document-progress">
      <div class="upload__bar" :style="{ width: `${store.uploadProgress}%` }" />
    </div>

    <ErrorBanner :message="localError" testid="upload-error" />
    <ErrorBanner v-if="!localError" :message="store.error" testid="upload-error" />
  </form>
</template>

<style scoped>
.upload {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.upload__row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.75rem;
}

.upload__field {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.85rem;
}

.upload__field input[type="file"] {
  font-size: 0.82rem;
}

.upload__progress {
  height: 6px;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
  max-width: 420px;
}

.upload__bar {
  height: 100%;
  background: var(--color-primary);
}
</style>
