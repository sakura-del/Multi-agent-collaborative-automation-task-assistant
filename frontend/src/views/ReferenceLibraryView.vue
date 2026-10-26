<script setup lang="ts">
/**
 * 引用库页（2026-10-26 引用库计划 §3.6）。
 *
 * ## 这一页为什么存在
 *
 * 取证发现导入页能正确解析 BibTeX / RIS，但解析完**什么也做不了** ——
 * 没有引用库表、没有保存路径，任务文献只来自检索源。用户把 Zotero 导出的
 * `.bib` 粘进来，看到结果，然后没有下文。
 *
 * 现在链路完整：导入 → 保存 → 本页可查可删 → 创建任务时选「我的引用库」。
 *
 * ## 页面要回答的两个问题
 *
 * 1. **我库里有什么**（条数 + 逐条标题/作者/年份/DOI）；
 * 2. **它能怎么用**（顶部说明：创建任务时可选它作文献源，不外呼检索）。
 *
 * ⚠️ 测试契约（不要改）：`library-row-<item_id>` / `library-delete-<item_id>` /
 * `library-delete-confirm-<item_id>` / `library-delete-cancel-<item_id>` /
 * `library-empty` / `library-load-error` / `library-summary` / `library-hint`。
 */
import { computed, onMounted, ref } from "vue";
import { Loader2, Trash2 } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  clearLibrary,
  deleteLibraryItem,
  getLibrarySummary,
  listLibraryItems,
} from "@/api/reference-library";
import type { LibraryItemOut, LibrarySummaryOut } from "@/api/types";

const items = ref<LibraryItemOut[]>([]);
const summary = ref<LibrarySummaryOut | null>(null);
const loading = ref(false);
const loadError = ref<string | null>(null);
const actionError = ref<string | null>(null);
const successMessage = ref<string | null>(null);
const deletingId = ref<string | null>(null);
const confirmingDeleteId = ref<string | null>(null);
const clearing = ref(false);
const confirmingClear = ref(false);

const hint = computed(() => {
  if (!summary.value || summary.value.total === 0) {
    return "把 BibTeX / RIS 导入后保存到这里，创建任务时就能只用这些文献。";
  }
  return (
    `共 ${summary.value.total} 条文献，全部可作任务文献源。` +
    `创建任务时选择「我的引用库」即可只使用这些文献（不外呼检索）。`
  );
});

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  loadError.value = null;
  // 列表与摘要分别处理失败：摘要挂了不该让列表消失（AGENTS §5 #23）
  const [listResult, summaryResult] = await Promise.allSettled([
    listLibraryItems({ limit: 200 }),
    getLibrarySummary(),
  ]);

  if (listResult.status === "fulfilled") {
    items.value = listResult.value.items;
  } else {
    loadError.value = (listResult.reason as Error).message || "引用库加载失败";
  }
  if (summaryResult.status === "fulfilled") {
    summary.value = summaryResult.value;
  }
  loading.value = false;
}

function requestDelete(itemId: string) {
  actionError.value = null;
  successMessage.value = null;
  confirmingDeleteId.value = itemId;
}

function cancelDelete() {
  confirmingDeleteId.value = null;
}

async function confirmDelete(item: LibraryItemOut) {
  confirmingDeleteId.value = null;
  actionError.value = null;
  successMessage.value = null;
  deletingId.value = item.item_id;
  try {
    await deleteLibraryItem(item.item_id);
    successMessage.value = `已删除「${item.title || item.ref_id}」`;
    await refresh();
  } catch (error) {
    actionError.value = (error as Error).message || "删除失败";
  } finally {
    deletingId.value = null;
  }
}

async function confirmClear() {
  clearing.value = true;
  actionError.value = null;
  successMessage.value = null;
  try {
    const result = await clearLibrary();
    successMessage.value = `已清空引用库（${result.deleted} 条）`;
    confirmingClear.value = false;
    await refresh();
  } catch (error) {
    actionError.value = (error as Error).message || "清空失败";
  } finally {
    clearing.value = false;
  }
}
</script>

