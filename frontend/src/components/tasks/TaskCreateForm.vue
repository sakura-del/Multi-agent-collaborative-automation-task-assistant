<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { X } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { listCitationStyles } from "@/api/citation-styles";
import type { CitationStyleSource, TaskCreate } from "@/api/types";

// 创建表单：只做校验与 payload 组装，提交动作由父组件负责（保持可测）
const props = withDefaults(
  defineProps<{
    pending?: boolean;
    error?: string | null;
    documents?: Array<{ id: string; title: string }>;
  }>(),
  { pending: false, error: null, documents: () => [] }
);

const emit = defineEmits<{ submit: [payload: TaskCreate] }>();

const TOPIC_MIN = 5;
const TOPIC_MAX = 200;
const KEYWORD_MAX = 10;
const DOC_MIN = 1;
const DOC_MAX = 50;

interface StyleOption {
  style_id: string;
  title: string;
  source: CitationStyleSource;
}

// 列表接口不可用时的内置降级项：表单必须始终可用（Issue #4 M2 §1.5）
const BUILT_IN_STYLES: StyleOption[] = [
  { style_id: "gbt7714", title: "GB/T 7714", source: "preset" },
  { style_id: "apa", title: "APA", source: "preset" },
];

const topic = ref("");
const keywords = ref<string[]>([]);
const keywordDraft = ref("");
const maxDocuments = ref(20);
const dateFrom = ref("");
const dateTo = ref("");
const language = ref<"zh" | "en">("zh");
// null = 列表未加载成功 → 渲染内置两项（降级）；非 null = 渲染动态下拉
const styleOptions = ref<StyleOption[] | null>(null);
const citationStyle = ref("gbt7714");
const offlineDataset = ref(false);
// 引用核验默认关闭：与全局默认一致，避免用户无感知地引入外呼延迟（Issue #3 遗留项）
const verifyReferences = ref(false);
const selectedDocuments = ref<string[]>([]);
const validationMessages = ref<string[]>([]);

const canSubmit = computed(() => !props.pending);

onMounted(async () => {
  try {
    const data = await listCitationStyles();
    const options: StyleOption[] = data.styles.map((style) => ({
      style_id: style.style_id,
      title: style.title,
      source: style.source,
    }));
    if (!options.length) {
      return;
    }
    styleOptions.value = options;
    const preferred = options.find((option) => option.style_id === data.default_style_id);
    citationStyle.value = preferred ? preferred.style_id : options[0].style_id;
  } catch {
    // 列表不可用时静默降级为内置两项：创建任务不能被引用样式接口拖垮
    styleOptions.value = null;
  }
});

function addKeyword() {
  const value = keywordDraft.value.trim().replace(/,$/, "");
  if (!value) {
    return;
  }
  if (!keywords.value.includes(value)) {
    keywords.value = [...keywords.value, value];
  }
  keywordDraft.value = "";
}

function removeKeyword(keyword: string) {
  keywords.value = keywords.value.filter((item) => item !== keyword);
}

function toggleDocument(id: string, checked: boolean) {
  selectedDocuments.value = checked
    ? [...new Set([...selectedDocuments.value, id])]
    : selectedDocuments.value.filter((item) => item !== id);
}

function validate(): string[] {
  const messages: string[] = [];
  const trimmedTopic = topic.value.trim();
  if (trimmedTopic.length < TOPIC_MIN || trimmedTopic.length > TOPIC_MAX) {
    messages.push(`主题需为 ${TOPIC_MIN}–${TOPIC_MAX} 个字符`);
  }
  const pendingKeyword = keywordDraft.value.trim();
  const effectiveKeywords = pendingKeyword
    ? [...new Set([...keywords.value, pendingKeyword])]
    : keywords.value;
  if (effectiveKeywords.length < 1) {
    messages.push("关键词至少 1 个");
  }
  if (effectiveKeywords.length > KEYWORD_MAX) {
    messages.push(`关键词最多 ${KEYWORD_MAX} 个`);
  }
  const documents = Number(maxDocuments.value);
  if (!Number.isFinite(documents) || documents < DOC_MIN || documents > DOC_MAX) {
    messages.push(`文献数需在 ${DOC_MIN}–${DOC_MAX} 之间`);
  }
  if (dateFrom.value && dateTo.value && dateFrom.value > dateTo.value) {
    messages.push("起始日期不能晚于结束日期");
  }
  if (selectedDocuments.value.length > DOC_MAX) {
    messages.push(`最多选择 ${DOC_MAX} 篇知识库文档`);
  }
  return messages;
}

function handleSubmit() {
  const messages = validate();
  validationMessages.value = messages;
  if (messages.length > 0) {
    return;
  }
  const pendingKeyword = keywordDraft.value.trim();
  const payload: TaskCreate = {
    topic: topic.value.trim(),
    keywords: pendingKeyword
      ? [...new Set([...keywords.value, pendingKeyword])]
      : [...keywords.value],
    max_documents: Number(maxDocuments.value),
    date_from: dateFrom.value || null,
    date_to: dateTo.value || null,
    output_language: language.value,
    citation_style: citationStyle.value,
    offline_dataset: offlineDataset.value,
    document_ids: [...selectedDocuments.value],
    verify_references: verifyReferences.value,
  };
  emit("submit", payload);
}
</script>

