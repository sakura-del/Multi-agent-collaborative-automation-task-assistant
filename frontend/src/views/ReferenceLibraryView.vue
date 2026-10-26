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
import { Loader2, Pencil, Trash2 } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
      </li>
    </ul>
  </section>
</template>
