<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { CircleCheck, Download, FileText, Loader2, Trash2, Upload } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import FilePicker from "@/components/common/FilePicker.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
const filePicker = ref<{ reset: () => void } | null>(null);

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
    filePicker.value?.reset();
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
  <section>
    <header class="mb-4">
      <h1 class="text-lg font-semibold tracking-tight">Word 模板</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">{{ hint }}</p>
    </header>

    <ErrorBanner :message="loadError" testid="template-load-error" />

    <form
      class="mb-3 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-card p-4"
      data-test="template-upload-form"
      @submit.prevent="handleUpload"
    >
      <div class="grid gap-1.5">
        <Label for="template-file">模板文件（.docx）</Label>
        <FilePicker
          id="template-file"
          ref="filePicker"
          data-test="template-file"
          :accept="ACCEPTED.join(',')"
          placeholder="选择 .docx 文件"
          @change="onFileChange"
        />
      </div>
      <div class="grid min-w-48 flex-1 gap-1.5">
        <Label for="template-name">显示名称</Label>
        <Input
          id="template-name"
          v-model="templateName"
          data-test="template-name"
          type="text"
          placeholder="如：学院毕业论文模板"
        />
      </div>
      <Button data-test="template-upload-submit" type="submit" :disabled="uploading">
        <Loader2 v-if="uploading" class="animate-spin" aria-hidden="true" />
        <Upload v-else :size="15" aria-hidden="true" />
        <span>{{ uploading ? "上传中…" : "上传模板" }}</span>
      </Button>
    </form>

    <ErrorBanner :message="uploadError" testid="template-upload-error" />
    <ErrorBanner :message="renderError" testid="template-render-error" />
    <Alert
      v-if="successMessage"
      variant="success"
      class="mb-3"
      data-test="template-upload-success"
    >
      <CircleCheck />
      <span>{{ successMessage }}</span>
    </Alert>

    <p v-if="loading && !templates.length" class="text-sm text-muted-foreground">加载中…</p>
    <EmptyState
      v-else-if="!templates.length"
      testid="template-empty"
      title="还没有模板"
      description="上传一个 .docx 文件，Markdown 成稿就能套用它的版式导出。"
    />
    <ul v-else class="space-y-2">
      <li
        v-for="template in templates"
        :key="template.template_id"
        class="rounded-lg border border-border bg-card px-3 py-2.5"
        :data-test="`template-row-${template.template_id}`"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex min-w-0 flex-wrap items-center gap-2">
            <FileText :size="15" class="shrink-0 text-muted-foreground" aria-hidden="true" />
            <span class="text-sm font-medium">{{ template.name }}</span>
            <Badge variant="muted">{{ template.placeholders.length }} 个占位符</Badge>
          </div>
          <div class="flex items-center gap-1.5">
            <Button
              variant="outline"
              size="sm"
              :data-test="`template-render-${template.template_id}`"
              :disabled="renderingId === template.template_id"
              title="用此模板渲染成稿并下载"
              @click="handleRender(template)"
            >
              <Loader2
                v-if="renderingId === template.template_id"
                class="animate-spin"
                aria-hidden="true"
              />
              <Download v-else :size="15" aria-hidden="true" />
              <span>{{ renderingId === template.template_id ? "渲染中…" : "渲染" }}</span>
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              :data-test="`template-delete-${template.template_id}`"
              :disabled="deletingId === template.template_id"
              :title="`删除 ${template.name}`"
              :aria-label="`删除 ${template.name}`"
              @click="handleDelete(template)"
            >
              <Trash2 :size="15" />
            </Button>
          </div>
        </div>

        <div v-if="placeholderKeys(template).length" class="mt-1.5 flex flex-wrap gap-1">
          <code
            v-for="key in placeholderKeys(template)"
            :key="key"
            class="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
            :data-test="`template-placeholder-${key}`"
          >
            {{ key }}
          </code>
        </div>

        <div class="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <code class="rounded bg-muted px-1 py-0.5">{{ template.template_id }}</code>
          <span>
            {{ Math.round(template.size / 1024) }} KB ·
            {{ Object.keys(template.style_map).length }} 个样式映射
          </span>
        </div>
      </li>
    </ul>
  </section>
</template>
