<script setup lang="ts">
import { computed, nextTick, reactive, ref, useId, watch } from "vue";
import { BookOpen, Download } from "lucide-vue-next";
import { Motion, useReducedMotion } from "motion-v";

import { taskApi } from "@/api/tasks";
import { renderTemplate } from "@/api/templates";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import ClaimEvidenceDrawer from "@/components/tasks/ClaimEvidenceDrawer.vue";
import EvidenceMatrixView from "@/components/tasks/EvidenceMatrixView.vue";
import ReferenceVerificationPanel from "@/components/tasks/ReferenceVerificationPanel.vue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { MotionDialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SPRING_LAYOUT } from "@/lib/ease";
import { toastError, toastSuccess } from "@/lib/toast";
import type {
  Artifact,
  BibliographyEntry,
  ClaimCheckResult,
  EvidenceMatrixCell,
  QualityFlag,
  ReferenceVerificationSummary,
  ReviewReport,
  VerificationStatus,
} from "@/api/types";
import { parseClaimChecks } from "@/utils/claim-verdict";
import {
  QUALITY_FLAG_LABELS,
  decorateCitationMarkers,
  parseQualityFlags,
  parseSourceType,
  retractedIndices,
} from "@/utils/citation-quality";
import { renderMarkdown } from "@/utils/markdown";

// 结果视图：左侧正文（Markdown 渲染成排版 + 可切源码），右侧克制的元数据栏。
//
// ## 为什么不再用纯文本
//
// 原先用 `<pre>{{ final_markdown }}</pre>`，注释写的原因是「不用 v-html」
// （安全考虑）。但用户看到的是一篇**带井号和星号的综述** —— 这是核心产出页，
// 可读性影响最大。
//
// 现在改为 marked + DOMPurify 两段式（见 utils/markdown.ts）：
// 渲染成排版，同时消毒；环境不支持可靠消毒时降级为转义纯文本。
// 这样既保住安全底线，又不是把 Markdown 原样摆在用户面前。
const props = defineProps<{
  taskId: string;
  report: ReviewReport;
  artifacts?: Artifact[];
}>();

const emit = defineEmits<{ refresh: [] }>();

const downloading = ref<"markdown" | "docx" | null>(null);
const errorMessage = ref<string | null>(null);
const tabGroupId = useId();
const reduced = useReducedMotion();
const markdownContainerRef = ref<HTMLElement | null>(null);
/** 源码视图开关（默认渲染视图）。 */
const showSource = ref(false);
/** 综述正文 / 证据矩阵 Tab（仅存在 evidence_matrix 时展示切换栏）。 */
const resultView = ref<"paper" | "matrix">("paper");
const hasMatrix = computed(
  () => Boolean(props.report.evidence_matrix?.questions?.length),
);

// 切回渲染视图后等待一帧确保容器可见，预留增量高亮/排版校验挂钩
watch(
  () => [props.report.final_markdown, resultView.value, showSource.value] as const,
  async ([, view, source]) => {
    if (view !== "paper" || source) return;
    await nextTick();
  },
  { flush: "post" },
);
const fillingQuestionIndex = ref<number | null>(null);
const gapNotice = ref<string | null>(null);
const activeMatrixCell = ref<{
  cell: EvidenceMatrixCell;
  question: string;
} | null>(null);

/** 消毒后的正文 HTML（未做质量标记；`v-html` 的安全性由 renderMarkdown 保证）。 */
const baseRenderedMarkdown = computed(() => renderMarkdown(props.report.final_markdown));

const SCORE_LABELS: Record<string, string> = {
  citation_traceability: "引用可追溯性",
  quality: "质量",
  coherence: "连贯性",
  coverage: "覆盖面",
};

const scores = computed(() =>
  Object.entries(props.report.scores ?? {}).map(([key, value]) => {
    const numeric = Number(value);
    const valid = typeof value === "number" && Number.isFinite(numeric) && numeric >= 0 && numeric <= 1;
    return {
      key,
      label: SCORE_LABELS[key] ?? key,
      valid,
      percent: valid ? Math.round(numeric * 100) : null,
    };
  })
);