<template>
  <form
    class="flex max-w-3xl flex-col gap-4 rounded-lg border border-border bg-card p-4"
    @submit.prevent="handleSubmit"
  >
    <div class="grid gap-1.5">
      <Label for="task-topic">主题</Label>
      <Input
        id="task-topic"
        v-model="topic"
        data-test="topic"
        type="text"
        :minlength="TOPIC_MIN"
        :maxlength="TOPIC_MAX"
        placeholder="例如：检索增强生成与多智能体协作的质量评估"
      />
    </div>

    <div class="grid gap-1.5">
      <Label for="task-keyword">关键词（回车添加，1–10 个）</Label>
      <Input
        id="task-keyword"
        v-model="keywordDraft"
        data-test="keyword-input"
        type="text"
        placeholder="输入关键词后按回车"
        @keydown.enter.prevent="addKeyword"
        @blur="addKeyword"
      />
      <ul v-if="keywords.length" class="flex flex-wrap gap-1.5 pt-1">
        <li
          v-for="keyword in keywords"
          :key="keyword"
          class="inline-flex items-center gap-1 rounded-md bg-secondary px-2 py-0.5 text-xs text-secondary-foreground"
        >
          <span>{{ keyword }}</span>
          <button
            type="button"
            class="rounded-sm text-muted-foreground transition-colors hover:text-foreground"
            :aria-label="`移除 ${keyword}`"
            @click="removeKeyword(keyword)"
          >
            <X :size="12" aria-hidden="true" />
          </button>
        </li>
      </ul>
    </div>

    <div class="grid gap-3 sm:grid-cols-3">
      <div class="grid gap-1.5">
        <Label for="task-documents">文献数（1–50）</Label>
        <Input
          id="task-documents"
          v-model="maxDocuments"
          data-test="max-documents"
          type="number"
          min="1"
          max="50"
        />
      </div>
      <div class="grid gap-1.5">
        <Label for="task-date-from">起始日期</Label>
        <Input id="task-date-from" v-model="dateFrom" data-test="date-from" type="date" />
      </div>
      <div class="grid gap-1.5">
        <Label for="task-date-to">结束日期</Label>
        <Input id="task-date-to" v-model="dateTo" data-test="date-to" type="date" />
      </div>
    </div>

    <div class="grid gap-3 sm:grid-cols-2">
      <div class="grid gap-1.5">
        <span class="text-sm font-medium leading-none">输出语言</span>
        <div class="inline-flex w-fit gap-1 rounded-lg bg-muted p-1" role="group">
          <button
            v-for="option in [
              { value: 'zh', label: '中文' },
              { value: 'en', label: '英文' },
            ] as const"
            :key="option.value"
            type="button"
            :data-test="`lang-${option.value}`"
            :aria-pressed="language === option.value"
            :class="[
              'rounded-md px-3 py-1 text-sm font-medium transition-colors',
              language === option.value
                ? 'bg-background text-foreground shadow-xs'
                : 'text-muted-foreground hover:text-foreground',
            ]"
            @click="language = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>

      <div class="grid gap-1.5">
        <Label v-if="styleOptions" for="task-citation-style">引用格式</Label>
        <span v-else class="text-sm font-medium leading-none">引用格式</span>
        <!-- 列表可用：动态下拉（预置 + 自定义）；不可用：内置两项降级 -->
        <select
          v-if="styleOptions"
          id="task-citation-style"
          v-model="citationStyle"
          data-test="style-select"
          class="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
        >
          <option v-for="style in styleOptions" :key="style.style_id" :value="style.style_id">
            {{ style.title }}{{ style.source === "custom" ? "（自定义）" : "" }}
          </option>
        </select>
        <div v-else class="inline-flex w-fit gap-1 rounded-lg bg-muted p-1" role="group">
          <button
            v-for="style in BUILT_IN_STYLES"
            :key="style.style_id"
            type="button"
            :data-test="`style-${style.style_id}`"
            :aria-pressed="citationStyle === style.style_id"
            :class="[
              'rounded-md px-3 py-1 text-sm font-medium transition-colors',
              citationStyle === style.style_id
                ? 'bg-background text-foreground shadow-xs'
                : 'text-muted-foreground hover:text-foreground',
            ]"
            @click="citationStyle = style.style_id"
          >
            {{ style.title }}
          </button>
        </div>
      </div>
    </div>

    <div class="grid gap-2">
      <label class="flex items-center gap-2 text-sm">
        <input
          v-model="offlineDataset"
          data-test="offline"
          type="checkbox"
          class="size-4 rounded border-input accent-primary"
        />
        <span>使用离线演示语料（冻结 10 篇，不访问 arXiv）</span>
      </label>

      <label class="flex items-start gap-2 text-sm" data-test="verify-references-label">
        <input
          v-model="verifyReferences"
          data-test="verify-references"
          type="checkbox"
          class="mt-0.5 size-4 rounded border-input accent-primary"
        />
        <span>引用核验：与 CrossRef/OpenAlex 比对文献元数据，可能增加任务耗时</span>
      </label>
    </div>

    <fieldset v-if="props.documents.length" class="grid gap-2 rounded-lg border border-border p-3">
      <legend class="px-1 text-sm font-medium">指定知识库文档（可选）</legend>
      <label
        v-for="document in props.documents"
        :key="document.id"
        class="flex items-center gap-2 text-sm"
      >
        <input
          type="checkbox"
          :data-test="`doc-${document.id}`"
          class="size-4 rounded border-input accent-primary"
          @change="toggleDocument(document.id, ($event.target as HTMLInputElement).checked)"
        />
        <span>{{ document.title }}</span>
      </label>
    </fieldset>

    <ErrorBanner
      v-if="validationMessages.length"
      :message="validationMessages.join('；')"
      testid="validation-error"
    />
    <ErrorBanner :message="props.error" testid="form-error" />

    <div class="flex gap-2">
      <Button data-test="submit" type="submit" :disabled="!canSubmit">
        <span>{{ props.pending ? "创建中…" : "创建任务" }}</span>
      </Button>
    </div>
  </form>
</template>
