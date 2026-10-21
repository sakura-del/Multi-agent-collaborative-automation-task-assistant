<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Check, Copy, KeyRound, Plus, Trash2 } from "lucide-vue-next";

import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createApiKey, listApiKeys, revokeApiKey } from "@/api/api-keys";
import type { ApiKeyInfo, ApiKeyScope } from "@/api/types";

/**
 * API Key 管理（Phase 2 · 开放 API）。
 *
 * ## 两个必须让用户理解的点
 *
 * 1. **明文只显示一次**：生成后弹窗里给出完整 Key + 复制按钮，并明确写
 *    「关掉后无法再次查看」。用户不理解这点就会关掉弹窗再来找，然后发现拿不到。
 * 2. **scope 决定 Key 能做什么**：只读 Key 只能查任务，不能建任务。
 *    最小权限能显著降低泄漏损失。
 *
 * ⚠️ 测试契约：`apikeys-empty` / `apikeys-error` / `apikeys-list` /
 * `apikey-row-<id>` / `apikey-create-form` / `apikey-name` / `apikey-scope-<scope>` /
 * `apikey-create-submit` / `apikey-created` / `apikey-copy` / `apikey-revoke-<id>`。
 */
const keys = ref<ApiKeyInfo[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);

const showCreate = ref(false);
const newName = ref("");
const selectedScopes = ref<ApiKeyScope[]>(["tasks:read", "tasks:write"]);
const expiresInDays = ref<number | undefined>(undefined);
const creating = ref(false);

/** 刚生成的明文（只显示一次，关闭后清空）。 */
const createdKey = ref<string | null>(null);
const copied = ref(false);

const busyId = ref<string | null>(null);

/** scope 的中文说明（用户不该去读文档才知道每个 scope 干什么）。 */
const SCOPE_INFO: { value: ApiKeyScope; label: string; hint: string }[] = [
  { value: "tasks:read", label: "读取任务", hint: "列出任务、查看状态与事件" },
  { value: "tasks:write", label: "创建与控制任务", hint: "建任务、确认规划、取消/重试" },
  { value: "artifacts:read", label: "下载产出", hint: "Markdown / DOCX / .bib / .ris" },
  { value: "annotations:write", label: "写批注", hint: "给任务加导师批注" },
];

const isEmpty = computed(() => keys.value.length === 0);

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    keys.value = await listApiKeys();
  } catch (err) {
    error.value = (err as Error).message || "Key 列表加载失败";
  } finally {
    loading.value = false;
  }
}

function toggleScope(scope: ApiKeyScope) {
  if (selectedScopes.value.includes(scope)) {
    selectedScopes.value = selectedScopes.value.filter((item) => item !== scope);
  } else {
    selectedScopes.value = [...selectedScopes.value, scope];
  }
}

async function submitCreate() {
  if (!newName.value.trim()) {
    error.value = "请填写 Key 名称";
    return;
  }
  if (selectedScopes.value.length === 0) {
    error.value = "至少选择一个权限范围";
    return;
  }
  creating.value = true;
  error.value = null;
  copied.value = false;
  try {
    const created = await createApiKey({
      name: newName.value.trim(),
      scopes: selectedScopes.value,
      ...(expiresInDays.value ? { expires_in_days: Number(expiresInDays.value) } : {}),
    });
    createdKey.value = created.key;
    showCreate.value = false;
    newName.value = "";
    selectedScopes.value = ["tasks:read", "tasks:write"];
    expiresInDays.value = undefined;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "生成失败";
  } finally {
    creating.value = false;
  }
}

async function copyKey() {
  if (!createdKey.value) {
    return;
  }
  try {
    await navigator.clipboard.writeText(createdKey.value);
    copied.value = true;
  } catch {
    error.value = "复制失败，请手动选中 Key 复制";
  }
}

/** 用户确认已保存后关闭弹窗（此后明文永久丢失）。 */
function dismissCreated() {
  createdKey.value = null;
  copied.value = false;
  success.value = "Key 已生成。请确认已保存，之后无法再次查看。";
}

async function handleRevoke(key: ApiKeyInfo) {
  busyId.value = key.key_id;
  error.value = null;
  try {
    await revokeApiKey(key.key_id);
    success.value = `已吊销「${key.name}」`;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "吊销失败";
  } finally {
    busyId.value = null;
  }
}

/** Key 状态：吊销 > 过期 > 正常。 */
function statusOf(key: ApiKeyInfo): { label: string; variant: "success" | "warning" | "muted" } {
  if (key.revoked_at) {
    return { label: "已吊销", variant: "muted" };
  }
  if (key.expires_at) {
    const expires = new Date(key.expires_at);
    if (!Number.isNaN(expires.getTime()) && expires.getTime() <= Date.now()) {
      return { label: "已过期", variant: "warning" };
    }
  }
  return { label: "可用", variant: "success" };
}

function formatTime(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}
</script>