/** 只允许 http/https 链接，其余退化为纯文本（避免 javascript:/data: 注入） */
function safeUrl(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  return /^https?:\/\//i.test(value) ? value : null;
}

// ===== 引用核验（Issue #3 遗留项）：stats 是 Record<string, unknown>，必须安全解析 =====

const VERIFICATION_STATUSES: readonly VerificationStatus[] = [
  "verified",
  "metadata_mismatch",
  "not_found",
  "unverified",
  "cn_source_unverified",
];

const SUMMARY_COUNT_KEYS = [
  "total",
  "verified",
  "metadata_mismatch",
  "not_found",
  "unverified",
  "cached_hits",
  "dropped",
] as const;

/** 非法值 → null（不渲染徽标，也不猜测结论） */
function parseVerificationStatus(value: unknown): VerificationStatus | null {
  return typeof value === "string" && (VERIFICATION_STATUSES as readonly string[]).includes(value)
    ? (value as VerificationStatus)
    : null;
}

/**
 * 安全解析 `stats.reference_verification`：缺键 / 类型不符 → null。
 *
 * 不抛错、也不臆造 0 值——离线任务默认不核验，展示层必须能区分「未核验」与「核验 0 条」。
 */
function parseVerificationSummary(value: unknown): ReferenceVerificationSummary | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const counts: Record<string, number> = {};
  for (const key of SUMMARY_COUNT_KEYS) {
    const raw = record[key];
    if (typeof raw !== "number" || !Number.isFinite(raw)) {
      return null;
    }
    counts[key] = raw;
  }
  if (typeof record.intercepted !== "boolean") {
    return null;
  }
  // 来源质量计数（Issue #31）是可选增量：缺键/非法值表示「后端未评估」，不是 0。
  const optionalCount = (key: string): number | undefined => {
    const raw = record[key];
    return typeof raw === "number" && Number.isFinite(raw) ? raw : undefined;
  };
  const retracted = optionalCount("retracted");
  const preprint = optionalCount("preprint");
  const lowCitation = optionalCount("low_citation");
  // 中文源跳过态（Issue #67）：旧报告没有该键，按 0 处理（只是徽标不出现）。
  const cnSourceUnverified = optionalCount("cn_source_unverified") ?? 0;
  return {
    total: counts.total,
    verified: counts.verified,
    metadata_mismatch: counts.metadata_mismatch,
    not_found: counts.not_found,
    unverified: counts.unverified,
    cn_source_unverified: cnSourceUnverified,
    cached_hits: counts.cached_hits,
    dropped: counts.dropped,
    intercepted: record.intercepted,
    ...(retracted !== undefined ? { retracted } : {}),
    ...(preprint !== undefined ? { preprint } : {}),
    ...(lowCitation !== undefined ? { low_citation: lowCitation } : {}),
  };
}

const verificationSummary = computed<ReferenceVerificationSummary | null>(() =>
  parseVerificationSummary(props.report.stats?.reference_verification)
);

const bibliography = computed<BibliographyEntry[]>(() =>
  (props.report.bibliography ?? []).map((item, position) => {
    const record = item as Record<string, unknown>;
    const index = Number(record.index ?? position + 1);
    const qualityFlags = parseQualityFlags(record.quality_flags);
    const sourceType = parseSourceType(record.source_type);
    return {
      index,
      title: String(record.title ?? record.source_id ?? `文献 ${index}`),
      url: safeUrl(record.source_url ?? record.url),
      verification_status: parseVerificationStatus(record.verification_status),
      is_retracted: typeof record.is_retracted === "boolean" ? record.is_retracted : null,
      // 质量字段只在后端真的给出时展开：旧报告（无这些键）与既有断言保持原样。
      ...(qualityFlags.length ? { quality_flags: qualityFlags } : {}),
      ...(sourceType ? { source_type: sourceType } : {}),
      ...(typeof record.cited_by_count === "number" && Number.isFinite(record.cited_by_count)
        ? { cited_by_count: record.cited_by_count }
        : {}),
      ...(typeof record.retraction_reason === "string" && record.retraction_reason
        ? { retraction_reason: record.retraction_reason }
        : {}),
      ...(typeof record.retraction_notice_doi === "string" && record.retraction_notice_doi
        ? { retraction_notice_doi: record.retraction_notice_doi }
        : {}),
    };
  })
);

