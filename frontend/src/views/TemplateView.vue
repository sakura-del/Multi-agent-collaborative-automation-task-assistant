<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Download, FileText, Trash2, Upload } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { deleteTemplate, listTemplates, renderTemplate, uploadTemplate } from "@/api/templates";
import type { TemplateOut } from "@/api/types";

// Word 模板管理页（Phase 1 P1-A）：列表 + 上传 .docx + 渲染下载 + 删除
// 上传/渲染失败必须把后端 `detail` 展示出来，绝不静默失败。
const ACCEPTED = [".docx"];

const templates = ref<TemplateOut[]>([]);
const loading = ref(false);
const loadError = ref<string | null>(null);
const uploadError = ref<string | null>(null);
const renderError = ref<string | null>(null);
const successMessage = ref<string | null>(null);
const uploading = ref(false);
const renderingId = ref<string | null>(null);
const deletingId = ref<string | null>(null);
const selectedFile = ref<File | null>(null);
const templateName = ref("");
const fileInput = ref<HTMLInputElement | null>(null);

const hint = computed(() => {
  if (!templates.value.length) {
    return "上传 .docx 模板即可用它渲染综述成稿；模板中的 {{ 占位符 }} 会被自动识别。";
  }
  return `${templates.value.length} 个模板 · 渲染时参考文献由当前引用样式生成`;
});

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  loadError.value = null;
  try {
    templates.value = await listTemplates();
  } catch (error) {
    loadError.value = (error as Error).message || "模板列表加载失败";
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
    return "请先选择模板文件";
  }
  if (!ACCEPTED.includes(extensionOf(file.name))) {
    return `仅支持 ${ACCEPTED.join(" / ")} 模板文件（.docm 等宏模板会被拒绝）`;
  }
  return null;
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  selectedFile.value = file;
  successMessage.value = null;
  uploadError.value = fileError(file);
  // 名称留空时用文件名（不含扩展名）作为默认显示名
  if (file && !templateName.value) {
    const index = file.name.lastIndexOf(".");
    templateName.value = index > 0 ? file.name.slice(0, index) : file.name;
  }
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
    const created = await uploadTemplate(
      selectedFile.value as File,
      templateName.value.trim() || "未命名模板"
    );
    successMessage.value = `已上传模板「${created.name}」（识别到 ${created.placeholders.length} 个占位符）`;
    selectedFile.value = null;
    templateName.value = "";
    if (fileInput.value) {
      fileInput.value.value = "";
    }
    await refresh();
  } catch (error) {
    // 后端 400 的 detail（含逐条 reasons）在这里展示
    uploadError.value = (error as Error).message || "模板上传失败";
  } finally {
    uploading.value = false;
  }
}

async function handleRender(template: TemplateOut) {
  renderError.value = null;
  successMessage.value = null;
  renderingId.value = template.template_id;
  try {
    // 演示用最小载荷：把模板里的占位符按 key 填示例文本；
    // 真实业务中这些内容来自综述正文与已 CSL 渲染的参考文献。
    const sections: Record<string, string> = {};
    const bibliography: string[] = [];
    for (const placeholder of template.placeholders) {
      if (placeholder.key.toLowerCase().includes("ref")) {
        bibliography.push(`[1] 示例参考文献（${placeholder.key}）`);
      } else {
        sections[placeholder.key] = placeholder.sample || `示例内容（${placeholder.key}）`;
      }
    }
    const blob = await renderTemplate(template.template_id, { sections, bibliography });
    downloadBlob(blob, `${template.name || template.template_id}.docx`);
    successMessage.value = `已用模板「${template.name}」生成成稿并开始下载`;
  } catch (error) {
    renderError.value = (error as Error).message || "模板渲染失败";
  } finally {
    renderingId.value = null;
  }
}

/** 触发浏览器下载（happy-dom 下无真实下载，但会调用 createObjectURL 与 click） */
function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function handleDelete(template: TemplateOut) {
  uploadError.value = null;
  renderError.value = null;
  successMessage.value = null;
  deletingId.value = template.template_id;
  try {
    await deleteTemplate(template.template_id);
    successMessage.value = `已删除模板「${template.name}」`;
    await refresh();
  } catch (error) {
    uploadError.value = (error as Error).message || "模板删除失败";
  } finally {
    deletingId.value = null;
  }
}

