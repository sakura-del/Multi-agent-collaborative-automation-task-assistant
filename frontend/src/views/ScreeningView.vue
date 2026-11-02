<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Check, CircleX, MinusCircle, Sparkles } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { Alert } from "@/components/ui/alert";
import { Badge, type BadgeVariants } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createDecision,
  getPrismaCounts,
  getScreeningPriority,
  listScreeningRecords,
} from "@/api/screening";
import type { PrismaCounts, ScreeningPriority, ScreeningRecord } from "@/api/types";

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

const records = ref<ScreeningRecord[]>([]);
const prisma = ref<PrismaCounts | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);
const busyId = ref<string | null>(null);
const excludeFormId = ref<string | null>(null);
const excludeReason = ref("");
const stageFilter = ref("");

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
    return "还没有候选记录。先在任务里执行检索，结果会自动进入筛选工作台。";
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
  }
  await refresh();
}

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    // 先并行取回两份数据，再**一起**赋值：
    // 若像早期实现那样先 `records.value = await ...` 再 `prisma.value = await ...`，
    // 中间会有一帧「新记录 + 旧计数」的不一致画面（实测截图捕获到
    // 「已排除」成功提示与「标题/摘要排除 0」同时存在，看起来像计数没更新）。
    const [nextRecords, nextPrisma] = await Promise.all([
      listScreeningRecords(props.taskId, stageFilter.value || undefined),
      getPrismaCounts(props.taskId),
    ]);
    records.value = nextRecords;
    prisma.value = nextPrisma;
    // 排序依赖任务内决策标签：决策/刷新后必须重算，否则列表顺序会停在旧标签上。
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
  busyId.value = record.record_id;
  error.value = null;
  success.value = null;
  try {
    await createDecision(record.record_id, {
      stage: stageFor(record),
      verdict: "include",
    });
    success.value = `已纳入「${record.title}」`;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "操作失败";
  } finally {
    busyId.value = null;
  }
}

function handleMaybe(record: ScreeningRecord) {
  void submitDecision(record, { stage: stageFor(record), verdict: "maybe" }, `待定「${record.title}」`);
}

function openExclude(record: ScreeningRecord) {
  excludeFormId.value = record.record_id;
  excludeReason.value = "";
  success.value = null;
}

async function submitExclude(record: ScreeningRecord) {
  // 排除必须给理由（PRISMA 2020 要求报告排除理由分类）：前端先拦，避免无意义往返
  if (!excludeReason.value.trim()) {
    error.value = "排除必须填写理由";
    return;
  }
  const reason = excludeReason.value.trim();
  // 先收起表单再提交/刷新：否则成功后表单会一直挂着（等刷新回来才消失），
  // 用户容易重复点「确认排除」
  excludeFormId.value = null;
  excludeReason.value = "";
  await submitDecision(
    record,
    {
      stage: stageFor(record),
      verdict: "exclude",
      exclusion_reason: reason,
    },
    `已排除「${record.title}」`
  );
}

async function submitDecision(
  record: ScreeningRecord,
  payload: { stage: "title_abstract" | "fulltext"; verdict: "include" | "exclude" | "maybe"; exclusion_reason?: string },
  okMessage: string
) {
  busyId.value = record.record_id;
  error.value = null;
  success.value = null;
  try {
    await createDecision(record.record_id, payload);
    success.value = okMessage;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "操作失败";
  } finally {
    busyId.value = null;
  }
}
</script>

