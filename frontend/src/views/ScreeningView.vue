<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { routerKey } from "vue-router";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
import DOMPurify from "dompurify";
import { Check, CircleX, Code2, Copy, MinusCircle, Sparkles, Table2 } from "lucide-vue-next";

import AnimatedCollapse from "@/components/common/AnimatedCollapse.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import FunnelChart from "@/components/charts/FunnelChart.vue";
import type { FunnelStageInput } from "@/components/charts/build-funnel";
import EmptyState from "@/components/common/EmptyState.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import CreateExtractionDialog, {
  type ExtractionCandidateSource,
} from "@/components/extractions/CreateExtractionDialog.vue";
import { Alert } from "@/components/ui/alert";
import { Badge, type BadgeVariants } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDelayedLoading } from "@/composables/useDelayedLoading";
import { useTheme } from "@/composables/useTheme";
import { TRANSITION_MICRO } from "@/lib/ease";
import { toastError, toastSuccess } from "@/lib/toast";
import type { ExtractionTableOut } from "@/api/extractions";
import {
  createDecision,
  getPrismaCounts,
  getScreeningPriority,
  listScreeningRecords,
} from "@/api/screening";
import type { PrismaCounts, ScreeningPriority, ScreeningRecord } from "@/api/types";
import { cleanAbstractLatex } from "@/utils/latex-text";

/**
 * PRISMA 筛选工作台（Phase 2）：候选列表 + 逐条决策 + PRISMA 计数。
 *
 * 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
 *
 * ⚠️ 测试契约（不要改）：`data-test="screening-row-<id>"` / `screening-include-<id>` /
 * `screening-exclude-<id>` / `screening-maybe-<id>` / `screening-exclude-form-<id>` /
 * `screening-exclude-reason-<id>` / `screening-exclude-submit-<id>` /
 * `screening-prisma` / `screening-mermaid` / `screening-error` / `screening-empty` /
 * `screening-stage-filter` / `screening-priority-toggle` / `screening-priority-status` /
 * `screening-priority-rank-<id>`。
 */
const props = defineProps<{ taskId: string }>();
const router = inject(routerKey, null);
const reducedMotion = useReducedMotion();
const { isDark } = useTheme();

const sectionRef = ref<HTMLElement | null>(null);
const records = ref<ScreeningRecord[]>([]);
const prisma = ref<PrismaCounts | null>(null);
const loading = ref(false);
const showSkeleton = useDelayedLoading(() => loading.value && !records.value.length);
const error = ref<string | null>(null);
const success = ref<string | null>(null);
const busyIds = ref<Set<string>>(new Set());

function isRecordBusy(recordId: string): boolean {
  return busyIds.value.has(recordId);
}

function setRecordBusy(recordId: string, busy: boolean) {
  const next = new Set(busyIds.value);
  if (busy) {
    next.add(recordId);
  } else {
    next.delete(recordId);
  }
  busyIds.value = next;
}
const excludeFormId = ref<string | null>(null);
const instantCloseExcludeId = ref<string | null>(null);
const excludeReason = ref("");
const excludeReasonErrorId = ref<string | null>(null);
const rollbackFlashId = ref<string | null>(null);
const priorityStale = ref(false);
const stageFilter = ref("");
const extractionDialogOpen = ref(false);
const extractionSources = ref<ExtractionCandidateSource[]>([]);
const loadingIncluded = ref(false);

const mermaidSvg = ref("");
const mermaidRenderError = ref(false);
const mermaidCopied = ref(false);

let rollbackTimer: ReturnType<typeof setTimeout> | null = null;
let successTimer: ReturnType<typeof setTimeout> | null = null;
let mermaidCopyTimer: ReturnType<typeof setTimeout> | null = null;
let mermaidRenderSeq = 0;
let mermaidModulePromise: Promise<typeof import("mermaid")> | null = null;

function loadMermaid() {
  if (!mermaidModulePromise) {
    mermaidModulePromise = import("mermaid").catch((err) => {
      mermaidModulePromise = null;
      throw err;
    });
  }
  return mermaidModulePromise;
}

function setSuccessBanner(message: string | null) {
  if (successTimer !== null) {
    clearTimeout(successTimer);
    successTimer = null;
  }
  success.value = message;
  if (message) {
    successTimer = setTimeout(() => {
      successTimer = null;
      success.value = null;
    }, 3200);
  }
}

