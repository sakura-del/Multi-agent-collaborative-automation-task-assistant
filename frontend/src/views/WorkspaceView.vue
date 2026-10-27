<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { Check, Copy, Plus, Users } from "lucide-vue-next";

import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { Badge, type BadgeVariants } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  acceptInvite,
  createInvite,
  createWorkspace,
  listWorkspaces,
} from "@/api/collaboration";
import type { Workspace, WorkspaceRole } from "@/api/types";
import { deriveSlugOrFallback } from "@/utils/workspace-slug";
import { formatDateTime } from "@/utils/task-status";

/**
 * 团队工作区（Phase 2）：我的团队列表 + 创建 + 用邀请码加入。
 *
 * ## 席位是计费单位
 *
 * `seat_used / seat_limit` 必须显式展示 —— 机构版按席位收费，用户要能自己
 * 看到「还能拉几个人」。这也是产品差异点（竞品常把席位藏在后台）。
 *
 * ## 为什么邀请码显示后要能复制
 *
 * SMTP 未配齐，邀请靠复制粘贴传递。码**只在生成时返回一次**（后端不提供
 * 列表接口），所以生成后必须立刻给复制按钮，否则用户关掉弹层就丢了。
 *
 * ⚠️ 测试契约：`workspaces-empty` / `workspaces-error` / `workspace-row-<id>` /
 * `workspace-create-form` / `workspace-invite-input` / `workspace-invite-submit` /
 * `workspace-new-invite`。
 */
const workspaces = ref<Workspace[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);

const showCreate = ref(false);
const newName = ref("");
const newSlug = ref("");
const newSeatLimit = ref(5);
const creating = ref(false);

const inviteCode = ref("");
const joining = ref(false);

/**
 * 用户是否手动编辑过 slug。
 *
 * **为什么需要这个标记**：自动派生挂在 name 的 `@blur` 上，而自动化工具
 * （Playwright `fill()`）会「先填 name → 焦点转移触发 blur → 再填 slug」，
 * 此时派生逻辑会与输入竞争，实测把 slug 拼成
 * `e2e-1792583811206-mobile-chromee2e-team-...`。
 *
 * 真实用户也会遇到类似情况（填了名字又回头改 slug，再改名字时 slug 被覆盖）。
 * 用「用户动过就不再自动改」这个规则，行为可预测。
 */
const slugTouched = ref(false);

/** 刚生成的邀请码（只显示一次）。 */
const generatedInvite = ref<{
  workspaceId: string;
  code: string;
  role: string;
  expiresAt: string | null;
} | null>(null);
const copied = ref(false);

const ROLE_LABELS: Record<WorkspaceRole, string> = {
  owner: "拥有者",
  admin: "管理员",
  member: "成员",
  viewer: "只读",
};

const ROLE_TONE: Record<WorkspaceRole, NonNullable<BadgeVariants["variant"]>> = {
  owner: "default",
  admin: "secondary",
  member: "muted",
  viewer: "outline",
};

const isEmpty = computed(() => workspaces.value.length === 0);

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    workspaces.value = await listWorkspaces();
  } catch (err) {
    error.value = (err as Error).message || "工作区加载失败";
  } finally {
    loading.value = false;
  }
}

/**
 * 从名字派生 slug。
 *
 * ## 为什么用 `watch` 而不是 `@input`
 *
 * 原先挂在 `@input="syncSlug"` 上，`syncSlug` 读 `newName` 这个 ref ——
 * 而浏览器的执行顺序是**模板 `@input` 监听器先于 `v-model` 的写入**
 * （模板事件在 `mountElement` 阶段注册，`vModelText` 指令的监听在 `created`
 * 钩子里更晚）。所以读到的永远是**上一个字符**：
 *
 * - `fill()`（整段值 + 单次 input）→ 读到空串 → slug 为空 → 创建报
 *   「请填写团队名与标识」（实测截图确认）；
 * - 逐字输入 → slug 永远少最后一个字符（实测断言 `'ab' !== 'abc'`）。
 *
 * 既有测试用「setValue 后再 trigger('input') 一次」掩盖了它。
 *
 * `watch` 在 ref 更新**之后**执行，与事件顺序解耦，且对粘贴 / 自动填充 /
 * 程序化赋值都成立。
 *
 * ## 规则：用户手动改过 slug 就不再自动覆盖
 *
 * 保留原设计（用户回头改名字不该冲掉自己起的标识）。
 */
