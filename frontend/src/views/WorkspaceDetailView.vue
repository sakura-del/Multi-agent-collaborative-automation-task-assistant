<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ArrowLeft, Copy, UserMinus } from "lucide-vue-next";
import { useRoute, useRouter } from "vue-router";

import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Badge, type BadgeVariants } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  changeMemberRole,
  createInvite,
  getWorkspace,
  leaveWorkspace,
  listMembers,
  removeMember,
} from "@/api/collaboration";
import type { Workspace, WorkspaceMember, WorkspaceRole } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

/**
 * 工作区详情：成员（席位）管理 + 邀请。
 *
 * ## 权限决定界面，不只是后端校验
 *
 * - `owner`：可改角色、可移除任意非 owner 成员；
 * - `admin`：可移除 member/viewer，**不能**改角色、不能动其他 admin；
 * - `member` / `viewer`：只能看列表 + 退出（viewer 还能批注，但那是任务页的事）。
 *
 * 按钮该隐藏就隐藏 —— 让用户点一下再吃 403 是最差的体验。
 * 但**后端仍然必须校验**（前端隐藏只是体验，不是安全边界）。
 *
 * ⚠️ 测试契约：`workspace-detail-error` / `workspace-detail-name` /
 * `workspace-seat-usage` / `member-row-<user_id>` / `member-role-<user_id>` /
 * `member-remove-<user_id>` / `workspace-leave`。
 */
const route = useRoute();
const router = useRouter();
const workspaceId = computed(() => String(route.params.id ?? ""));

const workspace = ref<Workspace | null>(null);
const members = ref<WorkspaceMember[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);
const busyUserId = ref<string | null>(null);

const generatedInvite = ref<{ code: string; role: string; expiresAt: string | null } | null>(null);
const copied = ref(false);
const inviteRole = ref<Exclude<WorkspaceRole, "owner">>("member");

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

const myRole = computed<WorkspaceRole | null>(() => workspace.value?.my_role ?? null);
const canInvite = computed(() => myRole.value === "owner" || myRole.value === "admin");
const canChangeRole = computed(() => myRole.value === "owner");

/**
 * 当前角色**实际能做什么**，一句话说清。
 *
 * ## 为什么必须显式展示
 *
 * 权限矩阵原先只写在代码注释里（owner 可改角色 / admin 只能移除 member 与 viewer /
 * member 只能建任务 / viewer 只能批注）。用户看不到这份说明，只能靠「点一下
 * 看有没有 403」来试 —— 这是最差的权限提示方式。
 */
const ROLE_CAPABILITY: Record<WorkspaceRole, string> = {
  owner: "可改成员角色、移除成员、生成邀请码",
  admin: "可移除成员、生成邀请码（不能改角色）",
  member: "可创建与查看团队任务，可提交批注",
  viewer: "只读：可查看团队任务并提出批注，不能创建任务",
};
const myCapability = computed(() =>
  myRole.value ? ROLE_CAPABILITY[myRole.value] : "",
);
const seatFull = computed(() => {
  const current = workspace.value;
  return current ? current.seat_used >= current.seat_limit : false;
});

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    workspace.value = await getWorkspace(workspaceId.value);
    members.value = await listMembers(workspaceId.value);
  } catch (err) {
    error.value = (err as Error).message || "工作区加载失败";
  } finally {
    loading.value = false;
  }
}

/** 能否移除这个成员：owner 不可移除；admin 只能移除 member/viewer。 */
function canRemove(member: WorkspaceMember): boolean {
  if (member.role === "owner") {
    return false;
  }
  if (myRole.value === "owner") {
    return true;
  }
  if (myRole.value === "admin") {
    return member.role === "member" || member.role === "viewer";
  }
  return false;
}

/** 能否改这个成员的角色：仅 owner，且目标不是 owner 自己。 */
function canEditRole(member: WorkspaceMember): boolean {
  return canChangeRole.value && member.role !== "owner";
}

