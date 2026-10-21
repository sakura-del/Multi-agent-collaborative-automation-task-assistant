<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ArrowLeft, Copy, UserMinus } from "lucide-vue-next";
import { useRoute } from "vue-router";

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
const workspaceId = computed(() => String(route.params.id ?? ""));

const workspace = ref<Workspace | null>(null);
const members = ref<WorkspaceMember[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);
const busyUserId = ref<string | null>(null);

const generatedInvite = ref<{ code: string; role: string } | null>(null);
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

async function handleLeave() {
  error.value = null;
  try {
    await leaveWorkspace(workspaceId.value);
    success.value = "已退出该团队";
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "退出失败";
  }
}

async function handleGenerateInvite() {
  error.value = null;
  copied.value = false;
  try {
    const invite = await createInvite(workspaceId.value, inviteRole.value);
    generatedInvite.value = { code: invite.code, role: invite.role };
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
        <span v-if="seatFull" class="text-amber-600">（已满，需扩容才能邀请）</span>
      </p>
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

    <section v-else class="rounded-lg border border-border">
      <h2 class="border-b border-border px-3 py-2 text-sm font-medium">成员（席位）</h2>
      <ul class="divide-y divide-border">
        <li
          v-for="member in members"
          :key="member.user_id"
          class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
          :data-test="`member-row-${member.user_id}`"
        >
          <div class="flex min-w-0 flex-wrap items-center gap-2">
            <span class="font-medium">{{ member.username }}</span>
            <span class="text-xs text-muted-foreground">{{ member.email }}</span>
            <Badge
              :variant="ROLE_TONE[member.role]"
              :data-test="`member-role-${member.user_id}`"
            >
              {{ ROLE_LABELS[member.role] }}
            </Badge>
            <span class="text-xs text-muted-foreground">
              加入于 {{ formatDate(member.joined_at) }}
            </span>
          </div>
          <div class="flex items-center gap-1.5">
            <select
              v-if="canEditRole(member)"
              :value="member.role"
              class="rounded-md border border-border bg-background px-2 py-1 text-xs"
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

    <div v-if="myRole && myRole !== 'owner'" class="mt-4">
      <Button variant="ghost" size="sm" data-test="workspace-leave" @click="handleLeave">
        退出该团队
      </Button>
    </div>
  </section>
</template>