/** 已撤稿的参考文献条目（来源质量 Issue #31）：正文顶部横幅与角标标红共用。 */
const retractedEntries = computed(() =>
  bibliography.value.filter(
    (entry) => entry.is_retracted === true || entry.quality_flags?.includes("retracted"),
  )
);

/** 正文 HTML：在消毒后的产物上把 [En] 换成可点击角标（撤稿文献用红色警示样式）。 */
const renderedMarkdown = computed(() =>
  decorateCitationMarkers(baseRenderedMarkdown.value, {
    retracted: retractedIndices(retractedEntries.value),
  }),
);

// ===== 论断级核验抽屉（Issue #31 Phase 2）=====

const claimChecks = computed(() => parseClaimChecks(props.report.claim_checks));

/** 当前打开的引用序号；null = 抽屉关闭 */
const activeCitation = ref<number | null>(null);

const activeEntry = computed<BibliographyEntry | null>(
  () => bibliography.value.find((entry) => entry.index === activeCitation.value) ?? null,
);

/** 同一篇文献可能被多句话引用：抽屉里给出所有相关论断，不能只展示第一条。 */
const activeClaims = computed(() => {
  if (activeMatrixCell.value !== null) {
    const idSet = new Set(activeMatrixCell.value.cell.claim_ids);
    return claimChecks.value.filter((check) => idSet.has(check.claim_id));
  }
  return activeCitation.value === null
    ? []
    : claimChecks.value.filter((check) => check.cited_ref_ids.includes(`E${activeCitation.value}`));
});

const drawerOpen = computed(
  () => activeCitation.value !== null || activeMatrixCell.value !== null,
);

const BUCKET_LABELS: Record<EvidenceMatrixCell["bucket"], string> = {
  strong: "强支持",
  weak_or_caveat: "弱支持 / 存疑",
  conflicting: "冲突",
  gap: "空白 / 无法核验",
};

const drawerTitle = computed(() => {
  if (!activeMatrixCell.value) return null;
  const { cell } = activeMatrixCell.value;
  return `证据矩阵 · Q${cell.question_index + 1} · ${BUCKET_LABELS[cell.bucket]}`;
});

const drawerSubtitle = computed(() => activeMatrixCell.value?.question ?? null);

function openMatrixCell(cell: EvidenceMatrixCell, question: string) {
  activeCitation.value = null;
  activeMatrixCell.value = { cell, question };
}

function closeDrawer() {
  activeCitation.value = null;
  activeMatrixCell.value = null;
}

async function handleFillGap(questionIndex: number, query: string | null) {
  fillingQuestionIndex.value = questionIndex;
  errorMessage.value = null;
  gapNotice.value = null;
  try {
    const res = await taskApi.fillMatrixGap(props.taskId, {
      question_index: questionIndex,
      query,
    });
    gapNotice.value =
      res.added_count > 0
        ? `已为 Q${questionIndex + 1} 定向补充 ${res.added_count} 篇文献（去重 ${res.duplicate_count} 篇）。`
        : `Q${questionIndex + 1} 未检出更多新文献（命中 ${res.duplicate_count} 篇已有文献）。`;
    emit("refresh");
  } catch (error) {
    errorMessage.value = (error as Error).message || "补检索失败";
  } finally {
    fillingQuestionIndex.value = null;
  }
}

