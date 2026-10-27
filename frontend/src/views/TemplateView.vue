<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { CircleCheck, Download, FileText, Loader2, Trash2, Upload } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import FilePicker from "@/components/common/FilePicker.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { deleteTemplate, listTemplates, renderTemplate, uploadTemplate } from "@/api/templates";
import type { TemplateOut } from "@/api/types";

// Word 模板管理页（Phase 1 P1-A · 2026-10-26 重设计）：列表 + 上传 .docx + 校验渲染 + 删除
//
// ## 这次重设计修的缺陷（取证得到的）
//
// 取证（服务器真实渲染 + Playwright 结构导出）：本页「渲染」按钮发送的载荷是
// **假内容** —— `sections[key] = "示例内容（key）"`、
// `bibliography = ["[1] 示例参考文献"]`。用真实模板（7 个占位符）渲染后成稿正文逐段是
// `示例内容（title）`、`示例内容（abstract）`…，**正文总字符数 129**。
// 而页面上 `HAS_DEMO_WORD = false` —— 用户完全不知道下载到的是占位文本。
//
// 后端契约本身是**严的**：`_validate_contract` 缺占位符内容会直接 400 拒绝
// （「拒绝静默丢内容」）。所以缺陷 100% 在前端：它主动填假内容绕过了契约。
//
// **修法（用户已确认）**：不假装生成成稿。
// - 按钮从「渲染」改为「校验模板」，语义是「验证这个模板能不能用」；
// - 页面与成功提示都明确写「用示例文本填充」；
// - 真实成稿由任务详情页的导出流程产生（那里有真实综述正文与已 CSL 渲染的参考文献）。
//
// 第二个缺陷：删除无二次确认（后端会 unlink 磁盘文件），已加确认。
const ACCEPTED = [".docx"];

const templates = ref<TemplateOut[]>([]);
const loading = ref(false);
const loadError = ref<string | null>(null);
const uploadError = ref<string | null>(null);
const renderError = ref<string | null>(null);
const uploadMessage = ref<string | null>(null);
const renderMessage = ref<string | null>(null);
const uploading = ref(false);
const renderingId = ref<string | null>(null);
const deletingId = ref<string | null>(null);
/** 待确认删除的模板 id（非空时该行显示确认条）。 */
const confirmingDeleteId = ref<string | null>(null);
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
  uploadMessage.value = null;
  uploadError.value = fileError(file);
  // 名称留空时用文件名（不含扩展名）作为默认显示名
  if (file && !templateName.value) {
    const index = file.name.lastIndexOf(".");
    templateName.value = index > 0 ? file.name.slice(0, index) : file.name;
  }
}

