<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { Plus } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import TaskCreateForm from "@/components/tasks/TaskCreateForm.vue";
import { Button } from "@/components/ui/button";
import { useTaskStore } from "@/stores/tasks";
import type { TaskCreate } from "@/api/types";
import { formatDateTime, taskProgress } from "@/utils/task-status";

// 任务列表：创建入口 + 列表（桌面表格 / 移动单列）；创建成功后进入详情页
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
//
// ⚠️ 测试契约（不要改）：`data-test="new-task"` / `data-test="empty-state"` /
// `data-test="task-row-<id>"`。
const store = useTaskStore();
const router = useRouter();

const showForm = ref(false);
const submitting = ref(false);
const errorMessage = ref<string | null>(null);

onMounted(() => {
  void refresh();
});

async function refresh() {
  errorMessage.value = null;
  try {
    await store.loadList();
  } catch (error) {
    errorMessage.value = (error as Error).message;
  }
}

async function handleCreate(payload: TaskCreate) {
  submitting.value = true;
  errorMessage.value = null;
  try {
    const created = await store.create(payload);
    showForm.value = false;
    await router.push({ name: "task-detail", params: { taskId: created.task_id } });
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    submitting.value = false;
  }
}

function openTask(taskId: string) {
  void router.push({ name: "task-detail", params: { taskId } });
}
</script>

<template>
  <section>
    <header class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-lg font-semibold tracking-tight">任务</h1>
        <p class="mt-0.5 text-sm text-muted-foreground">共 {{ store.items.length }} 个任务</p>
      </div>
      <Button data-test="new-task" @click="showForm = !showForm">
        <Plus :size="16" aria-hidden="true" />
        <span>{{ showForm ? "收起" : "新建任务" }}</span>
      </Button>
    </header>

    <ErrorBanner :message="errorMessage" />

    <TaskCreateForm
      v-if="showForm"
      class="mb-6"
      :pending="submitting"
      :error="errorMessage"
      @submit="handleCreate"
    />

    <p v-if="store.listLoading && !store.items.length" class="text-sm text-muted-foreground">
      加载中…
    </p>

    <EmptyState
      v-else-if="!store.items.length"
      testid="empty-state"
      title="还没有任务"
      description="新建一个文献综述任务，系统会先规划再执行，可随时确认与干预。"
    >
      <template #icon>
        <Plus class="mb-1 size-6 text-muted-foreground" aria-hidden="true" />
      </template>
    </EmptyState>

    <div v-else class="overflow-hidden rounded-lg border border-border bg-card">
      <!-- 桌面：表格。窄屏：改成卡片式（thead 隐藏，每行用 data-label 打标签） -->
      <table class="w-full border-collapse text-sm max-md:block">
        <thead class="max-md:hidden">
          <tr class="border-b border-border bg-muted/40 text-left">
            <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">标题</th>
            <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">状态</th>
            <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">进度</th>
            <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">更新时间</th>
            <th scope="col" class="px-3 py-2">
              <span class="sr-only">操作</span>
            </th>
          </tr>
        </thead>
        <tbody class="max-md:block">
          <tr
            v-for="task in store.items"
            :key="task.task_id"
            :data-test="`task-row-${task.task_id}`"
            class="border-b border-border last:border-b-0 hover:bg-muted/30 max-md:block max-md:px-3 max-md:py-2"
          >
            <td
              class="px-3 py-2 font-medium max-md:flex max-md:justify-between max-md:gap-3 max-md:px-0 max-md:py-1"
              data-label="标题"
            >
              {{ task.title }}
            </td>
            <td
              class="px-3 py-2 max-md:flex max-md:justify-between max-md:gap-3 max-md:px-0 max-md:py-1"
              data-label="状态"
            >
              <span class="hidden text-xs text-muted-foreground max-md:inline">状态</span>
              <StatusBadge :status="task.status" />
            </td>
            <td
              class="px-3 py-2 max-md:flex max-md:justify-between max-md:gap-3 max-md:px-0 max-md:py-1"
              data-label="进度"
            >
              <span class="hidden text-xs text-muted-foreground max-md:inline">进度</span>
              <div
                class="h-1.5 w-24 overflow-hidden rounded-full bg-secondary"
                role="progressbar"
                :aria-valuenow="taskProgress(task.status)"
                aria-valuemin="0"
                aria-valuemax="100"
                :title="`${taskProgress(task.status)}%`"
              >
                <div
                  class="h-full rounded-full bg-primary transition-[width] duration-300"
                  :style="{ width: `${taskProgress(task.status)}%` }"
                />
              </div>
            </td>
            <td
              class="px-3 py-2 text-muted-foreground max-md:flex max-md:justify-between max-md:gap-3 max-md:px-0 max-md:py-1"
              data-label="更新时间"
            >
              <span class="hidden text-xs text-muted-foreground max-md:inline">更新时间</span>
              <span>{{ formatDateTime(task.updated_at ?? task.created_at) }}</span>
            </td>
            <td
              class="px-3 py-2 text-right max-md:flex max-md:justify-end max-md:px-0 max-md:py-2"
              data-label="操作"
            >
              <Button variant="outline" size="sm" @click="openTask(task.task_id)">打开</Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