/** 事件委托：正文来自 v-html，逐个角标绑事件不现实，按 data-cite 找最近的角标。 */
function onPaperClick(event: MouseEvent) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const marker = target.closest<HTMLElement>("[data-cite]");
  if (!marker) {
    return;
  }
  const index = Number(marker.dataset.cite);
  if (Number.isInteger(index) && index > 0) {
    activeCitation.value = index;
  }
}

const retractedBannerText = computed(() => {
  const items = retractedEntries.value.map((entry) => {
    const reason = entry.retraction_reason ? `（${entry.retraction_reason}）` : "";
    const notice = entry.retraction_notice_doi ? `，撤稿声明 ${entry.retraction_notice_doi}` : "";
    return `[${entry.index}] ${entry.title}${reason}${notice}`;
  });
  return `以下文献已被撤稿：${items.join("；")}。正文对这些来源的引用不能作为论据，请移除相关论断或更换来源后再导出。`;
});

const QUALITY_TONES: Record<QualityFlag, string> = {
  retracted: "border-destructive/30 bg-destructive/10 text-destructive",
  preprint: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  low_citation: "border-border bg-muted text-muted-foreground",
};

/** 低被引标签带具体次数；撤稿标签悬停给出原因/声明 DOI。 */
function qualityBadgeLabel(flag: QualityFlag, entry: BibliographyEntry): string {
  if (flag === "low_citation" && typeof entry.cited_by_count === "number") {
    return `低被引 · ${entry.cited_by_count} 次`;
  }
  return QUALITY_FLAG_LABELS[flag];
}

function qualityBadgeTitle(flag: QualityFlag, entry: BibliographyEntry): string | undefined {
  if (flag !== "retracted") {
    return undefined;
  }
  if (entry.retraction_reason) {
    return `撤稿原因：${entry.retraction_reason}`;
  }
  if (entry.retraction_notice_doi) {
    return `撤稿声明：${entry.retraction_notice_doi}`;
  }
  return "该文献已被撤稿";
}

function findArtifact(kind: "markdown" | "docx"): Artifact | undefined {
  return (props.artifacts ?? []).find((item) => item.kind === kind);
}

async function download(kind: "markdown" | "docx") {
  const artifact = findArtifact(kind);
  if (!artifact) {
    return;
  }
  downloading.value = kind;
  errorMessage.value = null;
  let objectUrl: string | null = null;
  try {
    const { blob, filename } = await taskApi.downloadArtifact(props.taskId, artifact.artifact_id);
    objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename ?? artifact.download_name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    toastSuccess(kind === "markdown" ? "已导出 Markdown" : "已导出 DOCX");
  } catch (error) {
    errorMessage.value = (error as Error).message || "下载失败";
    toastError(errorMessage.value);
  } finally {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl); // 触发后立即释放，避免内存泄漏
    }
    downloading.value = null;
  }
}

// ===== 高校标准「文献综述」母版导出（Phase 1）=====
const THESIS_COVER_STORAGE_KEY = "vp:thesis-cover-meta";
const BUILTIN_THESIS_TEMPLATE_ID = "builtin:literature_review_cn_standard";
const thesisDialogOpen = ref(false);
const exportingThesis = ref(false);
const coverMeta = reactive({
  title: "",
  author: "",
  advisor: "",
  major: "",
  college: "",
});

const defaultThesisTitle = computed(() => {
  const match = /^\s*#\s+(.+)$/m.exec(props.report.final_markdown ?? "");
  return match?.[1]?.trim() || "文献综述";
});