function triggerRollbackFlash(recordId: string) {
  if (rollbackTimer !== null) {
    clearTimeout(rollbackTimer);
    rollbackTimer = null;
  }
  rollbackFlashId.value = recordId;
  rollbackTimer = setTimeout(() => {
    rollbackTimer = null;
    rollbackFlashId.value = null;
  }, 800);
}

const includedCount = computed(() => {
  const fromPrisma = prisma.value?.included ?? 0;
  const fromLoaded = records.value.filter((item) => item.current_stage === "included").length;
  return Math.max(fromPrisma, fromLoaded);
});

async function openExtractionFromIncluded() {
  error.value = null;
  loadingIncluded.value = true;
  try {
    const includedList =
      stageFilter.value === "included"
        ? records.value.filter((item) => item.current_stage === "included")
        : await listScreeningRecords(props.taskId, "included");
    if (!includedList.length) {
      error.value = "当前任务暂无已纳入（included）文献，无法生成抽取表";
      return;
    }
    extractionSources.value = includedList.map((item) => ({
      source_type: "screening_record",
      source_id: item.record_id,
      title: item.title,
    }));
    extractionDialogOpen.value = true;
  } catch (err) {
    error.value = (err as Error).message || "加载已纳入文献失败";
  } finally {
    loadingIncluded.value = false;
  }
}

function handleExtractionCreated(created: ExtractionTableOut) {
  extractionDialogOpen.value = false;
  if (router) {
    void router.push({ name: "extractions", params: { tableId: created.id } });
  }
}

/** 任务还没跑筛选时五个计数全是 0：不画空漏斗（容易误读成「筛选坏了」）。 */
const hasPrismaData = computed(
  () => (prisma.value?.identified_total ?? 0) > 0 || (prisma.value?.included ?? 0) > 0,
);

/**
 * PRISMA 阶段（顺序即流程顺序）。
 *
 * 每一段都是**进入该阶段的记录数**，不是排除数：排除数单独放在 `excluded`，
 * 由阶段行右侧的转化率体现。混用会出现「越排越多人」的错误观感。
 */
const funnelStages = computed<FunnelStageInput[]>(() => {
  const counts = prisma.value;
  if (!counts) return [];
  return [
    { id: "identified", label: "识别", value: counts.identified_total },
    { id: "deduped", label: "去重后", value: counts.records_screened },
    { id: "title_abstract", label: "标题/摘要", value: counts.title_abstract.screened },
    { id: "fulltext", label: "全文评估", value: counts.fulltext.assessed },
    { id: "included", label: "纳入", value: counts.included },
  ];
});

// 主动排序（默认关闭）：关闭时零额外请求，列表行为与旧版完全一致。
const priorityEnabled = ref(false);
const priority = ref<ScreeningPriority | null>(null);
const priorityLoading = ref(false);
const priorityError = ref<string | null>(null);

const STAGE_LABELS: Record<string, string> = {
  title_abstract: "标题/摘要",
  fulltext: "全文",
  included: "已纳入",
};

/** 业务阶段 → Badge 语义变体（与 StatusBadge 的口径一致）。 */
const STAGE_TONE: Record<string, NonNullable<BadgeVariants["variant"]>> = {
  title_abstract: "muted",
  fulltext: "default",
  included: "success",
};

const hint = computed(() => {
  if (!records.value.length) {
    return "还没有候选记录。先在任务里执行检索，结果会自动进入筛选队列。";
  }
  return `${records.value.length} 条候选记录 · 已纳入 ${prisma.value?.included ?? 0} 条`;
});

/** 推荐名次表：record_id → rank。只用于展示「推荐优先 #n」，不参与纳入/排除。 */
const priorityRanks = computed(
  () => new Map((priority.value?.items ?? []).map((item) => [item.record_id, item.rank]))
);

// 主动排序只在「标题/摘要」阶段有意义：全文/已纳入阶段后端语义不同，且全量已排序会掩盖阶段过滤。
const priorityDisabled = computed(
  () => priorityLoading.value || ["fulltext", "included"].includes(stageFilter.value)
);

/**
 * 列表展示顺序。
 *
 * 关闭排序、或后端给不出排序（标签不足/无候选/出错）时**保持原顺序**；
 * 只有 `ready` 才把有 rank 的记录按名次前置，其余记录原样追加在后面
 * （已筛记录不会因为排序被隐藏）。
 */
