<script setup lang="ts">
/**
 * 后台管理控制台（Issue #12）。
 *
 * ## 布局取舍（按 2026 仪表盘设计共识）
 *
 * - 首屏只放 6 张 KPI 卡（一屏看全，不滚动才能形成「一眼掌握」）；
 * - 下方 12 列栅格里放「任务 / 用户」两张表，按重要性：任务在前；
 * - 颜色只表状态且必须配文字（色盲用户 + 打印场景），加载用骨架屏不是转圈。
 *
 * ## 只读原则
 *
 * 管理端不代用户操作任务：没有「帮用户取消/重试」按钮。需要处置时去用户侧
 * 走已有命令，责任边界清晰。这里只做「看清」。
 *
 * ⚠️ 测试契约：`admin-days-<n>` / `admin-kpi-users` / `admin-kpi-tasks` /
 * `admin-kpi-failure` / `admin-kpi-pending` / `admin-kpi-tokens` /
 * `admin-kpi-byok` / `admin-tab-tasks` / `admin-tab-users` /
 * `admin-task-search` / `admin-task-status` / `admin-task-row` /
 * `admin-user-search` / `admin-user-role` / `admin-user-row` /
 * `admin-prev` / `admin-next` / `admin-total` / `admin-error`。
 */
import { computed, onMounted, ref, useId, watch } from "vue";
import { Motion, useReducedMotion } from "motion-v";
import { Loader2, RefreshCw } from "lucide-vue-next";

import {
  getAdminOverview,
  listAdminTasks,
  listAdminUsers,
  type AdminOverview,
  type AdminTaskRow,
  type AdminUserRow,
} from "@/api/admin";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, StatCard } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import { useDelayedLoading } from "@/composables/useDelayedLoading";
import { SPRING_LAYOUT } from "@/lib/ease";
import { toastSuccess } from "@/lib/toast";
import { formatDateTime } from "@/utils/task-status";

const DAY_OPTIONS = [7, 30, 90] as const;
const PAGE_SIZE = 20;
const instanceId = useId();
const reduced = useReducedMotion();

// ===== KPI =====
const overview = ref<AdminOverview | null>(null);
const days = ref<number>(7);
const overviewLoading = ref(true);
const showOverviewSkeleton = useDelayedLoading(overviewLoading);
const error = ref("");

// ===== 表 =====
type Tab = "tasks" | "users";
const tab = ref<Tab>("tasks");

const tasks = ref<AdminTaskRow[]>([]);
const taskTotal = ref(0);
const taskOffset = ref(0);
const taskQuery = ref("");
const taskStatus = ref("");
const tasksLoading = ref(true);
const showTasksSkeleton = useDelayedLoading(tasksLoading);
const tasksLoaded = ref(false);

const users = ref<AdminUserRow[]>([]);
const userTotal = ref(0);
const userOffset = ref(0);
const userQuery = ref("");
const userRole = ref<"" | "admin" | "user">("");
const usersLoading = ref(false);
const showUsersSkeleton = useDelayedLoading(usersLoading);
const usersLoaded = ref(false);

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "planning", label: "规划中" },
  { value: "waiting_approval", label: "待确认" },
  { value: "running", label: "执行中" },
  { value: "reviewing", label: "复盘中" },
  { value: "paused", label: "已暂停" },
  { value: "retrying", label: "重试中" },
  { value: "waiting_human", label: "待人工" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
] as const;

