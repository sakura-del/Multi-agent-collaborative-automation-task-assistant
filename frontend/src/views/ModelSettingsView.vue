<script setup lang="ts">
import BeContextMeter from "@/components/motion/BeContextMeter.vue";
import {
  ROUTABLE_NODE_TYPES,
  listNodeRouting,
  updateNodeRouting,
} from "@/api/llm-routing";
import { computed, onMounted, ref } from "vue";
import { Cpu, FileJson, Pencil, Plus, Star, Trash2, Wifi, X } from "lucide-vue-next";

import AnimatedCollapse from "@/components/common/AnimatedCollapse.vue";
import ConfirmBar from "@/components/common/ConfirmBar.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDelayedLoading } from "@/composables/useDelayedLoading";
import { toastSuccess } from "@/lib/toast";
import {
    createLLMCredential,
    deleteLLMCredential,
    listLLMCredentials,
    listLLMProviders,
    probeGeneration,
    probeLLMCredential,
    testLLMCredential,
    updateLLMCredential,
} from "@/api/llm-credentials";
import type {
    LLMCredentialInfo,
    LLMProbeResult,
    LLMProviderKey,
    LLMProviderOption,
} from "@/api/types";

/**
 * 我的模型（Issue #15 BYOK）。
 *
 * ## 三条必须让用户理解的事
 *
 * 1. **key 只进不出**：保存后只显示 `sk-…abcd`，平台也解不出原始值给前端；
 *    要换只能重新填一把。
 * 2. **失败不会自动改用平台模型**：用户 key 401 时任务直接失败并说明原因，
 *    不会在用户不知情时花平台的钱。这条必须写在界面上，否则用户会以为是 bug。
 * 3. **删除凭据不影响历史任务**：任务里存的是模型快照，仍能看到当时用的模型。
 *
 * ⚠️ 测试契约：`models-empty` / `models-error` / `models-list` /
 * `model-row-<id>` / `model-create-open` / `model-form` / `model-name` /
 * `model-provider` / `model-id` / `model-key` / `model-base-url` /
 * `model-submit` / `model-cancel` / `model-edit-<id>` / `model-set-default-<id>` /
 * `model-delete-<id>` / `model-confirm-delete-<id>` /
 * `model-probe` / `model-probe-result` / `model-probe-error` / `model-test-<id>` /
 * `model-search` / `model-count` / `models-no-match`。
 */
const credentials = ref<LLMCredentialInfo[]>([]);

/** 默认把全部可路由节点初始化为空串（跟随任务勾选），避免加载态下拉框显示空白（Issue #56）。 */
function emptyRouteDraft(): Record<string, string> {
  return Object.fromEntries(ROUTABLE_NODE_TYPES.map((item) => [item.value, ""]));
}

const routeDraft = ref<Record<string, string>>(emptyRouteDraft());
const routeSaving = ref(false);
const routeSuccess = ref<string | null>(null);
const routeError = ref<string | null>(null);

async function loadRouting() {
  try {
    const routes = await listNodeRouting();
    const draft = emptyRouteDraft();
    for (const route of routes) {
      draft[route.node_type] = route.credential_id;
    }
    routeDraft.value = draft;
  } catch {
    // 路由加载失败不阻塞凭据页：静默保持「全部跟随任务勾选」
    routeDraft.value = emptyRouteDraft();
  }
}

const routingMeterCategories = computed(() =>
    ROUTABLE_NODE_TYPES.map((node) => {
        const credentialId = routeDraft.value[node.value] || "";
        const credential = credentials.value.find(
            (item) => item.credential_id === credentialId,
        );
        return {
            id: node.value,
            label: node.label,
            // 展示模型名而不是凭据 ID（ID 只用于提交）
            model: credential ? `${credential.name} · ${credential.model}` : "",
        };
    }),
);

async function saveRouting() {
  routeSaving.value = true;
  routeSuccess.value = null;
  routeError.value = null;
  try {
    const routes = Object.entries(routeDraft.value)
      .filter(([, credentialId]) => credentialId !== "")
      .map(([nodeType, credentialId]) => ({ node_type: nodeType, credential_id: credentialId }));
    const saved = await updateNodeRouting(routes);
    const msg = saved.length
      ? `已保存 ${saved.length} 条节点路由；未配置的节点继续使用任务勾选的模型。`
      : "已清空全部节点路由；所有节点继续使用任务勾选的模型。";
    routeSuccess.value = msg;
    toastSuccess(msg);
  } catch (err) {
    routeError.value = (err as Error).message || "节点路由保存失败";
  } finally {
    routeSaving.value = false;
  }
}
const providers = ref<LLMProviderOption[]>([]);
const loading = ref(true);
const showSkeleton = useDelayedLoading(loading);
const error = ref<string | null>(null);

const showForm = ref(false);
const editingId = ref<string | null>(null);
const submitting = ref(false);