const displayRecords = computed(() => {
  if (!priorityEnabled.value || priority.value?.status !== "ready") {
    return records.value;
  }
  const ranked = records.value
    .filter((record) => priorityRanks.value.has(record.record_id))
    .sort(
      (left, right) =>
        (priorityRanks.value.get(left.record_id) ?? 0) -
        (priorityRanks.value.get(right.record_id) ?? 0)
    );
  const rest = records.value.filter((record) => !priorityRanks.value.has(record.record_id));
  return [...ranked, ...rest];
});

const priorityTone = computed<"success" | "warning" | "muted" | "destructive">(() => {
  if (priorityError.value) return "destructive";
  if (priority.value?.status === "insufficient_labels") return "warning";
  if (priority.value?.status === "ready") return "success";
  return "muted";
});

/** 决策应提交到的阶段：已纳入的不再可决策（模板里按钮也不渲染）。 */
function stageFor(record: ScreeningRecord): "title_abstract" | "fulltext" {
  return record.current_stage === "fulltext" ? "fulltext" : "title_abstract";
}

/**
 * 是否还能对该记录决策。
 *
 * 两种情况不能：
 * - 已纳入（`included` 是终态）；
 * - **本阶段已被我排除**（排除是终态，后端会返回 400，不如不显示按钮）。
 *   双人筛选时另一位筛选者不受影响——后端按「当前筛选者」返回结论。
 */
function canDecide(record: ScreeningRecord): boolean {
  if (record.current_stage === "included") {
    return false;
  }
  return record.current_verdict !== "exclude";
}

onMounted(() => {
  void refresh();
});

onBeforeUnmount(() => {
  ++mermaidRenderSeq;
  if (rollbackTimer !== null) {
    clearTimeout(rollbackTimer);
    rollbackTimer = null;
  }
  if (successTimer !== null) {
    clearTimeout(successTimer);
    successTimer = null;
  }
  if (mermaidCopyTimer !== null) {
    clearTimeout(mermaidCopyTimer);
    mermaidCopyTimer = null;
  }
});

watch(
  [() => prisma.value?.mermaid, isDark],
  async ([source, dark]) => {
    const seq = ++mermaidRenderSeq;
    const code = source?.trim() ?? "";
    if (!code) {
      mermaidSvg.value = "";
      mermaidRenderError.value = false;
      return;
    }
    try {
      const mermaid = (await loadMermaid()).default;
      if (seq !== mermaidRenderSeq) return;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: dark ? "dark" : "neutral",
        fontFamily: "inherit",
        htmlLabels: false,
        flowchart: { htmlLabels: false },
      });
      const { svg } = await mermaid.render(`prisma-mermaid-${Date.now()}-${seq}`, code);
      if (seq !== mermaidRenderSeq) return;
      mermaidSvg.value = DOMPurify.sanitize(svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
      });
      mermaidRenderError.value = false;
    } catch {
      if (seq !== mermaidRenderSeq) return;
      mermaidSvg.value = "";
      mermaidRenderError.value = true;
    }
  },
  { immediate: true },
);

async function copyMermaidSource() {
  const code = prisma.value?.mermaid ?? "";
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code);
    mermaidCopied.value = true;
    if (mermaidCopyTimer !== null) {
      clearTimeout(mermaidCopyTimer);
    }
    mermaidCopyTimer = setTimeout(() => {
      mermaidCopyTimer = null;
      mermaidCopied.value = false;
    }, 1800);
    toastSuccess("已复制 Mermaid 源码");
  } catch {
    toastError("复制失败，请手动选中源码复制");
  }
}

/**
 * 拉取主动排序结果。
 *
 * 失败不写 `error`（那是列表/决策的错误条）：排序只是锦上添花，
 * 出错时列表按原顺序照常可用，错误在排序状态提示里说明。
 */
async function loadPriority() {
  if (!priorityEnabled.value) return;
  priorityLoading.value = true;
  priorityError.value = null;
  try {
    priority.value = await getScreeningPriority(props.taskId);
    priorityStale.value = false;
  } catch (err) {
    priorityError.value = (err as Error).message || "主动排序加载失败";
    priority.value = null;
  } finally {
    priorityLoading.value = false;
  }
}

async function togglePriority() {
  if (priorityDisabled.value) return;
  priorityEnabled.value = !priorityEnabled.value;
  if (!priorityEnabled.value) {
    priority.value = null;
    priorityError.value = null;
    priorityStale.value = false;
    return;
  }
  await loadPriority();
}