const STATUS_LABEL: Record<string, string> = {
  created: "已创建",
  planning: "规划中",
  waiting_approval: "待确认",
  running: "执行中",
  reviewing: "复盘中",
  paused: "已暂停",
  retrying: "重试中",
  waiting_human: "待人工",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

/** 状态色只表语义且都带文字；不用纯色点。 */
function statusClass(status: string): string {
  if (status === "completed") {
    return "border-emerald-500/40 text-emerald-700 dark:text-emerald-400 bg-emerald-500/10";
  }
  if (status === "failed") return "border-destructive/40 text-destructive bg-destructive/10";
  if (status === "waiting_approval" || status === "waiting_human")
    return "border-amber-500/40 text-amber-700 dark:text-amber-400 bg-amber-500/10";
  if (status === "cancelled") return "border-border text-muted-foreground bg-muted";
  return "border-primary/30 text-primary bg-primary/5";
}

function percent(value: number | undefined): string {
  return `${((value ?? 0) * 100).toFixed(1)}%`;
}

function formatNumber(value: number | undefined): string {
  return (value ?? 0).toLocaleString("zh-CN");
}

const taskPage = computed(() => Math.floor(taskOffset.value / PAGE_SIZE) + 1);
const taskPages = computed(() => Math.max(1, Math.ceil(taskTotal.value / PAGE_SIZE)));
const userPage = computed(() => Math.floor(userOffset.value / PAGE_SIZE) + 1);
const userPages = computed(() => Math.max(1, Math.ceil(userTotal.value / PAGE_SIZE)));

async function loadOverview(): Promise<void> {
  overviewLoading.value = true;
  error.value = "";
  try {
    overview.value = await getAdminOverview(days.value);
  } catch (err) {
    error.value = (err as Error).message || "概览加载失败";
  } finally {
    overviewLoading.value = false;
  }
}

async function loadTasks(): Promise<void> {
  tasksLoading.value = true;
  try {
    const page = await listAdminTasks({
      status: taskStatus.value || undefined,
      q: taskQuery.value.trim() || undefined,
      limit: PAGE_SIZE,
      offset: taskOffset.value,
    });
    tasks.value = page.items;
    taskTotal.value = page.total;
    tasksLoaded.value = true;
  } catch (err) {
    error.value = (err as Error).message || "任务列表加载失败";
  } finally {
    tasksLoading.value = false;
  }
}

async function loadUsers(): Promise<void> {
  usersLoading.value = true;
  try {
    const page = await listAdminUsers({
      q: userQuery.value.trim() || undefined,
      role: userRole.value || undefined,
      limit: PAGE_SIZE,
      offset: userOffset.value,
    });
    users.value = page.items;
    userTotal.value = page.total;
    usersLoaded.value = true;
  } catch (err) {
    error.value = (err as Error).message || "用户列表加载失败";
  } finally {
    usersLoading.value = false;
  }
}

/** 筛选变化：从第一页重新查，避免停在越界页看到空表。 */
function applyTaskFilter(): void {
  taskOffset.value = 0;
  void loadTasks();
}

function applyUserFilter(): void {
  userOffset.value = 0;
  void loadUsers();
}

function onTaskPageChange(page: number): void {
  taskOffset.value = (page - 1) * PAGE_SIZE;
  void loadTasks();
}

function onUserPageChange(page: number): void {
  userOffset.value = (page - 1) * PAGE_SIZE;
  void loadUsers();
}

async function refreshManual(): Promise<void> {
  await Promise.all([
    loadOverview(),
    tab.value === "tasks" ? loadTasks() : loadUsers(),
  ]);
  if (!error.value) {
    toastSuccess("已刷新后台数据");
  }
}

watch(days, () => void loadOverview());
watch(tab, (value) => {
  if (value === "tasks" && tasks.value.length === 0) void loadTasks();
  if (value === "users" && users.value.length === 0) void loadUsers();
});

onMounted(async () => {
  await Promise.all([loadOverview(), loadTasks()]);
});
</script>

<template>
  <div data-test="admin-console">
    <PageHeader
      eyebrow="管理后台"
      title="后台管理"
      description="全站概览与跨用户任务/用户查询（只读）。仅管理员可见。"
    />

    <div class="mb-4 flex flex-wrap items-center gap-2">
      <Button
        v-for="option in DAY_OPTIONS"
        :key="option"
        type="button"
        :variant="days === option ? 'default' : 'outline'"
        size="sm"
        :data-test="`admin-days-${option}`"
        @click="days = option"
      >
        近 {{ option }} 天
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        class="ml-auto"
        :disabled="overviewLoading || tasksLoading || usersLoading"
        @click="refreshManual"
      >
        <RefreshCw
          class="size-3.5"
          :class="overviewLoading || tasksLoading || usersLoading ? 'animate-spin motion-reduce:animate-none' : ''"
          aria-hidden="true"
        />
        刷新
      </Button>
    </div>

    <ErrorBanner data-test="admin-error" :message="error" class="mb-4" @dismiss="error = ''" />

    <!-- KPI 条：一屏看全，数字同快照 -->
    <section class="mb-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <StatCard
        v-if="overview && !overviewLoading"
        label="用户总数"
        :value="formatNumber(overview.users.total)"
        :hint="`窗口新增 ${formatNumber(overview.users.new_in_window)} · 活跃 ${formatNumber(overview.users.active_in_window)}`"
        value-testid="admin-kpi-users"
      />
      <StatCard
        v-if="overview && !overviewLoading"
        label="任务总数"
        :value="formatNumber(overview.tasks.total)"
        :hint="`窗口内新增 ${formatNumber(overview.tasks.created_in_window)}`"
        value-testid="admin-kpi-tasks"
      />
      <StatCard
        v-if="overview && !overviewLoading"
        label="失败率（窗口）"
        :value="percent(overview.tasks.failure_rate)"
        :hint="`${formatNumber(overview.tasks.failed_in_window)} / ${formatNumber(overview.tasks.finished_in_window)} 已结束`"
        value-testid="admin-kpi-failure"
      />
      <StatCard
        v-if="overview && !overviewLoading"
        label="待处理任务"
        :value="formatNumber(overview.tasks.pending_action)"
        hint="待确认规划 + 待人工"
        value-testid="admin-kpi-pending"
      />
      <StatCard
        v-if="overview && !overviewLoading"
        label="Token 消耗（窗口）"
        :value="formatNumber(overview.tokens_used_in_window)"
        hint="按任务实际用量汇总"
        value-testid="admin-kpi-tokens"
      />
      <StatCard
        v-if="overview && !overviewLoading"
        label="BYOK 任务占比"
        :value="percent(overview.byok_ratio)"
        :hint="`${formatNumber(overview.byok_tasks_in_window)} 个任务 · ${formatNumber(overview.active_credentials)} 把活跃凭据`"
        value-testid="admin-kpi-byok"
      />
      <!-- 骨架屏：等宽占位，避免数字跳变 -->
      <div
        v-for="index in showOverviewSkeleton && overviewLoading ? 6 : 0"
        :key="`skeleton-${index}`"
        class="h-[86px] animate-pulse rounded-xl border border-border bg-muted/40 motion-reduce:animate-none"
      />
    </section>

    <Card>
      <CardHeader class="pb-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <CardTitle class="text-sm">全站数据</CardTitle>
          <div class="inline-flex rounded-md border border-border p-0.5">
            <button
              type="button"
              data-test="admin-tab-tasks"
              class="relative rounded px-2.5 py-1 text-xs transition-colors"
              :class="tab === 'tasks' ? 'font-medium text-primary' : 'text-muted-foreground hover:text-foreground'"
              @click="tab = 'tasks'"
            >
              <Motion
                v-if="tab === 'tasks'"
                :layout-id="reduced ? undefined : `admin-tab-${instanceId}`"
                :transition="SPRING_LAYOUT"
                aria-hidden="true"
                class="absolute inset-0 rounded bg-primary/10"
              />
              <span class="relative z-10">任务</span>
            </button>
            <button
              type="button"
              data-test="admin-tab-users"
              class="relative rounded px-2.5 py-1 text-xs transition-colors"
              :class="tab === 'users' ? 'font-medium text-primary' : 'text-muted-foreground hover:text-foreground'"
              @click="tab = 'users'"
            >
              <Motion
                v-if="tab === 'users'"
                :layout-id="reduced ? undefined : `admin-tab-${instanceId}`"
                :transition="SPRING_LAYOUT"
                aria-hidden="true"
                class="absolute inset-0 rounded bg-primary/10"
              />
              <span class="relative z-10">用户</span>
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <template v-if="tab === 'tasks'">
          <!-- ===== 任务 ===== -->
          <div class="mb-3 flex flex-wrap items-center gap-2">
            <select
              v-model="taskStatus"
              data-test="admin-task-status"
              class="h-8 rounded-md border border-border bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              @change="applyTaskFilter"
            >
              <option v-for="option in STATUS_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
            <Input
              v-model="taskQuery"
              data-test="admin-task-search"
              class="h-8 w-56 text-xs"
              placeholder="按标题搜索（回车）"
              @keyup.enter="applyTaskFilter"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              @click="applyTaskFilter"
            >
              查询
            </Button>
            <span data-test="admin-total" class="ml-auto text-xs text-muted-foreground">
              {{ tasksLoaded ? `共 ${formatNumber(taskTotal)} 条` : "加载中…" }}
            </span>
          </div>

          <div v-if="showTasksSkeleton" class="space-y-2">
            <div
              v-for="index in 5"
              :key="`task-skeleton-${index}`"
              class="h-9 animate-pulse rounded bg-muted/40 motion-reduce:animate-none"
            />
          </div>
          <EmptyState
            v-else-if="!tasksLoading && !showTasksSkeleton && tasks.length === 0"
            title="没有符合条件的任务"
            description="换个状态或关键词试试。"
          />
          <div v-else-if="!tasksLoading && !showTasksSkeleton" class="overflow-x-auto">
            <table class="w-full min-w-[46rem] text-sm">
              <thead>
                <tr class="border-b border-border text-left text-xs text-muted-foreground">
                  <th class="py-2 pr-3 font-medium whitespace-nowrap">任务</th>
                  <th class="py-2 pr-3 font-medium whitespace-nowrap">所有者</th>
                  <th class="py-2 pr-3 font-medium whitespace-nowrap">状态</th>
                  <th class="py-2 pr-3 font-medium whitespace-nowrap">模型</th>
                  <th class="py-2 pr-3 text-right font-medium whitespace-nowrap">Token</th>
                  <th class="py-2 font-medium whitespace-nowrap">创建时间</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in tasks"
                  :key="row.task_id"
                  data-test="admin-task-row"
                  class="border-b border-border/60 last:border-0"
                >
                  <td class="max-w-[16rem] py-2 pr-3 xl:max-w-[20rem]">
                    <p class="truncate" :title="row.title">{{ row.title }}</p>
                    <p class="truncate font-mono text-[10px] text-muted-foreground">
                      {{ row.task_id.slice(0, 8) }}
                    </p>
                  </td>
                  <td class="py-2 pr-3 text-xs whitespace-nowrap">{{ row.owner_username }}</td>
                  <td class="py-2 pr-3 whitespace-nowrap">
                    <span
                      class="inline-flex shrink-0 items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px]"
                      :class="statusClass(row.status)"
                    >
                      {{ STATUS_LABEL[row.status] ?? row.status }}
                    </span>
                  </td>
                  <td class="max-w-[13rem] py-2 pr-3 text-xs">
                    <div class="flex items-center gap-1 min-w-0">
                      <span v-if="row.is_byok" class="shrink-0 rounded bg-primary/10 px-1 py-0.5 text-[10px] text-primary">
                        BYOK
                      </span>
                      <span class="truncate text-muted-foreground" :title="row.model_label ?? '平台模型'">
                        {{ row.model_label ?? "平台模型" }}
                      </span>
                    </div>
                  </td>
                  <td class="py-2 pr-3 text-right text-xs whitespace-nowrap tabular-nums">
                    {{ formatNumber(row.used_tokens) }}
                  </td>
                  <td class="py-2 text-xs whitespace-nowrap text-muted-foreground tabular-nums">
                    {{ formatDateTime(row.created_at) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="mt-3 flex justify-end">
            <Pagination
              :model-value="taskPage"
              :total-pages="taskPages"
              :disabled="tasksLoading"
              prev-testid="admin-prev"
              next-testid="admin-next"
              aria-label="任务列表分页"
              @change="onTaskPageChange"
            />
          </div>
        </template>

        <template v-else>
          <!-- ===== 用户 ===== -->
          <div class="mb-3 flex flex-wrap items-center gap-2">
            <select
              v-model="userRole"
              data-test="admin-user-role"
              class="h-8 rounded-md border border-border bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              @change="applyUserFilter"
            >
              <option value="">全部角色</option>
              <option value="admin">管理员</option>
              <option value="user">普通用户</option>
            </select>
            <Input
              v-model="userQuery"
              data-test="admin-user-search"
              class="h-8 w-56 text-xs"
              placeholder="用户名 / 邮箱（回车）"
              @keyup.enter="applyUserFilter"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              @click="applyUserFilter"
            >
              查询
            </Button>
            <span class="ml-auto text-xs text-muted-foreground">
              {{ usersLoaded ? `共 ${formatNumber(userTotal)} 人` : "加载中…" }}
            </span>
          </div>

          <div v-if="showUsersSkeleton" class="space-y-2">
            <div
              v-for="index in 5"
              :key="`user-skeleton-${index}`"
              class="h-9 animate-pulse rounded bg-muted/40 motion-reduce:animate-none"
            />
          </div>
          <EmptyState
            v-else-if="!usersLoading && !showUsersSkeleton && users.length === 0"
            title="没有符合条件的用户"
            description="换个角色或关键词试试。"
          />
          <div v-else-if="!usersLoading && !showUsersSkeleton" class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border text-left text-xs text-muted-foreground">
                  <th class="py-2 pr-3 font-medium">用户</th>
                  <th class="py-2 pr-3 font-medium">角色</th>
                  <th class="py-2 pr-3 font-medium">状态</th>
                  <th class="py-2 pr-3 font-medium">套餐</th>
                  <th class="py-2 pr-3 text-right font-medium">任务</th>
                  <th class="py-2 pr-3 text-right font-medium">凭据</th>
                  <th class="py-2 font-medium">注册时间</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in users"
                  :key="row.user_id"
                  data-test="admin-user-row"
                  class="border-b border-border/60 last:border-0"
                >
                  <td class="py-2 pr-3">
                    <p class="text-xs font-medium">{{ row.username }}</p>
                    <p class="truncate text-[10px] text-muted-foreground">{{ row.email }}</p>
                  </td>
                  <td class="py-2 pr-3">
                    <Badge :variant="row.role === 'admin' ? 'default' : 'secondary'" class="text-[10px]">
                      {{ row.role === "admin" ? "管理员" : "用户" }}
                    </Badge>
                  </td>
                  <td class="py-2 pr-3 text-xs">
                    <span :class="row.is_active ? 'text-emerald-700 dark:text-emerald-400' : 'text-destructive'">
                      {{ row.is_active ? "正常" : "已停用" }}
                    </span>
                  </td>
                  <td class="py-2 pr-3 text-xs text-muted-foreground">
                    {{ row.plan_name ?? "—" }}
                  </td>
                  <td class="py-2 pr-3 text-right text-xs tabular-nums">
                    {{ formatNumber(row.task_count) }}
                  </td>
                  <td class="py-2 pr-3 text-right text-xs tabular-nums">
                    {{ formatNumber(row.credential_count) }}
                  </td>
                  <td class="py-2 text-xs text-muted-foreground">
                    {{ formatDateTime(row.created_at) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="mt-3 flex justify-end">
            <Pagination
              :model-value="userPage"
              :total-pages="userPages"
              :disabled="usersLoading"
              prev-testid="admin-user-prev"
              next-testid="admin-user-next"
              aria-label="用户列表分页"
              @change="onUserPageChange"
            />
          </div>
        </template>

        <p v-if="tab === 'tasks' && tasksLoading" class="sr-only">
          <Loader2 class="animate-spin motion-reduce:animate-none" /> 加载中
        </p>
      </CardContent>
    </Card>
  </div>
</template>