function placeholderKeys(template: TemplateOut): string[] {
  return template.placeholders.map((item) => item.key);
}
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>Word 模板</h1>
      <p class="page__hint">{{ hint }}</p>
    </header>

    <ErrorBanner :message="loadError" testid="template-load-error" />

    <form class="upload" data-test="template-upload-form" @submit.prevent="handleUpload">
      <div class="upload__row">
        <label class="upload__field">
          <span>模板文件（.docx）</span>
          <input
            ref="fileInput"
            data-test="template-file"
            type="file"
            :accept="ACCEPTED.join(',')"
            @change="onFileChange"
          />
        </label>
        <label class="upload__field">
          <span>显示名称</span>
          <input
            v-model="templateName"
            data-test="template-name"
            type="text"
            placeholder="如：学院毕业论文模板"
          />
        </label>
        <button
          class="btn btn--primary"
          data-test="template-upload-submit"
          type="submit"
          :disabled="uploading"
        >
          <Upload :size="15" />
          <span>{{ uploading ? "上传中…" : "上传模板" }}</span>
        </button>
      </div>
    </form>

    <ErrorBanner :message="uploadError" testid="template-upload-error" />
    <ErrorBanner :message="renderError" testid="template-render-error" />
    <p v-if="successMessage" class="banner banner--ok" data-test="template-upload-success">
      {{ successMessage }}
    </p>

    <p v-if="loading && !templates.length" class="page__hint">加载中…</p>
    <p v-else-if="!templates.length" class="page__hint" data-test="template-empty">
      还没有模板，先上传一个 .docx 文件。
    </p>
    <ul v-else class="template-list">
      <li
        v-for="template in templates"
        :key="template.template_id"
        class="template-row"
        :data-test="`template-row-${template.template_id}`"
      >
        <div class="template-row__main">
          <FileText :size="15" />
          <span class="template-row__title">{{ template.name }}</span>
          <span class="badge">{{ template.placeholders.length }} 个占位符</span>
        </div>
        <div class="template-row__placeholders">
          <code
            v-for="key in placeholderKeys(template)"
            :key="key"
            class="chip"
            :data-test="`template-placeholder-${key}`"
          >
            {{ key }}
          </code>
        </div>
        <div class="template-row__meta">
          <code>{{ template.template_id }}</code>
          <span>{{ Math.round(template.size / 1024) }} KB · {{ Object.keys(template.style_map).length }} 个样式映射</span>
        </div>
        <div class="template-row__actions">
          <button
            class="btn btn--ghost"
            type="button"
            :data-test="`template-render-${template.template_id}`"
            :disabled="renderingId === template.template_id"
            title="用此模板渲染成稿并下载"
            @click="handleRender(template)"
          >
            <Download :size="15" />
            <span>{{ renderingId === template.template_id ? "渲染中…" : "渲染" }}</span>
          </button>
          <button
            class="icon-button"
            type="button"
            :data-test="`template-delete-${template.template_id}`"
            :disabled="deletingId === template.template_id"
            :title="`删除 ${template.name}`"
            @click="handleDelete(template)"
          >
            <Trash2 :size="15" />
          </button>
        </div>
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

.upload__field input[type="text"] {
  padding: 0.35rem 0.5rem;
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

.template-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 1rem;
}

.template-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  padding: 0.625rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.template-row__main {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.template-row__title {
  font-size: 0.92rem;
  font-weight: 500;
}

.template-row__placeholders {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.chip {
  padding: 0.05rem 0.4rem;
  border-radius: 6px;
  background: #f3f4f6;
  color: #374151;
  font-size: 0.72rem;
}

.template-row__meta {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  margin-left: auto;
  text-align: right;
  font-size: 0.78rem;
  color: #6b7280;
  min-width: 0;
}

.template-row__meta code {
  color: #374151;
}

.template-row__actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.badge {
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.75rem;
  white-space: nowrap;
}
</style>
