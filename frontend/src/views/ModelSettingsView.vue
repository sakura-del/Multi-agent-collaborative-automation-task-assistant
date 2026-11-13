<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Check, Cpu, Pencil, Plus, Star, Trash2 } from "lucide-vue-next";

import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createLLMCredential,
  deleteLLMCredential,
  listLLMCredentials,
  listLLMProviders,
  updateLLMCredential,
} from "@/api/llm-credentials";
import type {
  LLMCredentialInfo,
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
 * `model-delete-<id>` / `model-confirm-delete-<id>`。
 */
const credentials = ref<LLMCredentialInfo[]>([]);
const providers = ref<LLMProviderOption[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);

const showForm = ref(false);
const editingId = ref<string | null>(null);
const submitting = ref(false);

const formName = ref("");
const formProvider = ref<LLMProviderKey>("deepseek");
const formModel = ref("");
const formApiKey = ref("");
const formBaseUrl = ref("");
const formDefault = ref(false);

const busyId = ref<string | null>(null);
const confirmDeleteId = ref<string | null>(null);

const isEditing = computed(() => editingId.value !== null);
const selectedProvider = computed(
  () => providers.value.find((item) => item.provider === formProvider.value) ?? null
);
const requiresBaseUrl = computed(() => selectedProvider.value?.requires_base_url ?? false);
const isEmpty = computed(() => credentials.value.length === 0);

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  error.value = null;
  // 两个接口独立失败：厂商目录挂了不该让「已保存的凭据」也消失
  const [listResult, providerResult] = await Promise.allSettled([
    listLLMCredentials(),
    listLLMProviders(),
  ]);
  if (listResult.status === "fulfilled") {
    credentials.value = listResult.value;
  } else {
    error.value = (listResult.reason as Error)?.message || "模型凭据加载失败";
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
  formBaseUrl.value = credential.provider === "custom" ? credential.base_url : "";
  formDefault.value = credential.is_default;
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
  return null;
}

async function submitForm() {
  const problem = validate();
  if (problem) {
    error.value = problem;
    return;
  }
  submitting.value = true;
  error.value = null;
  success.value = null;
  try {
    if (editingId.value) {
      await updateLLMCredential(editingId.value, {
        name: formName.value.trim(),
        model: formModel.value.trim(),
        ...(formApiKey.value.trim() ? { api_key: formApiKey.value.trim() } : {}),
        ...(requiresBaseUrl.value ? { base_url: formBaseUrl.value.trim() } : {}),
      });
      success.value = `已更新「${formName.value.trim()}」`;
    } else {
      await createLLMCredential({
        name: formName.value.trim(),
        provider: formProvider.value,
        model: formModel.value.trim(),
        api_key: formApiKey.value.trim(),
        ...(requiresBaseUrl.value ? { base_url: formBaseUrl.value.trim() } : {}),
        is_default: formDefault.value,
      });
      success.value = `已添加「${formName.value.trim()}」`;
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
    await updateLLMCredential(credential.credential_id, { is_default: true });
    success.value = `「${credential.name}」已设为默认模型`;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "设置默认失败";
  } finally {
    busyId.value = null;
  }
}

async function removeCredential(credential: LLMCredentialInfo) {
  if (confirmDeleteId.value !== credential.credential_id) {
    // 第一次点击只进入确认态：删掉后 key 无法恢复
    confirmDeleteId.value = credential.credential_id;
    return;
  }
  busyId.value = credential.credential_id;
  error.value = null;
  try {
    await deleteLLMCredential(credential.credential_id);
    success.value = `已删除「${credential.name}」`;
    confirmDeleteId.value = null;
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
      eyebrow="Model settings"
      title="我的模型"
      description="接入你自己的模型 API Key。任务会使用你选择的模型，密钥加密存储、只进不出。"
    >
      <Button variant="secondary" size="sm" data-test="model-create-open" @click="openCreate">
        <Plus />
        添加模型
      </Button>
    </PageHeader>

    <ErrorBanner :message="error" testid="models-error" />

    <p
      v-if="success"
      class="mb-3 flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-sm"
      data-test="models-success"
    >
      <Check class="size-4 text-primary" />
      {{ success }}
    </p>

    <!-- 新增 / 编辑表单 -->
    <form
      v-if="showForm"
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
            class="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-60"
          >
            <option v-for="item in providers" :key="item.provider" :value="item.provider">
              {{ item.label }}
            </option>
          </select>
          <p v-if="isEditing" class="text-xs text-muted-foreground">
            厂商不可修改；换厂商请新建一把。
          </p>
        </div>
      </div>

      <div class="grid gap-2 sm:grid-cols-2">
        <div class="grid gap-1.5">
          <Label for="model-id">模型名</Label>
          <Input
            id="model-id"
            v-model="formModel"
            data-test="model-id"
            placeholder="例如：deepseek-chat / gpt-4o-mini"
            maxlength="160"
          />
        </div>
        <div class="grid gap-1.5">
          <Label for="model-key">
            API Key
            <span v-if="isEditing" class="font-normal text-muted-foreground">（留空不更换）</span>
          </Label>
          <Input
            id="model-key"
            v-model="formApiKey"
            data-test="model-key"
            type="password"
            autocomplete="off"
            :placeholder="isEditing ? '留空则保持原 key' : '粘贴厂商控制台的 API Key'"
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

      <p class="rounded-md bg-muted px-2.5 py-2 text-xs text-muted-foreground">
        保存后不会立刻联网校验。任务首次调用时若 key 无效，会直接失败并提示厂商错误，
        不会自动改用平台模型。
      </p>

      <div class="flex gap-2">
        <Button type="submit" size="sm" :disabled="submitting" data-test="model-submit">
          {{ submitting ? "保存中…" : isEditing ? "保存修改" : "保存" }}
        </Button>
        <Button type="button" variant="ghost" size="sm" data-test="model-cancel" @click="closeForm">
          取消
        </Button>
      </div>
    </form>

    <p v-if="loading" class="text-sm text-muted-foreground">加载中…</p>

    <EmptyState
      v-else-if="isEmpty && !error"
      data-test="models-empty"
      title="还没有接入自己的模型"
      description="添加一把 API Key 后，建任务时就能选择它；不添加则一切照旧使用平台默认模型。"
    />

    <ul
      v-else
      class="divide-y divide-border rounded-lg border border-border"
      data-test="models-list"
    >
      <li
        v-for="credential in credentials"
        :key="credential.credential_id"
        class="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-sm"
        :data-test="`model-row-${credential.credential_id}`"
      >
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-1.5">
            <Cpu class="size-3.5 text-muted-foreground" />
            <span class="font-medium">{{ credential.name }}</span>
            <Badge v-if="credential.is_default" variant="default">默认</Badge>
            <Badge :variant="statusOf(credential).variant">{{ statusOf(credential).label }}</Badge>
          </div>
          <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span class="font-medium text-foreground">{{ credential.provider_label }}</span>
            <span>{{ credential.model }}</span>
            <code class="rounded bg-muted px-1 py-0.5">{{ credential.masked_key }}</code>
          </div>
          <div
            v-if="credential.provider === 'custom'"
            class="mt-0.5 truncate text-xs text-muted-foreground"
          >
            {{ credential.base_url }}
          </div>
          <p v-if="credential.last_error" class="mt-0.5 text-xs text-warning">
            {{ credential.last_error }}
          </p>
        </div>

        <div class="flex shrink-0 flex-wrap items-center gap-1">
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
            variant="ghost"
            size="sm"
            :disabled="busyId === credential.credential_id"
            :data-test="
              confirmDeleteId === credential.credential_id
                ? `model-confirm-delete-${credential.credential_id}`
                : `model-delete-${credential.credential_id}`
            "
            @click="removeCredential(credential)"
          >
            <Trash2 />
            {{ confirmDeleteId === credential.credential_id ? "确认删除？" : "删除" }}
          </Button>
        </div>
      </li>
    </ul>

    <p v-if="!isEmpty" class="mt-3 text-xs text-muted-foreground">
      删除凭据不会影响历史任务：任务里保存了当时使用的模型快照。
    </p>
  </section>
</template>
