<script setup lang="ts">
import { computed, inject, nextTick, onMounted, ref, watch } from "vue";
import { routerKey } from "vue-router";
import { Check, ChevronDown, Loader2, Plus, Sparkles, X } from "lucide-vue-next";
import { useReducedMotion } from "motion-v";

import AnimatedCollapse from "@/components/common/AnimatedCollapse.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { toastInfo, toastWarning } from "@/lib/toast";
import {
    clearTaskDraft,
    loadTaskDraft,
    saveTaskDraft,
    type TaskCreateDraftValues,
} from "@/lib/task-draft";
import { getBillingUsage } from "@/api/billing";
import { listCitationStyles } from "@/api/citation-styles";
import { listDataSourceCredentials } from "@/api/data-source-credentials";
import { suggestKeywords, type KeywordSuggestion } from "@/api/intent";
import { listLLMCredentials } from "@/api/llm-credentials";
import { getLibrarySummary } from "@/api/reference-library";
import {
    DEFAULT_WORKFLOW_PRESET,
    WORKFLOW_PRESETS,
    getWorkflowPreset,
} from "@/config/workflow-presets";
import type {
    CitationStyleSource,
    LLMCredentialInfo,
    SnowballDirection,
    TaskCreate,
    WorkflowPreset,
} from "@/api/types";

// 创建表单：只做校验与 payload 组装，提交动作由父组件负责（保持可测）
const props = withDefaults(
    defineProps<{
        pending?: boolean;
        error?: string | null;
        documents?: Array<{ id: string; title: string }>;
        // 草稿按用户隔离（localStorage 命名空间）；父组件传 auth.user?.id，
        // 未登录/单测未注入时为 undefined → 落 anonymous 命名空间
        draftUserId?: string | null;
    }>(),
    { pending: false, error: null, documents: () => [] },
);

const emit = defineEmits<{ submit: [payload: TaskCreate]; dismissError: [] }>();

const TOPIC_MIN = 5;
const TOPIC_MAX = 200;
const KEYWORD_MAX = 10;
const DOC_MIN = 1;
const DOC_MAX = 50;

// 检索文献源（Issue #67）：万方中文源需用户自带 App Key（BYOK），
// 其余免费源无 Key 也可用。不勾选 = 由后端按语言与万方 Key 自动决定。
interface SourceOption {
    value: string;
    label: string;
    hint: string;
    requiresWanfangKey?: boolean;
}

const SOURCE_OPTIONS: SourceOption[] = [
    {
        value: "wanfang",
        label: "万方中文",
        hint: "中文期刊 / 学位论文 / 会议（需自带万方 Key；每次检索约 ¥0.01，单任务最多 5 次）",
        requiresWanfangKey: true,
    },
    { value: "openalex", label: "OpenAlex", hint: "免费开放学术元数据" },
    { value: "arxiv", label: "arXiv", hint: "预印本（免费）" },
    { value: "pubmed", label: "PubMed", hint: "生物医学文献（免费）" },
    { value: "semantic_scholar", label: "Semantic Scholar", hint: "免费开放学术元数据" },
];

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

const workflowPreset = ref<WorkflowPreset>(DEFAULT_WORKFLOW_PRESET);
const activePreset = computed(() => getWorkflowPreset(workflowPreset.value));
const isVerifyCitations = computed(
    () => workflowPreset.value === "verify_citations",
);
const isCitationSnowballing = computed(
    () => workflowPreset.value === "citation_snowballing",
);

const topic = ref("");
const draftText = ref("");
const seedDois = ref<string[]>([]);
const seedDraft = ref("");
const snowballDirection = ref<SnowballDirection>("both");
const SEED_MAX = 5;
const keywords = ref<string[]>([]);
const keywordDraft = ref("");
const maxDocuments = ref(activePreset.value.defaults.maxDocuments);
const dateFrom = ref("");
const dateTo = ref("");
const language = ref<"zh" | "en">("zh");
// null = 列表未加载成功 → 渲染内置两项（降级）；非 null = 渲染动态下拉
const styleOptions = ref<StyleOption[] | null>(null);
const citationStyle = ref("gbt7714");
const offlineDataset = ref(false);
// 引用库作为文献源（2026-10-26 引用库计划）：默认关，开启后完全不外呼检索
// null 表示摘要请求仍在加载中，避免数据尚未返回前误报「0 篇 / 引用库为空」
const useReferenceLibrary = ref(false);
const libraryCount = ref<number | null>(null);
// 检索源选择（Issue #67）：空数组 = 交给后端自动决定；万方芯片依赖用户已配置 App Key
const selectedSources = ref<string[]>([]);
const hasWanfangKey = ref(false);
// 引用核验默认关闭：与全局默认一致，避免用户无感知地引入外呼延迟（Issue #3 遗留项）
const verifyReferences = ref(activePreset.value.defaults.verifyReferences);

function selectPreset(presetId: WorkflowPreset) {
    workflowPreset.value = presetId;
    const preset = getWorkflowPreset(presetId);
    maxDocuments.value = preset.defaults.maxDocuments;
    verifyReferences.value = preset.defaults.verifyReferences;
}
const selectedDocuments = ref<string[]>([]);
const validationMessages = ref<string[]>([]);
const validationErrorFields = ref<Set<string>>(new Set());
const advancedOpen = ref(false);
const reduced = useReducedMotion();
const optionalRouter = inject(routerKey, null);

const ERROR_RING_CLASS =
    "border-destructive ring-2 ring-destructive/20 transition-[border-color,box-shadow] duration-150";

/** 安全导航：无 Vue Router 实例的单测环境下退回浏览器原生跳转 */
function goTo(path: string) {
    if (optionalRouter) {
        void optionalRouter.push(path);
    } else {
        window.location.assign(path);
    }
}

// 套餐对平台模型单次文献篇数的上限（默认 50，免费版从 /billing/usage 读取为 15）
const platformDocLimit = ref(DOC_MAX);
const effectiveDocMax = computed(() =>
    modelSource.value === "platform" ? platformDocLimit.value : DOC_MAX,
);
const maxDocPresets = computed(() => {
    const cap = effectiveDocMax.value;
    if (cap < 20) {
        return [5, 10, cap].filter((n, idx, arr) => n <= cap && arr.indexOf(n) === idx);
    }
    return [5, 10, 20];
});

// BYOK（Issue #15）：显式二选一——「平台模型」还是「自带模型」。
// 2026-11-17 用户要求：选择必须摆在明面上，不能藏在下拉第一项里。
// 列表失败静默降级为「只有平台模型」——建任务不能被模型列表拖垮。
const credentials = ref<LLMCredentialInfo[]>([]);
const modelSource = ref<"platform" | "byok">("platform");
const selectedCredential = ref("");

/** 可选的自带模型：只列 active 的；停用/校验失败的选了必失败。 */
const usableCredentials = computed(() =>
    credentials.value.filter((item) => item.status === "active"),
);
const selectedCredentialInfo = computed(
    () =>
        usableCredentials.value.find(
            (item) => item.credential_id === selectedCredential.value,
        ) ?? null,
);

