<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { Plus } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import TaskCreateForm from "@/components/tasks/TaskCreateForm.vue";
import { useTaskStore } from "@/stores/tasks";
import type { TaskCreate } from "@/api/types";
import { formatDateTime, taskProgress } from "@/utils/task-status";

// 任务列表：创建入口 + 列表（桌面表格 / 移动单列）；创建成功后进入详情页
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
  <section class="page">
    <header class="page__header page__header--row">
      <div>
        <h1>任务</h1>
        <p class="page__hint">共 {{ store.items.length }} 个任务</p>
      </div>
      <button
        class="btn btn--primary"
        data-test="new-task"
        type="button"
        @click="showForm = !showForm"
      >
        <Plus :size="16" />
        <span>{{ showForm ? "收起" : "新建任务" }}</span>
      </button>
    </header>

    <ErrorBanner :message="errorMessage" />

    <TaskCreateForm
      v-if="showForm"
      class="page__form"
      :pending="submitting"
      :error="errorMessage"
      @submit="handleCreate"
    />

    <p v-if="store.listLoading && !store.items.length" class="page__hint">加载中…</p>

    <div v-else-if="!store.items.length" class="empty" data-test="empty-state">
      <p>还没有任务。</p>
      <button class="btn btn--primary" type="button" @click="showForm = true">新建任务</button>
    </div>

    <table v-else class="task-table">
      <thead>
        <tr>
          <th scope="col">标题</th>
          <th scope="col">状态</th>
          <th scope="col">进度</th>
          <th scope="col">更新时间</th>
          <th scope="col"><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="task in store.items" :key="task.task_id" :data-test="`task-row-${task.task_id}`">
          <td data-label="标题" class="task-table__title">{{ task.title }}</td>
          <td data-label="状态"><StatusBadge :status="task.status" /></td>
          <td data-label="进度">
            <div class="progress" :title="`${taskProgress(task.status)}%`">
              <div class="progress__bar" :style="{ width: `${taskProgress(task.status)}%` }" />
            </div>
          </td>
          <td data-label="更新时间">{{ formatDateTime(task.updated_at ?? task.created_at) }}</td>
          <td data-label="操作">
            <button class="btn" type="button" @click="openTask(task.task_id)">打开</button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.page__header--row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.page__form {
  margin-bottom: 1.5rem;
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 1.5rem;
  border: 1px dashed var(--color-border);
  border-radius: 12px;
  color: #475569;
}

.task-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  overflow: hidden;
}

.task-table th,
.task-table td {
  text-align: left;
  padding: 0.625rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  font-size: 0.9rem;
}

.task-table thead th {
  background: #f8fafc;
  font-weight: 600;
  color: #475569;
}

.task-table__title {
  font-weight: 500;
}

.progress {
  width: 96px;
  height: 6px;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
}

.progress__bar {
  height: 100%;
  background: var(--color-primary);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}

@media (max-width: 720px) {
  .task-table,
  .task-table tbody,
  .task-table tr,
  .task-table td {
    display: block;
    width: 100%;
  }

  .task-table thead {
    display: none;
  }

  .task-table tr {
    border-bottom: 1px solid var(--color-border);
    padding: 0.5rem 0.25rem;
  }

  .task-table td {
    border: none;
    padding: 0.25rem 0.75rem;
    display: flex;
    justify-content: space-between;
    gap: 1rem;
  }

  .task-table td::before {
    content: attr(data-label);
    color: #64748b;
    font-size: 0.8rem;
  }
}
</style>