function loadSavedCoverMeta() {
  try {
    const raw = localStorage.getItem(THESIS_COVER_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Partial<typeof coverMeta>;
    if (typeof parsed.author === "string") coverMeta.author = parsed.author;
    if (typeof parsed.advisor === "string") coverMeta.advisor = parsed.advisor;
    if (typeof parsed.major === "string") coverMeta.major = parsed.major;
    if (typeof parsed.college === "string") coverMeta.college = parsed.college;
  } catch {
    /* 隐私模式 / JSON 损坏：忽略历史填写 */
  }
}

function saveCoverMeta() {
  try {
    localStorage.setItem(
      THESIS_COVER_STORAGE_KEY,
      JSON.stringify({
        author: coverMeta.author.trim(),
        advisor: coverMeta.advisor.trim(),
        major: coverMeta.major.trim(),
        college: coverMeta.college.trim(),
      }),
    );
  } catch {
    /* 忽略存储受限异常 */
  }
}

function openThesisTemplateDialog() {
  loadSavedCoverMeta();
  if (!coverMeta.title.trim()) {
    coverMeta.title = defaultThesisTitle.value;
  }
  errorMessage.value = null;
  thesisDialogOpen.value = true;
}

function formatBibliographyLines(): string[] {
  return (props.report.bibliography ?? []).map((item, position) => {
    const record = item as Record<string, unknown>;
    const idx = Number(record.index ?? position + 1);
    const title = String(record.title ?? record.source_id ?? `文献 ${idx}`).trim();
    const rawAuthors = record.authors;
    const authors = Array.isArray(rawAuthors)
      ? rawAuthors.map((a) => String(a).trim()).filter(Boolean).slice(0, 3).join(", ")
      : typeof rawAuthors === "string"
        ? rawAuthors.trim()
        : "";
    const venue = String(record.venue ?? record.journal ?? "").trim();
    const year = record.year ?? record.issued_year ?? "";
    const head = authors ? `${authors}. ` : "";
    const tail = [venue, year ? String(year) : ""].filter(Boolean).join(", ");
    return `[${idx}] ${head}${title}${tail ? `. ${tail}.` : "."}`;
  });
}

async function confirmThesisTemplateExport() {
  if (exportingThesis.value) return;
  exportingThesis.value = true;
  errorMessage.value = null;
  saveCoverMeta();
  const safeTitle = coverMeta.title.trim() || defaultThesisTitle.value;
  let objectUrl: string | null = null;
  try {
    const blob = await renderTemplate(BUILTIN_THESIS_TEMPLATE_ID, {
      sections: {
        title: safeTitle,
        author: coverMeta.author.trim() || " ",
        advisor: coverMeta.advisor.trim() || " ",
        major: coverMeta.major.trim() || " ",
        college: coverMeta.college.trim() || " ",
        body: props.report.final_markdown ?? "",
      },
      bibliography: formatBibliographyLines(),
      bibliography_entries: props.report.bibliography ?? [],
      filename: `${safeTitle}-高校标准综述.docx`,
    });
    objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = `${safeTitle}-高校标准综述.docx`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    thesisDialogOpen.value = false;
    toastSuccess("已按高校标准母版导出 DOCX");
  } catch (error) {
    errorMessage.value = (error as Error).message || "高校标准母版导出失败";
    toastError(errorMessage.value);
  } finally {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
    }
    exportingThesis.value = false;
  }
}
</script>

