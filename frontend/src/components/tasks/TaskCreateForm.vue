<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { X } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { listCitationStyles } from "@/api/citation-styles";
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
    }>(),
    { pending: false, error: null, documents: () => [] },
);

const emit = defineEmits<{ submit: [payload: TaskCreate] }>();

const TOPIC_MIN = 5;
const TOPIC_MAX = 200;
const KEYWORD_MAX = 10;
const DOC_MIN = 1;
const DOC_MAX = 50;

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

const canSubmit = computed(() => !props.pending);

onMounted(async () => {
    // 三个初始化接口彼此独立，改为并行加载并单独容错：
    // 避免引用样式列表慢或为空时阻塞/跳过引用库条数与自带模型列表（Issue #55）
    const [stylesResult, summaryResult, credentialsResult] =
        await Promise.allSettled([
            listCitationStyles(),
            getLibrarySummary(),
            listLLMCredentials(),
        ]);

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
            const preferred = options.find(
                (option) => option.style_id === data.default_style_id,
            );
            citationStyle.value = preferred
                ? preferred.style_id
                : options[0].style_id;
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
        //（实测「Missing credentials」）。
        if (modelSource.value === "platform") {
            const preferred = list.find(
                (item) => item.is_default && item.status === "active",
            );
            if (preferred) {
                modelSource.value = "byok";
                selectedCredential.value = preferred.credential_id;
            }
        }
    } else {
        credentials.value = [];
    }
});