const formName = ref("");
const formProvider = ref<LLMProviderKey>("deepseek");
const formModel = ref("");
const formApiKey = ref("");
const formBaseUrl = ref("");
const formDefault = ref(false);
/** 容量声明：保持字符串原样，提交时再解析（清空要能区分「未填」与 0）。 */
const formContextWindow = ref("");
const formMaxOutput = ref("");
// 能力声明（参照 DeepSeek Harness 模型目录）：输入类型 + 推理等级档位
const formSupportsText = ref(true);
const formSupportsImage = ref(false);
const formTiers = ref<{ label: string; value: string }[]>([]);
// 失败回退平台（Issue #28）：默认关——静默烧平台钱必须由用户显式选择
const formAllowFallback = ref(false);
// json_mode 只由「生成能力探测」写入，表单里不可勾选；编辑时原样带回，避免整体替换把它抹掉
const formJsonMode = ref(false);

function addTier() {
    if (formTiers.value.length >= 4) return;
    formTiers.value.push({ label: "", value: "" });
}

const busyId = ref<string | null>(null);
const confirmDeleteId = ref<string | null>(null);
/** 列表内搜索（凭据上限 20，客户端过滤足够；不需要服务端分页）。 */
const search = ref("");

/**
 * 测试连接状态（Issue #15 P1）。
 *
 * 刻意分成表单态与列表态两套：表单里测的是「还没保存的配置」，
 * 列表里测的是「库里的凭据」。混用一个 ref 会让结果串台。
 */
const probeBusy = ref(false);
const probeResult = ref<LLMProbeResult | null>(null);
const probeError = ref<string | null>(null);
const modelOptions = ref<string[]>([]);
/** 下拉里选中「自定义」后切回输入框：厂商 /models 不一定列全所有可用模型。 */
const customModel = ref(false);
const testingId = ref<string | null>(null);

/**
 * 模型预设（2026-11-17）：上下文窗口 / 最大输出是**模型本身的属性**，
 * 放在设置模型的地方随模型一起保存，不是给用户看的展示项。
 * 快捷值按厂商常见规格成对给出，一键填两框；仍可手改。
 */
const MODEL_PRESETS = [
    { label: "1M / 128K", context: 1_000_000, output: 128_000 },
    { label: "200K / 64K", context: 200_000, output: 64_000 },
    { label: "128K / 16K", context: 128_000, output: 16_384 },
    { label: "64K / 8K", context: 65_536, output: 8_192 },
] as const;

function applyPreset(preset: (typeof MODEL_PRESETS)[number]) {
    formContextWindow.value = String(preset.context);
    formMaxOutput.value = String(preset.output);
}

const isEditing = computed(() => editingId.value !== null);
const selectedProvider = computed(
    () =>
        providers.value.find((item) => item.provider === formProvider.value) ??
        null,
);
const requiresBaseUrl = computed(
    () => selectedProvider.value?.requires_base_url ?? false,
);
const isEmpty = computed(() => credentials.value.length === 0);
const filteredCredentials = computed(() => {
    const query = search.value.trim().toLowerCase();
    if (!query) {
        return credentials.value;
    }
    return credentials.value.filter((credential) =>
        [credential.name, credential.provider_label, credential.model].some(
            (value) => value.toLowerCase().includes(query),
        ),
    );
});

onMounted(() => {
    void refresh();
});

/** 下拉里「自定义」的哨兵值：不能与真实模型名冲突。 */
const CUSTOM_MODEL = "__custom__";

/** 把输入框里的字符串解析成正整数；空串 = 未填写（null）。 */
function parseTokens(raw: string): number | null {
    const text = raw.trim();
    if (!text) {
        return null;
    }
    const value = Number(text);
    if (!Number.isFinite(value) || !Number.isInteger(value) || value <= 0) {
        return null;
    }
    return value;
}

/**
 * 人类可读的 token 数。
 *
 * 1000000 写成「1M」、128000 写成「128K」—— 这是用户在厂商文档里看到的写法，
 * 写成一串零要靠数才知道是多大。
 */
function formatTokens(value: number | null | undefined): string {
    if (value === null || value === undefined) {
        return "未设置";
    }
    if (value >= 1_000_000) {
        const millions = value / 1_000_000;
        return `${Number.isInteger(millions) ? millions : millions.toFixed(1)}M`;
    }
    if (value >= 1_000) {
        const thousands = value / 1_000;
        return `${Number.isInteger(thousands) ? thousands : thousands.toFixed(1)}K`;
    }
    return String(value);
}

/** 下拉选中真实模型 or 切到自定义输入。 */
function selectModel(value: string) {
    if (value === CUSTOM_MODEL) {
        customModel.value = true;
        formModel.value = "";
        return;
    }
    customModel.value = false;
    formModel.value = value;
}

async function refresh() {
    loading.value = true;
    error.value = null;
    // 两个接口独立失败：厂商目录挂了不该让「已保存的凭据」也消失
    void loadRouting();
    const [listResult, providerResult] = await Promise.allSettled([
        listLLMCredentials(),
        listLLMProviders(),
    ]);
    if (listResult.status === "fulfilled") {
        credentials.value = listResult.value;
    } else {
        error.value =
            (listResult.reason as Error)?.message || "模型凭据加载失败";
    }
    if (providerResult.status === "fulfilled") {
        providers.value = providerResult.value;
    }
    loading.value = false;
}

function resetForm() {
    editingId.value = null;
    formName.value = "";
    formProvider.value = "deepseek";
    formModel.value = "";
    formApiKey.value = "";
    formBaseUrl.value = "";
    formDefault.value = false;
    formContextWindow.value = "";
    formMaxOutput.value = "";
    formSupportsText.value = true;
    formSupportsImage.value = false;
    formTiers.value = [];
    probeResult.value = null;
    probeError.value = null;
    modelOptions.value = [];
    customModel.value = false;
}

