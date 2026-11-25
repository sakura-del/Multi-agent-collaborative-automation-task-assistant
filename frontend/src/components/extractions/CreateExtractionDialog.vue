<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useReducedMotion } from "motion-v";
import { ChevronDown, ChevronUp, Plus, Trash2, X } from "lucide-vue-next";

import {
  createExtractionTable,
  type ExtractionSourceType,
  type ExtractionTableOut,
} from "@/api/extractions";
import AnimatedCollapse from "@/components/common/AnimatedCollapse.vue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import MotionDialog from "@/components/ui/dialog/MotionDialog.vue";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EXTRACTION_TEMPLATES, type ExtractionColumnSpec } from "@/config/extraction-presets";
import { deriveExtractionKey } from "@/utils/extraction-key";

export interface ExtractionCandidateSource {
  source_type: ExtractionSourceType;
  source_id: string;
  title: string;
}

interface DraftColumn extends ExtractionColumnSpec {
  keyDirty?: boolean;
}

interface ColumnErrors {
  label?: boolean;
  key?: boolean;
  prompt?: boolean;
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

const reducedMotion = useReducedMotion();
const dialogBodyRef = ref<HTMLElement | null>(null);
const title = ref("");
const selectedTemplateId = ref<string>(EXTRACTION_TEMPLATES[0].id);
const columns = ref<DraftColumn[]>([]);
const showAdvancedKeys = ref(false);
const titleInvalid = ref(false);
const columnErrors = ref<ColumnErrors[]>([]);
const submitting = ref(false);
const error = ref<string | null>(null);

const hasAbstractOnlySources = computed(() =>
  props.sources.some((src) => src.source_type === "screening_record"),
);

function applyTemplate(templateId: string) {
  const tpl = EXTRACTION_TEMPLATES.find((item) => item.id === templateId) ?? EXTRACTION_TEMPLATES[0];
  selectedTemplateId.value = tpl.id;
  columns.value = tpl.columns.map((col) => ({ ...col, keyDirty: true }));
  columnErrors.value = [];
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    error.value = null;
    titleInvalid.value = false;
    showAdvancedKeys.value = false;
    title.value =
      props.defaultTitle?.trim() ||
      `论文对比抽取表（${props.sources.length} 篇）`;
    applyTemplate(EXTRACTION_TEMPLATES[0].id);
  },
  { immediate: true },
);

function otherKeys(excludeIndex: number): string[] {
  return columns.value
    .filter((_, idx) => idx !== excludeIndex)
    .map((c) => c.key)
    .filter(Boolean);
}

function onColLabelInput(idx: number, val: string | number) {
  const col = columns.value[idx];
  if (!col) return;
  const text = String(val);
  col.label = text;
  if (columnErrors.value[idx]?.label && text.trim()) {
    columnErrors.value[idx].label = false;
  }
  if (!col.keyDirty) {
    col.key = text.trim() ? deriveExtractionKey(text, otherKeys(idx)) : "";
  }
}

function onColKeyInput(idx: number, val: string | number) {
  const col = columns.value[idx];
  if (!col) return;
  const text = String(val);
  col.key = text;
  col.keyDirty = text.trim().length > 0;
  if (columnErrors.value[idx]?.key && /^[a-z][a-z0-9_]{0,47}$/.test(text.trim().toLowerCase())) {
    columnErrors.value[idx].key = false;
  }
}

function onColPromptInput(idx: number, val: string | number) {
  const col = columns.value[idx];
  if (!col) return;
  const text = String(val);
  col.prompt = text;
  if (columnErrors.value[idx]?.prompt && text.trim().length >= 2) {
    columnErrors.value[idx].prompt = false;
  }
}

function addBlankColumn() {
  if (columns.value.length >= 12) return;
  const nextIndex = columns.value.length + 1;
  const label = `自定义字段 ${nextIndex}`;
  const key = deriveExtractionKey(
    label,
    columns.value.map((c) => c.key),
  );
  columns.value.push({
    key,
    label,
    prompt: "提取论文中与该字段相关的明确表述与数值",
    keyDirty: false,
  });
}

function removeColumn(index: number) {
  if (columns.value.length <= 1) return;
  columns.value.splice(index, 1);
  columnErrors.value.splice(index, 1);
}

async function focusFirstInvalidControl(needExpandKeys: boolean) {
  if (needExpandKeys) {
    showAdvancedKeys.value = true;
  }
  await nextTick();
  const invalidEl = dialogBodyRef.value?.querySelector<HTMLElement>('[aria-invalid="true"]');
  if (invalidEl) {
    invalidEl.scrollIntoView?.({
      block: "center",
      behavior: reducedMotion.value ? "auto" : "smooth",
    });
    invalidEl.focus?.({ preventScroll: true });
  }
}

