<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { AlertTriangle, CheckCircle2, FileText, Layers, Search } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import DocumentTable from "@/components/knowledge/DocumentTable.vue";
import DocumentUpload from "@/components/knowledge/DocumentUpload.vue";
import { useKnowledgeStore } from "@/stores/knowledge";
import type { KnowledgeDocument } from "@/api/types";
import { docTypeLabel, needsAttention } from "@/utils/knowledge-display";

// 知识库页：汇总（含**真实**索引状态）+ 搜索/筛选 + 上传 + 文档表。
//
// ## 这次重设计解决什么（2026-10-23，截图实测）
//
// 1. **索引状态曾是假的**：`/knowledge/stats` 硬编码 `vector_index_ready=True`，
//    页面若展示它就是把谎言做成 UI（本会话已因同类问题导致向量检索静默失效）。
//    后端已改为真实计算，页面据此显示「就绪 / 未就绪」。
// 2. **没有汇总**：原先只有一行「11 篇文档 · 2835 个分块」，看不出「有没有要
//    我处理的」。现在三张卡：文档/分块、需要处理（索引失败）、索引状态。
// 3. **没有搜索/筛选**：11 篇能扫，100+ 不可用。
//
// ⚠️ 契约（E2E `literature-review` 直接操作，不要收起来）：
// `document-file` / `document-title` / `document-submit` / `document-table`。
const store = useKnowledgeStore();
const actionError = ref<string | null>(null);
const keyword = ref("");
/** 类型筛选：`all` 或具体 doc_type。 */
const typeFilter = ref<string>("all");

onMounted(() => {
  void refresh();
});

async function refresh() {
  actionError.value = null;
  try {
    await store.loadAll();
  } catch (error) {
    actionError.value = (error as Error).message;
  }
}

async function handleRemove(docId: string) {
  actionError.value = null;
  try {
    await store.remove(docId);
  } catch (error) {
    actionError.value = (error as Error).message;
  }
}

// ===== 汇总 =====

const documents = computed<KnowledgeDocument[]>(() => store.documents);
const totalDocs = computed(() => store.stats?.total_docs ?? documents.value.length);
const totalChunks = computed(() => store.stats?.total_chunks ?? 0);
/** 索引失败文档数：唯一需要用户动手的一类。 */
const failedCount = computed(() => {
  const fromStats = store.stats?.index_failed_count;
  return typeof fromStats === "number" ? fromStats : documents.value.filter(needsAttention).length;
});
/** 正在索引：只需等待，不需要操作。 */
const indexingCount = computed(() => store.stats?.indexing_count ?? 0);
const indexReady = computed(() => Boolean(store.stats?.vector_index_ready));

// ===== 类型分布（筛选用的动态选项） =====

const typeOptions = computed(() => {
  const counts = new Map<string, number>();
  for (const item of documents.value) {
    const key = (item.doc_type || "").toLowerCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => ({ type, count, label: docTypeLabel(type) }));
});

// ===== 搜索 + 筛选 =====

const visibleDocuments = computed<KnowledgeDocument[]>(() => {
  const text = keyword.value.trim().toLowerCase();
  return documents.value.filter((item) => {
    if (typeFilter.value !== "all" && (item.doc_type || "").toLowerCase() !== typeFilter.value) {
      return false;
    }
    if (!text) {
      return true;
    }
    // 搜索标题 + 原始文件名（用户按哪个记都能搜到）
    return (
      (item.title ?? "").toLowerCase().includes(text) ||
      (item.original_name ?? "").toLowerCase().includes(text)
    );
  });
});

const isFiltered = computed(() => typeFilter.value !== "all" || keyword.value.trim().length > 0);
</script>

