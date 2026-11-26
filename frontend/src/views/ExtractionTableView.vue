<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { routerKey } from "vue-router";
import { useReducedMotion } from "motion-v";
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Download,
  FileSearch,
  Loader2,
  Plus,
  Quote,
  RefreshCw,
  Table2,
  Trash2,
  X,
} from "lucide-vue-next";

import {
  addExtractionColumn,
  deleteExtractionTable,
  exportExtractionTableCsv,
  getExtractionTable,
  listExtractionTables,
  runExtractionTable,
  type ExtractionCellOut,
  type ExtractionSourceOut,
  type ExtractionTableOut,
  type ExtractionTableStatus,
  type ExtractionTableSummaryOut,
} from "@/api/extractions";
import AnimatedCollapse from "@/components/common/AnimatedCollapse.vue";
import ConfirmBar from "@/components/common/ConfirmBar.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { Alert } from "@/components/ui/alert";
import { Badge, type BadgeVariants } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import MotionDrawer from "@/components/ui/drawer/MotionDrawer.vue";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDelayedLoading } from "@/composables/useDelayedLoading";
import { toastSuccess } from "@/lib/toast";
import { highlightQuote } from "@/utils/claim-highlight";
import { formatEvidenceLocator } from "@/utils/claim-verdict";
import { deriveExtractionKey } from "@/utils/extraction-key";
import { formatDateTime } from "@/utils/task-status";

const props = defineProps<{
  tableId?: string;
}>();
const router = inject(routerKey, null);
const reducedMotion = useReducedMotion();

const loading = ref(false);
const showSkeleton = useDelayedLoading(loading);
const error = ref<string | null>(null);
const tables = ref<ExtractionTableSummaryOut[]>([]);
const table = ref<ExtractionTableOut | null>(null);
const activeTableId = ref<string | null>(props.tableId || null);

// 列表删除二次确认
const confirmDeleteId = ref<string | null>(null);
const deletingId = ref<string | null>(null);

// 导出 CSV 选项
const includeQuotes = ref(true);
const exporting = ref(false);

// 添加列折叠表单与 B3 自动派生
const addColOpen = ref(false);
const showAdvancedKey = ref(false);
const keyDirty = ref(false);
let syncingKey = false;
const newColLabel = ref("");
const newColKey = ref("");
const newColPrompt = ref("");
const addingCol = ref(false);
const addColErrors = ref<{ label?: boolean; key?: boolean; prompt?: boolean }>({});
const addColFormRef = ref<HTMLElement | null>(null);
const running = ref(false);

// 原文溯源抽屉
interface ActiveCellContext {
  source: ExtractionSourceOut;
  columnLabel: string;
  cell: ExtractionCellOut;
}
const drawerCell = ref<ActiveCellContext | null>(null);

const STATUS_LABELS: Record<ExtractionTableStatus, string> = {
  draft: "草稿",
  running: "抽取中",
  completed: "已完成",
  failed: "失败",
};

const STATUS_VARIANTS: Record<ExtractionTableStatus, NonNullable<BadgeVariants["variant"]>> = {
  draft: "muted",
  running: "warning",
  completed: "success",
  failed: "destructive",
};

let pollTimer: ReturnType<typeof setInterval> | null = null;

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function ensurePolling() {
  if (table.value?.status === "running" && activeTableId.value) {
    if (pollTimer === null) {
      pollTimer = setInterval(() => {
        if (activeTableId.value) {
          void loadDetail(activeTableId.value, { silent: true });
        }
      }, 3000);
    }
  } else {
    stopPolling();
  }
}

const cellMap = computed(() => {
  const map = new Map<string, ExtractionCellOut>();
  for (const cell of table.value?.cells ?? []) {
    map.set(`${cell.source_type}:${cell.source_id}:${cell.column_key}`, cell);
  }
  return map;
});

function getCell(source: ExtractionSourceOut, colKey: string): ExtractionCellOut | null {
  return cellMap.value.get(`${source.source_type}:${source.source_id}:${colKey}`) ?? null;
}

function sourceScope(source: ExtractionSourceOut): "full_text" | "abstract_only" {
  return source.source_type === "screening_record" ? "abstract_only" : "full_text";
}