function openCreate() {
    resetForm();
    if (providers.value.length > 0) {
        formProvider.value = providers.value[0].provider;
    }
    formDefault.value = credentials.value.length === 0;
    showForm.value = true;
}

function openEdit(credential: LLMCredentialInfo) {
    editingId.value = credential.credential_id;
    formName.value = credential.name;
    formProvider.value = credential.provider;
    formModel.value = credential.model;
    formApiKey.value = "";
    formBaseUrl.value =
        credential.provider === "custom" ? credential.base_url : "";
    formDefault.value = credential.is_default;
    formContextWindow.value = credential.context_window?.toString() ?? "";
    formMaxOutput.value = credential.max_output_tokens?.toString() ?? "";
    const caps = (credential.capabilities ?? {}) as {
        text?: boolean;
        image?: boolean;
        json_mode?: boolean;
        reasoning_tiers?: { label: string; value: string }[];
    };
    formSupportsText.value = caps.text !== false;
    formSupportsImage.value = caps.image === true;
    formAllowFallback.value = credential.allow_platform_fallback;
    formJsonMode.value = caps.json_mode === true;
    formTiers.value = Array.isArray(caps.reasoning_tiers)
        ? caps.reasoning_tiers.map((tier) => ({
              label: String(tier.label ?? ""),
              value: String(tier.value ?? ""),
          }))
        : [];
    probeResult.value = null;
    probeError.value = null;
    modelOptions.value = [];
    customModel.value = false;
    showForm.value = true;
}

function closeForm() {
    showForm.value = false;
    resetForm();
}

function validate(): string | null {
    if (!formName.value.trim()) {
        return "请填写名称（用于辨认是哪把 key）";
    }
    if (!formModel.value.trim()) {
        return "请填写模型名，例如 deepseek-chat";
    }
    if (!isEditing.value && !formApiKey.value.trim()) {
        return "请填写 API Key";
    }
    if (requiresBaseUrl.value && !formBaseUrl.value.trim()) {
        return "自定义端点必须填写 base_url（OpenAI 兼容）";
    }
    if (
        formContextWindow.value.trim() &&
        parseTokens(formContextWindow.value) === null
    ) {
        return "上下文窗口需为正整数（token）";
    }
    if (
        formMaxOutput.value.trim() &&
        parseTokens(formMaxOutput.value) === null
    ) {
        return "最大输出需为正整数（token）";
    }
    const context = parseTokens(formContextWindow.value);
    const output = parseTokens(formMaxOutput.value);
    if (context !== null && output !== null && output > context) {
        return "最大输出不能大于上下文窗口";
    }
    return null;
}

/**
 * 测试连接（表单态）。
 *
 * 编辑已有凭据且 key 留空时，只能测库里的那份（前端拿不到明文），
 * 因此走 `credential_id`；否则测用户刚填的配置，**不落库**。
 */
async function probeForm() {
    probeResult.value = null;
    probeError.value = null;
    modelOptions.value = [];

    const editing = editingId.value;
    const useSaved = editing !== null && !formApiKey.value.trim();
    if (!useSaved) {
        if (!formApiKey.value.trim()) {
            probeError.value = "请先填写 API Key 再测试";
            return;
        }
        if (requiresBaseUrl.value && !formBaseUrl.value.trim()) {
            probeError.value = "自定义端点必须先填写 base_url";
            return;
        }
    }

    probeBusy.value = true;
    try {
        const result = await probeLLMCredential(
            useSaved
                ? { credential_id: editing }
                : {
                      provider: formProvider.value,
                      api_key: formApiKey.value.trim(),
                      ...(requiresBaseUrl.value
                          ? { base_url: formBaseUrl.value.trim() }
                          : {}),
                  },
        );
        probeResult.value = result;
        if (result.ok) {
            modelOptions.value = result.models;
            customModel.value = false;
            // 模型名还空着就替用户填第一个，省掉手打错名字导致的 404
            if (!formModel.value.trim() && result.models.length > 0) {
                formModel.value = result.models[0];
            }
        }
    } catch (err) {
        // 4xx/5xx 与「连通性失败」是两件事：这里只可能是请求本身被拒
        probeError.value = (err as Error).message || "测试请求失败";
    } finally {
        probeBusy.value = false;
    }
}

/** 测试已保存凭据（列表态）；失败会累加计数，达阈值自动标为 invalid。 */
async function probeSaved(credential: LLMCredentialInfo) {
    testingId.value = credential.credential_id;
    error.value = null;
    try {
        const result = await testLLMCredential(credential.credential_id);
        // 必须先刷新再写消息：refresh() 会清空 error，顺序反了消息就没了
        await refresh();
        if (result.ok) {
            toastSuccess(
                `「${credential.name}」连接正常（${result.latency_ms}ms，${
                    result.models.length
                } 个模型）`,
            );
        } else {
            error.value = `「${credential.name}」测试失败：${result.error ?? "未知错误"}`;
        }
    } catch (err) {
        error.value = (err as Error).message || "测试失败";
    } finally {
        testingId.value = null;
    }
}

