<script setup lang="ts">
import { ref } from "vue";
import { Trash2 } from "lucide-vue-next";

import StatusBadge from "@/components/common/StatusBadge.vue";
import { Button } from "@/components/ui/button";
import type { KnowledgeDocument } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

// 文档表：删除需二次确认；失败原因取自后端截断后的字段（不含栈）
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
//
// ⚠️ 测试契约（不要改）：data-test="document-table" / "document-row-<id>" /
// "document-delete-<id>" / "document-delete-confirm-<id>"。
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
  <div class="overflow-hidden rounded-lg border border-border bg-card">
    <table class="w-full border-collapse text-sm" data-test="document-table">
      <thead>
        <tr class="border-b border-border bg-muted/40 text-left">
          <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">标题</th>
          <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">类型</th>
          <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">来源</th>
          <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">索引状态</th>
          <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">分块</th>
          <th scope="col" class="px-3 py-2 font-medium text-muted-foreground">创建时间</th>
          <th scope="col" class="px-3 py-2">
            <span class="sr-only">操作</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="doc in documents"
          :key="doc.id"
          :data-test="`document-row-${doc.id}`"
          class="border-b border-border align-top last:border-b-0 hover:bg-muted/30"
        >
          <td class="px-3 py-2" data-label="标题">
            <span class="block font-medium">{{ doc.title }}</span>
            <span v-if="doc.original_name" class="block text-xs text-muted-foreground">
              {{ doc.original_name }}
            </span>
          </td>
          <td class="px-3 py-2 text-muted-foreground" data-label="类型">{{ doc.doc_type }}</td>
          <td class="px-3 py-2 text-muted-foreground" data-label="来源">{{ doc.source }}</td>
          <td class="px-3 py-2" data-label="索引状态">
            <StatusBadge :status="doc.index_status" />
            <span v-if="doc.index_status === 'failed'" class="mt-0.5 block text-xs text-destructive">
              索引失败{{ doc.failure_reason ? `：${doc.failure_reason}` : "" }}
            </span>
          </td>
          <td class="px-3 py-2 text-muted-foreground" data-label="分块">{{ doc.chunk_count }}</td>
          <td class="px-3 py-2 text-muted-foreground" data-label="创建时间">
            {{ formatDateTime(doc.created_at) }}
          </td>
          <td class="px-3 py-2" data-label="操作">
            <div class="flex items-center justify-end gap-1.5">
              <Button
                variant="ghost"
                size="icon-sm"
                :title="confirming === doc.id ? '再次点击确认删除' : '删除文档'"
                :aria-label="`删除 ${doc.title}`"
                :data-test="`document-delete-${doc.id}`"
                @click="requestRemove(doc.id)"
              >
                <Trash2 :size="15" />
              </Button>
              <Button
                v-if="confirming === doc.id"
                variant="destructive"
                size="sm"
                :data-test="`document-delete-confirm-${doc.id}`"
                @click="requestRemove(doc.id)"
              >
                确认删除
              </Button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