const drawerHighlight = computed(() => {
  const cell = drawerCell.value?.cell;
  if (!cell?.passage) {
    return { html: "", matched: false };
  }
  return highlightQuote(cell.passage, cell.quote);
});

const drawerLocatorLabel = computed(() =>
  formatEvidenceLocator(drawerCell.value?.cell.locator),
);

async function loadList() {
  loading.value = true;
  error.value = null;
  stopPolling();
  try {
    tables.value = await listExtractionTables();
  } catch (err) {
    error.value = (err as Error).message || "加载抽取表列表失败";
  } finally {
    loading.value = false;
  }
}

async function loadDetail(id: string, options: { silent?: boolean } = {}) {
  if (!options.silent) {
    loading.value = true;
  }
  error.value = null;
  try {
    table.value = await getExtractionTable(id);
    ensurePolling();
  } catch (err) {
    error.value = (err as Error).message || "加载抽取表详情失败";
    stopPolling();
  } finally {
    if (!options.silent) {
      loading.value = false;
    }
  }
}

function openTable(id: string) {
  activeTableId.value = id;
  drawerCell.value = null;
  if (router) {
    void router.push({ name: "extractions", params: { tableId: id } });
  }
  void loadDetail(id);
}

function backToList() {
  stopPolling();
  activeTableId.value = null;
  table.value = null;
  drawerCell.value = null;
  if (router) {
    void router.push({ name: "extractions" });
  }
  void loadList();
}

function existingColumnKeys(): string[] {
  return table.value?.columns.map((c) => c.key) ?? [];
}

watch(newColLabel, (val) => {
  if (addColErrors.value.label && val.trim()) {
    addColErrors.value.label = false;
  }
  if (keyDirty.value) return;
  syncingKey = true;
  newColKey.value = val.trim() ? deriveExtractionKey(val, existingColumnKeys()) : "";
  syncingKey = false;
});

function onNewColKeyInput(val: string | number) {
  if (syncingKey) return;
  const text = String(val);
  keyDirty.value = text.trim().length > 0;
  if (addColErrors.value.key && /^[a-z][a-z0-9_]{0,47}$/.test(text.trim().toLowerCase())) {
    addColErrors.value.key = false;
  }
}

watch(newColPrompt, (val) => {
  if (addColErrors.value.prompt && val.trim().length >= 2) {
    addColErrors.value.prompt = false;
  }
});

function cellProgress(item: ExtractionTableSummaryOut): {
  show: boolean;
  completed: number;
  failed: number;
  total: number;
  ratio: number;
} {
  const total = item.total_cells ?? 0;
  const completed = item.completed_cells ?? 0;
  const failed = item.failed_cells ?? 0;
  const done = completed + failed;
  const ratio = total > 0 ? Math.max(0, Math.min(1, done / total)) : 0;
  return {
    show: total > 0,
    completed,
    failed,
    total,
    ratio,
  };
}

async function handleDelete(id: string) {
  deletingId.value = id;
  error.value = null;
  try {
    await deleteExtractionTable(id);
    tables.value = tables.value.filter((item) => item.id !== id);
    confirmDeleteId.value = null;
    toastSuccess("已删除抽取表");
  } catch (err) {
    error.value = (err as Error).message || "删除抽取表失败";
  } finally {
    deletingId.value = null;
  }
}

async function handleRunAll() {
  if (!table.value) return;
  running.value = true;
  error.value = null;
  try {
    table.value = await runExtractionTable(table.value.id, {});
    ensurePolling();
  } catch (err) {
    error.value = (err as Error).message || "触发重跑失败";
  } finally {
    running.value = false;
  }
}

function cancelAddCol() {
  addColOpen.value = false;
  showAdvancedKey.value = false;
  addColErrors.value = {};
}

async function focusFirstInvalidAddCol() {
  if (addColErrors.value.key && !addColErrors.value.label && !addColErrors.value.prompt) {
    showAdvancedKey.value = true;
  }
  await nextTick();
  const invalidEl = addColFormRef.value?.querySelector<HTMLElement>('[aria-invalid="true"]');
  if (invalidEl) {
    invalidEl.scrollIntoView?.({
      block: "center",
      behavior: reducedMotion.value ? "auto" : "smooth",
    });
    invalidEl.focus?.({ preventScroll: true });
  }
}