/**
 * 生成能力探测（Issue #28）：planner 门禁依赖 capabilities.json_mode。
 * ⚠️ 用用户 key 发一次真实请求（约 100 token），按钮文案已注明。
 */
const probingGenId = ref<string | null>(null);
const genProbeResult = ref<{ ok: boolean; error: string | null } | null>(null);

async function probeGenerationFor(credential: LLMCredentialInfo) {
    probingGenId.value = credential.credential_id;
    genProbeResult.value = null;
    error.value = null;
    try {
        const result = await probeGeneration(credential.credential_id);
        await refresh();
        genProbeResult.value = { ok: result.ok, error: result.error };
        if (result.ok) {
            toastSuccess(
                `「${credential.name}」通过结构化输出探测（${result.latency_ms}ms），可固定为规划模型`,
            );
        } else {
            error.value = `「${credential.name}」生成探测未通过：${result.error ?? "未知错误"}`;
        }
    } catch (err) {
        error.value = (err as Error).message || "生成探测失败";
    } finally {
        probingGenId.value = null;
    }
}

/** 能力徽章文案：文本 · 图片 · N 档推理 */
function capabilitiesLabel(credential: LLMCredentialInfo): string {
    const caps = (credential.capabilities ?? {}) as {
        text?: boolean;
        image?: boolean;
        json_mode?: boolean;
        reasoning_tiers?: unknown[];
    };
    const parts = [caps.text !== false ? "文本" : "", caps.image ? "图片" : ""].filter(
        Boolean,
    );
    if (caps.json_mode) parts.push("JSON");
    const tiers = Array.isArray(caps.reasoning_tiers) ? caps.reasoning_tiers.length : 0;
    if (tiers) parts.push(`${tiers} 档推理`);
    return parts.length ? parts.join(" · ") : "未声明能力";
}

async function submitForm() {
    const problem = validate();
    if (problem) {
        error.value = problem;
        return;
    }
    submitting.value = true;
    error.value = null;
    // 空输入 = 清除声明（update 显式传 null，后端按「清除」处理）
    const contextWindow = parseTokens(formContextWindow.value);
    const maxOutput = parseTokens(formMaxOutput.value);
    // 能力声明：只提交填写完整的档位（空 label/value 行视为未填）
    const capabilities = {
        text: formSupportsText.value,
        image: formSupportsImage.value,
        json_mode: formJsonMode.value,
        reasoning_tiers: formTiers.value
            .map((tier) => ({ label: tier.label.trim(), value: tier.value.trim() }))
            .filter((tier) => tier.label && tier.value),
    };
    try {
        if (editingId.value) {
            await updateLLMCredential(editingId.value, {
                name: formName.value.trim(),
                model: formModel.value.trim(),
                context_window: contextWindow,
                max_output_tokens: maxOutput,
                allow_platform_fallback: formAllowFallback.value,
                capabilities,
                ...(formApiKey.value.trim()
                    ? { api_key: formApiKey.value.trim() }
                    : {}),
                ...(requiresBaseUrl.value
                    ? { base_url: formBaseUrl.value.trim() }
                    : {}),
            });
            toastSuccess(`已更新「${formName.value.trim()}」`);
        } else {
            await createLLMCredential({
                name: formName.value.trim(),
                provider: formProvider.value,
                model: formModel.value.trim(),
                api_key: formApiKey.value.trim(),
                context_window: contextWindow,
                max_output_tokens: maxOutput,
                allow_platform_fallback: formAllowFallback.value,
                capabilities,
                ...(requiresBaseUrl.value
                    ? { base_url: formBaseUrl.value.trim() }
                    : {}),
                is_default: formDefault.value,
            });
            toastSuccess(`已添加「${formName.value.trim()}」`);
        }
        closeForm();
        await refresh();
    } catch (err) {
        error.value = (err as Error).message || "保存失败";
    } finally {
        submitting.value = false;
    }
}

async function setDefault(credential: LLMCredentialInfo) {
    if (credential.is_default) {
        return;
    }
    busyId.value = credential.credential_id;
    error.value = null;
    try {
        await updateLLMCredential(credential.credential_id, {
            is_default: true,
        });
        toastSuccess(`「${credential.name}」已设为默认模型`);
        await refresh();
    } catch (err) {
        error.value = (err as Error).message || "设置默认失败";
    } finally {
        busyId.value = null;
    }
}

function requestRemoveCredential(credential: LLMCredentialInfo) {
    confirmDeleteId.value = credential.credential_id;
}

async function confirmRemoveCredential(credential: LLMCredentialInfo) {
    busyId.value = credential.credential_id;
    error.value = null;
    try {
        await deleteLLMCredential(credential.credential_id);
        confirmDeleteId.value = null;
        toastSuccess(`已删除「${credential.name}」`);
        await refresh();
    } catch (err) {
        error.value = (err as Error).message || "删除失败";
    } finally {
        busyId.value = null;
    }
}

function statusOf(credential: LLMCredentialInfo): {
    label: string;
    variant: "success" | "warning" | "muted";
} {
    if (credential.status === "disabled") {
        return { label: "已停用", variant: "muted" };
    }
    if (credential.status === "invalid") {
        return { label: "校验失败", variant: "warning" };
    }
    return { label: "可用", variant: "success" };
}
</script>