async function handleSubmit() {
  error.value = null;
  titleInvalid.value = false;
  columnErrors.value = columns.value.map(() => ({}));

  const trimmedTitle = title.value.trim();
  if (!trimmedTitle) {
    titleInvalid.value = true;
    error.value = "请填写抽取表标题";
    await focusFirstInvalidControl(false);
    return;
  }
  if (!props.sources.length) {
    error.value = "请至少选择一篇论文";
    return;
  }

  const normalizedCols: ExtractionColumnSpec[] = [];
  const seenKeys = new Set<string>();
  let firstErrorMessage: string | null = null;
  let needExpandKeys = false;

  for (let i = 0; i < columns.value.length; i += 1) {
    const col = columns.value[i];
    const label = col.label.trim();
    const rawKey = col.key.trim().toLowerCase();
    const key = rawKey || (label ? deriveExtractionKey(label, seenKeys) : "");
    const prompt = col.prompt.trim();

    const labelBad = !label;
    const keyBad = !/^[a-z][a-z0-9_]{0,47}$/.test(key) || seenKeys.has(key);
    const promptBad = prompt.length < 2;

    if (labelBad || keyBad || promptBad) {
      columnErrors.value[i] = { label: labelBad, key: keyBad, prompt: promptBad };
      if (!firstErrorMessage) {
        if (labelBad || promptBad) {
          firstErrorMessage = `请完善第 ${i + 1} 列的展示名称与抽取指令`;
        } else if (seenKeys.has(key)) {
          firstErrorMessage = `存在重复列标识：${key}`;
          needExpandKeys = true;
        } else {
          firstErrorMessage = `列标识「${col.key}」须以小写字母开头，仅含小写字母、数字与下划线`;
          needExpandKeys = true;
        }
      }
      continue;
    }

    seenKeys.add(key);
    normalizedCols.push({ key, label, prompt });
  }

  if (firstErrorMessage) {
    error.value = firstErrorMessage;
    await focusFirstInvalidControl(needExpandKeys);
    return;
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
  <MotionDialog
    :open="props.open"
    aria-label="新建论文抽取表"
    class="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden p-0"
    data-test="create-extraction-dialog"
    @update:open="(v) => { if (!v) emit('close'); }"
  >
    <header class="flex items-center justify-between border-b border-border px-5 py-3.5">
      <div>
        <h2 class="text-sm font-semibold text-foreground">新建论文抽取表</h2>
        <p class="mt-0.5 text-xs text-muted-foreground">
          已选 {{ sources.length }} 篇文献，可套用模板或自定义列指令
        </p>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="关闭弹窗"
        @click="emit('close')"
      >
        <X :size="16" />
      </Button>
    </header>

    <div ref="dialogBodyRef" class="flex-1 space-y-4 overflow-y-auto px-5 py-4">
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
          :aria-invalid="titleInvalid ? 'true' : undefined"
          placeholder="例如：多智能体 RAG 评测对比"
          @input="titleInvalid = false"
        />
      </div>

      <div class="space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="text-xs font-medium text-foreground">快速套用预设列模板</span>
          <div class="flex flex-wrap gap-1.5">
            <Button
              v-for="tpl in EXTRACTION_TEMPLATES"
              :key="tpl.id"
              type="button"
              size="sm"
              :variant="selectedTemplateId === tpl.id ? 'default' : 'outline'"
              :data-test="`extraction-template-${tpl.id}`"
              @click="applyTemplate(tpl.id)"
            >
              {{ tpl.label }}
            </Button>
          </div>
        </div>

        <div class="flex items-center justify-between">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            class="h-7 px-2 text-xs text-muted-foreground"
            data-test="extraction-toggle-advanced-keys"
            @click="showAdvancedKeys = !showAdvancedKeys"
          >
            <ChevronUp v-if="showAdvancedKeys" :size="13" aria-hidden="true" />
            <ChevronDown v-else :size="13" aria-hidden="true" />
            <span>自定义技术标识（默认自动生成）</span>
          </Button>
        </div>

        <div class="space-y-2">
          <div
            v-for="(col, idx) in columns"
            :key="idx"
            class="space-y-2 rounded-lg border border-border bg-muted/20 p-2.5"
          >
            <div class="grid grid-cols-1 gap-2 sm:grid-cols-[10rem_minmax(0,1fr)_auto]">
              <Input
                :model-value="col.label"
                :aria-invalid="columnErrors[idx]?.label ? 'true' : undefined"
                placeholder="列名（如样本量）"
                class="h-8 text-xs"
                :aria-label="`第 ${idx + 1} 列名称`"
                @update:model-value="(val) => onColLabelInput(idx, val)"
              />
              <Input
                :model-value="col.prompt"
                :aria-invalid="columnErrors[idx]?.prompt ? 'true' : undefined"
                placeholder="抽取指令（要求模型提取什么）"
                class="h-8 text-xs"
                :aria-label="`第 ${idx + 1} 列抽取指令`"
                @update:model-value="(val) => onColPromptInput(idx, val)"
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

            <AnimatedCollapse :open="showAdvancedKeys" :keep-mounted="true">
              <div class="grid gap-1 sm:max-w-xs">
                <Label class="text-[11px] text-muted-foreground">技术标识（key）</Label>
                <Input
                  :model-value="col.key"
                  :aria-invalid="columnErrors[idx]?.key ? 'true' : undefined"
                  placeholder="key（如 sample_size）"
                  class="h-8 font-mono text-xs"
                  :aria-label="`第 ${idx + 1} 列标识`"
                  @update:model-value="(val) => onColKeyInput(idx, val)"
                />
              </div>
            </AnimatedCollapse>
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
  </MotionDialog>
</template>