// ===== 草稿保留（2026-11-26 用户反馈：去导入文献再回来，填好的内容全丢了）=====
// 表单挂在 AnimatedCollapse 里且未 keepMounted：收起表单、点「前往导入文献」、
// 甚至误刷新都会把已填内容连同组件一起丢掉。这里按用户命名空间落 localStorage，
// 恢复后「自动展开表单」由父组件（TaskListView）负责，避免表单直接操作父状态。
const restoredFromDraft = ref(false);
const draftDirty = ref(false);

/** 当前表单快照（白名单字段，与 task-draft 的 sanitize 对齐） */
function draftValues(): TaskCreateDraftValues {
    return {
        workflowPreset: workflowPreset.value,
        topic: topic.value,
        draftText: draftText.value,
        seedDois: [...seedDois.value],
        seedDraft: seedDraft.value,
        snowballDirection: snowballDirection.value,
        keywords: [...keywords.value],
        keywordDraft: keywordDraft.value,
        maxDocuments: maxDocuments.value,
        dateFrom: dateFrom.value,
        dateTo: dateTo.value,
        language: language.value,
        citationStyle: citationStyle.value,
        offlineDataset: offlineDataset.value,
        useReferenceLibrary: useReferenceLibrary.value,
        verifyReferences: verifyReferences.value,
        modelSource: modelSource.value,
        selectedCredential: selectedCredential.value,
        selectedDocuments: [...selectedDocuments.value],
        literatureSources: [...selectedSources.value],
    };
}

/** 把草稿写回表单；越界/非法值直接忽略，保持表单自身默认值 */ 
function applyDraft(values: TaskCreateDraftValues) {
    const presetIds = WORKFLOW_PRESETS.map((preset) => preset.id);
    if (presetIds.includes(values.workflowPreset as WorkflowPreset)) {
        workflowPreset.value = values.workflowPreset as WorkflowPreset;
    }
    topic.value = values.topic;
    draftText.value = values.draftText;
    seedDois.value = values.seedDois.slice(0, SEED_MAX);
    seedDraft.value = values.seedDraft;
    if (
        values.snowballDirection === "both" ||
        values.snowballDirection === "backward" ||
        values.snowballDirection === "forward"
    ) {
        snowballDirection.value = values.snowballDirection;
    }
    keywords.value = values.keywords;
    keywordDraft.value = values.keywordDraft;
    const documents = Number(values.maxDocuments);
    if (Number.isFinite(documents) && documents >= DOC_MIN) {
        maxDocuments.value = documents;
    }
    dateFrom.value = values.dateFrom;
    dateTo.value = values.dateTo;
    if (values.language === "zh" || values.language === "en") {
        language.value = values.language;
    }
    if (values.citationStyle) {
        citationStyle.value = values.citationStyle;
    }
    offlineDataset.value = values.offlineDataset;
    useReferenceLibrary.value = values.useReferenceLibrary;
    verifyReferences.value = values.verifyReferences;
    if (values.modelSource === "platform" || values.modelSource === "byok") {
        modelSource.value = values.modelSource;
    }
    selectedCredential.value = values.selectedCredential;
    selectedDocuments.value = values.selectedDocuments;
    const knownSourceValues = new Set(SOURCE_OPTIONS.map((option) => option.value));
    selectedSources.value = values.literatureSources.filter((name) =>
        knownSourceValues.has(name),
    );
}

// 挂载即恢复：恢复过程本身不标记 dirty（onMounted 里的接口默认值调整也不会
// 把草稿改成「用户没写过」的内容）。
const initialDraft = loadTaskDraft(props.draftUserId);
if (initialDraft) {
    applyDraft(initialDraft.values);
    restoredFromDraft.value = true;
}

/** 只有真实交互才算「有草稿」：避免打开表单就被保存一份空草稿 */
function markDraftDirty() {
    draftDirty.value = true;
}

function persistDraft() {
    if (!draftDirty.value) return;
    saveTaskDraft(props.draftUserId, draftValues());
}

watch(() => draftValues(), persistDraft);

/** 去导入文献前先落盘：导入页没有返回入口时用户至少不会丢内容 */
function goToImport() {
    draftDirty.value = true;
    persistDraft();
    goTo("/references/import");
}

/** 切换检索源；万方无 Key 时不可选（按钮已禁用，这里兜底防程序化选中） */
function toggleSource(value: string) {
    const option = SOURCE_OPTIONS.find((item) => item.value === value);
    if (!option) return;
    if (option.requiresWanfangKey && !hasWanfangKey.value) {
        toastInfo("万方中文源需要先在「我的模型」设置页填写万方 App Key");
        return;
    }
    selectedSources.value = selectedSources.value.includes(value)
        ? selectedSources.value.filter((name) => name !== value)
        : [...selectedSources.value, value];
}

/** 回到表单初始状态（不含接口派生值：styleOptions/libraryCount 等） */
function resetToDefaults() {
    workflowPreset.value = DEFAULT_WORKFLOW_PRESET;
    topic.value = "";
    draftText.value = "";
    seedDois.value = [];
    seedDraft.value = "";
    snowballDirection.value = "both";
    keywords.value = [];
    keywordDraft.value = "";
    maxDocuments.value = activePreset.value.defaults.maxDocuments;
    dateFrom.value = "";
    dateTo.value = "";
    language.value = "zh";
    citationStyle.value = "gbt7714";
    offlineDataset.value = false;
    useReferenceLibrary.value = false;
    verifyReferences.value = activePreset.value.defaults.verifyReferences;
    selectedDocuments.value = [];
    selectedSources.value = [];
    modelSource.value = "platform";
    selectedCredential.value = "";
    validationMessages.value = [];
    validationErrorFields.value = new Set();
}

/** 放弃草稿：清存储并回到默认表单（提示由这里负责） */
function discardDraft() {
    draftDirty.value = false;
    restoredFromDraft.value = false;
    clearTaskDraft(props.draftUserId);
    resetToDefaults();
    toastInfo("草稿已清除");
}

/** 提交成功后由父组件调用：清掉已提交的草稿，避免下次进来回填旧内容 */
function clearDraft() {
    draftDirty.value = false;
    restoredFromDraft.value = false;
    clearTaskDraft(props.draftUserId);
}

defineExpose({ clearDraft });

const canSubmit = computed(() => !props.pending);