async function handleAddColumnSubmit() {
  if (!table.value) return;
  const label = newColLabel.value.trim();
  const rawKey = newColKey.value.trim().toLowerCase();
  const key = rawKey || (label ? deriveExtractionKey(label, existingColumnKeys()) : "");
  const prompt = newColPrompt.value.trim();

  const labelInvalid = !label;
  const keyInvalid = !/^[a-z][a-z0-9_]{0,47}$/.test(key);
  const promptInvalid = prompt.length < 2;

  if (labelInvalid || keyInvalid || promptInvalid) {
    addColErrors.value = {
      label: labelInvalid,
      key: keyInvalid,
      prompt: promptInvalid,
    };
    if (labelInvalid || promptInvalid) {
      error.value = "请填写完整的列名称与抽取指令";
    } else {
      error.value = "列标识须以小写字母开头，仅含小写字母、数字和下划线";
    }
    await focusFirstInvalidAddCol();
    return;
  }

  addColErrors.value = {};
  addingCol.value = true;
  error.value = null;
  try {
    // 新增列后仅对新列调 `/run` 补抽，不重跑既有旧列
    const updated = await addExtractionColumn(table.value.id, {
      column: { key, label, prompt },
      auto_run: false,
    });
    table.value = updated;
    addColOpen.value = false;
    showAdvancedKey.value = false;
    keyDirty.value = false;
    syncingKey = true;
    newColLabel.value = "";
    newColKey.value = "";
    newColPrompt.value = "";
    syncingKey = false;

    try {
      table.value = await runExtractionTable(updated.id, {
        column_keys: [key],
      });
      ensurePolling();
      toastSuccess("已添加列并开始补抽");
    } catch (runErr) {
      const detail = (runErr as Error).message;
      error.value = detail
        ? `列「${label}」已添加，但自动补抽启动失败（${detail}），请点击「重跑全部」重试`
        : `列「${label}」已添加，但自动补抽启动失败，请点击「重跑全部」重试`;
    }
  } catch (err) {
    error.value = (err as Error).message || "新增列失败";
  } finally {
    addingCol.value = false;
  }
}