<template>
  <section>
    <header class="mb-5">
      <h1 class="text-xl font-semibold tracking-tight">知识库</h1>
      <p class="mt-1 text-sm text-muted-foreground">
        文献综述的证据来源。支持 pdf / docx / md / txt，单文件 ≤ 30 MB。
      </p>
    </header>

    <ErrorBanner :message="actionError" />

    <!-- 汇总：三张卡。第二张「需要处理」只在 >0 时用警示色 —— 它是唯一
         需要用户动手的信息（索引失败要重新上传）。 -->
    <div class="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
      <div
        data-test="kb-stat-docs"
        class="rounded-lg border border-border bg-card p-3"
      >
        <p class="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          <FileText :size="12" aria-hidden="true" /> 文档 / 分块
        </p>
        <p class="mt-1 text-2xl font-semibold text-foreground tabular-nums">
          {{ totalDocs }}
          <span class="text-sm font-normal text-muted-foreground">
            / {{ totalChunks.toLocaleString() }}
          </span>
        </p>
      </div>

      <div
        data-test="kb-stat-failed"
        :class="[
          'rounded-lg border p-3',
          failedCount > 0
            ? 'border-warning/30 bg-warning/8'
            : 'border-border bg-card',
        ]"
      >
        <p class="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          <AlertTriangle :size="12" aria-hidden="true" /> 需要处理
        </p>
        <p
          :class="[
            'mt-1 text-2xl font-semibold tabular-nums',
            failedCount > 0 ? 'text-warning' : 'text-muted-foreground',
          ]"
        >
          {{ failedCount }}
        </p>
        <p v-if="indexingCount > 0" class="mt-0.5 text-[11px] text-muted-foreground">
          另有 {{ indexingCount }} 篇索引中
        </p>
      </div>

      <!-- **真实性关键**：显示的是后端实算的 vector_index_ready，不是硬编码 -->
      <div data-test="kb-index-status" class="rounded-lg border border-border bg-card p-3">
        <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          向量索引
        </p>
        <p
          :class="[
            'mt-1 flex items-center gap-1.5 text-sm font-medium',
            indexReady ? 'text-success' : 'text-warning',
          ]"
        >
          <CheckCircle2 v-if="indexReady" :size="15" aria-hidden="true" />
          <AlertTriangle v-else :size="15" aria-hidden="true" />
          <span>{{ indexReady ? "就绪" : "未就绪（向量与分块不一致）" }}</span>
        </p>
        <p v-if="!indexReady" class="mt-1 text-[11px] text-muted-foreground">
          语义检索可能失效，请联系管理员重建索引
        </p>
      </div>
    </div>

    <DocumentUpload @uploaded="refresh" />

    <!-- 搜索 + 类型筛选 -->
    <div v-if="documents.length" class="mb-3 flex flex-wrap items-center gap-2">
      <div class="flex flex-wrap gap-1" role="group" aria-label="按类型筛选">
        <button
          type="button"
          data-test="kb-filter-all"
          :aria-pressed="typeFilter === 'all'"
          :class="[
            'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            typeFilter === 'all'
              ? 'bg-foreground text-background'
              : 'bg-muted text-muted-foreground hover:text-foreground',
          ]"
          @click="typeFilter = 'all'"
        >
          全部
          <span class="ml-1 tabular-nums opacity-70">{{ documents.length }}</span>
        </button>
        <button
          v-for="option in typeOptions"
          :key="option.type"
          type="button"
          :data-test="`kb-filter-${option.type}`"
          :aria-pressed="typeFilter === option.type"
          :class="[
            'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            typeFilter === option.type
              ? 'bg-foreground text-background'
              : 'bg-muted text-muted-foreground hover:text-foreground',
          ]"
          @click="typeFilter = option.type"
        >
          {{ option.label }}
          <span class="ml-1 tabular-nums opacity-70">{{ option.count }}</span>
        </button>
      </div>

      <div class="relative ml-auto w-full sm:w-64">
        <Search
          :size="14"
          class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <input
          v-model="keyword"
          data-test="kb-search"
          type="search"
          placeholder="搜索标题或文件名…"
          aria-label="搜索文档"
          class="w-full rounded-md border border-input bg-card py-1.5 pr-2.5 pl-8 text-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring"
        />
      </div>
    </div>

    <p v-if="store.loading && !documents.length" class="text-sm text-muted-foreground">
      加载中…
    </p>

    <!-- 真·空态（区别于筛选无结果） -->
    <EmptyState
      v-else-if="!documents.length"
      testid="knowledge-empty"
      title="知识库还是空的"
      description="上传一篇文档（pdf / docx / md / txt）即可用于文献综述的证据检索。"
    >
      <template #icon>
        <Layers class="mb-1 size-6 text-muted-foreground" aria-hidden="true" />
      </template>
    </EmptyState>

    <!-- 筛选/搜索无结果：给「怎么回到有结果」的引导 -->
    <div
      v-else-if="!visibleDocuments.length"
      data-test="kb-filtered-empty"
      class="rounded-lg border border-border bg-card px-4 py-10 text-center"
    >
      <p class="text-sm font-medium">没有符合条件的文档</p>
      <p class="mt-1 text-xs text-muted-foreground">试试换个关键词，或切回「全部」。</p>
      <button
        type="button"
        data-test="kb-reset-filters"
        class="mt-3 rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
        @click="
          typeFilter = 'all';
          keyword = '';
        "
      >
        清除筛选
      </button>
    </div>

    <DocumentTable v-else :documents="visibleDocuments" @remove="handleRemove" />

    <p
      v-if="isFiltered && visibleDocuments.length"
      class="mt-2 text-xs text-muted-foreground"
      data-test="kb-filter-summary"
    >
      显示 {{ visibleDocuments.length }} / {{ documents.length }} 篇
    </p>
  </section>
</template>