onMounted(async () => {
    // 四个初始化接口彼此独立，改为并行加载并单独容错：
    // 避免引用样式列表慢或为空时阻塞/跳过引用库条数与自带模型列表（Issue #55）
    const [stylesResult, summaryResult, credentialsResult, usageResult, sourcesResult] =
        await Promise.allSettled([
            listCitationStyles(),
            getLibrarySummary(),
            listLLMCredentials(),
            getBillingUsage(),
            listDataSourceCredentials(),
        ]);

    if (
        usageResult.status === "fulfilled" &&
        typeof usageResult.value?.max_documents_per_task === "number" &&
        usageResult.value.max_documents_per_task >= DOC_MIN
    ) {
        platformDocLimit.value = Math.min(DOC_MAX, usageResult.value.max_documents_per_task);
        if (modelSource.value === "platform" && maxDocuments.value > platformDocLimit.value) {
            maxDocuments.value = platformDocLimit.value;
        }
    }

    if (
        stylesResult.status === "fulfilled" &&
        Array.isArray(stylesResult.value?.styles)
    ) {
        const data = stylesResult.value;
        const options: StyleOption[] = data.styles.map((style) => ({
            style_id: style.style_id,
            title: style.title,
            source: style.source,
        }));
        if (options.length) {
            styleOptions.value = options;
            // 草稿恢复的引用格式优先；只有它已不在当前选项里才退回默认项
            const restoredStyleStillValid =
                restoredFromDraft.value &&
                options.some((option) => option.style_id === citationStyle.value);
            if (!restoredStyleStillValid) {
                const preferred = options.find(
                    (option) => option.style_id === data.default_style_id,
                );
                citationStyle.value = preferred
                    ? preferred.style_id
                    : options[0].style_id;
            }
        }
    } else {
        // 列表不可用时静默降级为内置两项：创建任务不能被引用样式接口拖垮
        styleOptions.value = null;
    }

    // 引用库摘要：只为提示「将有 N 篇可用」，失败不影响表单（局部失败不连坐）
    if (
        summaryResult.status === "fulfilled" &&
        typeof summaryResult.value?.usable_as_source === "number"
    ) {
        libraryCount.value = summaryResult.value.usable_as_source;
        // 草稿里勾了「使用我的引用库」，但引用库其实是空的：禁用态却勾选
        // 会让提交必然失败（reference_library=true 空库），恢复时直接取消
        if (libraryCount.value === 0 && useReferenceLibrary.value) {
            useReferenceLibrary.value = false;
        }
    } else {
        libraryCount.value = 0;
    }

    // 自带模型列表：失败就只剩「平台模型」，同样不阻塞建任务
    if (
        credentialsResult.status === "fulfilled" &&
        Array.isArray(credentialsResult.value)
    ) {
        const list = credentialsResult.value;
        credentials.value = list;
        // 用户把某把凭据标成「默认」，建任务就该显式选中「自带模型」：
        // 否则会静默走平台模型，平台密钥缺失时任务直接卡在规划阶段
        //（实测「Missing credentials」）。草稿恢复时尊重用户当时的选择。
        if (!restoredFromDraft.value && modelSource.value === "platform") {
            const preferred = list.find(
                (item) => item.is_default && item.status === "active",
            );
            if (preferred) {
                modelSource.value = "byok";
                selectedCredential.value = preferred.credential_id;
            }
        }
        // 草稿里选的凭据已失效（被删除/停用）→ 回平台模型，避免提交必失败
        const activeIds = new Set(
            list.filter((item) => item.status === "active").map((item) => item.credential_id),
        );
        if (modelSource.value === "byok" && !activeIds.has(selectedCredential.value)) {
            modelSource.value = "platform";
            selectedCredential.value = "";
        }
    } else {
        credentials.value = [];
    }

    // 万方 BYOK 状态：接口失败一律按「没有 Key」处理（只是少一个可选源，不阻塞建任务）
    if (sourcesResult.status === "fulfilled") {
        const sourceRows = Array.isArray(sourcesResult.value)
            ? sourcesResult.value
            : [];
        const wanfang = sourceRows.find(
            (item) => item.provider === "wanfang",
        );
        hasWanfangKey.value = Boolean(wanfang?.configured);
    } else {
        hasWanfangKey.value = false;
    }
    // 草稿里勾过万方但 Key 已被删除/失效 → 剔除，避免提交一个必然被后端剔除的源
    if (!hasWanfangKey.value && selectedSources.value.includes("wanfang")) {
        selectedSources.value = selectedSources.value.filter((name) => name !== "wanfang");
    }
});

// 勾选「使用我的引用库」时，互斥清空离线演示语料，并同步可用条数（Issue #58 / UX-P1-03）
watch([useReferenceLibrary, libraryCount], ([enabled, count]) => {
    if (enabled) {
        offlineDataset.value = false;
    }
    // 引用库为空时不允许保持勾选（禁用态却勾选 → 提交 reference_library=true 必失败）
    if (enabled && count === 0) {
        useReferenceLibrary.value = false;
        return;
    }
    if (enabled && count !== null && count > 0 && Number(maxDocuments.value) > count) {
        maxDocuments.value = count;
    }
});

// 切回平台模型时，若篇数超过平台套餐单次上限则自动对齐
watch(modelSource, (source) => {
    if (source === "platform" && Number(maxDocuments.value) > platformDocLimit.value) {
        maxDocuments.value = platformDocLimit.value;
    }
});

/** 失焦时对文献数做安全夹紧，避免打断正在输入或拦截测试边界断言 */
function clampMaxDocuments() {
    const cap = effectiveDocMax.value;
    const raw = Number(maxDocuments.value);
    if (!Number.isFinite(raw)) {
        maxDocuments.value = Math.min(cap, activePreset.value.defaults.maxDocuments);
        toastInfo("文献数已恢复为预设值");
        return;
    }
    const clamped = Math.min(cap, Math.max(DOC_MIN, Math.round(raw)));
    if (clamped !== raw) {
        maxDocuments.value = clamped;
        toastInfo(`文献数已调整至 ${DOC_MIN}–${cap} 之间`);
    }
}

/** 高级设置摘要标签：展示当前非显性默认值 */
const advancedSummary = computed(() => {
    const langLabel = language.value === "zh" ? "中文" : "英文";
    const styleTitle =
        styleOptions.value?.find((item) => item.style_id === citationStyle.value)
            ?.title ??
        BUILT_IN_STYLES.find((item) => item.style_id === citationStyle.value)
            ?.title ??
        citationStyle.value;
    const verifyLabel = verifyReferences.value ? "已开启引用核验" : "未开启引用核验";
    const dateLabel =
        dateFrom.value || dateTo.value
            ? ` · ${dateFrom.value || "不限"} ~ ${dateTo.value || "至今"}`
            : "";
    return `${langLabel} · ${styleTitle} · ${verifyLabel}${dateLabel}`;
});

function isEnterKey(event: KeyboardEvent): boolean {
    return event.key === "Enter" || event.code === "Enter" || event.keyCode === 13;
}

