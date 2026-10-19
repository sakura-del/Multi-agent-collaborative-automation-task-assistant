<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Trash2, Upload } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
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
const fileInput = ref<HTMLInputElement | null>(null);

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
    if (fileInput.value) {
      fileInput.value.value = "";
    }
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
  <section class="page">
    <header class="page__header">
      <h1>引用样式</h1>
      <p class="page__hint">{{ hint }}</p>
    </header>

    <ErrorBanner :message="loadError" testid="style-load-error" />

    <form class="upload" data-test="style-upload-form" @submit.prevent="handleUpload">
      <div class="upload__row">
        <label class="upload__field">
          <span>样式文件</span>
          <input
            ref="fileInput"
            data-test="style-file"
            type="file"
            :accept="ACCEPTED.join(',')"
            @change="onFileChange"
          />
        </label>
        <button
          class="btn btn--primary"
          data-test="style-upload-submit"
          type="submit"
          :disabled="uploading"
        >
          <Upload :size="15" />
          <span>{{ uploading ? "上传中…" : "上传样式" }}</span>
        </button>
      </div>
    </form>

    <ErrorBanner :message="uploadError" testid="style-upload-error" />
    <p v-if="successMessage" class="banner banner--ok" data-test="style-upload-success">
      {{ successMessage }}
    </p>

    <p v-if="loading && !styles.length" class="page__hint">加载中…</p>
    <p v-else-if="!styles.length" class="page__hint" data-test="style-empty">
      还没有可用样式，先上传一个 .csl 文件。
    </p>
    <ul v-else class="style-list">
      <li
        v-for="style in styles"
        :key="style.style_id"
        class="style-row"
        :data-test="`style-row-${style.style_id}`"
      >
        <div class="style-row__main">
          <span class="style-row__title">{{ style.title }}</span>
          <span class="badge" :class="{ 'badge--custom': style.source === 'custom' }">
            {{ style.source === "custom" ? "自定义" : "预置" }}
          </span>
          <span v-if="style.style_id === defaultStyleId" class="badge badge--accent">默认</span>
        </div>
        <div class="style-row__meta">
          <code>{{ style.style_id }}</code>
          <span>{{ style.numbering }} · {{ style.ordering }} · {{ style.default_locale }}</span>
        </div>
        <button
          v-if="style.source === 'custom'"
          class="icon-button"
          type="button"
          :data-test="`style-delete-${style.style_id}`"
          :disabled="deletingId === style.style_id"
          :title="`删除 ${style.title}`"
          @click="handleDelete(style.style_id)"
        >
          <Trash2 :size="15" />
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.page__header {
  margin-bottom: 1rem;
}

.upload {
  margin-bottom: 0.5rem;
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

.banner--ok {
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  background: #ecfdf5;
  color: #047857;
  font-size: 0.88rem;
}

.style-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 1rem;
}

.style-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.625rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.style-row__main {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.style-row__title {
  font-size: 0.92rem;
  font-weight: 500;
}

.style-row__meta {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  margin-left: auto;
  text-align: right;
  font-size: 0.78rem;
  color: #6b7280;
  min-width: 0;
}

.style-row__meta code {
  color: #374151;
}

.badge {
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.75rem;
  white-space: nowrap;
}

.badge--custom {
  background: #f5f3ff;
  color: #6d28d9;
}

.badge--accent {
  background: #ecfdf5;
  color: #047857;
}
</style>