async function handleExportCsv() {
  if (!table.value) return;
  exporting.value = true;
  error.value = null;
  try {
    const { blob, filename } = await exportExtractionTableCsv(
      table.value.id,
      includeQuotes.value,
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toastSuccess("已导出 CSV");
  } catch (err) {
    error.value = (err as Error).message || "导出 CSV 失败";
  } finally {
    exporting.value = false;
  }
}

function openCellDrawer(source: ExtractionSourceOut, columnLabel: string, cell: ExtractionCellOut) {
  if (cell.status !== "extracted") return;
  drawerCell.value = { source, columnLabel, cell };
}

watch(
  () => props.tableId,
  (nextId) => {
    activeTableId.value = nextId || null;
    drawerCell.value = null;
    if (nextId) {
      void loadDetail(nextId);
    } else {
      void loadList();
    }
  },
);

onMounted(() => {
  if (activeTableId.value) {
    void loadDetail(activeTableId.value);
  } else {
    void loadList();
  }
});

onBeforeUnmount(() => {
  stopPolling();
});
</script>

<template>
  <section>
    <!-- 列表模式（/extractions） -->
    <template v-if="!activeTableId">
      <PageHeader
        eyebrow="数据抽取"
        title="论文数据抽取表"
        description="按自定义列批量抽取文献核心信息，逐格绑定原文引用与页码定位，支持导出 CSV。"
      />

      <ErrorBanner :message="error" testid="extraction-error" @dismiss="error = null" />

      <div
        v-if="showSkeleton && !tables.length"
        class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
        data-test="extraction-list-skeleton"
      >
        <div
          v-for="i in 3"
          :key="i"
          class="h-36 rounded-lg border border-border bg-card p-4"
        >
          <div class="h-4 w-2/3 animate-pulse rounded bg-muted motion-reduce:animate-none" />
          <div class="mt-3 h-3 w-1/2 animate-pulse rounded bg-muted/80 motion-reduce:animate-none" />
          <div class="mt-6 h-7 w-24 animate-pulse rounded bg-muted/60 motion-reduce:animate-none" />
        </div>
      </div>

      <EmptyState
        v-else-if="!loading && !tables.length"
        testid="extraction-list-empty"
        title="还没有论文抽取表"
        description="前往「知识库」勾选已就绪 PDF，或在「PRISMA 筛选」点击「生成数据抽取表」即可创建。"
      >
        <template #icon>
          <Table2 class="mb-1 size-6 text-muted-foreground" aria-hidden="true" />
        </template>
      </EmptyState>

      <ul
        v-else-if="tables.length"
        class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
        data-test="extraction-table-list"
      >
        <li
          v-for="item in tables"
          :key="item.id"
          :data-test="`extraction-list-item-${item.id}`"
          class="flex flex-col justify-between rounded-lg border border-border bg-card p-4 transition-colors hover:border-foreground/30"
        >
          <div>
            <div class="flex items-start justify-between gap-2">
              <button
                type="button"
                class="min-w-0 text-left text-sm font-semibold text-foreground break-words hover:underline"
                @click="openTable(item.id)"
              >
                {{ item.title }}
              </button>
              <Badge :variant="STATUS_VARIANTS[item.status]" class="shrink-0">
                {{ STATUS_LABELS[item.status] }}
              </Badge>
            </div>
            <p class="mt-2 text-xs text-muted-foreground tabular-nums">
              {{ item.source_count }} 篇论文 · {{ item.column_count }} 个字段
            </p>
            <div v-if="cellProgress(item).show" class="mt-2 space-y-1">
              <div class="flex items-center justify-between text-[11px] text-muted-foreground tabular-nums">
                <span>
                  已完成 {{ cellProgress(item).completed }}/{{ cellProgress(item).total }} 格
                </span>
                <span v-if="cellProgress(item).failed > 0" class="text-destructive">
                  失败 {{ cellProgress(item).failed }}
                </span>
              </div>
              <div
                class="h-1.5 w-full overflow-hidden rounded-full bg-muted"
                role="progressbar"
                :aria-valuenow="cellProgress(item).completed + cellProgress(item).failed"
                aria-valuemin="0"
                :aria-valuemax="cellProgress(item).total"
                :aria-label="`${item.title} 抽取进度`"
              >
                <div
                  :data-test="`extraction-progress-bar-${item.id}`"
                  class="h-full w-full origin-left rounded-full bg-primary transition-transform duration-200 ease-out motion-reduce:transition-none"
                  :style="{ transform: `scaleX(${cellProgress(item).ratio})` }"
                />
              </div>
            </div>
            <p class="mt-1.5 text-[11px] text-muted-foreground">
              更新于 {{ formatDateTime(item.updated_at) }}
            </p>
          </div>
          <!-- 移动端操作按钮左对齐，避免卡片右侧删除按钮与全局右下角会话悬浮球重叠（Issue #63） -->
          <div
            data-test="extraction-card-actions"
            class="mt-3 flex items-center justify-start gap-3 border-t border-border pt-2.5 sm:justify-between"
          >
            <Button size="sm" variant="outline" @click="openTable(item.id)">打开对比表</Button>
            <Button
              size="icon-sm"
              variant="ghost"
              :data-test="`extraction-delete-${item.id}`"
              aria-label="删除抽取表"
              @click="confirmDeleteId = item.id"
            >
              <Trash2 :size="14" />
            </Button>
          </div>
          <ConfirmBar
            v-if="confirmDeleteId === item.id"
            class="mt-2.5"
            title="确认删除该抽取表？"
            consequence="删除后将永久清除已抽取的数据列与原文溯源，不可恢复。"
            confirm-label="确认删除"
            :loading="deletingId === item.id"
            :confirm-testid="`extraction-delete-confirm-${item.id}`"
            :cancel-testid="`extraction-delete-cancel-${item.id}`"
            @confirm="handleDelete(item.id)"
            @cancel="confirmDeleteId = null"
          />
        </li>
      </ul>
    </template>

    <!-- 详情对比模式（/extractions/:tableId） -->
    <template v-else>
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap items-center gap-2.5">
          <Button variant="ghost" size="sm" @click="backToList">
            <ArrowLeft :size="14" aria-hidden="true" />
            全部抽取表
          </Button>
          <h1 class="text-lg font-semibold text-foreground" data-test="extraction-table-title">
            {{ table?.title || "加载抽取表…" }}
          </h1>
          <Badge
            v-if="table"
            :variant="STATUS_VARIANTS[table.status]"
            data-test="extraction-table-status"
          >
            <Loader2
              v-if="table.status === 'running'"
              :size="11"
              class="mr-1 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
            {{ STATUS_LABELS[table.status] }}
          </Badge>
        </div>

        <div v-if="table" class="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            :disabled="table.columns.length >= 12"
            data-test="extraction-add-col-toggle"
            @click="addColOpen = !addColOpen"
          >
            <Plus :size="13" aria-hidden="true" />
            {{ addColOpen ? "收起" : "添加列" }}
          </Button>

          <Button
            size="sm"
            variant="outline"
            :disabled="running || table.status === 'running'"
            data-test="extraction-rerun-all"
            @click="handleRunAll"
          >
            <RefreshCw :size="13" aria-hidden="true" />
            重跑全部
          </Button>

          <label class="flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-muted-foreground">
            <input
              v-model="includeQuotes"
              type="checkbox"
              data-test="extraction-export-include-quotes"
              class="accent-foreground"
            />
            含原文与定位
          </label>

          <Button
            size="sm"
            :disabled="exporting"
            data-test="extraction-export-csv"
            @click="handleExportCsv"
          >
            <Download :size="13" aria-hidden="true" />
            {{ exporting ? "导出中…" : "导出 CSV" }}
          </Button>
        </div>
      </div>

      <ErrorBanner :message="error || table?.error || null" testid="extraction-error" @dismiss="error = null" />

      <!-- 添加列折叠表单 -->
      <AnimatedCollapse v-if="table" :open="addColOpen">
        <div
          ref="addColFormRef"
          class="mb-4 space-y-2.5 rounded-lg border border-border bg-muted/30 p-3.5"
          data-test="extraction-add-col-form"
        >
          <div class="grid gap-2 sm:grid-cols-[12rem_minmax(0,1fr)_auto]">
            <div>
              <Label for="new-col-label" class="sr-only">列名称</Label>
              <Input
                id="new-col-label"
                v-model="newColLabel"
                data-test="new-col-label"
                :aria-invalid="addColErrors.label ? 'true' : undefined"
                placeholder="列名称（如：开源代码）"
                class="h-8 text-xs"
              />
            </div>
            <div>
              <Label for="new-col-prompt" class="sr-only">抽取指令</Label>
              <Input
                id="new-col-prompt"
                v-model="newColPrompt"
                data-test="new-col-prompt"
                :aria-invalid="addColErrors.prompt ? 'true' : undefined"
                placeholder="抽取指令（例如：提取代码仓库或权重链接）"
                class="h-8 text-xs"
              />
            </div>
            <div class="flex items-center gap-1.5">
              <Button
                size="sm"
                :disabled="addingCol"
                data-test="new-col-submit"
                @click="handleAddColumnSubmit"
              >
                {{ addingCol ? "补抽中…" : "添加并补抽该列" }}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-test="new-col-cancel"
                @click="cancelAddCol"
              >
                取消
              </Button>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              class="h-7 px-2 text-xs text-muted-foreground"
              data-test="new-col-advanced-toggle"
              @click="showAdvancedKey = !showAdvancedKey"
            >
              <ChevronUp v-if="showAdvancedKey" :size="13" aria-hidden="true" />
              <ChevronDown v-else :size="13" aria-hidden="true" />
              <span>自定义技术标识（默认自动生成{{ newColKey ? `：${newColKey}` : "" }}）</span>
            </Button>
          </div>

          <AnimatedCollapse :open="showAdvancedKey" :keep-mounted="true">
            <div class="grid gap-1.5 sm:max-w-xs">
              <Label for="new-col-key" class="text-xs text-muted-foreground">技术标识（key）</Label>
              <Input
                id="new-col-key"
                :model-value="newColKey"
                data-test="new-col-key"
                :aria-invalid="addColErrors.key ? 'true' : undefined"
                placeholder="key（如：code_url）"
                class="h-8 font-mono text-xs"
                @update:model-value="(val) => { newColKey = String(val); onNewColKeyInput(val); }"
              />
            </div>
          </AnimatedCollapse>
        </div>
      </AnimatedCollapse>

      <div
        v-if="showSkeleton && !table"
        class="space-y-2 rounded-lg border border-border bg-card p-4"
        data-test="extraction-detail-skeleton"
      >
        <div class="h-8 w-full animate-pulse rounded bg-muted motion-reduce:animate-none" />
        <div class="h-14 w-full animate-pulse rounded bg-muted/80 motion-reduce:animate-none" />
        <div class="h-14 w-full animate-pulse rounded bg-muted/60 motion-reduce:animate-none" />
      </div>

      <!-- 横向对比矩阵 -->
      <div
        v-else-if="table"
        class="relative overflow-x-auto rounded-lg border border-border bg-card"
      >
        <table class="w-full border-separate border-spacing-0 text-xs" data-test="extraction-table">
          <thead>
            <tr class="text-left">
              <th
                scope="col"
                data-test="extraction-sticky-header"
                class="sticky left-0 z-20 w-64 min-w-64 max-w-72 border-r border-b border-border-strong bg-muted px-3.5 py-2.5 font-semibold text-foreground shadow-[4px_0_8px_-4px_rgba(0,0,0,0.08)]"
              >
                论文（{{ table.sources.length }}）
              </th>
              <th
                v-for="col in table.columns"
                :key="col.key"
                scope="col"
                class="min-w-44 border-b border-border bg-muted/40 px-3.5 py-2.5 font-semibold text-foreground"
                :title="col.prompt"
              >
                <div>{{ col.label }}</div>
                <div class="mt-0.5 line-clamp-1 text-[10px] font-normal text-muted-foreground">
                  {{ col.prompt }}
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="src in table.sources"
              :key="`${src.source_type}:${src.source_id}`"
              class="group align-top hover:bg-muted/20"
              :data-test="`extraction-row-${src.source_id}`"
            >
              <td
                class="sticky left-0 z-10 w-64 min-w-64 max-w-72 border-r border-b border-r-border-strong border-b-border bg-card px-3.5 py-3 shadow-[4px_0_8px_-4px_rgba(0,0,0,0.08)] group-last:border-b-0"
              >
                <p class="font-medium leading-snug text-foreground">{{ src.title }}</p>
                <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <span v-if="src.year" class="text-[11px] text-muted-foreground tabular-nums">
                    {{ src.year }}
                  </span>
                  <Badge
                    :variant="sourceScope(src) === 'full_text' ? 'outline' : 'warning'"
                    class="text-[10px]"
                  >
                    {{ sourceScope(src) === "full_text" ? "全文" : "摘要" }}
                  </Badge>
                </div>
              </td>

              <td
                v-for="col in table.columns"
                :key="col.key"
                class="border-b border-border px-3.5 py-3 group-last:border-b-0"
                :data-test="`extraction-cell-${src.source_id}-${col.key}`"
              >
                <template v-if="getCell(src, col.key)">
                  <button
                    v-if="getCell(src, col.key)!.status === 'extracted'"
                    type="button"
                    class="group flex w-full flex-col gap-1 rounded-md p-1 -m-1 text-left transition-colors hover:bg-muted/60"
                    :data-test="`cell-extracted-${src.source_id}-${col.key}`"
                    @click="openCellDrawer(src, col.label, getCell(src, col.key)!)"
                  >
                    <span class="line-clamp-3 leading-relaxed text-foreground">
                      {{ getCell(src, col.key)!.value }}
                    </span>
                    <span class="inline-flex items-center gap-1 text-[10px] text-muted-foreground group-hover:text-foreground">
                      <Quote :size="11" aria-hidden="true" />
                      <span>
                        {{
                          formatEvidenceLocator(getCell(src, col.key)!.locator) ||
                          (getCell(src, col.key)!.quote ? "查看原文引用" : "查看原文片段")
                        }}
                      </span>
                    </span>
                  </button>

                  <span
                    v-else-if="getCell(src, col.key)!.status === 'not_reported'"
                    class="inline-flex flex-col text-muted-foreground"
                    data-test="cell-not-reported"
                  >
                    <span>未报告</span>
                    <span class="text-[10px]">
                      基于{{ getCell(src, col.key)!.evidence_scope === "full_text" ? "全文" : "摘要" }}
                    </span>
                  </span>

                  <span
                    v-else-if="getCell(src, col.key)!.status === 'pending'"
                    class="inline-flex items-center gap-1 text-muted-foreground"
                  >
                    <Loader2 :size="11" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    抽取中…
                  </span>

                  <span v-else class="text-destructive" data-test="cell-error">
                    抽取失败
                  </span>
                </template>
                <span v-else class="text-muted-foreground">待抽取</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- 右侧滑出：单元格原文溯源抽屉 -->
    <MotionDrawer
      :open="Boolean(drawerCell)"
      side="right"
      width-class="w-full max-w-md"
      aria-label="单元格原文溯源"
      :panel-attrs="{ 'data-test': 'extraction-evidence-drawer' }"
      @update:open="(v) => { if (!v) drawerCell = null; }"
    >
      <template v-if="drawerCell">
        <header class="flex items-start justify-between gap-2 border-b border-border px-4 py-3.5">
          <div class="min-w-0">
            <p class="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
              <FileSearch :size="13" aria-hidden="true" />
              原文溯源 · {{ drawerCell.columnLabel }}
            </p>
            <h2 class="mt-0.5 text-sm font-semibold text-foreground">
              {{ drawerCell.source.title }}
            </h2>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="关闭溯源抽屉"
            data-test="extraction-drawer-close"
            @click="drawerCell = null"
          >
            <X :size="16" />
          </Button>
        </header>

        <div class="flex-1 space-y-4 overflow-y-auto px-4 py-4 text-xs">
          <div>
            <p class="text-[11px] font-medium text-muted-foreground">抽取结果</p>
            <p
              class="mt-1 rounded-md border border-border bg-muted/30 p-2.5 text-sm leading-relaxed text-foreground"
              data-test="extraction-drawer-value"
            >
              {{ drawerCell.cell.value }}
            </p>
            <div class="mt-2 flex flex-wrap items-center gap-2">
              <Badge
                :variant="drawerCell.cell.evidence_scope === 'full_text' ? 'outline' : 'warning'"
              >
                {{ drawerCell.cell.evidence_scope === "full_text" ? "基于全文分块" : "基于题录/摘要" }}
              </Badge>
              <span
                v-if="typeof drawerCell.cell.confidence === 'number'"
                class="text-[11px] text-muted-foreground tabular-nums"
              >
                把握 {{ Math.round(drawerCell.cell.confidence * 100) }}%
              </span>
            </div>
          </div>

          <Alert v-if="drawerCell.cell.evidence_scope === 'abstract_only'" variant="warning">
            该条文献来源于外部检索题录，仅读取了标题与摘要。
          </Alert>

          <p
            v-if="drawerLocatorLabel"
            class="inline-flex w-fit items-center rounded border border-border bg-muted/60 px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
            data-test="extraction-cell-locator"
          >
            出处定位：{{ drawerLocatorLabel }}
          </p>

          <div v-if="drawerHighlight.html" class="space-y-1.5">
            <p class="text-[11px] font-medium text-muted-foreground">原文片段</p>
            <!-- highlightQuote 内部先做 HTML 转义，仅插入固定 class 的 <mark> -->
            <p
              class="rounded-md bg-muted/60 p-3 text-xs leading-relaxed text-foreground"
              data-test="extraction-cell-passage"
              v-html="drawerHighlight.html"
            />
            <p
              v-if="drawerCell.cell.quote && !drawerHighlight.matched"
              class="text-[11px] text-muted-foreground"
            >
              未在片段中精确匹配引文，已展示完整原文片段供核对。
            </p>
          </div>
          <p v-else class="text-muted-foreground">未附带原文片段。</p>
        </div>
      </template>
    </MotionDrawer>
  </section>
</template>