/** 关键词输入框按键处理：按 Enter 只添加标签，绝不触发外层表单隐式提交（Issue #58）。 */
function onKeywordKeydown(event: KeyboardEvent) {
    if (!isEnterKey(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.isComposing) return;
    addKeyword();
}

/** 种子论文输入框按键处理：按 Enter 只添加种子，绝不触发外层表单隐式提交（Issue #58）。 */
function onSeedDoiKeydown(event: KeyboardEvent) {
    if (!isEnterKey(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.isComposing) return;
    addSeedDoi();
}

/** 表单级兜底：拦截来自关键词/种子输入框的 Enter，防止任何浏览器/输入法路径误触整表提交。 */
function onFormKeydown(event: KeyboardEvent) {
    if (!isEnterKey(event)) return;
    const target = event.target as HTMLElement | null;
    if (target?.id === "task-keyword" || target?.id === "task-seed-doi") {
        event.preventDefault();
    }
}

/** 将批量输入按常见逗号/分号拆分、去空白并保序去重 */
function splitBatchInput(raw: string): string[] {
    const seen = new Set<string>();
    const items: string[] = [];
    for (const part of raw.split(/[,，;；]+/)) {
        const trimmed = part.trim();
        if (trimmed && !seen.has(trimmed)) {
            seen.add(trimmed);
            items.push(trimmed);
        }
    }
    return items;
}

/** 合并已有条目列表与未失焦草稿拆分结果（保序去重） */
function mergeBatchItems(existing: string[], draft: string): string[] {
    const seen = new Set(existing);
    const next = [...existing];
    for (const part of splitBatchInput(draft)) {
        if (!seen.has(part)) {
            seen.add(part);
            next.push(part);
        }
    }
    return next;
}

function addKeyword() {
    const parts = splitBatchInput(keywordDraft.value);
    if (!parts.length) {
        keywordDraft.value = "";
        return;
    }
    const next = mergeBatchItems(keywords.value, keywordDraft.value);
    keywords.value = next;
    keywordDraft.value = "";
    if (next.length > KEYWORD_MAX) {
        toastWarning(`关键词最多 ${KEYWORD_MAX} 个，请删减超出部分`);
    } else {
        validationErrorFields.value.delete("keyword");
    }
}

function removeKeyword(keyword: string) {
    keywords.value = keywords.value.filter((item) => item !== keyword);
    if (keywords.value.length >= 1 && keywords.value.length <= KEYWORD_MAX) {
        validationErrorFields.value.delete("keyword");
    }
}

const suggestedKeywords = ref<KeywordSuggestion[]>([]);
const suggestingKeywords = ref(false);
const suggestError = ref<string | null>(null);

const canSuggestKeywords = computed(
    () => topic.value.trim().length >= TOPIC_MIN && !suggestingKeywords.value,
);

function isKeywordSelected(term: string): boolean {
    const key = term.trim().toLowerCase();
    return keywords.value.some((k) => k.trim().toLowerCase() === key);
}

/** 请求 AI 根据主题与语言拆分中英互补学术检索关键词（Issue #70）。 */
async function handleSuggestKeywords(refresh = false) {
    const trimmed = topic.value.trim();
    if (trimmed.length < TOPIC_MIN || suggestingKeywords.value) return;
    suggestingKeywords.value = true;
    suggestError.value = null;
    try {
        const rawExisting = refresh
            ? [...keywords.value, ...suggestedKeywords.value.map((s) => s.term)]
            : [...keywords.value];
        const dedupedExisting: string[] = [];
        const seen = new Set<string>();
        for (const item of rawExisting) {
            const clean = item.trim();
            const key = clean.toLowerCase();
            if (!clean || seen.has(key)) continue;
            seen.add(key);
            dedupedExisting.push(clean);
            if (dedupedExisting.length >= KEYWORD_MAX) break;
        }
        const res = await suggestKeywords({
            topic: trimmed,
            output_language: language.value,
            existing_keywords: dedupedExisting,
            max_keywords: 8,
        });
        suggestedKeywords.value = res.suggestions;
    } catch (err) {
        suggestError.value =
            (err as Error).message || "AI 拆分失败，请手动输入关键词";
    } finally {
        suggestingKeywords.value = false;
    }
}

function toggleSuggestedKeyword(term: string) {
    if (isKeywordSelected(term)) {
        keywords.value = keywords.value.filter(
            (k) => k.trim().toLowerCase() !== term.trim().toLowerCase(),
        );
        return;
    }
    if (keywords.value.length >= KEYWORD_MAX) {
        suggestError.value = `最多选择 ${KEYWORD_MAX} 个关键词`;
        return;
    }
    suggestError.value = null;
    keywords.value = [...keywords.value, term];
    validationErrorFields.value.delete("keyword");
}

function applyAllSuggestions() {
    const next = [...keywords.value];
    for (const item of suggestedKeywords.value) {
        if (next.length >= KEYWORD_MAX) break;
        if (!next.some((k) => k.trim().toLowerCase() === item.term.toLowerCase())) {
            next.push(item.term);
        }
    }
    suggestError.value = null;
    keywords.value = next;
    if (next.length >= 1 && next.length <= KEYWORD_MAX) {
        validationErrorFields.value.delete("keyword");
    }
}

function addSeedDoi() {
    const parts = splitBatchInput(seedDraft.value);
    if (!parts.length) {
        seedDraft.value = "";
        return;
    }
    const next = [...seedDois.value];
    let truncated = false;
    for (const part of parts) {
        if (next.includes(part)) continue;
        if (next.length >= SEED_MAX) {
            truncated = true;
            break;
        }
        next.push(part);
    }
    seedDois.value = next;
    seedDraft.value = "";
    if (truncated) {
        toastWarning(`种子论文最多 ${SEED_MAX} 篇，已截断超出部分`);
    }
}

function removeSeedDoi(seed: string) {
    seedDois.value = seedDois.value.filter((item) => item !== seed);
}

function toggleDocument(id: string, checked: boolean) {
    selectedDocuments.value = checked
        ? [...new Set([...selectedDocuments.value, id])]
        : selectedDocuments.value.filter((item) => item !== id);
}

const FIELD_SELECTORS: Record<string, string> = {
    topic: '[data-test="topic"]',
    draft: '[data-test="draft-text"]',
    keyword: '[data-test="keyword-input"]',
    seed: '[data-test="seed-doi-input"]',
    maxDocuments: '[data-test="max-documents"]',
    dateRange: '[data-test="date-to"]',
    model: '[data-test="model-select"]',
};

function validate(): { messages: string[]; fields: Set<string>; firstField: string | null } {
    const messages: string[] = [];
    const fields = new Set<string>();
    let firstField: string | null = null;
    const mark = (field: string, message: string) => {
        messages.push(message);
        fields.add(field);
        if (!firstField) firstField = field;
    };

    if (isVerifyCitations.value) {
        const trimmedDraft = draftText.value.trim();
        if (!trimmedDraft && selectedDocuments.value.length === 0) {
            mark("draft", "验证引用模式请粘贴待核验草稿，或至少勾选一篇知识库文档");
        }
        const trimmedTopic = topic.value.trim();
        if (
            trimmedTopic &&
            (trimmedTopic.length < TOPIC_MIN || trimmedTopic.length > TOPIC_MAX)
        ) {
            mark("topic", `主题需为 ${TOPIC_MIN}–${TOPIC_MAX} 个字符`);
        }
        if (selectedDocuments.value.length > DOC_MAX) {
            messages.push(`最多选择 ${DOC_MAX} 篇知识库文档`);
        }
        if (modelSource.value === "byok" && !selectedCredentialInfo.value) {
            mark("model", "已选择自带模型，请从列表中选出一把可用凭据");
        }
        return { messages, fields, firstField };
    }

    const trimmedTopic = topic.value.trim();
    if (trimmedTopic.length < TOPIC_MIN || trimmedTopic.length > TOPIC_MAX) {
        mark("topic", `主题需为 ${TOPIC_MIN}–${TOPIC_MAX} 个字符`);
    }
    const effectiveKeywords = mergeBatchItems(keywords.value, keywordDraft.value);
    const effectiveSeeds = mergeBatchItems(seedDois.value, seedDraft.value);
    if (isCitationSnowballing.value) {
        if (effectiveSeeds.length > SEED_MAX) {
            mark("seed", `种子论文最多 ${SEED_MAX} 篇`);
        }
        if (effectiveKeywords.length < 1 && effectiveSeeds.length < 1) {
            mark("keyword", "关键词或种子论文至少填写 1 项");
        }
    } else if (effectiveKeywords.length < 1) {
        mark("keyword", "关键词至少 1 个");
    }
    if (effectiveKeywords.length > KEYWORD_MAX) {
        mark("keyword", `关键词最多 ${KEYWORD_MAX} 个`);
    }
    const documents = Number(maxDocuments.value);
    const maxAllowed = effectiveDocMax.value;
    if (
        !Number.isFinite(documents) ||
        documents < DOC_MIN ||
        documents > maxAllowed
    ) {
        mark("maxDocuments", `文献数需在 ${DOC_MIN}–${maxAllowed} 之间`);
    }
    if (dateFrom.value && dateTo.value && dateFrom.value > dateTo.value) {
        mark("dateRange", "起始日期不能晚于结束日期");
    }
    if (selectedDocuments.value.length > DOC_MAX) {
        messages.push(`最多选择 ${DOC_MAX} 篇知识库文档`);
    }
    // 显式选了「自带模型」就必须选到具体一把：不让它静默回退平台（违背 BYOK 原则）
    if (modelSource.value === "byok" && !selectedCredentialInfo.value) {
        mark("model", "已选择自带模型，请从列表中选出一把可用凭据");
    }
    return { messages, fields, firstField };
}

function resolveVerifyTopic(): string {
    const manual = topic.value.trim();
    if (manual.length >= TOPIC_MIN) {
        return manual;
    }
    const firstLine = draftText.value
        .trim()
        .split(/\r?\n/)[0]
        ?.replace(/^#+\s*/, "")
        .trim();
    if (firstLine && firstLine.length >= TOPIC_MIN) {
        return firstLine.slice(0, 60);
    }
    return "草稿引用核验任务";
}

async function handleSubmit() {
    if (props.pending) return;
    const result = validate();
    validationMessages.value = result.messages;
    validationErrorFields.value = result.fields;
    if (result.messages.length > 0) {
        if (result.firstField === "dateRange" && !advancedOpen.value) {
            advancedOpen.value = true;
        }
        await nextTick();
        if (result.firstField) {
            const selector = FIELD_SELECTORS[result.firstField];
            const el = selector ? (document.querySelector(selector) as HTMLElement | null) : null;
            if (el) {
                el.scrollIntoView?.({
                    block: "center",
                    behavior: reduced.value ? "auto" : "smooth",
                });
                el.focus?.({ preventScroll: true });
            }
        }
        return;
    }
    if (isVerifyCitations.value) {
        const payload: TaskCreate = {
            topic: resolveVerifyTopic(),
            keywords: ["引用核验"],
            workflow_preset: "verify_citations",
            draft_text: draftText.value.trim() || null,
            document_ids: [...selectedDocuments.value],
            verify_references: true,
            ...(modelSource.value === "byok" && selectedCredentialInfo.value
                ? { llm_credential_id: selectedCredential.value }
                : {}),
        };
        emit("submit", payload);
        return;
    }

    const effectiveKeywords = mergeBatchItems(keywords.value, keywordDraft.value);
    const effectiveSeeds = mergeBatchItems(seedDois.value, seedDraft.value).slice(0, SEED_MAX);
    const payload: TaskCreate = {
        topic: topic.value.trim(),
        keywords:
            effectiveKeywords.length > 0
                ? effectiveKeywords
                : effectiveSeeds.slice(0, SEED_MAX),
        workflow_preset: workflowPreset.value,
        max_documents: Number(maxDocuments.value),
        date_from: dateFrom.value || null,
        date_to: dateTo.value || null,
        output_language: language.value,
        citation_style: citationStyle.value,
        offline_dataset: offlineDataset.value,
        reference_library: useReferenceLibrary.value,
        document_ids: [...selectedDocuments.value],
        verify_references: verifyReferences.value,
        // 引用库/离线语料模式不传检索源；不选 = 后端按语言与万方 Key 自动决定
        ...(!useReferenceLibrary.value &&
        !offlineDataset.value &&
        selectedSources.value.length > 0
            ? { literature_sources: [...selectedSources.value] }
            : {}),
        ...(isCitationSnowballing.value
            ? {
                  seed_dois: effectiveSeeds,
                  snowball_direction: snowballDirection.value,
              }
            : {}),
        ...(modelSource.value === "byok" && selectedCredentialInfo.value
            ? { llm_credential_id: selectedCredential.value }
            : {}),
    };
    emit("submit", payload);
}
</script>

<template>
    <Card
        as="form"
        class="min-w-0 max-w-3xl gap-4 p-4"
        @submit.prevent
        @keydown="onFormKeydown"
        @input.capture="markDraftDirty"
        @change.capture="markDraftDirty"
        @click.capture="markDraftDirty"
    >
        <!-- 草稿恢复提示：从导入文献页回来 / 刷新页面后自动回填，页面零解释容易像 bug -->
        <div
            v-if="restoredFromDraft"
            data-test="draft-restored"
            role="status"
            class="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
        >
            <span>已恢复上次未提交的草稿（离开本页或收起表单都不会丢失）</span>
            <button
                type="button"
                data-test="draft-discard"
                class="text-primary underline underline-offset-2 hover:opacity-85"
                @click="discardDraft"
            >
                清除草稿
            </button>
        </div>
        <div class="grid gap-1.5">
            <span class="text-sm font-medium leading-none">工作流预设</span>
            <div
                role="radiogroup"
                aria-label="工作流预设"
                class="grid gap-2 sm:grid-cols-2"
            >
                <button
                    v-for="preset in WORKFLOW_PRESETS"
                    :key="preset.id"
                    type="button"
                    role="radio"
                    :data-test="`preset-${preset.id}`"
                    :aria-checked="workflowPreset === preset.id"
                    :class="[
                        'flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors',
                        workflowPreset === preset.id
                            ? 'border-primary bg-primary/5 text-foreground shadow-xs'
                            : 'border-border bg-background text-muted-foreground hover:border-border-strong hover:text-foreground',
                    ]"
                    @click="selectPreset(preset.id)"
                >
                    <span class="text-sm font-medium text-foreground">
                        {{ preset.label }}
                    </span>
                    <span class="text-xs leading-relaxed text-muted-foreground">
                        {{ preset.description }}
                    </span>
                </button>
            </div>
        </div>

        <div class="grid gap-1.5">
            <Label for="task-topic">{{
                isVerifyCitations ? "任务标题（可选）" : "主题"
            }}</Label>
            <Input
                id="task-topic"
                v-model="topic"
                data-test="topic"
                type="text"
                :minlength="TOPIC_MIN"
                :maxlength="TOPIC_MAX"
                :placeholder="activePreset.topicPlaceholder"
                :class="validationErrorFields.has('topic') ? ERROR_RING_CLASS : ''"
            />
        </div>

        <div v-if="isVerifyCitations" class="grid gap-1.5">
            <Label for="task-draft-text">
                待核验草稿（含 [E#] 或 [1] 引用与文末参考文献列表）
            </Label>
            <textarea
                id="task-draft-text"
                v-model="draftText"
                data-test="draft-text"
                rows="8"
                maxlength="30000"
                placeholder="粘贴待核验段落及文末 ## 参考文献 列表，系统将直接核验每条引用的真实性、撤稿状态与论断支撑度…"
                :class="[
                    'flex min-h-[11rem] w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background',
                    validationErrorFields.has('draft') ? ERROR_RING_CLASS : '',
                ]"
            />
            <p class="text-xs text-muted-foreground">
                若同时勾选下方知识库文档，系统将基于原文分块做全文级论断核验；未勾选时按题录/摘要级核验。
            </p>
        </div>

        <template v-if="!isVerifyCitations">
        <div
            v-if="isCitationSnowballing"
            class="grid gap-3 rounded-lg border border-border bg-muted/20 p-3"
        >
            <div class="grid gap-1.5" @keydown.capture="onSeedDoiKeydown">
                <Label for="task-seed-doi">
                    种子论文 DOI / 标题（选填，支持逗号或分号批量拆分，最多 {{ SEED_MAX }} 篇）
                </Label>
                <div class="flex items-center gap-2">
                    <Input
                        id="task-seed-doi"
                        v-model="seedDraft"
                        data-test="seed-doi-input"
                        type="text"
                        placeholder="例如：10.1145/3442381.3450000（支持逗号批量粘贴）"
                        :class="['flex-1', validationErrorFields.has('seed') ? ERROR_RING_CLASS : '']"
                        @blur="addSeedDoi"
                    />
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        data-test="seed-doi-add-btn"
                        class="shrink-0"
                        @click="addSeedDoi"
                    >
                        <Plus :size="14" aria-hidden="true" />
                        <span>添加</span>
                    </Button>
                </div>
                <ul v-if="seedDois.length" class="flex flex-wrap gap-1.5 pt-1">
                    <li
                        v-for="seed in seedDois"
                        :key="seed"
                        class="inline-flex items-center gap-1 rounded-md bg-secondary px-2 py-0.5 text-xs text-secondary-foreground"
                    >
                        <span>{{ seed }}</span>
                        <button
                            type="button"
                            class="rounded-sm text-muted-foreground transition-colors hover:text-foreground"
                            :aria-label="`移除种子 ${seed}`"
                            @click="removeSeedDoi(seed)"
                        >
                            <X :size="12" aria-hidden="true" />
                        </button>
                    </li>
                </ul>
            </div>

            <div class="grid gap-1.5">
                <span class="text-sm font-medium leading-none">扩展方向</span>
                <div
                    class="inline-flex w-fit flex-wrap gap-1 rounded-lg bg-muted p-1"
                    role="group"
                    aria-label="引文滚雪球扩展方向"
                >
                    <button
                        v-for="option in [
                            { value: 'both', label: '双向扩展' },
                            { value: 'backward', label: '查参考文献（向后）' },
                            { value: 'forward', label: '查施引文献（向前）' },
                        ] as const"
                        :key="option.value"
                        type="button"
                        :data-test="`snowball-dir-${option.value}`"
                        :aria-pressed="snowballDirection === option.value"
                        :class="[
                            'rounded-md px-3 py-1 text-xs font-medium transition-colors',
                            snowballDirection === option.value
                                ? 'bg-background text-foreground shadow-xs'
                                : 'text-muted-foreground hover:text-foreground',
                        ]"
                        @click="snowballDirection = option.value"
                    >
                        {{ option.label }}
                    </button>
                </div>
            </div>
        </div>

        <div class="grid gap-1.5">
            <div class="flex items-center justify-between gap-2">
                <Label for="task-keyword">关键词（回车或点击添加，支持逗号/分号批量拆分，1–10 个）</Label>
                <button
                    type="button"
                    data-test="suggest-keywords-btn"
                    :disabled="!canSuggestKeywords"
                    class="inline-flex shrink-0 items-center gap-1 rounded-md border border-primary/30 bg-primary/5 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
                    @click="handleSuggestKeywords(false)"
                >
                    <Loader2
                        v-if="suggestingKeywords"
                        class="size-3.5 animate-spin motion-reduce:animate-none"
                        aria-hidden="true"
                    />
                    <Sparkles v-else class="size-3.5" aria-hidden="true" />
                    <span>{{ suggestingKeywords ? "拆分中…" : "AI 拆分关键词" }}</span>
                </button>
            </div>
            <div class="flex items-center gap-2" @keydown.capture="onKeywordKeydown">
                <Input
                    id="task-keyword"
                    v-model="keywordDraft"
                    data-test="keyword-input"
                    type="text"
                    placeholder="输入关键词后按回车或点添加"
                    :class="['flex-1', validationErrorFields.has('keyword') ? ERROR_RING_CLASS : '']"
                    @blur="addKeyword"
                />
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    data-test="keyword-add-btn"
                    class="shrink-0"
                    @click="addKeyword"
                >
                    <Plus :size="14" aria-hidden="true" />
                    <span>添加</span>
                </Button>
            </div>
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
            <!-- AI 候选关键词芯片区（点击一键选中/取消，Issue #70） -->
            <div
                v-if="suggestedKeywords.length"
                data-test="suggested-keywords-panel"
                class="rounded-lg border border-border/70 bg-muted/30 p-2.5"
            >
                <div class="mb-2 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                    <span>点击候选词直接填入（含中英检索词，提升跨库召回）</span>
                    <div class="flex items-center gap-2">
                        <button
                            type="button"
                            data-test="apply-all-suggestions"
                            class="font-medium text-primary hover:underline"
                            @click="applyAllSuggestions"
                        >
                            全部填入
                        </button>
                        <button
                            type="button"
                            data-test="refresh-suggestions"
                            class="hover:text-foreground disabled:opacity-50"
                            :disabled="suggestingKeywords"
                            @click="handleSuggestKeywords(true)"
                        >
                            换一批
                        </button>
                    </div>
                </div>
                <div class="flex flex-wrap gap-1.5">
                    <button
                        v-for="item in suggestedKeywords"
                        :key="item.term"
                        type="button"
                        data-test="suggested-keyword-chip"
                        :aria-pressed="isKeywordSelected(item.term)"
                        class="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs transition-colors"
                        :class="
                            isKeywordSelected(item.term)
                                ? 'border-primary bg-primary/10 font-medium text-primary'
                                : 'border-border bg-background text-foreground hover:bg-muted'
                        "
                        @click="toggleSuggestedKeyword(item.term)"
                    >
                        <Check v-if="isKeywordSelected(item.term)" class="size-3" aria-hidden="true" />
                        <span>{{ item.term }}</span>
                        <span class="text-[10px] opacity-60">{{ item.lang === "en" ? "EN" : "中" }}</span>
                    </button>
                </div>
            </div>
            <p v-if="suggestError" data-test="suggest-error" class="text-xs text-destructive">
                {{ suggestError }}
            </p>
        </div>

        <!-- 一级可见区：文献数量 + 来源 -->
        <div class="grid gap-3 rounded-lg border border-border/70 bg-muted/15 p-3">
            <div class="flex flex-wrap items-end gap-3">
                <div class="grid gap-1.5">
                    <Label for="task-documents">文献数（1–{{ effectiveDocMax }}）</Label>
                    <Input
                        id="task-documents"
                        v-model="maxDocuments"
                        data-test="max-documents"
                        type="number"
                        min="1"
                        :max="effectiveDocMax"
                        :class="['w-28', validationErrorFields.has('maxDocuments') ? ERROR_RING_CLASS : '']"
                        @blur="clampMaxDocuments"
                    />
                </div>
                <div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="常用文献数量预设">
                    <Button
                        v-for="presetCount in maxDocPresets"
                        :key="presetCount"
                        type="button"
                        size="sm"
                        :variant="Number(maxDocuments) === presetCount ? 'secondary' : 'outline'"
                        :data-test="`max-docs-chip-${presetCount}`"
                        @click="maxDocuments = presetCount"
                    >
                        {{ presetCount }} 篇
                    </Button>
                </div>
            </div>
            <p
                v-if="effectiveDocMax < DOC_MAX"
                data-test="platform-doc-limit-hint"
                class="text-xs text-muted-foreground"
            >
                当前套餐使用平台模型单次最多支持 {{ effectiveDocMax }} 篇文献；切换「自带模型」或升级套餐可设至 {{ DOC_MAX }} 篇。
            </p>

            <div class="grid gap-2">
                <label
                    class="flex items-start gap-2 text-sm"
                    data-test="reference-library-label"
                >
                    <input
                        v-model="useReferenceLibrary"
                        data-test="reference-library"
                        type="checkbox"
                        :disabled="libraryCount === 0"
                        class="mt-0.5 size-4 rounded border-input accent-primary disabled:cursor-not-allowed disabled:opacity-50"
                    />
                    <span>
                        使用我的引用库（{{
                            libraryCount === null
                                ? "加载中…"
                                : `${libraryCount} 篇`
                        }}，不访问外部检索源）
                        <span
                            v-if="libraryCount === 0"
                            class="text-muted-foreground"
                        >
                            —— 引用库为空，
                            <a
                                href="/references/import"
                                class="text-primary underline underline-offset-2 hover:opacity-85"
                                @click.prevent="goToImport()"
                            >
                                前往导入文献 →
                            </a>
                            <span data-test="import-draft-hint" class="text-muted-foreground">
                                （导入后返回本页，草稿会自动恢复）
                            </span>
                        </span>
                    </span>
                </label>

                <label class="flex items-center gap-2 text-sm">
                    <input
                        v-model="offlineDataset"
                        data-test="offline"
                        type="checkbox"
                        :disabled="useReferenceLibrary"
                        class="size-4 rounded border-input accent-primary"
                    />
                    <span>使用离线演示语料（冻结 10 篇，不访问 arXiv）</span>
                </label>
            </div>

            <!-- 检索文献源（Issue #67）：万方中文源走用户自带 Key（BYOK） -->
            <div
                v-if="!useReferenceLibrary && !offlineDataset"
                class="grid gap-1.5 border-t border-border/70 pt-3"
            >
                <span class="text-sm font-medium leading-none">检索文献源（选填）</span>
                <div class="flex flex-wrap gap-1.5" role="group" aria-label="检索文献源">
                    <button
                        v-for="option in SOURCE_OPTIONS"
                        :key="option.value"
                        type="button"
                        :data-test="`source-option-${option.value}`"
                        :disabled="Boolean(option.requiresWanfangKey) && !hasWanfangKey"
                        :aria-pressed="selectedSources.includes(option.value)"
                        :title="option.hint"
                        :class="[
                            'rounded-md border px-2.5 py-1 text-xs transition-colors',
                            selectedSources.includes(option.value)
                                ? 'border-primary bg-primary/10 text-foreground'
                                : 'border-border bg-background text-muted-foreground hover:text-foreground',
                            Boolean(option.requiresWanfangKey) && !hasWanfangKey
                                ? 'cursor-not-allowed opacity-50'
                                : '',
                        ]"
                        @click="toggleSource(option.value)"
                    >
                        {{ option.label }}
                    </button>
                </div>
                <p
                    v-if="hasWanfangKey"
                    data-test="wanfang-key-hint"
                    class="text-xs text-muted-foreground"
                >
                    已检测到你的万方 Key，中文综述将自动启用万方 + OpenAlex。
                </p>
                <p
                    v-else
                    data-test="wanfang-key-missing"
                    class="text-xs leading-relaxed text-muted-foreground"
                >
                    万方中文源需要自带 App Key：
                    <a
                        href="/settings/models#literature-sources"
                        class="text-primary underline underline-offset-2 hover:opacity-85"
                        @click.prevent="goTo('/settings/models#literature-sources')"
                    >
                        去设置填写 Key
                    </a>
                    <span class="mx-1">·</span>
                    <a
                        href="https://www.wfdata.com/skill-api?tab=mine"
                        target="_blank"
                        rel="noopener noreferrer"
                        data-test="wanfang-get-key-link"
                        class="text-primary underline underline-offset-2 hover:opacity-85"
                    >
                        获取万方 Key ↗
                    </a>
                </p>
                <p class="text-xs text-muted-foreground">
                    不选 = 自动：中文主题且已配置万方 Key 时用万方 + OpenAlex，否则 arXiv + OpenAlex。
                </p>
            </div>
        </div>
        </template>

        <div class="grid gap-1.5">
            <span class="text-sm font-medium leading-none">使用的模型</span>
            <!-- 显式二选一：平台模型 / 自带模型。没有可用自带模型时锁死在平台。 -->
            <div
                class="inline-flex w-fit gap-1 rounded-lg bg-muted p-1"
                role="group"
            >
                <button
                    type="button"
                    data-test="model-source-platform"
                    :aria-pressed="modelSource === 'platform'"
                    :class="[
                        'rounded-md px-3 py-1 text-sm font-medium transition-colors',
                        modelSource === 'platform'
                            ? 'bg-background text-foreground shadow-xs'
                            : 'text-muted-foreground hover:text-foreground',
                    ]"
                    @click="modelSource = 'platform'"
                >
                    平台模型
                </button>
                <button
                    type="button"
                    data-test="model-source-byok"
                    :aria-pressed="modelSource === 'byok'"
                    :disabled="usableCredentials.length === 0"
                    :title="
                        usableCredentials.length === 0
                            ? '先到「我的模型」添加一把 API Key'
                            : undefined
                    "
                    :class="[
                        'rounded-md px-3 py-1 text-sm font-medium transition-colors',
                        modelSource === 'byok'
                            ? 'bg-background text-foreground shadow-xs'
                            : 'text-muted-foreground hover:text-foreground',
                        usableCredentials.length === 0
                            ? 'cursor-not-allowed opacity-50 hover:text-muted-foreground'
                            : '',
                    ]"
                    @click="
                        usableCredentials.length > 0 && (modelSource = 'byok')
                    "
                >
                    自带模型
                </button>
            </div>

            <div v-if="modelSource === 'byok'" class="grid gap-1.5">
                <select
                    id="task-model"
                    v-model="selectedCredential"
                    data-test="model-select"
                    :class="[
                        'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background',
                        validationErrorFields.has('model') ? ERROR_RING_CLASS : '',
                    ]"
                >
                    <option value="" disabled>请选择一把自带模型…</option>
                    <option
                        v-for="item in usableCredentials"
                        :key="item.credential_id"
                        :value="item.credential_id"
                    >
                        {{ item.name }}（{{ item.provider_label }} ·
                        {{ item.model }}）
                    </option>
                </select>
                <p class="text-xs text-muted-foreground">
                    规划与写作都会走这把凭据；key
                    失效会直接失败并说明原因，不会自动改用平台模型。
                </p>
            </div>
            <p v-else class="text-xs text-muted-foreground">
                任务将使用平台配置的模型。要用自己的 API Key，请先
                <a
                    v-if="usableCredentials.length === 0"
                    href="/settings/models"
                    class="text-primary underline underline-offset-2 hover:opacity-85"
                    @click.prevent="goTo('/settings/models')"
                >
                    前往我的模型添加 →
                </a>
                <span v-else>切到「自带模型」。</span>
            </p>
        </div>

        <fieldset
            v-if="props.documents.length"
            data-test="task-documents-fieldset"
            class="grid min-w-0 gap-2 rounded-lg border border-border p-3"
        >
            <legend class="px-1 text-sm font-medium">
                指定知识库文档（可选）
            </legend>
            <label
                v-for="document in props.documents"
                :key="document.id"
                class="flex min-w-0 items-start gap-2 text-sm"
            >
                <input
                    type="checkbox"
                    :data-test="`doc-${document.id}`"
                    class="mt-0.5 size-4 shrink-0 rounded border-input accent-primary"
                    @change="
                        toggleDocument(
                            document.id,
                            ($event.target as HTMLInputElement).checked,
                        )
                    "
                />
                <span class="min-w-0 flex-1 break-all leading-snug">{{ document.title }}</span>
            </label>
        </fieldset>

        <!-- 二级折叠区：默认收起但 keep-mounted 常驻 DOM，保证单测与无障碍查询不丢失 -->
        <div
            v-if="!isVerifyCitations"
            class="rounded-lg border border-border bg-card/60"
        >
            <button
                type="button"
                data-test="toggle-advanced-options"
                :aria-expanded="advancedOpen"
                class="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted/40"
                @click="advancedOpen = !advancedOpen"
            >
                <span class="font-medium text-foreground">
                    高级设置：日期、语言、格式与核验（可选）
                </span>
                <span class="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
                    <span
                        :class="
                            verifyReferences || dateFrom || dateTo
                                ? 'font-medium text-primary'
                                : ''
                        "
                    >
                        {{ advancedSummary }}
                    </span>
                    <ChevronDown
                        class="size-4 shrink-0 transition-transform duration-150 motion-reduce:transition-none"
                        :class="advancedOpen ? 'rotate-180' : 'rotate-0'"
                        aria-hidden="true"
                    />
                </span>
            </button>

            <AnimatedCollapse :open="advancedOpen" keep-mounted>
                <div class="grid gap-3 border-t border-border px-3 py-3">
                    <div class="grid gap-3 sm:grid-cols-2">
                        <div class="grid gap-1.5">
                            <Label for="task-date-from">起始日期</Label>
                            <Input
                                id="task-date-from"
                                v-model="dateFrom"
                                data-test="date-from"
                                type="date"
                            />
                        </div>
                        <div class="grid gap-1.5">
                            <Label for="task-date-to">结束日期</Label>
                            <Input
                                id="task-date-to"
                                v-model="dateTo"
                                data-test="date-to"
                                type="date"
                                :class="validationErrorFields.has('dateRange') ? ERROR_RING_CLASS : ''"
                            />
                        </div>
                    </div>

                    <div class="grid gap-3 sm:grid-cols-2">
                        <div class="grid gap-1.5">
                            <span class="text-sm font-medium leading-none">输出语言</span>
                            <div
                                class="inline-flex w-fit gap-1 rounded-lg bg-muted p-1"
                                role="group"
                            >
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
                            <Label v-if="styleOptions" for="task-citation-style"
                                >引用格式</Label
                            >
                            <span v-else class="text-sm font-medium leading-none"
                                >引用格式</span
                            >
                            <!-- 列表可用：动态下拉（预置 + 自定义）；不可用：内置两项降级 -->
                            <select
                                v-if="styleOptions"
                                id="task-citation-style"
                                v-model="citationStyle"
                                data-test="style-select"
                                class="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
                            >
                                <option
                                    v-for="style in styleOptions"
                                    :key="style.style_id"
                                    :value="style.style_id"
                                >
                                    {{ style.title
                                    }}{{ style.source === "custom" ? "（自定义）" : "" }}
                                </option>
                            </select>
                            <div
                                v-else
                                class="inline-flex w-fit gap-1 rounded-lg bg-muted p-1"
                                role="group"
                            >
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

                    <label
                        class="flex items-start gap-2 text-sm"
                        data-test="verify-references-label"
                    >
                        <input
                            v-model="verifyReferences"
                            data-test="verify-references"
                            type="checkbox"
                            class="mt-0.5 size-4 rounded border-input accent-primary"
                        />
                        <span
                            >引用核验：与 CrossRef/OpenAlex
                            比对文献元数据，可能增加任务耗时</span
                        >
                    </label>
                </div>
            </AnimatedCollapse>
        </div>

        <ErrorBanner
            v-if="validationMessages.length"
            :message="validationMessages.join('；')"
            testid="validation-error"
            @dismiss="validationMessages = []"
        />
        <ErrorBanner
            :message="props.error"
            testid="form-error"
            @dismiss="emit('dismissError')"
        />

        <div class="flex gap-2">
            <!-- 使用 type="button" + @click 显式提交，彻底禁用多字段表单的浏览器原生回车隐式提交（Issue #58） -->
            <Button
                data-test="submit"
                type="button"
                :disabled="!canSubmit"
                @click="handleSubmit"
            >
                <span>{{ props.pending ? "创建中…" : "创建任务" }}</span>
            </Button>
        </div>
    </Card>
</template>