async function handleRemove(member: WorkspaceMember) {
  busyUserId.value = member.user_id;
  error.value = null;
  try {
    await removeMember(workspaceId.value, member.user_id);
    success.value = `已移除 ${member.username}`;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "移除失败";
  } finally {
    busyUserId.value = null;
  }
}

async function handleRoleChange(member: WorkspaceMember, role: string) {
  busyUserId.value = member.user_id;
  error.value = null;
  try {
    await changeMemberRole(
      workspaceId.value,
      member.user_id,
      role as Exclude<WorkspaceRole, "owner">
    );
    success.value = `已把 ${member.username} 设为${ROLE_LABELS[role as WorkspaceRole]}`;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "改角色失败";
  } finally {
    busyUserId.value = null;
  }
}

/**
 * 退出团队。
 *
 * ## 为什么成功后直接回列表，而不是 `refresh()`
 *
 * 原先成功后调 `refresh()`，而 `refresh()` 会 `GET /workspaces/{id}` ——
 * 此时用户**已不是成员**，后端按「不泄漏存在性」统一返回 404。
 * 于是「退出成功」立刻跟着一条「工作区不存在」的错误横幅（实测缺陷）。
 *
 * 正确行为：退出即离开该页 → 回团队列表。
 */
async function handleLeave() {
  error.value = null;
  try {
    await leaveWorkspace(workspaceId.value);
    await router.push({ name: "workspaces" });
  } catch (err) {
    // 退出失败要留在原页并说明原因（不能假装成功跳走）
    error.value = (err as Error).message || "退出失败";
  }
}