/**
 * 切换阶段过滤。
 *
 * 全文/已纳入阶段禁用主动排序：进入这两个阶段时**关闭**开关（而不是留着开关
 * 却悄悄不生效），避免用户以为排序还在起作用。
 */
async function handleStageChange() {
  if (priorityEnabled.value && ["fulltext", "included"].includes(stageFilter.value)) {
    priorityEnabled.value = false;
    priority.value = null;
    priorityError.value = null;
    priorityStale.value = false;
  }
  await refresh();
}

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    const [nextRecords, nextPrisma] = await Promise.all([
      listScreeningRecords(props.taskId, stageFilter.value || undefined),
      getPrismaCounts(props.taskId),
    ]);
    records.value = nextRecords;
    prisma.value = nextPrisma;
    if (priorityEnabled.value) {
      await loadPriority();
    }
  } catch (err) {
    error.value = (err as Error).message || "筛选数据加载失败";
  } finally {
    loading.value = false;
  }
}

async function handleInclude(record: ScreeningRecord) {
  await submitDecision(
    record,
    { stage: stageFor(record), verdict: "include" },
    `已纳入「${record.title}」`,
  );
}

function handleMaybe(record: ScreeningRecord) {
  void submitDecision(
    record,
    { stage: stageFor(record), verdict: "maybe" },
    `待定「${record.title}」`,
  );
}

function openExclude(record: ScreeningRecord) {
  instantCloseExcludeId.value = null;
  excludeReasonErrorId.value = null;
  excludeFormId.value = record.record_id;
  excludeReason.value = "";
  setSuccessBanner(null);
}

function cancelExclude() {
  excludeReasonErrorId.value = null;
  excludeFormId.value = null;
  excludeReason.value = "";
}

function onExcludeInputKeydown(record: ScreeningRecord, event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === "Enter") {
    event.preventDefault();
    void submitExclude(record);
  } else if (event.key === "Escape") {
    event.stopPropagation();
    event.preventDefault();
    cancelExclude();
  }
}

async function submitExclude(record: ScreeningRecord) {
  // 排除必须给理由（PRISMA 2020 要求报告排除理由分类）：前端先拦，避免无意义往返
  if (!excludeReason.value.trim()) {
    error.value = "排除必须填写理由";
    excludeReasonErrorId.value = record.record_id;
    await nextTick();
    const inputEl = sectionRef.value?.querySelector<HTMLElement>(
      `[data-test="screening-exclude-reason-${record.record_id}"]`,
    );
    inputEl?.scrollIntoView?.({
      block: "center",
      behavior: reducedMotion.value ? "auto" : "smooth",
    });
    inputEl?.focus?.({ preventScroll: true });
    return;
  }
  const reason = excludeReason.value.trim();
  excludeReasonErrorId.value = null;
  // 提交后同步销毁表单（守卫提交瞬间收起，不因退场延迟挂起）
  instantCloseExcludeId.value = record.record_id;
  excludeFormId.value = null;
  excludeReason.value = "";
  await submitDecision(
    record,
    {
      stage: stageFor(record),
      verdict: "exclude",
      exclusion_reason: reason,
    },
    `已排除「${record.title}」`,
  );
}

async function submitDecision(
  record: ScreeningRecord,
  payload: { stage: "title_abstract" | "fulltext"; verdict: "include" | "exclude" | "maybe"; exclusion_reason?: string },
  okMessage: string,
) {
  if (isRecordBusy(record.record_id)) return;
  setRecordBusy(record.record_id, true);
  error.value = null;
  setSuccessBanner(null);

  const snapshot = {
    current_stage: record.current_stage,
    current_verdict: record.current_verdict,
    current_exclusion_reason: record.current_exclusion_reason,
  };

  // B4 Pin-in-Place：原地乐观更新状态，绝不整表 refresh 造成滚动跳位
  if (payload.verdict === "include") {
    record.current_stage = record.current_stage === "title_abstract" ? "fulltext" : "included";
    record.current_verdict = "include";
    record.current_exclusion_reason = null;
  } else if (payload.verdict === "maybe") {
    record.current_verdict = "maybe";
  } else {
    record.current_stage = payload.stage;
    record.current_verdict = "exclude";
    record.current_exclusion_reason = payload.exclusion_reason ?? null;
  }

  try {
    const updated = await createDecision(record.record_id, payload);
    record.current_stage = updated.current_stage;
    // 筛选决策已有底部非阻塞 screening-success 提示条，不再额外弹顶部 Toaster（避免移动端窄屏多层堆叠遮挡首卡与导航）
    setSuccessBanner(okMessage);
    if (priorityEnabled.value && priority.value?.status === "ready") {
      priorityStale.value = true;
    }
    try {
      const nextPrisma = await getPrismaCounts(props.taskId);
      prisma.value = nextPrisma;
    } catch {
      // PRISMA 计数静默刷新失败不回滚已落库决策
    }
  } catch (err) {
    record.current_stage = snapshot.current_stage;
    record.current_verdict = snapshot.current_verdict;
    record.current_exclusion_reason = snapshot.current_exclusion_reason;
    triggerRollbackFlash(record.record_id);
    const msg = (err as Error).message || "操作失败";
    error.value = msg;
    toastError(msg);
  } finally {
    setRecordBusy(record.record_id, false);
  }
}
</script>