async function handleUpload() {
  uploadMessage.value = null;
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
    uploadMessage.value = `已上传模板「${created.name}」（识别到 ${created.placeholders.length} 个占位符）`;
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

/**
 * 用**示例文本**校验模板结构（不产出真实成稿）。
 *
 * ## 为什么明确是「校验」而不是「渲染」
 *
 * 这一页没有真实综述内容可填：正文在任务里、参考文献由任务级引用样式渲染。
 * 所以这里只能用示例文本填充，目的是让用户**验证模板能不能用**
 * （占位符是否齐全、样式是否套上、能否打开）。
 *
 * 旧实现把按钮叫「渲染」、成功提示叫「已生成成稿」，
 * 用户下载后看到的却是 `示例内容（title）` 这类占位文本 —— 那是误导。
 *
 * 后端 `_validate_contract` 会在缺占位符时 400 拒绝，所以这次调用同时是一次
 * **真实的结构校验**：模板有问题会在这里暴露，不用等到真正导出时才发现。
 */
async function handleValidate(template: TemplateOut) {
  renderError.value = null;
  renderMessage.value = null;
  renderingId.value = template.template_id;
  try {
    // 示例载荷：占位符 key → 可辨识的示例文本（含 key 名，便于对照版式位置）
    const sections: Record<string, string> = {};
    const bibliography: string[] = [];
    for (const item of template.placeholders) {
      // 后端 `PlaceholderOut.sample` 有值就用它（模板作者声明的样例更贴切）
      const sample = item.sample?.trim() || `示例内容（${item.key}）`;
      if (item.key.toLowerCase().includes("ref")) {
        bibliography.push(`[1] ${sample}`);
      } else {
        sections[item.key] = sample;
      }
    }
    const blob = await renderTemplate(template.template_id, { sections, bibliography });
    downloadBlob(blob, `${template.name || template.template_id}-校验.docx`);
    renderMessage.value =
      `模板「${template.name}」校验通过，已下载填充示例文本的 .docx。` +
      `该文件仅用于确认版式与占位符是否齐全，**不是**真实综述成稿；` +
      `真实成稿请在任务详情页导出（那里使用任务正文与已渲染的参考文献）。`;
  } catch (error) {
    renderError.value = (error as Error).message || "模板校验失败";
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

/** 第一次点击：进入确认态（不删）。 */
function requestDelete(template: TemplateOut) {
  uploadError.value = null;
  renderError.value = null;
  uploadMessage.value = null;
  renderMessage.value = null;
  confirmingDeleteId.value = template.template_id;
}

function cancelDelete() {
  confirmingDeleteId.value = null;
}

/** 确认后才真正删除（后端会 unlink 磁盘文件）。 */
async function confirmDelete(template: TemplateOut) {
  confirmingDeleteId.value = null;
  uploadError.value = null;
  renderError.value = null;
  uploadMessage.value = null;
  renderMessage.value = null;
  deletingId.value = template.template_id;
  try {
    await deleteTemplate(template.template_id);
    uploadMessage.value = `已删除模板「${template.name}」`;
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

/**
 * 占位符的展示文本：**优先用后端给的 `label`**，没有才退回 key。
 *
 * 后端 `PlaceholderOut` 有 `label` / `required` / `sample` 三个字段，
 * 只显示 key 会让模板作者写的说明白写（实测服务器返回的 label 为空，
 * 但那是这份模板没声明 —— 前端不能因此永远不显示 label）。
 */
function placeholderLabel(item: TemplateOut["placeholders"][number]): string {
  const label = item.label?.trim();
  if (!label) {
    return item.key;
  }
  return label === item.key ? label : `${label}（${item.key}）`;
}
</script>

<template>
  <section>
    <PageHeader eyebrow="Document templates" title="Word 模板" :description="hint" />

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
      v-if="uploadMessage"
      variant="success"
      class="mb-3"
      data-test="template-upload-success"
    >
      <CircleCheck />
      <span>{{ uploadMessage }}</span>
    </Alert>
    <Alert
      v-if="renderMessage"
      variant="muted"
      class="mb-3"
      data-test="template-render-success"
    >
      <CircleCheck />
      <span>{{ renderMessage }}</span>
    </Alert>

    <p v-if="loading && !templates.length" class="text-sm text-muted-foreground">加载中…</p>
    <EmptyState
      v-else-if="!templates.length"
      testid="template-empty"
      title="还没有模板"
      description="上传一个 .docx 文件，Markdown 成稿就能套用它的版式导出。"
    />
    <template v-else>
      <!-- 示例标注：用户必须知道「校验」下载的是占位文本，不是真实成稿 -->
      <p
        data-test="template-sample-note"
        class="mb-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
      >
        「校验模板」会用<strong>示例文本</strong>填充占位符并下载一份 .docx，
        用于确认版式与占位符是否齐全；<strong>它不是真实综述成稿</strong>。
        真实成稿请在任务详情页导出（那里使用任务正文与已渲染的参考文献）。
      </p>

      <ul class="space-y-2">
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
                title="用示例文本填充并下载，确认模板可用（不是真实成稿）"
                @click="handleValidate(template)"
              >
                <Loader2
                  v-if="renderingId === template.template_id"
                  class="animate-spin"
                  aria-hidden="true"
                />
                <Download v-else :size="15" aria-hidden="true" />
                <span>{{ renderingId === template.template_id ? "校验中…" : "校验模板" }}</span>
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                :data-test="`template-delete-${template.template_id}`"
                :disabled="deletingId === template.template_id"
                :title="`删除 ${template.name}`"
                :aria-label="`删除 ${template.name}`"
                @click="requestDelete(template)"
              >
                <Trash2 :size="15" />
              </Button>
            </div>
          </div>

          <!-- 删除确认：后端会 unlink 磁盘文件 -->
          <div
            v-if="confirmingDeleteId === template.template_id"
            class="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-destructive/30 bg-destructive/8 px-2.5 py-1.5"
          >
            <span class="text-xs text-foreground">
              删除后文件将从磁盘移除，需要重新上传。确认删除「{{ template.name }}」？
            </span>
            <button
              type="button"
              :data-test="`template-delete-confirm-${template.template_id}`"
              class="rounded bg-destructive px-2 py-0.5 text-xs font-medium text-destructive-foreground"
              @click="confirmDelete(template)"
            >
              确认删除
            </button>
            <button
              type="button"
              :data-test="`template-delete-cancel-${template.template_id}`"
              class="rounded border border-border px-2 py-0.5 text-xs font-medium"
              @click="cancelDelete"
            >
              取消
            </button>
          </div>

          <div v-if="template.placeholders.length" class="mt-1.5 flex flex-wrap gap-1">
            <code
              v-for="item in template.placeholders"
              :key="item.key"
              class="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
              :data-test="`template-placeholder-${item.key}`"
            >
              {{ placeholderLabel(item) }}
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
    </template>
  </section>
</template>