<template>
  <section>
    <header class="mb-4 flex flex-wrap items-start justify-between gap-2">
      <div>
        <h1 class="text-lg font-semibold tracking-tight">API Key</h1>
        <p class="mt-0.5 text-sm text-muted-foreground">
          用 Key 从脚本、MCP 客户端或其他系统调用本服务。Key 只显示一次，请妥善保存。
        </p>
      </div>
      <Button variant="secondary" size="sm" @click="showCreate = !showCreate">
        <Plus />
        生成 Key
      </Button>
    </header>

    <ErrorBanner :message="error" testid="apikeys-error" />

    <p
      v-if="success"
      class="mb-3 flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-sm"
      data-test="apikeys-success"
    >
      <Check class="size-4 text-primary" />
      {{ success }}
    </p>

    <!-- 刚生成的明文：唯一一次展示 -->
    <div
      v-if="createdKey"
      class="mb-4 rounded-lg border border-primary/40 bg-primary/8 px-3 py-2.5"
      data-test="apikey-created"
    >
      <p class="text-xs font-medium text-primary">
        Key 已生成 —— <span class="font-semibold">只显示这一次</span>，关闭后无法再次查看
      </p>
      <div class="mt-1.5 flex flex-wrap items-center gap-2">
        <code class="break-all rounded bg-card px-2 py-1 text-xs">{{ createdKey }}</code>
        <Button variant="ghost" size="sm" data-test="apikey-copy" @click="copyKey">
          <Copy />
          {{ copied ? "已复制" : "复制" }}
        </Button>
        <Button variant="secondary" size="sm" data-test="apikey-dismiss" @click="dismissCreated">
          我已保存
        </Button>
      </div>
    </div>

    <!-- 创建表单 -->
    <form
      v-if="showCreate"
      class="mb-4 rounded-lg border border-border bg-card p-3"
      data-test="apikey-create-form"
      @submit.prevent="submitCreate"
    >
      <div class="grid gap-2 sm:grid-cols-2">
        <div>
          <Label for="apikey-name">名称（用于辨认）</Label>
          <Input
            id="apikey-name"
            v-model="newName"
            placeholder="例如：我的同步脚本"
            data-test="apikey-name"
          />
        </div>
        <div>
          <Label for="apikey-expires">有效期（天，留空=永久）</Label>
          <Input
            id="apikey-expires"
            v-model.number="expiresInDays"
            type="number"
            min="1"
            max="3650"
            placeholder="留空为永久"
          />
        </div>
      </div>

      <fieldset class="mt-2">
        <legend class="text-xs text-muted-foreground">权限范围（最小权限原则）</legend>
        <div class="mt-1 grid gap-1 sm:grid-cols-2">
          <label
            v-for="scope in SCOPE_INFO"
            :key="scope.value"
            class="flex cursor-pointer items-start gap-1.5 rounded-md border border-border px-2 py-1.5 text-sm"
          >
            <input
              type="checkbox"
              class="mt-0.5"
              :checked="selectedScopes.includes(scope.value)"
              :data-test="`apikey-scope-${scope.value}`"
              @change="toggleScope(scope.value)"
            />
            <span>
              <span class="font-medium">{{ scope.label }}</span>
              <span class="block text-xs text-muted-foreground">{{ scope.hint }}</span>
            </span>
          </label>
        </div>
      </fieldset>

      <div class="mt-2 flex gap-1.5">
        <Button
          type="submit"
          size="sm"
          :disabled="creating"
          data-test="apikey-create-submit"
        >
          {{ creating ? "生成中…" : "生成" }}
        </Button>
        <Button type="button" variant="ghost" size="sm" @click="showCreate = false">
          取消
        </Button>
      </div>
    </form>

    <p v-if="loading" class="text-sm text-muted-foreground">加载中…</p>

    <EmptyState
      v-else-if="isEmpty && !error"
      data-test="apikeys-empty"
      title="还没有 API Key"
      description="生成一个 Key 后，就可以从脚本或 MCP 客户端调用本服务了。"
    />

    <ul v-else class="divide-y divide-border rounded-lg border border-border" data-test="apikeys-list">
      <li
        v-for="key in keys"
        :key="key.key_id"
        class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
        :data-test="`apikey-row-${key.key_id}`"
      >
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-1.5">
            <KeyRound class="size-3.5 text-muted-foreground" />
            <span class="font-medium">{{ key.name }}</span>
            <Badge :variant="statusOf(key).variant">{{ statusOf(key).label }}</Badge>
            <code class="rounded bg-muted px-1 py-0.5 text-xs">{{ key.prefix }}…</code>
          </div>
          <div class="mt-0.5 flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span>{{ key.scopes.join(" · ") }}</span>
            <span>最后使用：{{ formatTime(key.last_used_at) }}</span>
            <span v-if="key.expires_at">过期：{{ formatTime(key.expires_at) }}</span>
          </div>
        </div>
        <Button
          v-if="!key.revoked_at"
          variant="ghost"
          size="sm"
          :disabled="busyId === key.key_id"
          :data-test="`apikey-revoke-${key.key_id}`"
          @click="handleRevoke(key)"
        >
          <Trash2 />
          吊销
        </Button>
      </li>
    </ul>
  </section>
</template>