// 勾选「使用我的引用库」时，若当前文献数大于引用库可用条数，自动同步到可用条数（Issue #58）
watch([useReferenceLibrary, libraryCount], ([enabled, count]) => {
    if (enabled && count !== null && count > 0 && Number(maxDocuments.value) > count) {
        maxDocuments.value = count;
    }
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

function addSeedDoi() {
    const value = seedDraft.value.trim().replace(/,$/, "");
    if (!value) {
        return;
    }
    if (seedDois.value.length < SEED_MAX && !seedDois.value.includes(value)) {
        seedDois.value = [...seedDois.value, value];
    }
    seedDraft.value = "";
}

function removeSeedDoi(seed: string) {
    seedDois.value = seedDois.value.filter((item) => item !== seed);
}

function toggleDocument(id: string, checked: boolean) {
    selectedDocuments.value = checked
        ? [...new Set([...selectedDocuments.value, id])]
        : selectedDocuments.value.filter((item) => item !== id);
}

function validate(): string[] {
    const messages: string[] = [];
    if (isVerifyCitations.value) {
        const trimmedDraft = draftText.value.trim();
        if (!trimmedDraft && selectedDocuments.value.length === 0) {
            messages.push("验证引用模式请粘贴待核验草稿，或至少勾选一篇知识库文档");
        }
        const trimmedTopic = topic.value.trim();
        if (
            trimmedTopic &&
            (trimmedTopic.length < TOPIC_MIN || trimmedTopic.length > TOPIC_MAX)
        ) {
            messages.push(`主题需为 ${TOPIC_MIN}–${TOPIC_MAX} 个字符`);
        }
        if (selectedDocuments.value.length > DOC_MAX) {
            messages.push(`最多选择 ${DOC_MAX} 篇知识库文档`);
        }
        if (modelSource.value === "byok" && !selectedCredentialInfo.value) {
            messages.push("已选择自带模型，请从列表中选出一把可用凭据");
        }
        return messages;
    }

    const trimmedTopic = topic.value.trim();
    if (trimmedTopic.length < TOPIC_MIN || trimmedTopic.length > TOPIC_MAX) {
        messages.push(`主题需为 ${TOPIC_MIN}–${TOPIC_MAX} 个字符`);
    }
    const pendingKeyword = keywordDraft.value.trim();
    const effectiveKeywords = pendingKeyword
        ? [...new Set([...keywords.value, pendingKeyword])]
        : keywords.value;
    const pendingSeed = seedDraft.value.trim();
    const effectiveSeeds = pendingSeed
        ? [...new Set([...seedDois.value, pendingSeed])]
        : seedDois.value;
    if (isCitationSnowballing.value) {
        if (effectiveSeeds.length > SEED_MAX) {
            messages.push(`种子论文最多 ${SEED_MAX} 篇`);
        }
        if (effectiveKeywords.length < 1 && effectiveSeeds.length < 1) {
            messages.push("关键词或种子论文至少填写 1 项");
        }
    } else if (effectiveKeywords.length < 1) {
        messages.push("关键词至少 1 个");
    }
    if (effectiveKeywords.length > KEYWORD_MAX) {
        messages.push(`关键词最多 ${KEYWORD_MAX} 个`);
    }
    const documents = Number(maxDocuments.value);
    if (
        !Number.isFinite(documents) ||
        documents < DOC_MIN ||
        documents > DOC_MAX
    ) {
        messages.push(`文献数需在 ${DOC_MIN}–${DOC_MAX} 之间`);
    }
    if (dateFrom.value && dateTo.value && dateFrom.value > dateTo.value) {
        messages.push("起始日期不能晚于结束日期");
    }
    if (selectedDocuments.value.length > DOC_MAX) {
        messages.push(`最多选择 ${DOC_MAX} 篇知识库文档`);
    }
    // 显式选了「自带模型」就必须选到具体一把：不让它静默回退平台（违背 BYOK 原则）
    if (modelSource.value === "byok" && !selectedCredentialInfo.value) {
        messages.push("已选择自带模型，请从列表中选出一把可用凭据");
    }
    return messages;
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

function handleSubmit() {
    const messages = validate();
    validationMessages.value = messages;
    if (messages.length > 0) {
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

    const pendingKeyword = keywordDraft.value.trim();
    const effectiveKeywords = pendingKeyword
        ? [...new Set([...keywords.value, pendingKeyword])]
        : [...keywords.value];
    const pendingSeed = seedDraft.value.trim();
    const effectiveSeeds = pendingSeed
        ? [...new Set([...seedDois.value, pendingSeed])].slice(0, SEED_MAX)
        : [...seedDois.value];
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
        class="max-w-3xl gap-4 p-4"
        @submit.prevent="handleSubmit"
        @keydown="onFormKeydown"
    >
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
                class="flex min-h-[11rem] w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
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
                    种子论文 DOI / 标题（选填，回车添加，最多 {{ SEED_MAX }} 篇）
                </Label>
                <Input
                    id="task-seed-doi"
                    v-model="seedDraft"
                    data-test="seed-doi-input"
                    type="text"
                    placeholder="例如：10.1145/3442381.3450000 或经典论文题名（留空将自动检索 Top 3 种子）"
                    @blur="addSeedDoi"
                />
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

        <div class="grid gap-1.5" @keydown.capture="onKeywordKeydown">
            <Label for="task-keyword">关键词（回车添加，1–10 个）</Label>
            <Input
                id="task-keyword"
                v-model="keywordDraft"
                data-test="keyword-input"
                type="text"
                placeholder="输入关键词后按回车"
                @blur="addKeyword"
            />
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
        </div>

        <div class="grid gap-3 sm:grid-cols-3">
            <div class="grid gap-1.5">
                <Label for="task-documents">文献数（1–50）</Label>
                <Input
                    id="task-documents"
                    v-model="maxDocuments"
                    data-test="max-documents"
                    type="number"
                    min="1"
                    max="50"
                />
            </div>
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
                    class="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
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
                任务将使用平台配置的模型。要用自己的 API Key，切到「自带模型」。
            </p>
        </div>

        <div v-if="!isVerifyCitations" class="grid gap-2">
            <label
                class="flex items-start gap-2 text-sm"
                data-test="reference-library-label"
            >
                <input
                    v-model="useReferenceLibrary"
                    data-test="reference-library"
                    type="checkbox"
                    class="mt-0.5 size-4 rounded border-input accent-primary"
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
                        —— 引用库为空，先去「导入文献」保存几条再启用
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

        <fieldset
            v-if="props.documents.length"
            class="grid gap-2 rounded-lg border border-border p-3"
        >
            <legend class="px-1 text-sm font-medium">
                指定知识库文档（可选）
            </legend>
            <label
                v-for="document in props.documents"
                :key="document.id"
                class="flex items-center gap-2 text-sm"
            >
                <input
                    type="checkbox"
                    :data-test="`doc-${document.id}`"
                    class="size-4 rounded border-input accent-primary"
                    @change="
                        toggleDocument(
                            document.id,
                            ($event.target as HTMLInputElement).checked,
                        )
                    "
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