<template>
  <section ref="sectionRef">
    <PageHeader eyebrow="PRISMA 筛选" title="PRISMA 筛选" :description="hint" />

    <ErrorBanner :message="error" testid="screening-error" @dismiss="error = null" />

    <!-- 严禁 Teleport：保留在组件树内，脱离垂直文档流实现零 CLS 且避开右上角 Toaster 与右下角悬浮球 -->
    <div
      v-if="success"
      data-test="screening-success"
      role="status"
      class="pointer-events-auto fixed bottom-5 left-4 right-20 z-30 flex items-center gap-2 rounded-lg border border-success/40 bg-card px-3 py-2 text-xs font-medium text-foreground shadow-raised sm:left-auto sm:right-20 sm:max-w-md"
    >
      <Check :size="14" class="shrink-0 text-success" aria-hidden="true" />
      <span class="truncate">{{ success }}</span>
    </div>

    <!-- 筛选工具栏：阶段过滤 + 智能排序同一行紧凑收纳 -->
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2">
      <div class="flex flex-wrap items-center gap-2">
        <Label class="sr-only" for="screening-stage-filter">按阶段过滤</Label>
        <select
          id="screening-stage-filter"
          v-model="stageFilter"
          data-test="screening-stage-filter"
          class="flex h-8 rounded-md border border-input bg-background px-2.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          @change="handleStageChange()"
        >
          <option value="">全部阶段</option>
          <option value="title_abstract">标题/摘要</option>
          <option value="fulltext">全文</option>
          <option value="included">已纳入</option>
        </select>

        <Button
          variant="outline"
          size="sm"
          type="button"
          data-test="screening-priority-toggle"
          :aria-pressed="priorityEnabled"
          :disabled="priorityDisabled"
          @click="togglePriority"
        >
          <Sparkles :size="13" aria-hidden="true" />
          智能排序：{{ priorityEnabled ? "开" : "关" }}
        </Button>

        <Button
          v-if="includedCount > 0"
          size="sm"
          type="button"
          data-test="screening-create-extraction"
          :disabled="loadingIncluded"
          @click="openExtractionFromIncluded"
        >
          <Table2 :size="13" aria-hidden="true" />
          {{ loadingIncluded ? "加载中…" : `生成数据抽取表（已纳入 ${includedCount} 篇）` }}
        </Button>
      </div>
      <span class="text-xs text-muted-foreground">
        按标题/摘要排序，不自动纳入或排除
      </span>
    </div>

    <Alert
      v-if="priorityEnabled"
      :variant="priorityTone"
      class="mb-3"
      data-test="screening-priority-status"
    >
      <div class="flex flex-wrap items-center justify-between gap-2">
        <span v-if="priorityLoading">正在计算优先级…</span>
        <span v-else-if="priorityError">{{ priorityError }}</span>
        <span v-else-if="priority">{{ priority.message }}</span>
        <Button
          v-if="priorityStale"
          size="sm"
          variant="outline"
          type="button"
          data-test="screening-priority-reorder"
          :disabled="priorityLoading"
          @click="loadPriority"
        >
          推荐顺序已更新，点击重排
        </Button>
      </div>
    </Alert>

    <div class="grid gap-5" :class="prisma && (hasPrismaData || prisma.mermaid) ? 'xl:grid-cols-[minmax(0,1fr)_22rem] xl:items-start' : ''">
      <!-- 左侧：候选筛选队列（首屏焦点） -->
      <div class="min-w-0">
        <div v-if="showSkeleton && !records.length" class="space-y-2" data-test="screening-skeleton">
          <div
            v-for="i in 3"
            :key="i"
            class="h-40 rounded-lg border border-border bg-card p-3.5"
          >
            <div class="h-4 w-2/3 animate-pulse rounded bg-muted motion-reduce:animate-none" />
            <div class="mt-2 h-3 w-1/3 animate-pulse rounded bg-muted/80 motion-reduce:animate-none" />
            <div class="mt-3 h-12 w-full animate-pulse rounded bg-muted/60 motion-reduce:animate-none" />
          </div>
        </div>

        <EmptyState
          v-else-if="!loading && !records.length"
          testid="screening-empty"
          title="还没有候选记录"
          description="先在任务里执行检索，检索结果会作为快照进入筛选队列；跨源重复已在落库时去除。"
        />

        <ul v-else-if="records.length" class="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
          <li
            v-for="record in displayRecords"
            :key="record.record_id"
            :class="[
              'px-3.5 py-3 transition-colors duration-150 [contain:layout_style]',
              rollbackFlashId === record.record_id
                ? 'bg-destructive/5 ring-1 ring-inset ring-destructive/60'
                : 'hover:bg-muted/25',
            ]"
            :data-test="`screening-row-${record.record_id}`"
          >
            <div class="flex flex-wrap items-start justify-between gap-2">
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="text-sm font-medium text-foreground">{{ cleanAbstractLatex(record.title) }}</span>
                  <Badge :variant="STAGE_TONE[record.current_stage] ?? 'muted'">
                    {{ STAGE_LABELS[record.current_stage] ?? record.current_stage }}
                  </Badge>
                  <Badge
                    v-if="priorityRanks.get(record.record_id)"
                    variant="success"
                    :data-test="`screening-priority-rank-${record.record_id}`"
                  >
                    推荐优先 #{{ priorityRanks.get(record.record_id) }}
                  </Badge>
                  <Badge
                    v-if="record.current_verdict === 'exclude'"
                    variant="destructive"
                    :data-test="`screening-excluded-badge-${record.record_id}`"
                  >
                    已排除{{ record.current_exclusion_reason ? `：${record.current_exclusion_reason}` : "" }}
                  </Badge>
                  <Badge
                    v-else-if="record.current_verdict === 'maybe'"
                    variant="warning"
                    :data-test="`screening-maybe-badge-${record.record_id}`"
                  >
                    待定
                  </Badge>
                </div>
                <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>{{ record.authors.join("；") || "（无作者）" }}</span>
                  <code class="rounded bg-muted px-1 py-0.5 text-[11px]">
                    {{ record.source_id.includes(":") ? record.source_id : `${record.source}:${record.source_id}` }}
                  </code>
                  <code v-if="record.doi" class="rounded bg-muted px-1 py-0.5 text-[11px]">
                    doi:{{ record.doi }}
                  </code>
                </div>
              </div>

              <div
                v-if="canDecide(record)"
                class="flex shrink-0 flex-wrap items-center gap-1.5"
              >
                <Button
                  size="sm"
                  :data-test="`screening-include-${record.record_id}`"
                  type="button"
                  :disabled="isRecordBusy(record.record_id)"
                  @click="handleInclude(record)"
                >
                  <Check :size="14" aria-hidden="true" />
                  <span>纳入</span>
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  :data-test="`screening-maybe-${record.record_id}`"
                  type="button"
                  :disabled="isRecordBusy(record.record_id)"
                  @click="handleMaybe(record)"
                >
                  <MinusCircle :size="14" aria-hidden="true" />
                  <span>待定</span>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  :data-test="`screening-exclude-${record.record_id}`"
                  type="button"
                  :disabled="isRecordBusy(record.record_id)"
                  @click="openExclude(record)"
                >
                  <CircleX :size="14" aria-hidden="true" />
                  <span>排除</span>
                </Button>
              </div>
            </div>

            <p
              v-if="record.abstract"
              data-test="screening-abstract"
              class="mt-2 line-clamp-3 text-xs leading-relaxed text-muted-foreground"
            >
              {{ cleanAbstractLatex(record.abstract) }}
            </p>

            <AnimatedCollapse
              v-if="instantCloseExcludeId !== record.record_id"
              :open="excludeFormId === record.record_id"
            >
              <div
                class="mt-2.5 flex flex-wrap items-end gap-2 rounded-md border border-border bg-muted/40 p-2.5"
                :data-test="`screening-exclude-form-${record.record_id}`"
              >
                <div class="grid min-w-56 flex-1 gap-1.5">
                  <Label :for="`screening-exclude-reason-${record.record_id}`" class="text-xs">排除理由（必填）</Label>
                  <Input
                    :id="`screening-exclude-reason-${record.record_id}`"
                    v-model="excludeReason"
                    :data-test="`screening-exclude-reason-${record.record_id}`"
                    :aria-invalid="excludeReasonErrorId === record.record_id ? 'true' : undefined"
                    type="text"
                    placeholder="如：不相关 / 无全文 / 人群不符"
                    @input="excludeReasonErrorId === record.record_id && (excludeReasonErrorId = null)"
                    @keydown="onExcludeInputKeydown(record, $event)"
                  />
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  :data-test="`screening-exclude-cancel-${record.record_id}`"
                  type="button"
                  @click="cancelExclude"
                >
                  取消
                </Button>
                <Button
                  size="sm"
                  :data-test="`screening-exclude-submit-${record.record_id}`"
                  type="button"
                  @click="submitExclude(record)"
                >
                  确认排除
                </Button>
              </div>
            </AnimatedCollapse>
          </li>
        </ul>
      </div>

      <!-- 右侧：PRISMA 漏斗与 Mermaid 流程图 -->
      <aside v-if="prisma && (hasPrismaData || prisma.mermaid)" class="space-y-4">
        <FunnelChart
          v-if="hasPrismaData"
          data-test="screening-prisma"
          :stages="funnelStages"
          title="筛选漏斗"
          unit="条"
          :period-label="`共 ${prisma.identified_total} 条识别记录`"
        />

        <div v-if="prisma.mermaid" class="rounded-lg border border-border bg-card p-3">
          <div class="mb-2 flex items-center justify-between gap-2">
            <h2 class="text-xs font-semibold tracking-tight text-foreground">PRISMA 流程图</h2>
            <Button
              variant="ghost"
              size="sm"
              type="button"
              class="h-7 px-2 text-xs"
              data-test="screening-mermaid-copy"
              @click="copyMermaidSource"
            >
              <AnimatePresence mode="wait" :initial="false">
                <Motion
                  :key="mermaidCopied ? 'copied' : 'copy'"
                  as="span"
                  class="inline-flex items-center gap-1"
                  :initial="{ opacity: 0, scale: reducedMotion ? 1 : 0.85 }"
                  :animate="{ opacity: 1, scale: 1 }"
                  :exit="{ opacity: 0, scale: reducedMotion ? 1 : 0.85 }"
                  :transition="TRANSITION_MICRO"
                >
                  <Check v-if="mermaidCopied" :size="12" class="text-success" aria-hidden="true" />
                  <Copy v-else :size="12" aria-hidden="true" />
                  <span>{{ mermaidCopied ? "已复制" : "复制源码" }}</span>
                </Motion>
              </AnimatePresence>
            </Button>
          </div>

          <div
            v-if="mermaidSvg"
            class="mb-2.5 overflow-x-auto rounded-md border border-border bg-background p-2 [&>svg]:mx-auto [&>svg]:h-auto [&>svg]:max-w-full"
            data-test="screening-mermaid-diagram"
            v-html="mermaidSvg"
          />
          <p v-else-if="mermaidRenderError" class="mb-2 text-[11px] text-muted-foreground">
            流程图预览暂不可用，可复制下方 Mermaid 源码在论文工具中渲染。
          </p>

          <div class="space-y-1">
            <p class="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
              <Code2 :size="12" aria-hidden="true" />
              <span>Mermaid 源码</span>
            </p>
            <pre
              class="max-h-48 overflow-auto rounded-md bg-muted/50 p-2.5 font-mono text-[11px] leading-relaxed text-muted-foreground"
              data-test="screening-mermaid"
              >{{ prisma.mermaid }}</pre
            >
          </div>
        </div>
      </aside>
    </div>

    <CreateExtractionDialog
      :open="extractionDialogOpen"
      :sources="extractionSources"
      :task-id="props.taskId"
      :default-title="`PRISMA 纳入文献抽取表（${extractionSources.length} 篇）`"
      @close="extractionDialogOpen = false"
      @created="handleExtractionCreated"
    />
  </section>
</template>