<template>
    <section>
        <PageHeader
            eyebrow="模型配置"
            title="我的模型"
            description="接入你自己的模型 API Key。任务会使用你选择的模型，密钥加密存储、只进不出。"
        >
            <Button
                variant="secondary"
                size="sm"
                data-test="model-create-open"
                @click="openCreate"
            >
                <Plus />
                添加模型
            </Button>
        </PageHeader>

        <ErrorBanner :message="error" testid="models-error" @dismiss="error = null" />

        <!-- 新增 / 编辑表单（平滑折叠展开） -->
        <AnimatedCollapse :open="showForm">
            <form
                class="mb-4 grid gap-3 rounded-lg border border-border bg-card p-3"
                data-test="model-form"
                @submit.prevent="submitForm"
            >
                <div class="grid gap-2 sm:grid-cols-2">
                    <div class="grid gap-1.5">
                        <Label for="model-name">名称（用于辨认）</Label>
                        <Input
                            id="model-name"
                            v-model="formName"
                            data-test="model-name"
                            placeholder="例如：我的 DeepSeek"
                            maxlength="64"
                        />
                    </div>
                    <div class="grid gap-1.5">
                        <Label for="model-provider">厂商</Label>
                        <select
                            id="model-provider"
                            v-model="formProvider"
                            data-test="model-provider"
                            :disabled="isEditing"
                            class="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-60"
                        >
                            <option
                                v-for="item in providers"
                                :key="item.provider"
                                :value="item.provider"
                            >
                                {{ item.label }}
                            </option>
                        </select>
                        <p v-if="isEditing" class="text-xs text-muted-foreground">
                            厂商不可修改；换厂商请新建一把。
                        </p>
                    </div>
                </div>

                <div class="grid gap-2 sm:grid-cols-2">
                    <div class="grid content-start gap-1.5 sm:col-span-2">
                        <Label for="model-id">模型名</Label>
                        <!-- 拉到模型列表后变成真下拉；厂商 /models 列不全时可选「自定义」手填 -->
                        <select
                            v-if="modelOptions.length > 0 && !customModel"
                            id="model-id"
                            data-test="model-select"
                            class="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
                            :value="
                                modelOptions.includes(formModel) ? formModel : ''
                            "
                            @change="
                                selectModel(
                                    ($event.target as HTMLSelectElement).value,
                                )
                            "
                        >
                            <option value="" disabled>请选择模型…</option>
                            <option
                                v-for="item in modelOptions"
                                :key="item"
                                :value="item"
                            >
                                {{ item }}
                            </option>
                            <option :value="CUSTOM_MODEL">自定义…</option>
                        </select>
                        <div
                            v-if="customModel || modelOptions.length === 0"
                            class="grid gap-1.5"
                        >
                            <Input
                                v-if="customModel"
                                id="model-id"
                                v-model="formModel"
                                data-test="model-id"
                                placeholder="手动输入模型名，例如 glm-4.6"
                                maxlength="160"
                            />
                            <Input
                                v-else
                                id="model-id"
                                v-model="formModel"
                                data-test="model-id"
                                placeholder="例如：deepseek-chat / gpt-4o-mini"
                                maxlength="160"
                            />
                            <Button
                                v-if="customModel"
                                type="button"
                                variant="link"
                                size="sm"
                                class="h-auto justify-self-start px-0 text-xs"
                                data-test="model-back-to-select"
                                @click="customModel = false"
                            >
                                返回下拉选择
                            </Button>
                        </div>
                        <p
                            v-if="modelOptions.length > 0"
                            class="text-xs text-muted-foreground"
                        >
                            已从厂商拉到
                            {{ modelOptions.length }}
                            个模型；先「测试连接」可刷新列表。
                        </p>

                        <!-- 模型预设：上下文 / 最大输出随模型一起保存；留空 = 沿用厂商默认 -->
                        <div
                            class="mt-1 flex flex-wrap items-center gap-1.5 text-xs"
                        >
                            <span class="text-muted-foreground">上下文</span>
                            <Input
                                id="model-context"
                                v-model="formContextWindow"
                                data-test="model-context"
                                inputmode="numeric"
                                placeholder="留空 = 厂商默认"
                                class="h-7 w-28 px-2 text-xs"
                            />
                            <span class="text-muted-foreground">最大输出</span>
                            <Input
                                id="model-output"
                                v-model="formMaxOutput"
                                data-test="model-output"
                                inputmode="numeric"
                                placeholder="留空 = 厂商默认"
                                class="h-7 w-28 px-2 text-xs"
                            />
                            <span class="ml-1 text-muted-foreground">预设：</span>
                            <Button
                                v-for="(preset, index) in MODEL_PRESETS"
                                :key="preset.label"
                                type="button"
                                variant="outline"
                                size="sm"
                                class="h-6 px-1.5 text-xs font-normal text-muted-foreground hover:border-primary hover:text-primary"
                                :data-test="`model-preset-${index}`"
                                :title="`上下文 ${preset.context} · 输出 ${preset.output}`"
                                @click="applyPreset(preset)"
                            >
                                {{ preset.label }}
                            </Button>
                        </div>

                        <!-- 能力声明（参照 DeepSeek Harness 模型目录）：输入类型 + 推理等级档位 -->
                        <div class="mt-2.5 grid gap-3 sm:grid-cols-2">
                            <div>
                                <span class="text-xs text-muted-foreground">输入类型</span>
                                <div class="mt-1 flex items-center gap-3 text-sm">
                                    <label class="flex items-center gap-1.5">
                                        <input
                                            v-model="formSupportsText"
                                            data-test="model-cap-text"
                                            type="checkbox"
                                            class="size-4 rounded border-input accent-primary"
                                        />
                                        <span>文本</span>
                                    </label>
                                    <label class="flex items-center gap-1.5">
                                        <input
                                            v-model="formSupportsImage"
                                            data-test="model-cap-image"
                                            type="checkbox"
                                            class="size-4 rounded border-input accent-primary"
                                        />
                                        <span>图片</span>
                                    </label>
                                </div>
                                <p class="mt-1 text-[10px] leading-4 text-muted-foreground">
                                    勾了「图片」才允许在会话中传图（需视觉模型）
                                </p>
                            </div>
                            <div>
                                <div class="flex items-center justify-between">
                                    <span class="text-xs text-muted-foreground">结构化输出（生成探测）</span>
                                    <span
                                        class="rounded-full border px-1.5 py-0.5 text-[10px]"
                                        :class="
                                            formJsonMode
                                                ? 'border-success/40 bg-success/10 text-success'
                                                : 'border-border bg-muted text-muted-foreground'
                                        "
                                        data-test="model-json-mode"
                                    >
                                        {{ formJsonMode ? "已通过" : "未探测" }}
                                    </span>
                                </div>
                                <p class="mt-1 text-[10px] leading-4 text-muted-foreground">
                                    由「探测 JSON」写入，不能手填；不通过的凭据不能固定为规划（planner）
                                </p>
                            </div>
                            <div>
                                <div class="flex items-center justify-between">
                                    <span class="text-xs text-muted-foreground">推理等级档位</span>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        data-test="model-tier-add"
                                        class="h-6 px-1.5 text-[10px] font-normal text-muted-foreground hover:border-primary hover:text-primary"
                                        :disabled="formTiers.length >= 4"
                                        @click="addTier"
                                    >
                                        + 添加档位
                                    </Button>
                                </div>
                                <div
                                    v-for="(tier, index) in formTiers"
                                    :key="index"
                                    class="mt-1 flex items-center gap-1.5"
                                >
                                    <Input
                                        v-model="tier.label"
                                        :data-test="`model-tier-label-${index}`"
                                        placeholder="low"
                                        class="h-7 w-20 px-2 text-xs"
                                        maxlength="24"
                                    />
                                    <span class="text-xs text-muted-foreground">→</span>
                                    <Input
                                        v-model="tier.value"
                                        :data-test="`model-tier-value-${index}`"
                                        placeholder="low"
                                        class="h-7 flex-1 px-2 text-xs"
                                        maxlength="64"
                                    />
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon-sm"
                                        class="text-muted-foreground hover:text-destructive"
                                        :aria-label="`删除档位 ${index + 1}`"
                                        @click="formTiers.splice(index, 1)"
                                    >
                                        <X class="size-3.5" aria-hidden="true" />
                                    </Button>
                                </div>
                                <p
                                    v-if="!formTiers.length"
                                    class="mt-1 text-[10px] leading-4 text-muted-foreground"
                                >
                                    留空 = 不支持推理参数（会话中不展示思考档位）
                                </p>
                            </div>
                        </div>
                    </div>
                    <div class="grid content-start gap-1.5 sm:col-span-2">
                        <label class="flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2.5">
                            <input
                                v-model="formAllowFallback"
                                data-test="model-allow-fallback"
                                type="checkbox"
                                class="mt-0.5 size-4 rounded border-input accent-primary"
                            />
                            <span class="text-xs leading-5">
                                <span class="font-medium">凭据不可用时回退平台模型</span>
                                <span class="block text-[10px] leading-4 text-muted-foreground">
                                    仅在 key 被拒（401/402/403，欠费或失效）时改走平台模型并**按平台计费**；
                                    限流（429）与超时不会回退。默认关闭：任务失败并提示原因。
                                </span>
                            </span>
                        </label>
                        <Label for="model-key">
                            API Key
                            <span
                                v-if="isEditing"
                                class="font-normal text-muted-foreground"
                                >（留空不更换）</span
                            >
                        </Label>
                        <Input
                            id="model-key"
                            v-model="formApiKey"
                            data-test="model-key"
                            type="password"
                            autocomplete="off"
                            :placeholder="
                                isEditing
                                    ? '留空则保持原 key'
                                    : '粘贴厂商控制台的 API Key'
                            "
                        />
                    </div>
                </div>

                <div v-if="requiresBaseUrl" class="grid gap-1.5">
                    <Label for="model-base-url">Base URL（OpenAI 兼容）</Label>
                    <Input
                        id="model-base-url"
                        v-model="formBaseUrl"
                        data-test="model-base-url"
                        placeholder="https://your-relay.example.com/v1"
                        maxlength="512"
                    />
                    <p class="text-xs text-muted-foreground">
                        仅支持 https、443 端口；不能指向内网或本机地址。
                    </p>
                </div>

                <label v-if="!isEditing" class="flex items-center gap-2 text-sm">
                    <input
                        v-model="formDefault"
                        data-test="model-default"
                        type="checkbox"
                        class="size-4 rounded border-input accent-primary"
                    />
                    <span>设为默认模型（首个凭据会自动成为默认）</span>
                </label>

                <div
                    v-if="probeError"
                    class="rounded-md border border-destructive/40 bg-destructive/5 px-2.5 py-2 text-xs text-destructive"
                    data-test="model-probe-error"
                >
                    {{ probeError }}
                </div>
                <div
                    v-else-if="probeResult"
                    class="rounded-md border border-border bg-muted px-2.5 py-2 text-xs"
                    :class="probeResult.ok ? 'text-foreground' : 'text-destructive'"
                    data-test="model-probe-result"
                >
                    <template v-if="probeResult.ok">
                        连接成功（{{ probeResult.latency_ms }}ms），拉到
                        {{ probeResult.models.length }} 个模型。
                    </template>
                    <template v-else
                        >连接失败：{{ probeResult.error ?? "未知错误" }}</template
                    >
                </div>

                <p
                    class="rounded-md bg-muted px-2.5 py-2 text-xs text-muted-foreground"
                >
                    「测试连接」会真实请求厂商的
                    <code>/models</code>；保存本身<strong>不做</strong>联网校验。
                    任务调用时若 key
                    无效，会直接失败并提示厂商错误，不会自动改用平台模型。
                </p>

                <div class="flex flex-wrap gap-2">
                    <Button
                        type="submit"
                        size="sm"
                        :disabled="submitting"
                        data-test="model-submit"
                    >
                        {{
                            submitting ? "保存中…" : isEditing ? "保存修改" : "保存"
                        }}
                    </Button>
                    <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        :disabled="probeBusy"
                        data-test="model-probe"
                        @click="probeForm"
                    >
                        <Wifi />
                        {{ probeBusy ? "测试中…" : "测试连接" }}
                    </Button>
                    <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        data-test="model-cancel"
                        @click="closeForm"
                    >
                        取消
                    </Button>
                </div>
            </form>
        </AnimatedCollapse>

        <div v-if="showSkeleton" class="min-h-[16rem] space-y-3" aria-busy="true" aria-label="加载中">
            <div class="h-9 w-64 animate-pulse rounded-md bg-muted/60 motion-reduce:animate-none" />
            <div class="h-20 animate-pulse rounded-lg border border-border bg-muted/40 motion-reduce:animate-none" />
            <div class="h-20 animate-pulse rounded-lg border border-border bg-muted/40 motion-reduce:animate-none" />
        </div>

        <EmptyState
            v-else-if="!loading && !showSkeleton && isEmpty && !error"
            data-test="models-empty"
            title="还没有接入自己的模型"
            description="添加一把 API Key 后，建任务时就能选择它；不添加则一切照旧使用平台默认模型。"
        />

        <template v-else-if="!loading && !showSkeleton">
            <div class="mb-3 flex flex-wrap items-center gap-2">
                <Input
                    v-model="search"
                    data-test="model-search"
                    class="sm:max-w-xs"
                    placeholder="搜索名称 / 厂商 / 模型"
                    aria-label="搜索模型凭据"
                />
                <span
                    class="text-xs text-muted-foreground"
                    data-test="model-count"
                >
                    共 {{ credentials.length }} 把
                </span>
            </div>

            <ul
                class="divide-y divide-border rounded-lg border border-border"
                data-test="models-list"
            >
                <li
                    v-for="credential in filteredCredentials"
                    :key="credential.credential_id"
                    class="flex flex-col gap-2.5 px-3.5 py-3 text-sm"
                    :data-test="`model-row-${credential.credential_id}`"
                >
                    <div class="flex flex-col gap-2.5 xl:flex-row xl:items-center xl:justify-between">
                        <div class="min-w-0 flex-1">
                            <div class="flex flex-wrap items-center gap-1.5">
                                <Cpu class="size-3.5 text-muted-foreground" />
                                <span class="font-medium">{{
                                    credential.name
                                }}</span>
                                <Badge
                                    v-if="credential.is_default"
                                    variant="default"
                                    >默认</Badge
                                >
                                <Badge :variant="statusOf(credential).variant">{{
                                    statusOf(credential).label
                                }}</Badge>
                            </div>
                            <div
                                class="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"
                            >
                                <span class="font-medium text-foreground">{{
                                    credential.provider_label
                                }}</span>
                                <span>{{ credential.model }}</span>
                                <span
                                    data-test="model-capabilities-badge"
                                    class="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                                >
                                    {{ capabilitiesLabel(credential) }}
                                </span>
                                <code class="rounded bg-muted px-1 py-0.5">{{
                                    credential.masked_key
                                }}</code>
                            </div>
                            <div
                                v-if="credential.provider === 'custom'"
                                class="mt-0.5 break-all text-xs text-muted-foreground"
                                :title="credential.base_url"
                            >
                                {{ credential.base_url }}
                            </div>
                            <p
                                v-if="credential.last_error"
                                class="mt-0.5 text-xs text-warning"
                            >
                                {{ credential.last_error }}
                            </p>
                        </div>

                        <div class="flex shrink-0 flex-wrap items-center gap-1">
                            <Button
                                variant="ghost"
                                size="sm"
                                :disabled="probingGenId === credential.credential_id"
                                :data-test="`model-probe-gen-${credential.credential_id}`"
                                :title="'用你的 key 发一次约 100 token 的真实请求，验证能否按指令输出 JSON'"
                                @click="probeGenerationFor(credential)"
                            >
                                <FileJson />
                                {{
                                    probingGenId === credential.credential_id
                                        ? "探测中…"
                                        : "探测 JSON"
                                }}
                            </Button>
                            <Button
                                variant="ghost"
                                size="sm"
                                :disabled="testingId === credential.credential_id"
                                :data-test="`model-test-${credential.credential_id}`"
                                @click="probeSaved(credential)"
                            >
                                <Wifi />
                                {{
                                    testingId === credential.credential_id
                                        ? "测试中…"
                                        : "测试"
                                }}
                            </Button>
                            <Button
                                v-if="!credential.is_default"
                                variant="ghost"
                                size="sm"
                                :disabled="busyId === credential.credential_id"
                                :data-test="`model-set-default-${credential.credential_id}`"
                                @click="setDefault(credential)"
                            >
                                <Star />
                                设为默认
                            </Button>
                            <Button
                                variant="ghost"
                                size="sm"
                                :data-test="`model-edit-${credential.credential_id}`"
                                @click="openEdit(credential)"
                            >
                                <Pencil />
                                编辑
                            </Button>
                            <Button
                                v-if="confirmDeleteId !== credential.credential_id"
                                variant="ghost"
                                size="sm"
                                :disabled="busyId === credential.credential_id"
                                :data-test="`model-delete-${credential.credential_id}`"
                                @click="requestRemoveCredential(credential)"
                            >
                                <Trash2 />
                                删除
                            </Button>
                        </div>
                    </div>

                    <ConfirmBar
                        v-if="confirmDeleteId === credential.credential_id"
                        title="确认删除该模型凭据？"
                        consequence="删除后无法恢复该密钥，但不影响历史任务中已保存的模型快照。"
                        confirm-label="确认删除"
                        cancel-label="取消"
                        :confirm-testid="`model-confirm-delete-${credential.credential_id}`"
                        :cancel-testid="`model-cancel-delete-${credential.credential_id}`"
                        :loading="busyId === credential.credential_id"
                        @confirm="confirmRemoveCredential(credential)"
                        @cancel="confirmDeleteId = null"
                    />
                </li>
            </ul>

            <p
                v-if="filteredCredentials.length === 0"
                data-test="models-no-match"
                class="mt-3 text-sm text-muted-foreground"
            >
                没有匹配「{{ search.trim() }}」的凭据。
            </p>
        </template>

        <p v-if="!isEmpty" class="mt-3 text-xs text-muted-foreground">
            删除凭据不会影响历史任务：任务里保存了当时使用的模型快照。
        </p>
    </section>

    <!-- ===== 节点级模型路由（混合路由用户化，Issue #22 延伸）===== -->
    <section
        data-test="node-routing"
        class="mt-6 rounded-xl border border-border bg-card p-5"
    >
        <div class="flex flex-wrap items-start justify-between gap-2">
            <div>
                <h2 class="text-base font-semibold tracking-tight">节点级模型路由</h2>
                <p class="mt-1 text-xs leading-5 text-muted-foreground">
                    给不同阶段指定不同的模型（如：证据抽取与校验走本地免费模型，规划与写作走云端强模型）。
                    不配置的节点继续使用任务勾选的那一个模型。
                </p>
            </div>
            <Button
                data-test="routing-save"
                type="button"
                variant="outline"
                size="sm"
                :disabled="routeSaving"
                @click="saveRouting"
            >
                {{ routeSaving ? "保存中…" : "保存路由" }}
            </Button>
        </div>
        <BeContextMeter
            class="mt-4"
            :capacity="ROUTABLE_NODE_TYPES.length"
            :categories="routingMeterCategories"
            model="按执行阶段独立指定模型"
        />
        <ErrorBanner
            :message="routeError"
            testid="routing-error"
            class="mt-3 mb-0"
            @dismiss="routeError = null"
        />
        <p
            v-if="routeSuccess"
            data-test="routing-message"
            class="mt-2 text-xs text-muted-foreground"
        >
            {{ routeSuccess }}
        </p>
        <div class="mt-4 grid gap-3 md:grid-cols-2">
            <label
                v-for="node in ROUTABLE_NODE_TYPES"
                :key="node.value"
                class="flex flex-col gap-1"
                :data-test="`routing-${node.value}`"
            >
                <span class="text-xs font-medium text-foreground">
                    {{ node.label }}
                    <span class="ml-1 font-normal text-muted-foreground">· {{ node.hint }}</span>
                </span>
                <select
                    v-model="routeDraft[node.value]"
                    class="h-9 rounded-md border border-border bg-background px-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                    <option value="">跟随任务勾选的模型</option>
                    <option
                        v-for="cred in credentials"
                        :key="cred.credential_id"
                        :value="cred.credential_id"
                    >
                        {{ cred.name }}（{{ cred.model }}）
                    </option>
                </select>
            </label>
        </div>
    </section>
</template>