watch(newName, (value) => {
  if (slugTouched.value) {
    return;
  }
  // 纯中文名推导为空 → 给兜底，保证「只填名字也能创建」
  newSlug.value = value.trim() ? deriveSlugOrFallback(value) : "";
});

/** 用户手动输入 slug → 标记为已编辑，后续不再自动派生。 */
function onSlugInput() {
  slugTouched.value = true;
}

async function submitCreate() {
  if (!newName.value.trim() || !newSlug.value.trim()) {
    error.value = "请填写团队名与标识";
    return;
  }
  creating.value = true;
  error.value = null;
  try {
    const workspace = await createWorkspace({
      name: newName.value.trim(),
      slug: newSlug.value.trim(),
      seat_limit: newSeatLimit.value,
    });
    success.value = `已创建「${workspace.name}」`;
    showCreate.value = false;
    newName.value = "";
    newSlug.value = "";
    newSeatLimit.value = 5;
    slugTouched.value = false;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "创建失败";
  } finally {
    creating.value = false;
  }
}

async function submitJoin() {
  const code = inviteCode.value.trim();
  if (!code) {
    error.value = "请粘贴邀请码";
    return;
  }
  joining.value = true;
  error.value = null;
  try {
    const workspace = await acceptInvite(code);
    success.value = `已加入「${workspace.name}」`;
    inviteCode.value = "";
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "加入失败";
  } finally {
    joining.value = false;
  }
}

async function generateInvite(workspaceId: string, role: "member" | "viewer" = "member") {
  error.value = null;
  copied.value = false;
  try {
    const invite = await createInvite(workspaceId, role);
    generatedInvite.value = {
      workspaceId,
      code: invite.code,
      role: invite.role,
      // 有效期必须带上：接口返回了但原页面没显示，用户会以为永久有效
      expiresAt: invite.expires_at ?? null,
    };
  } catch (err) {
    error.value = (err as Error).message || "生成邀请码失败";
  }
}

async function copyCode() {
  if (!generatedInvite.value) {
    return;
  }
  try {
    await navigator.clipboard.writeText(generatedInvite.value.code);
    copied.value = true;
  } catch {
    // 剪贴板被拒（非 HTTPS / 无权限）：码已经显示在页面上，用户可以手动选中复制
    error.value = "复制失败，请手动选中邀请码复制";
  }
}

/** 席位用量文案：满员要显式提示，否则用户不知道为什么加不进来。 */
function seatText(workspace: Workspace): string {
  const full = workspace.seat_used >= workspace.seat_limit;
  return full
    ? `席位已满 ${workspace.seat_used}/${workspace.seat_limit}`
    : `席位 ${workspace.seat_used}/${workspace.seat_limit}`;
}

/** 席位是否已满（已满时不能生成邀请码 —— 生成了也兑不了）。 */
function isSeatFull(workspace: Workspace): boolean {
  return workspace.seat_used >= workspace.seat_limit;
}

/** 席位占用百分比（夹在 0–100，避免上限为 0 时除零）。 */
function seatPercent(workspace: Workspace): number {
  if (!workspace.seat_limit) {
    return 0;
  }
  return Math.min(100, Math.round((workspace.seat_used / workspace.seat_limit) * 100));
}

/** 汇总：团队数 / 席位占用 / 已满团队数。 */
const summary = computed(() => {
  let seatsUsed = 0;
  let seatsLimit = 0;
  let fullTeams = 0;
  for (const item of workspaces.value) {
    seatsUsed += item.seat_used;
    seatsLimit += item.seat_limit;
    if (item.seat_used >= item.seat_limit) {
      fullTeams += 1;
    }
  }
  return { teams: workspaces.value.length, seatsUsed, seatsLimit, fullTeams };
});
</script>

