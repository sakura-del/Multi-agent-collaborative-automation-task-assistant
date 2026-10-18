<script setup lang="ts">
import { computed, ref } from "vue";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import type { TaskCreate } from "@/api/types";

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

const topic = ref("");
const keywords = ref<string[]>([]);
const keywordDraft = ref("");
const maxDocuments = ref(20);
const dateFrom = ref("");
const dateTo = ref("");
const language = ref<"zh" | "en">("zh");
const citationStyle = ref<"gbt7714" | "apa">("gbt7714");
const offlineDataset = ref(false);
const selectedDocuments = ref<string[]>([]);
const validationMessages = ref<string[]>([]);

const canSubmit = computed(() => !props.pending);

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
  };
  emit("submit", payload);
}
</script>

<template>
  <form class="task-form" @submit.prevent="handleSubmit">
    <div class="field">
      <label for="task-topic">主题</label>
      <input
        id="task-topic"
        v-model="topic"
        data-test="topic"
        class="input"
        type="text"
        :minlength="TOPIC_MIN"
        :maxlength="TOPIC_MAX"
        placeholder="例如：检索增强生成与多智能体协作的质量评估"
      />
    </div>

    <div class="field">
      <label for="task-keyword">关键词（回车添加，1–10 个）</label>
      <input
        id="task-keyword"
        v-model="keywordDraft"
        data-test="keyword-input"
        class="input"
        type="text"
        @keydown.enter.prevent="addKeyword"
        @blur="addKeyword"
      />
      <ul v-if="keywords.length" class="chips">
        <li v-for="keyword in keywords" :key="keyword" class="chip">
          <span>{{ keyword }}</span>
          <button type="button" :aria-label="`移除 ${keyword}`" @click="removeKeyword(keyword)">
            ×
          </button>
        </li>
      </ul>
    </div>

    <div class="field-row">
      <div class="field">
        <label for="task-documents">文献数（1–50）</label>
        <input
          id="task-documents"
          v-model="maxDocuments"
          data-test="max-documents"
          class="input"
          type="number"
          min="1"
          max="50"
        />
      </div>
      <div class="field">
        <label for="task-date-from">起始日期</label>
        <input
          id="task-date-from"
          v-model="dateFrom"
          data-test="date-from"
          class="input"
          type="date"
        />
      </div>
      <div class="field">
        <label for="task-date-to">结束日期</label>
        <input id="task-date-to" v-model="dateTo" data-test="date-to" class="input" type="date" />
      </div>
    </div>

    <div class="field-row">
      <div class="field">
        <span class="field__label">输出语言</span>
        <div class="segmented">
          <button
            type="button"
            data-test="lang-zh"
            :class="{ active: language === 'zh' }"
            @click="language = 'zh'"
          >
            中文
          </button>
          <button
            type="button"
            data-test="lang-en"
            :class="{ active: language === 'en' }"
            @click="language = 'en'"
          >
            英文
          </button>
        </div>
      </div>
      <div class="field">
        <span class="field__label">引用格式</span>
        <div class="segmented">
          <button
            type="button"
            data-test="style-gbt7714"
            :class="{ active: citationStyle === 'gbt7714' }"
            @click="citationStyle = 'gbt7714'"
          >
            GB/T 7714
          </button>
          <button
            type="button"
            data-test="style-apa"
            :class="{ active: citationStyle === 'apa' }"
            @click="citationStyle = 'apa'"
          >
            APA
          </button>
        </div>
      </div>
    </div>

    <label class="checkbox">
      <input v-model="offlineDataset" data-test="offline" type="checkbox" />
      <span>使用离线演示语料（冻结 10 篇，不访问 arXiv）</span>
    </label>

    <fieldset v-if="props.documents.length" class="documents">
      <legend>指定知识库文档（可选）</legend>
      <label v-for="document in props.documents" :key="document.id" class="checkbox">
        <input
          type="checkbox"
          :data-test="`doc-${document.id}`"
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

    <div class="actions">
      <button class="btn btn--primary" data-test="submit" type="submit" :disabled="!canSubmit">
        {{ props.pending ? "创建中…" : "创建任务" }}
      </button>
    </div>
  </form>
</template>

<style scoped>
.task-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 720px;
}

.field__label {
  display: block;
  font-size: 0.85rem;
  margin-bottom: 0.25rem;
}

.field-row {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}

.field-row .field {
  flex: 1 1 160px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  list-style: none;
  margin-top: 0.5rem;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.1rem 0.5rem;
  background: #eff6ff;
  color: #1d4ed8;
  border-radius: 999px;
  font-size: 0.82rem;
}

.chip button {
  border: none;
  background: transparent;
  color: inherit;
  font-size: 0.9rem;
  line-height: 1;
}

.segmented {
  display: inline-flex;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
}

.segmented button {
  padding: 0.4rem 0.75rem;
  border: none;
  background: var(--color-card);
  font-size: 0.88rem;
}

.segmented button.active {
  background: var(--color-primary);
  color: #fff;
}

.checkbox {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.88rem;
}

.documents {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.documents legend {
  font-size: 0.85rem;
  padding: 0 0.25rem;
}

.actions {
  display: flex;
  gap: 0.5rem;
}
</style>