<template>
  <section>
    <header class="mb-4">
      <h1 class="text-lg font-semibold tracking-tight">引用库</h1>
      <p class="mt-0.5 text-sm text-muted-foreground" data-test="library-hint">{{ hint }}</p>
    </header>

    <ErrorBanner :message="loadError" testid="library-load-error" />
    <ErrorBanner :message="actionError" testid="library-action-error" />

    <p
      v-if="successMessage"
      class="mb-3 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm"
      data-test="library-success"
    >
      {{ successMessage }}
    </p>

    <div
      v-if="summary && summary.total > 0"
      class="mb-3 flex flex-wrap items-center justify-between gap-2"
    >
      <p class="text-xs text-muted-foreground" data-test="library-summary">
        共 <strong>{{ summary.total }}</strong> 条 · 可作文献源
        <strong>{{ summary.usable_as_source }}</strong> 条
      </p>
      <div v-if="!confirmingClear" class="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          data-test="library-clear"
          @click="confirmingClear = true"
        >
          清空引用库
        </Button>
      </div>
      <div
        v-else
        class="flex flex-wrap items-center gap-2 rounded-md border border-destructive/30 bg-destructive/8 px-2.5 py-1.5"
      >
        <span class="text-xs">清空后无法恢复，确认删除全部 {{ summary.total }} 条？</span>
        <button
          type="button"
          data-test="library-clear-confirm"
          class="rounded bg-destructive px-2 py-0.5 text-xs font-medium text-destructive-foreground"
          :disabled="clearing"
          @click="confirmClear"
        >
          {{ clearing ? "清空中…" : "确认清空" }}
        </button>
        <button
          type="button"
          data-test="library-clear-cancel"
          class="rounded border border-border px-2 py-0.5 text-xs font-medium"
          @click="confirmingClear = false"
        >
          取消
        </button>
      </div>
    </div>

    <p v-if="loading && !items.length" class="text-sm text-muted-foreground">加载中…</p>
    <EmptyState
      v-else-if="!items.length"
      testid="library-empty"
      title="引用库还是空的"
      description="去「导入文献」页粘贴 BibTeX / RIS，解析后点「保存到引用库」即可。"
    />

    <ul v-else class="space-y-2">
      <li
        v-for="item in items"
        :key="item.item_id"
        class="rounded-lg border border-border bg-card px-3 py-2.5"
        :data-test="`library-row-${item.item_id}`"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="text-sm font-medium">{{ item.title || "（无标题）" }}</div>
            <div class="mt-0.5 text-xs text-muted-foreground">
              {{ item.authors.length ? item.authors.join("；") : "（无作者）" }}
            </div>
            <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="muted">{{ item.type }}</Badge>
              <span v-if="item.container_title">{{ item.container_title }}</span>
              <span v-if="item.year">{{ item.year }}</span>
              <code v-if="item.doi" class="rounded bg-muted px-1 py-0.5">{{ item.doi }}</code>
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            :data-test="`library-delete-${item.item_id}`"
            :disabled="deletingId === item.item_id"
            :title="`删除 ${item.title || item.ref_id}`"
            :aria-label="`删除 ${item.title || item.ref_id}`"
            @click="requestDelete(item.item_id)"
          >
            <Trash2 :size="15" />
          </Button>
        </div>

        <div
          v-if="confirmingDeleteId === item.item_id"
          class="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-destructive/30 bg-destructive/8 px-2.5 py-1.5"
        >
          <span class="text-xs">确认从引用库删除「{{ item.title || item.ref_id }}」？</span>
          <button
            type="button"
            :data-test="`library-delete-confirm-${item.item_id}`"
            class="rounded bg-destructive px-2 py-0.5 text-xs font-medium text-destructive-foreground"
            @click="confirmDelete(item)"
          >
            确认删除
          </button>
          <button
            type="button"
            :data-test="`library-delete-cancel-${item.item_id}`"
            class="rounded border border-border px-2 py-0.5 text-xs font-medium"
            @click="cancelDelete"
          >
            取消
          </button>
        </div>
      </li>
    </ul>
  </section>
</template>
