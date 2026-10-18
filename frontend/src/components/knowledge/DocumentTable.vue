<script setup lang="ts">
import { ref } from "vue";
import { Trash2 } from "lucide-vue-next";

import StatusBadge from "@/components/common/StatusBadge.vue";
import type { KnowledgeDocument } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

// 文档表：无边框表格；删除需二次确认；失败原因取自后端截断后的字段（不含栈）
defineProps<{ documents: KnowledgeDocument[] }>();
const emit = defineEmits<{ remove: [docId: string] }>();

const confirming = ref<string | null>(null);

function requestRemove(docId: string) {
  if (confirming.value !== docId) {
    confirming.value = docId;
    return;
  }
  confirming.value = null;
  emit("remove", docId);
}
</script>

<template>
  <table class="doc-table" data-test="document-table">
    <thead>
      <tr>
        <th scope="col">标题</th>
        <th scope="col">类型</th>
        <th scope="col">来源</th>
        <th scope="col">索引状态</th>
        <th scope="col">分块</th>
        <th scope="col">创建时间</th>
        <th scope="col"><span class="sr-only">操作</span></th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="doc in documents" :key="doc.id" :data-test="`document-row-${doc.id}`">
        <td data-label="标题">
          <span class="doc-table__title">{{ doc.title }}</span>
          <span v-if="doc.original_name" class="doc-table__file">{{ doc.original_name }}</span>
        </td>
        <td data-label="类型">{{ doc.doc_type }}</td>
        <td data-label="来源">{{ doc.source }}</td>
        <td data-label="索引状态">
          <StatusBadge :status="doc.index_status" />
          <span v-if="doc.index_status === 'failed'" class="doc-table__failure">
            索引失败{{ doc.failure_reason ? `：${doc.failure_reason}` : "" }}
          </span>
        </td>
        <td data-label="分块">{{ doc.chunk_count }}</td>
        <td data-label="创建时间">{{ formatDateTime(doc.created_at) }}</td>
        <td data-label="操作">
          <button
            class="icon-button"
            type="button"
            :title="confirming === doc.id ? '再次点击确认删除' : '删除文档'"
            :aria-label="`删除 ${doc.title}`"
            :data-test="`document-delete-${doc.id}`"
            @click="requestRemove(doc.id)"
          >
            <Trash2 :size="15" />
          </button>
          <button
            v-if="confirming === doc.id"
            class="btn btn--danger"
            type="button"
            :data-test="`document-delete-confirm-${doc.id}`"
            @click="requestRemove(doc.id)"
          >
            确认删除
          </button>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.doc-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.doc-table th,
.doc-table td {
  text-align: left;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--color-border);
  vertical-align: top;
}

.doc-table thead th {
  color: #64748b;
  font-weight: 600;
  font-size: 0.82rem;
}

.doc-table__title {
  display: block;
}

.doc-table__file {
  display: block;
  color: #94a3b8;
  font-size: 0.78rem;
}

.doc-table__failure {
  display: block;
  color: var(--color-error);
  font-size: 0.78rem;
  margin-top: 0.15rem;
}

.btn--danger {
  color: var(--color-error);
  border-color: #fecaca;
  margin-left: 0.35rem;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