<template>
  <section>
    <PageHeader eyebrow="PRISMA screening" title="PRISMA 筛选" :description="hint" />

    <ErrorBanner :message="error" testid="screening-error" />

    <Alert v-if="success" variant="success" class="mb-3" data-test="screening-success">
      <Check />
      <span>{{ success }}</span>
    </Alert>

    <!-- PRISMA 计数条：所有数字由后端明细现算，前端只展示 -->
    <div
      v-if="prisma"
      class="mb-4 flex flex-wrap gap-2"
      data-test="screening-prisma"
    >
      <div class="min-w-24 rounded-lg border border-border bg-card px-3 py-2">
        <p class="text-xs text-muted-foreground">识别</p>
        <p class="text-lg font-semibold tabular-nums">{{ prisma.identified_total }}</p>
      </div>
      <div class="min-w-24 rounded-lg border border-border bg-card px-3 py-2">
        <p class="text-xs text-muted-foreground">去重后</p>
        <p class="text-lg font-semibold tabular-nums">{{ prisma.records_screened }}</p>
      </div>
      <div class="min-w-24 rounded-lg border border-border bg-card px-3 py-2">
        <p class="text-xs text-muted-foreground">标题/摘要排除</p>
        <p class="text-lg font-semibold tabular-nums">{{ prisma.title_abstract.excluded }}</p>
      </div>
      <div class="min-w-24 rounded-lg border border-border bg-card px-3 py-2">
        <p class="text-xs text-muted-foreground">全文排除</p>
        <p class="text-lg font-semibold tabular-nums">{{ prisma.fulltext.excluded }}</p>
      </div>
      <div class="min-w-24 rounded-lg border border-primary/40 bg-primary/8 px-3 py-2">
        <p class="text-xs text-primary">纳入</p>
        <p class="text-lg font-semibold tabular-nums text-primary">{{ prisma.included }}</p>
      </div>
    </div>

    <div class="mb-3">
      <Label class="sr-only" for="screening-stage-filter">按阶段过滤</Label>
      <select
        id="screening-stage-filter"
        v-model="stageFilter"
        data-test="screening-stage-filter"
        class="flex h-9 rounded-md border border-input bg-background px-3 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
        @change="handleStageChange()"
      >
        <option value="">全部阶段</option>
        <option value="title_abstract">标题/摘要</option>
        <option value="fulltext">全文</option>
        <option value="included">已纳入</option>
      </select>
    </div>

    <div class="mb-3 flex flex-wrap items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        type="button"
        data-test="screening-priority-toggle"
        :aria-pressed="priorityEnabled"
        :disabled="priorityDisabled"
        @click="togglePriority"
      >
        <Sparkles :size="15" aria-hidden="true" />
        智能排序：{{ priorityEnabled ? "开" : "关" }}
      </Button>
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
      <span v-if="priorityLoading">正在计算优先级…</span>
      <span v-else-if="priorityError">{{ priorityError }}</span>
      <span v-else-if="priority">{{ priority.message }}</span>
    </Alert>

    <p v-if="loading && !records.length" class="text-sm text-muted-foreground">加载中…</p>

    <EmptyState
      v-else-if="!records.length"
      testid="screening-empty"
      title="还没有候选记录"
      description="先在任务里执行检索，检索结果会作为快照进入筛选工作台；跨源重复已在落库时去除。"
    />

    <ul v-else class="space-y-2">
      <li
        v-for="record in displayRecords"
        :key="record.record_id"
        class="rounded-lg border border-border bg-card px-3 py-2.5"
        :data-test="`screening-row-${record.record_id}`"
      >
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-sm font-medium">{{ record.title }}</span>
              <Badge :variant="STAGE_TONE[record.current_stage] ?? 'muted'">
                {{ STAGE_LABELS[record.current_stage] ?? record.current_stage }}
              </Badge>
              <!-- 只展示推荐名次，不展示模型分数/概率，也不改变记录本身 -->
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
            <div class="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>{{ record.authors.join("；") || "（无作者）" }}</span>
              <code class="rounded bg-muted px-1 py-0.5">
                {{ record.source }}:{{ record.source_id }}
              </code>
              <code v-if="record.doi" class="rounded bg-muted px-1 py-0.5">
                doi:{{ record.doi }}
              </code>
            </div>
          </div>

          <div
            v-if="canDecide(record)"
            class="flex flex-wrap items-center gap-1.5"
          >
            <Button
              size="sm"
              :data-test="`screening-include-${record.record_id}`"
              type="button"
              :disabled="busyId === record.record_id"
              @click="handleInclude(record)"
            >
              <Check :size="15" aria-hidden="true" />
              <span>纳入</span>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              :data-test="`screening-maybe-${record.record_id}`"
              type="button"
              :disabled="busyId === record.record_id"
              @click="handleMaybe(record)"
            >
              <MinusCircle :size="15" aria-hidden="true" />
              <span>待定</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              :data-test="`screening-exclude-${record.record_id}`"
              type="button"
              :disabled="busyId === record.record_id"
              @click="openExclude(record)"
            >
              <CircleX :size="15" aria-hidden="true" />
              <span>排除</span>
            </Button>
          </div>
        </div>

        <p v-if="record.abstract" class="mt-1.5 line-clamp-3 text-sm text-muted-foreground">
          {{ record.abstract }}
        </p>

        <div
          v-if="excludeFormId === record.record_id"
          class="mt-2 flex flex-wrap items-end gap-2 rounded-md border border-border bg-muted/40 p-2.5"
          :data-test="`screening-exclude-form-${record.record_id}`"
        >
          <div class="grid min-w-56 flex-1 gap-1.5">
            <Label :for="`screening-exclude-reason-${record.record_id}`">排除理由（必填）</Label>
            <Input
              :id="`screening-exclude-reason-${record.record_id}`"
              v-model="excludeReason"
              :data-test="`screening-exclude-reason-${record.record_id}`"
              type="text"
              placeholder="如：不相关 / 无全文 / 人群不符"
              @keydown.enter.prevent="submitExclude(record)"
            />
          </div>
          <Button
            :data-test="`screening-exclude-submit-${record.record_id}`"
            type="button"
            @click="submitExclude(record)"
          >
            确认排除
          </Button>
        </div>
      </li>
    </ul>

    <!-- Mermaid 源码：可直接复制进论文/GitHub 渲染 -->
    <div v-if="prisma?.mermaid" class="mt-6">
      <h2 class="mb-1.5 text-base font-semibold tracking-tight">PRISMA 流程图（Mermaid 源码）</h2>
      <pre
        class="overflow-auto rounded-lg border border-border bg-card p-3 font-mono text-xs leading-relaxed"
        data-test="screening-mermaid"
        >{{ prisma.mermaid }}</pre
      >
    </div>
  </section>
</template>