async function handleGenerateInvite() {
  error.value = null;
  copied.value = false;
  try {
    const invite = await createInvite(workspaceId.value, inviteRole.value);
    generatedInvite.value = {
      code: invite.code,
      role: invite.role,
      // 接口返回了有效期（默认 7 天）但原页面没显示 —— 用户会以为永久有效
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
    error.value = "复制失败，请手动选中邀请码复制";
  }
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("zh-CN");
}
</script>

<template>
  <section>
    <header class="mb-4">
      <RouterLink
        :to="{ name: 'workspaces' }"
        class="inline-flex items-center gap-1 text-xs text-muted-foreground hover:underline"
      >
        <ArrowLeft class="size-3.5" />
        返回团队列表
      </RouterLink>
      <h1 class="mt-1 text-lg font-semibold tracking-tight" data-test="workspace-detail-name">
        {{ workspace?.name ?? "工作区" }}
      </h1>
      <p
        v-if="workspace"
        class="mt-0.5 text-sm text-muted-foreground tabular-nums"
        data-test="workspace-seat-usage"
      >
        席位 {{ workspace.seat_used }}/{{ workspace.seat_limit }}
        <!-- 用 token（text-warning）而不是硬编码 amber：AGENTS §6.5.3 要求只用
             tokens，硬编码调色板在深色模式下对比度不足 -->
        <span v-if="seatFull" class="text-warning">（已满，需扩容才能邀请）</span>
      </p>

      <!-- 我的权限：把权限矩阵从代码注释搬到界面上 -->
      <div
        v-if="myRole"
        data-test="workspace-my-role"
        class="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1.5"
      >
        <Badge :variant="ROLE_TONE[myRole]">我的角色：{{ ROLE_LABELS[myRole] }}</Badge>
        <span class="text-xs text-muted-foreground">{{ myCapability }}</span>
      </div>
    </header>

    <ErrorBanner :message="error" testid="workspace-detail-error" />

    <p
      v-if="success"
      class="mb-3 rounded-md border border-border bg-card px-3 py-2 text-sm"
      data-test="workspace-detail-success"
    >
      {{ success }}
    </p>

    <!-- 邀请区 -->
    <div v-if="canInvite" class="mb-4 rounded-lg border border-border bg-card p-3">
      <div class="flex flex-wrap items-end gap-1.5">
        <div>
          <label class="text-xs text-muted-foreground" for="invite-role">受邀者角色</label>
          <select
            id="invite-role"
            v-model="inviteRole"
            class="mt-1 block rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          >
            <option value="member">成员（可创建任务）</option>
            <option value="viewer">只读（可批注）</option>
          </select>
        </div>
        <Button
          variant="secondary"
          size="sm"
          :disabled="seatFull"
          data-test="workspace-generate-invite"
          @click="handleGenerateInvite"
        >
          生成邀请码
        </Button>
      </div>

      <div
        v-if="generatedInvite"
        class="mt-2 rounded-md border border-primary/40 bg-primary/8 px-3 py-2"
        data-test="workspace-detail-invite"
      >
        <p class="text-xs text-primary">邀请码（只显示这一次，请立即复制）</p>
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
    </div>

    <p v-if="loading" class="text-sm text-muted-foreground">加载中…</p>

    <EmptyState
      v-else-if="!members.length && !error"
      title="还没有成员"
      description="生成邀请码发给同学，对方粘贴后即可加入。"
    />

    <section v-else class="overflow-hidden rounded-lg border border-border bg-card">
      <header class="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 class="text-sm font-medium">成员（席位）</h2>
        <span v-if="workspace" class="text-xs text-muted-foreground tabular-nums">
          {{ workspace.seat_used }} / {{ workspace.seat_limit }}
        </span>
      </header>
      <ul class="divide-y divide-border">
        <li
          v-for="member in members"
          :key="member.user_id"
          class="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-sm"
          :data-test="`member-row-${member.user_id}`"
        >
          <div class="flex min-w-0 items-center gap-2.5">
            <!-- 首字母头像：多人团队里比纯文字更快区分 -->
            <span
              class="grid size-7 shrink-0 place-items-center rounded-full bg-muted text-xs font-medium text-muted-foreground"
              aria-hidden="true"
            >
              {{ member.username.slice(0, 1).toUpperCase() }}
            </span>
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium text-foreground">{{ member.username }}</span>
                <Badge
                  :variant="ROLE_TONE[member.role]"
                  :data-test="`member-role-${member.user_id}`"
                >
                  {{ ROLE_LABELS[member.role] }}
                </Badge>
              </div>
              <p class="truncate text-xs text-muted-foreground">
                {{ member.email }} · 加入于 {{ formatDate(member.joined_at) }}
              </p>
            </div>
          </div>
          <div class="flex items-center gap-1.5">
            <select
              v-if="canEditRole(member)"
              :value="member.role"
              :aria-label="`修改 ${member.username} 的角色`"
              class="rounded-md border border-input bg-background px-2 py-1 text-xs"
              :disabled="busyUserId === member.user_id"
              :data-test="`member-role-select-${member.user_id}`"
              @change="
                handleRoleChange(
                  member,
                  ($event.target as HTMLSelectElement).value
                )
              "
            >
              <option value="admin">管理员</option>
              <option value="member">成员</option>
              <option value="viewer">只读</option>
            </select>
            <Button
              v-if="canRemove(member)"
              variant="ghost"
              size="sm"
              :disabled="busyUserId === member.user_id"
              :aria-label="`移除 ${member.username}`"
              :data-test="`member-remove-${member.user_id}`"
              @click="handleRemove(member)"
            >
              <UserMinus />
              移除
            </Button>
          </div>
        </li>
      </ul>
    </section>

    <!-- 危险操作独立分区：退出是不可逆的成员变更，不该和「邀请」混在一起 -->
    <section v-if="myRole" class="mt-6 rounded-lg border border-destructive/25 p-3">
      <h2 class="text-sm font-medium text-destructive">危险操作</h2>
      <div v-if="myRole !== 'owner'" class="mt-1.5 flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" data-test="workspace-leave" @click="handleLeave">
          退出该团队
        </Button>
        <span class="text-xs text-muted-foreground">
          退出后不再看到该团队的共享任务，可由拥有者重新邀请
        </span>
      </div>
      <!-- 拥有者是团队的最后一个锚点：没有转让/解散功能前，退出会留下无主团队 -->
      <p v-else class="mt-1.5 text-xs text-muted-foreground">
        你是拥有者，无法退出。团队需由拥有者保留，或联系管理员处理。
      </p>
    </section>
  </section>
</template>
