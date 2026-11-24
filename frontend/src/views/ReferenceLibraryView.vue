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
 * `library-empty` / `library-load-error` / `library-summary` / `library-hint` /
 * `library-edit-<item_id>` / `library-edit-form-<item_id>` /
 * `library-edit-title-<item_id>` / `library-edit-year-<item_id>` /
 * `library-edit-save-<item_id>` / `library-edit-cancel-<item_id>`。
 *
 * ## 编辑为什么只开三个字段（2026-10-26 遗留计划 §3.5）
 *
 * 真实需求是「改个错字」和「补一个漏掉的年份」，不是完整书目编辑器。
 * 开全部 15 个字段会让这一行变成表单墙，反而没人用。
 * 需要改冷门字段时删了重导更直接。
 */
import { computed, onMounted, ref } from "vue";
import { Loader2, Pencil, Search, Trash2, X } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import { Input } from "@/components/ui/input";
import {
  clearLibrary,
  deleteLibraryItem,
  getLibrarySummary,
  listLibraryItems,
  updateLibraryItem,
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

// ===== 编辑状态 =====
const editingId = ref<string | null>(null);
const editTitle = ref("");
const editYear = ref("");
const editContainerTitle = ref("");
const savingEdit = ref(false);

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

/** 每页条数：与任务列表一致。原来一次拉 200 条，第 201 条起永远看不到。 */
const PAGE_SIZE = 20;
const page = ref(1);
// 搜索与年份筛选（Issue #56）
const searchQuery = ref("");
const yearFilter = ref("");
const filteredTotal = ref<number | null>(null);
const hasActiveFilter = computed(
  () => searchQuery.value.trim().length > 0 || yearFilter.value.trim().length > 0,
);
const effectiveTotal = computed(() => filteredTotal.value ?? summary.value?.total ?? 0);
const totalPages = computed(() => Math.max(1, Math.ceil(effectiveTotal.value / PAGE_SIZE)));

async function refresh(target = page.value) {
  loading.value = true;
  loadError.value = null;
  const current = Math.max(1, Math.floor(target));
  const cleanQuery = searchQuery.value.trim();
  const parsedYear = /^\d{4}$/.test(yearFilter.value.trim())
    ? Number(yearFilter.value.trim())
    : undefined;
  const listParams: { limit: number; offset: number; q?: string; year?: number } = {
    limit: PAGE_SIZE,
    offset: (current - 1) * PAGE_SIZE,
  };
  if (cleanQuery) {
    listParams.q = cleanQuery;
  }
  if (parsedYear !== undefined) {
    listParams.year = parsedYear;
  }
  // 列表与摘要分别处理失败：摘要挂了不该让列表消失（AGENTS §5 #23）
  const [listResult, summaryResult] = await Promise.allSettled([
    listLibraryItems(listParams),
    getLibrarySummary(),
  ]);

  if (listResult.status === "fulfilled") {
    items.value = listResult.value.items;
    filteredTotal.value = listResult.value.total;
  } else {
    loadError.value = (listResult.reason as Error).message || "引用库加载失败";
  }
  if (summaryResult.status === "fulfilled") {
    summary.value = summaryResult.value;
  }
  page.value = current;
  loading.value = false;
}

function applyFilter() {
  void refresh(1);
}

function resetFilter() {
  searchQuery.value = "";
  yearFilter.value = "";
  void refresh(1);
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
    // 删的是本页最后一条时回退一页，避免停在空白页
    await refresh(items.value.length <= 1 && page.value > 1 ? page.value - 1 : page.value);
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

// ===== 编辑 =====

function startEdit(item: LibraryItemOut) {
  actionError.value = null;
  successMessage.value = null;
  confirmingDeleteId.value = null;
  editingId.value = item.item_id;
  editTitle.value = item.title;
  editYear.value = item.year === null ? "" : String(item.year);
  editContainerTitle.value = item.container_title ?? "";
}

function cancelEdit() {
  editingId.value = null;
}

async function saveEdit(item: LibraryItemOut) {
  // 只发**真正改了的**字段：后端用 exclude_unset 判断，
  // 发全量会把用户没动的字段覆盖成空值。
  const changes: Record<string, unknown> = {};
  const title = editTitle.value.trim();
  if (title !== item.title) {
    changes.title = title;
  }
  const yearText = editYear.value.trim();
  const yearNumber = yearText === "" ? null : Number(yearText);
  if (yearNumber !== item.year) {
    if (yearNumber !== null && (!Number.isInteger(yearNumber) || yearNumber < 1000 || yearNumber > 2200)) {
      actionError.value = "年份需为 1000–2200 之间的整数";
      return;
    }
    changes.issued_year = yearNumber;
  }
  const containerTitle = editContainerTitle.value.trim();
  if (containerTitle !== (item.container_title ?? "")) {
    changes.container_title = containerTitle || null;
  }

  if (Object.keys(changes).length === 0) {
    editingId.value = null;
    return;
  }

  savingEdit.value = true;
  actionError.value = null;
  successMessage.value = null;
  try {
    await updateLibraryItem(item.item_id, changes);
    successMessage.value = `已更新「${title || item.ref_id}」`;
    editingId.value = null;
    await refresh();
  } catch (error) {
    // 后端会重新跑准入检查（把年份改空 → 400），原因要如实展示
    actionError.value = (error as Error).message || "保存失败";
  } finally {
    savingEdit.value = false;
  }
}
</script>

<template>
  <section>
    <PageHeader
      eyebrow="Reference library"
      title="引用库"
      :description="hint"
      description-testid="library-hint"
    />

    <ErrorBanner :message="loadError" testid="library-load-error" />
    <ErrorBanner :message="actionError" testid="library-action-error" />

    <p
      v-if="successMessage"
      class="mb-3 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm"
      data-test="library-success"
    >
      {{ successMessage }}
    </p>

    <!--
      摘要始终渲染（0 条也显示）：用户需要知道「引用库为空」这个事实本身。
      真实浏览器验证时发现：原先 `v-if="summary && summary.total > 0"` 把摘要
      和清空按钮一起藏了，空库时页面只有一句「还是空的」，看不到计数。
      清空按钮只在有条目时才显示（空库没什么可清）。
    -->
    <div v-if="summary" class="mb-3 flex flex-wrap items-center justify-between gap-2">
      <p class="text-xs text-muted-foreground" data-test="library-summary">
        共 <strong>{{ summary.total }}</strong> 条 · 可作文献源
        <strong>{{ summary.usable_as_source }}</strong> 条
      </p>
      <div v-if="summary.total > 0 && !confirmingClear" class="flex items-center gap-2">
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
        v-else-if="summary.total > 0"
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

    <!-- 搜索与年份筛选栏（Issue #56）：仅在引用库非空或已有筛选条件时展示 -->
    <form
      v-if="(summary?.total ?? 0) > 0 || hasActiveFilter"
      class="mb-3 flex flex-wrap items-center gap-2"
      data-test="library-filter-bar"
      @submit.prevent="applyFilter"
    >
      <div class="relative min-w-52 flex-1 sm:max-w-xs">
        <Search
          :size="14"
          class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          v-model="searchQuery"
          data-test="library-search"
          type="search"
          placeholder="搜索题名 / 作者 / DOI / 期刊…"
          aria-label="搜索引用库"
          class="h-8 pl-8 text-xs"
        />
      </div>
      <Input
        v-model="yearFilter"
        data-test="library-year-filter"
        type="text"
        inputmode="numeric"
        maxlength="4"
        placeholder="年份（如 2024）"
        aria-label="按年份筛选"
        class="h-8 w-32 text-xs"
      />
      <Button type="submit" variant="outline" size="sm" data-test="library-filter-submit">
        筛选
      </Button>
      <Button
        v-if="hasActiveFilter"
        type="button"
        variant="ghost"
        size="sm"
        data-test="library-filter-reset"
        @click="resetFilter"
      >
        <X :size="13" aria-hidden="true" />
        清除
      </Button>
      <span v-if="hasActiveFilter && filteredTotal !== null" class="text-xs text-muted-foreground">
        匹配 {{ filteredTotal }} 条
      </span>
    </form>

    <p v-if="loading && !items.length" class="text-sm text-muted-foreground">加载中…</p>
    <div
      v-else-if="!items.length && hasActiveFilter"
      data-test="library-filtered-empty"
      class="rounded-lg border border-border bg-card px-4 py-8 text-center"
    >
      <p class="text-sm font-medium">没有符合条件的文献</p>
      <p class="mt-1 text-xs text-muted-foreground">试试更换关键词或清空年份筛选。</p>
      <Button
        variant="outline"
        size="sm"
        class="mt-3"
        data-test="library-filtered-empty-reset"
        @click="resetFilter"
      >
        清除筛选
      </Button>
    </div>
    <EmptyState
      v-else-if="!items.length"
      testid="library-empty"
      title="引用库还是空的"
      description="去「导入文献」页粘贴 BibTeX / RIS，解析后点「保存到引用库」即可。"
    />

    <ul v-else class="space-y-2">
      <Card
        v-for="item in items"
        :key="item.item_id"
        as="li"
        size="compact"
        class="gap-0 px-3 py-2.5"
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
          <div class="flex shrink-0 items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              :data-test="`library-edit-${item.item_id}`"
              :disabled="editingId === item.item_id"
              :title="`编辑 ${item.title || item.ref_id}`"
              :aria-label="`编辑 ${item.title || item.ref_id}`"
              @click="startEdit(item)"
            >
              <Pencil :size="15" />
            </Button>
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
        </div>

        <!-- 编辑表单：只开三个最常改的字段（见文件头注释） -->
        <div
          v-if="editingId === item.item_id"
          class="mt-2 space-y-2 rounded-md border border-border bg-muted/30 px-2.5 py-2"
          :data-test="`library-edit-form-${item.item_id}`"
        >
          <label class="block text-xs">
            <span class="text-muted-foreground">题名</span>
            <Input
              v-model="editTitle"
              :data-test="`library-edit-title-${item.item_id}`"
              class="mt-0.5 h-8 text-sm"
            />
          </label>
          <div class="grid grid-cols-2 gap-2">
            <label class="block text-xs">
              <span class="text-muted-foreground">年份（留空将无法作为文献源）</span>
              <Input
                v-model="editYear"
                :data-test="`library-edit-year-${item.item_id}`"
                inputmode="numeric"
                class="mt-0.5 h-8 text-sm"
              />
            </label>
            <label class="block text-xs">
              <span class="text-muted-foreground">期刊 / 会议</span>
              <Input
                v-model="editContainerTitle"
                :data-test="`library-edit-container-${item.item_id}`"
                class="mt-0.5 h-8 text-sm"
              />
            </label>
          </div>
          <div class="flex items-center gap-2">
            <button
              type="button"
              :data-test="`library-edit-save-${item.item_id}`"
              class="rounded bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground"
              :disabled="savingEdit"
              @click="saveEdit(item)"
            >
              {{ savingEdit ? "保存中…" : "保存" }}
            </button>
            <button
              type="button"
              :data-test="`library-edit-cancel-${item.item_id}`"
              class="rounded border border-border px-2 py-0.5 text-xs font-medium"
              @click="cancelEdit"
            >
              取消
            </button>
          </div>
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
      </Card>
    </ul>

    <div
      v-if="totalPages > 1"
      class="mt-3 flex flex-wrap items-center justify-between gap-2"
    >
      <span class="text-xs text-muted-foreground" data-test="library-page-summary">
        共 {{ effectiveTotal }} 条 · 第 {{ page }} / {{ totalPages }} 页
      </span>
      <Pagination
        :model-value="page"
        :total-pages="totalPages"
        aria-label="引用库分页"
        @update:model-value="refresh"
      />
    </div>
  </section>
</template>