<template>
  <section>
    <PageHeader
      eyebrow="Team workspaces"
      title="团队"
      description="团队工作区内的任务对所有成员可见；导师可作为「只读」成员查看并批注。"
    >
      <Button variant="secondary" size="sm" @click="showCreate = !showCreate">
        <Plus />
        创建团队
      </Button>
    </PageHeader>

    <ErrorBanner :message="error" testid="workspaces-error" />

    <!-- 汇总：机构版**按席位计费**，用户要能一眼看到自己一共占了多少席位。 -->
    <div
      v-if="!isEmpty"
      data-test="workspace-summary"
      class="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3"
    >
      <div class="rounded-lg border border-border bg-card p-3">
        <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">团队</p>
        <p class="mt-1 text-2xl font-semibold text-foreground tabular-nums">{{ summary.teams }}</p>
      </div>
      <div class="rounded-lg border border-border bg-card p-3">
        <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          席位占用
        </p>
        <p class="mt-1 text-2xl font-semibold text-foreground tabular-nums">
          {{ summary.seatsUsed }}
          <span class="text-sm font-normal text-muted-foreground">/ {{ summary.seatsLimit }}</span>
        </p>
      </div>
      <div
        :class="[
          'rounded-lg border p-3',
          summary.fullTeams > 0 ? 'border-warning/30 bg-warning/8' : 'border-border bg-card',
        ]"
      >
        <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          席位已满
        </p>
        <p
          :class="[
            'mt-1 text-2xl font-semibold tabular-nums',
            summary.fullTeams > 0 ? 'text-warning' : 'text-muted-foreground',
          ]"
        >
          {{ summary.fullTeams }}
          <span class="text-sm font-normal text-muted-foreground">个团队</span>
        </p>
      </div>
    </div>

    <p
      v-if="success"
      class="mb-3 flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-sm"
      data-test="workspaces-success"
    >
      <Check class="size-4 text-primary" />
      {{ success }}
    </p>

    <!-- 创建表单 -->
    <form
      v-if="showCreate"
      class="mb-4 rounded-lg border border-border bg-card p-3"
      data-test="workspace-create-form"
      @submit.prevent="submitCreate"
    >
      <div class="grid gap-2 sm:grid-cols-3">
        <div>
          <Label for="ws-name">团队名</Label>
          <Input
            id="ws-name"
            v-model="newName"
            placeholder="例如：张三课题组"
          />
        </div>
        <div>
          <Label for="ws-slug">标识（URL 用）</Label>
          <Input
            id="ws-slug"
            v-model="newSlug"
            placeholder="zhangsan-lab"
            @input="onSlugInput"
          />
        </div>
        <div>
          <Label for="ws-seat">席位上限</Label>
          <Input id="ws-seat" v-model.number="newSeatLimit" type="number" min="1" max="500" />
        </div>
      </div>
      <div class="mt-2 flex gap-1.5">
        <Button type="submit" size="sm" :disabled="creating">
          {{ creating ? "创建中…" : "创建" }}
        </Button>
        <Button type="button" variant="ghost" size="sm" @click="showCreate = false">
          取消
        </Button>
      </div>
    </form>

    <!-- 用邀请码加入 -->
    <form
      class="mb-4 flex flex-wrap items-end gap-1.5 rounded-lg border border-border bg-card p-3"
      @submit.prevent="submitJoin"
    >
      <div class="min-w-56 flex-1">
        <Label for="ws-invite">有邀请码？粘贴加入</Label>
        <Input
          id="ws-invite"
          v-model="inviteCode"
          placeholder="邀请码（一次性）"
          data-test="workspace-invite-input"
        />
      </div>
      <Button
        type="submit"
        variant="secondary"
        size="sm"
        :disabled="joining"
        data-test="workspace-invite-submit"
      >
        {{ joining ? "加入中…" : "加入" }}
      </Button>
    </form>

    <!-- 刚生成的邀请码：只显示一次，给复制按钮 -->
    <div
      v-if="generatedInvite"
      class="mb-4 rounded-lg border border-primary/40 bg-primary/8 px-3 py-2.5"
      data-test="workspace-new-invite"
    >
      <p class="text-xs text-primary">邀请码（只显示这一次，请立即复制发给对方）</p>
      <div class="mt-1 flex flex-wrap items-center gap-2">
        <code class="rounded bg-card px-2 py-1 text-sm">{{ generatedInvite.code }}</code>
        <Badge variant="outline">{{ ROLE_LABELS[generatedInvite.role as WorkspaceRole] }}</Badge>
        <span v-if="generatedInvite.expiresAt" class="text-xs text-muted-foreground">
          有效期至 {{ formatDateTime(generatedInvite.expiresAt) }}
        </span>
        <Button variant="ghost" size="sm" @click="copyCode">
          <Copy />
          {{ copied ? "已复制" : "复制" }}
        </Button>
      </div>
    </div>

    <p v-if="loading" class="text-sm text-muted-foreground">加载中…</p>

    <EmptyState
      v-else-if="isEmpty && !error"
      data-test="workspaces-empty"
      title="还没有加入任何团队"
      description="创建一个团队并邀请同学，或粘贴导师给的邀请码加入。"
    />

    <ul v-else class="grid gap-2 sm:grid-cols-2">
      <li
        v-for="workspace in workspaces"
        :key="workspace.workspace_id"
        class="rounded-lg border border-border bg-card p-3"
        :data-test="`workspace-row-${workspace.workspace_id}`"
      >
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div class="min-w-0">
            <RouterLink
              :to="{ name: 'workspace-detail', params: { id: workspace.workspace_id } }"
              class="text-sm font-medium hover:underline"
            >
              {{ workspace.name }}
            </RouterLink>
            <p class="mt-0.5 text-xs text-muted-foreground">{{ workspace.slug }}</p>
          </div>
          <Badge v-if="workspace.my_role" :variant="ROLE_TONE[workspace.my_role]">
            {{ ROLE_LABELS[workspace.my_role] }}
          </Badge>
        </div>
        <div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span class="inline-flex items-center gap-1 tabular-nums">
            <Users class="size-3.5" />
            {{ seatText(workspace) }}
          </span>
          <Badge v-if="workspace.seat_used >= workspace.seat_limit" variant="warning">
            需扩容
          </Badge>
        </div>
        <!-- 席位进度条：席位是计费单位，「还剩几个」比纯数字更快看出来 -->
        <div
          class="mt-1.5 h-1 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          :aria-valuenow="workspace.seat_used"
          aria-valuemin="0"
          :aria-valuemax="workspace.seat_limit"
          :aria-label="`${workspace.name} 席位占用`"
        >
          <div
            :class="[
              'h-full rounded-full transition-[width] duration-200',
              isSeatFull(workspace) ? 'bg-warning' : 'bg-primary/60',
            ]"
            :style="{ width: `${seatPercent(workspace)}%` }"
          />
        </div>
        <div class="mt-2 flex gap-1.5">
          <!-- 席位已满时禁用：生成的码对方也兑不了（accept 会因超席位被拒），
               详情页早已如此处理，列表页原来漏了。 -->
          <Button
            v-if="workspace.my_role === 'owner' || workspace.my_role === 'admin'"
            variant="secondary"
            size="sm"
            :disabled="isSeatFull(workspace)"
            :title="isSeatFull(workspace) ? '席位已满，扩容后才能邀请' : '生成一次性邀请码'"
            @click="generateInvite(workspace.workspace_id)"
          >
            生成邀请码
          </Button>
          <Button variant="ghost" size="sm" as-child>
            <RouterLink
              :to="{ name: 'workspace-detail', params: { id: workspace.workspace_id } }"
            >
              管理成员
            </RouterLink>
          </Button>
        </div>
      </li>
    </ul>
  </section>
</template>
