<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Plus, Trash2, X } from "lucide-vue-next";

import {
  createExtractionTable,
  type ExtractionSourceType,
  type ExtractionTableOut,
} from "@/api/extractions";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EXTRACTION_TEMPLATES, type ExtractionColumnSpec } from "@/config/extraction-presets";

export interface ExtractionCandidateSource {
  source_type: ExtractionSourceType;
  source_id: string;
  title: string;
}

const props = defineProps<{
  open: boolean;
  sources: ExtractionCandidateSource[];
  taskId?: string | null;
  defaultTitle?: string;
}>();

const emit = defineEmits<{
  close: [];
  created: [table: ExtractionTableOut];
}>();

const title = ref("");
const selectedTemplateId = ref<string>(EXTRACTION_TEMPLATES[0].id);
const columns = ref<ExtractionColumnSpec[]>([]);
const submitting = ref(false);
const error = ref<string | null>(null);

const hasAbstractOnlySources = computed(() =>
  props.sources.some((src) => src.source_type === "screening_record"),
);

function applyTemplate(templateId: string) {
  const tpl = EXTRACTION_TEMPLATES.find((item) => item.id === templateId) ?? EXTRACTION_TEMPLATES[0];
  selectedTemplateId.value = tpl.id;
  columns.value = tpl.columns.map((col) => ({ ...col }));
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    error.value = null;
    title.value =
      props.defaultTitle?.trim() ||
      `论文对比抽取表（${props.sources.length} 篇）`;
    applyTemplate(EXTRACTION_TEMPLATES[0].id);
  },
  { immediate: true },
);

function addBlankColumn() {
  if (columns.value.length >= 12) return;
  const nextIndex = columns.value.length + 1;
  columns.value.push({
    key: `custom_col_${nextIndex}`,
    label: `自定义字段 ${nextIndex}`,
    prompt: "提取论文中与该字段相关的明确表述与数值",
  });
}

function removeColumn(index: number) {
  if (columns.value.length <= 1) return;
  columns.value.splice(index, 1);
}

async function handleSubmit() {
  error.value = null;
  const trimmedTitle = title.value.trim();
  if (!trimmedTitle) {
    error.value = "请填写抽取表标题";
    return;
  }
  if (!props.sources.length) {
    error.value = "请至少选择一篇论文";
    return;
  }
  const normalizedCols: ExtractionColumnSpec[] = [];
  const seenKeys = new Set<string>();
  for (const col of columns.value) {
    const key = col.key.trim().toLowerCase();
    const label = col.label.trim();
    const prompt = col.prompt.trim();
    if (!/^[a-z][a-z0-9_]{0,47}$/.test(key)) {
      error.value = `列标识「${col.key}」须以小写字母开头，仅含小写字母、数字与下划线`;
      return;
    }
    if (seenKeys.has(key)) {
      error.value = `存在重复列标识：${key}`;
      return;
    }
    if (!label || prompt.length < 2) {
      error.value = `请完善列「${key}」的展示名称与抽取指令`;
      return;
    }
    seenKeys.add(key);
    normalizedCols.push({ key, label, prompt });
  }

  submitting.value = true;
  try {
    const created = await createExtractionTable({
      title: trimmedTitle,
      task_id: props.taskId ?? null,
      columns: normalizedCols,
      sources: props.sources.map((src) => ({
        source_type: src.source_type,
        source_id: src.source_id,
      })),
      auto_run: true,
    });
    emit("created", created);
  } catch (err) {
    error.value = (err as Error).message || "创建抽取表失败";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-test="create-extraction-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="新建论文抽取表"
      @click.self="emit('close')"
    >
      <div class="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-xl">
        <header class="flex items-center justify-between border-b border-border px-5 py-3.5">
          <div>
            <h2 class="text-sm font-semibold text-foreground">新建论文抽取表</h2>
            <p class="mt-0.5 text-xs text-muted-foreground">
              已选 {{ sources.length }} 篇文献，可套用模板或自定义列指令
            </p>
          </div>
          <button
            type="button"
            class="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="关闭弹窗"
            @click="emit('close')"
          >
            <X :size="16" />
          </button>
        </header>

        <div class="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <Alert
            v-if="hasAbstractOnlySources"
            variant="warning"
            data-test="extraction-abstract-warning"
          >
            外部检索文献仅基于摘要抽取，未提及字段将标为未报告
          </Alert>

          <Alert v-if="error" variant="destructive" data-test="extraction-create-error">
            {{ error }}
          </Alert>

          <div class="grid gap-1.5">
            <Label for="extraction-title-input" class="text-xs">表名</Label>
            <Input
              id="extraction-title-input"
              v-model="title"
              data-test="extraction-title-input"
              placeholder="例如：多智能体 RAG 评测对比"
            />
          </div>

          <div class="space-y-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="text-xs font-medium text-foreground">快速套用预设列模板</span>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="tpl in EXTRACTION_TEMPLATES"
                  :key="tpl.id"
                  type="button"
                  :data-test="`extraction-template-${tpl.id}`"
                  :class="[
                    'rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
                    selectedTemplateId === tpl.id
                      ? 'border-foreground bg-foreground text-background'
                      : 'border-border bg-muted/40 text-muted-foreground hover:text-foreground',
                  ]"
                  @click="applyTemplate(tpl.id)"
                >
                  {{ tpl.label }}
                </button>
              </div>
            </div>

            <div class="space-y-2">
              <div
                v-for="(col, idx) in columns"
                :key="idx"
                class="grid grid-cols-1 gap-2 rounded-lg border border-border bg-muted/20 p-2.5 sm:grid-cols-[9rem_9rem_minmax(0,1fr)_auto]"
              >
                <Input
                  v-model="col.label"
                  placeholder="列名（如样本量）"
                  class="h-8 text-xs"
                  :aria-label="`第 ${idx + 1} 列名称`"
                />
                <Input
                  v-model="col.key"
                  placeholder="key（如 sample_size）"
                  class="h-8 font-mono text-xs"
                  :aria-label="`第 ${idx + 1} 列标识`"
                />
                <Input
                  v-model="col.prompt"
                  placeholder="抽取指令（要求模型提取什么）"
                  class="h-8 text-xs"
                  :aria-label="`第 ${idx + 1} 列抽取指令`"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  :disabled="columns.length <= 1"
                  aria-label="删除列"
                  @click="removeColumn(idx)"
                >
                  <Trash2 :size="14" />
                </Button>
              </div>
            </div>

            <Button
              type="button"
              variant="outline"
              size="sm"
              :disabled="columns.length >= 12"
              data-test="extraction-add-draft-col"
              @click="addBlankColumn"
            >
              <Plus :size="13" aria-hidden="true" />
              添加列（{{ columns.length }}/12）
            </Button>
          </div>
        </div>

        <footer class="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <Button type="button" variant="outline" size="sm" @click="emit('close')">取消</Button>
          <Button
            type="button"
            size="sm"
            :disabled="submitting"
            data-test="extraction-create-submit"
            @click="handleSubmit"
          >
            {{ submitting ? "创建中…" : "创建并开始抽取" }}
          </Button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>