<template>
  <section class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_17rem]" data-test="task-result">
    <!-- 正文是核心交付物：纸页式排版，不再把长文关进 70vh 的内滚动卡片。 -->
    <article class="min-w-0" data-test="result-paper">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <div>
            <p class="text-[9px] font-semibold tracking-[0.16em] text-primary">终稿复盘</p>
            <h2 class="mt-1 text-base font-semibold tracking-tight">最终综述</h2>
          </div>
          <div
            v-if="hasMatrix"
            role="tablist"
            aria-label="结果展示切换"
            class="inline-flex gap-1 rounded-lg bg-muted p-0.5 text-xs"
          >
            <button
              type="button"
              role="tab"
              data-test="result-tab-paper"
              :aria-selected="resultView === 'paper'"
              :class="[
                'relative rounded-md px-2.5 py-1 font-medium transition-colors',
                resultView === 'paper'
                  ? 'bg-background text-foreground shadow-xs'
                  : 'text-muted-foreground hover:text-foreground',
              ]"
              @click="resultView = 'paper'"
            >
              综述正文
              <Motion
                v-if="resultView === 'paper'"
                :layout-id="reduced ? undefined : `result-tab-pill-${tabGroupId}`"
                :transition="SPRING_LAYOUT"
                class="pointer-events-none absolute inset-x-1 bottom-0.5 h-0.5 rounded-full bg-primary"
              />
            </button>
            <button
              type="button"
              role="tab"
              data-test="result-tab-matrix"
              :aria-selected="resultView === 'matrix'"
              :class="[
                'relative rounded-md px-2.5 py-1 font-medium transition-colors',
                resultView === 'matrix'
                  ? 'bg-background text-foreground shadow-xs'
                  : 'text-muted-foreground hover:text-foreground',
              ]"
              @click="resultView = 'matrix'"
            >
              证据矩阵
              <Motion
                v-if="resultView === 'matrix'"
                :layout-id="reduced ? undefined : `result-tab-pill-${tabGroupId}`"
                :transition="SPRING_LAYOUT"
                class="pointer-events-none absolute inset-x-1 bottom-0.5 h-0.5 rounded-full bg-primary"
              />
            </button>
          </div>
        </div>
        <!-- 源码/预览切换仍保留，核对引用标记与复制时可查看原始 Markdown。 -->
        <button
          v-if="resultView === 'paper'"
          type="button"
          data-test="toggle-md-source"
          class="rounded px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          @click="showSource = !showSource"
        >
          {{ showSource ? "渲染视图" : "源码" }}
        </button>
      </div>

      <Alert
        v-if="gapNotice"
        variant="info"
        class="mb-4"
        data-test="matrix-gap-notice"
        :description="gapNotice"
      />

      <!-- 撤稿警示：只要正文引用了已撤稿文献，就必须在正文顶部明确提示移除。 -->
      <Alert
        v-if="retractedEntries.length"
        variant="destructive"
        :dismissible="false"
        class="mb-4"
        data-test="retracted-banner"
        :title="`本文引用了 ${retractedEntries.length} 篇已撤稿文献`"
        :description="retractedBannerText"
      />

      <EvidenceMatrixView
        v-if="props.report.evidence_matrix"
        v-show="resultView === 'matrix'"
        :matrix="props.report.evidence_matrix"
        :filling-index="fillingQuestionIndex"
        @select-cell="openMatrixCell"
        @fill-gap="handleFillGap"
      />

      <!-- renderMarkdown 继续负责 marked + DOMPurify 两段式安全链；v-show 保活避免切换 Tab 丢失滚动与 DOM 状态。 -->
      <div
        ref="markdownContainerRef"
        v-show="resultView === 'paper' && !showSource"
        data-test="result-markdown"
        class="md-body max-h-none overflow-visible rounded-lg border border-border bg-card px-5 py-6 font-display sm:px-8 sm:py-8 lg:px-10"
        v-html="renderedMarkdown"
        @click="onPaperClick"
      />

      <pre
        v-show="resultView === 'paper' && showSource"
        data-test="result-markdown-source"
        class="max-h-[70vh] overflow-auto rounded-lg border border-border bg-card p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap"
        >{{ props.report.final_markdown }}</pre
      >
    </article>

    <!-- 证据栏始终在正文之后：桌面并列，移动端正文优先。所有数字都来自真实报告。 -->
    <aside class="flex flex-col gap-5" data-test="evidence-rail">
      <section class="border-b border-border pb-5">
        <ReferenceVerificationPanel :summary="verificationSummary" :entries="bibliography" />
      </section>

      <section class="border-b border-border pb-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h3 class="text-sm font-medium">质量检查</h3>
          <span class="text-[9px] tracking-wider text-muted-foreground">百分制评分</span>
        </div>
        <ul v-if="scores.length" class="flex flex-col gap-2.5" data-test="result-scores">
          <li
            v-for="score in scores"
            :key="score.key"
            class="grid grid-cols-[5rem_1fr_2rem] items-center gap-2 text-xs"
          >
            <span class="text-muted-foreground">{{ score.label }}</span>
            <template v-if="score.valid">
              <span
                class="h-1 overflow-hidden rounded-full bg-muted"
                role="meter"
                :aria-label="score.label"
                :aria-valuenow="score.percent ?? undefined"
                aria-valuemin="0"
                aria-valuemax="100"
              >
                <span
                  class="block h-full w-full origin-left rounded-full bg-primary transition-transform duration-250 motion-reduce:transition-none"
                  :style="{ transform: `scaleX(${(score.percent ?? 0) / 100})` }"
                />
              </span>
              <span class="text-right font-medium tabular-nums">{{ score.percent }}</span>
            </template>
            <span
              v-else
              data-test="result-score-unavailable"
              class="col-span-2 text-right text-muted-foreground"
            >
              不可用
            </span>
          </li>
        </ul>
        <p v-else data-test="result-scores-empty" class="text-xs leading-5 text-muted-foreground">
          暂无评分。当前报告未提供可用的质量分数。
        </p>
      </section>

      <section class="border-b border-border pb-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h3 class="text-sm font-medium">参考文献</h3>
          <span v-if="bibliography.length" class="text-[9px] tracking-wider text-muted-foreground">
            共 {{ bibliography.length }} 条
          </span>
        </div>
        <ol v-if="bibliography.length" class="flex flex-col gap-3" data-test="result-bibliography">
          <li
            v-for="entry in bibliography"
            :key="entry.index"
            class="flex items-start gap-2 text-sm"
            :data-test="`bibliography-item-${entry.index}`"
          >
            <span class="mt-0.5 w-5 shrink-0 font-mono text-[10px] text-primary">
              {{ String(entry.index).padStart(2, "0") }}
            </span>
            <div class="flex min-w-0 flex-col gap-1">
              <a
                v-if="entry.url"
                :data-test="`bibliography-link-${entry.index}`"
                :href="entry.url"
                target="_blank"
                rel="noopener noreferrer"
                class="min-w-0 font-display text-xs leading-5 break-words text-foreground underline-offset-4 hover:text-primary hover:underline"
              >
                {{ entry.title }}
              </a>
              <span v-else class="min-w-0 font-display text-xs leading-5 break-words">{{ entry.title }}</span>
              <!-- 来源质量标签（Issue #31）：撤稿=红、预印本=琥珀、低被引=中性灰。 -->
              <span
                v-if="entry.quality_flags?.length"
                class="flex flex-wrap items-center gap-1"
                :data-test="`bibliography-quality-${entry.index}`"
              >
                <span
                  v-for="flag in entry.quality_flags"
                  :key="flag"
                  class="inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] leading-3 font-medium"
                  :class="QUALITY_TONES[flag]"
                  :title="qualityBadgeTitle(flag, entry)"
                  :data-test="`bibliography-flag-${entry.index}-${flag}`"
                >
                  {{ qualityBadgeLabel(flag, entry) }}
                </span>
              </span>
            </div>
          </li>
        </ol>
        <p
          v-else
          data-test="result-bibliography-empty"
          class="text-xs leading-5 text-muted-foreground"
        >
          暂无参考文献。当前报告没有提供可展示的条目。
        </p>
      </section>

      <section v-if="props.report.issues?.length" class="border-b border-border pb-5">
        <h3 class="mb-2 text-sm font-medium">待改进</h3>
        <ul class="list-disc space-y-1 pl-4 text-xs leading-5 text-muted-foreground" data-test="result-issues">
          <li v-for="issue in props.report.issues" :key="issue">{{ issue }}</li>
        </ul>
      </section>

      <section>
        <h3 class="mb-2 text-sm font-medium">导出与交付</h3>
        <div class="flex flex-wrap gap-1.5">
          <Button
            variant="outline"
            size="sm"
            data-test="download-markdown"
            type="button"
            :disabled="!findArtifact('markdown') || downloading !== null"
            @click="download('markdown')"
          >
            <Download :size="15" aria-hidden="true" /> Markdown
          </Button>
          <Button
            variant="outline"
            size="sm"
            data-test="download-docx"
            type="button"
            :disabled="!findArtifact('docx') || downloading !== null"
            @click="download('docx')"
          >
            <Download :size="15" aria-hidden="true" /> DOCX
          </Button>
          <Button
            variant="outline"
            size="sm"
            data-test="export-thesis-template-btn"
            type="button"
            :disabled="downloading !== null || exportingThesis"
            @click="openThesisTemplateDialog"
          >
            <BookOpen :size="15" aria-hidden="true" /> 高校标准母版
          </Button>
        </div>
        <ErrorBanner :message="errorMessage" class="mt-2" @dismiss="errorMessage = null" />
      </section>
    </aside>

    <!-- 高校标准综述母版封面信息弹窗（记住除标题外的常用信息） -->
    <MotionDialog
      v-model:open="thesisDialogOpen"
      title="按高校标准母版导出 Word"
      aria-label="高校标准综述母版封面信息"
      initial-focus="#thesis-cover-title"
      :panel-attrs="{ 'data-test': 'thesis-template-dialog', class: 'p-4' }"
    >
      <p class="mb-3 text-xs leading-relaxed text-muted-foreground">
        生成符合高校开题/学位论文规范的可打印 .docx（中仿宋 + 英 Times New Roman、三线表、学术插图、奇数页起章）。
      </p>
      <div class="grid gap-2.5">
        <div class="grid gap-1">
          <Label for="thesis-cover-title">论文题目</Label>
          <Input
            id="thesis-cover-title"
            v-model="coverMeta.title"
            data-test="thesis-cover-title"
            placeholder="输入综述题目"
          />
        </div>
        <div class="grid gap-2 sm:grid-cols-2">
          <div class="grid gap-1">
            <Label for="thesis-cover-author">作者姓名</Label>
            <Input
              id="thesis-cover-author"
              v-model="coverMeta.author"
              data-test="thesis-cover-author"
              placeholder="如 张三"
            />
          </div>
          <div class="grid gap-1">
            <Label for="thesis-cover-advisor">指导教师</Label>
            <Input
              id="thesis-cover-advisor"
              v-model="coverMeta.advisor"
              data-test="thesis-cover-advisor"
              placeholder="如 李四 教授"
            />
          </div>
        </div>
        <div class="grid gap-2 sm:grid-cols-2">
          <div class="grid gap-1">
            <Label for="thesis-cover-major">专业</Label>
            <Input
              id="thesis-cover-major"
              v-model="coverMeta.major"
              data-test="thesis-cover-major"
              placeholder="如 计算机科学与技术"
            />
          </div>
          <div class="grid gap-1">
            <Label for="thesis-cover-college">学院</Label>
            <Input
              id="thesis-cover-college"
              v-model="coverMeta.college"
              data-test="thesis-cover-college"
              placeholder="如 计算机学院"
            />
          </div>
        </div>
      </div>
      <div class="mt-4 flex justify-end gap-2">
        <Button variant="ghost" size="sm" type="button" @click="thesisDialogOpen = false">
          取消
        </Button>
        <Button
          size="sm"
          type="button"
          data-test="thesis-template-confirm"
          :disabled="exportingThesis"
          @click="confirmThesisTemplateExport"
        >
          {{ exportingThesis ? "生成中…" : "确认导出 DOCX" }}
        </Button>
      </div>
    </MotionDialog>

    <!-- 论断核验抽屉：点正文角标或证据矩阵非空格子打开，展示来源质量与逐句判定。 -->
    <ClaimEvidenceDrawer
      :open="drawerOpen"
      :index="activeCitation"
      :entry="activeEntry"
      :claims="activeClaims"
      :title="drawerTitle"
      :subtitle="drawerSubtitle"
      @close="closeDrawer"
    />
  </section>
</template>
