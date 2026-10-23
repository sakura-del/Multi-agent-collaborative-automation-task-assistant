<script setup lang="ts">
import { computed, ref } from "vue";
import { Trash2 } from "lucide-vue-next";

import StatusBadge from "@/components/common/StatusBadge.vue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { KnowledgeDocument } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";
import {
  docTypeLabel,
  shouldShowOriginalName,
  shouldShowSourceColumn,
} from "@/utils/knowledge-display";

// 文档表：删除需二次确认；失败原因取自后端截断后的字段（不含栈）。
// 样式走 shadcn-vue / beUI tokens（AGENTS.md §6.5）。
//
// ## 重设计要点（2026-10-23）
//
// 1. **标题去噪**：`original_name` 与 `title` 实质相同（只差扩展名）时不渲染
//    第二行 —— 实测 11 篇里 10 篇都是这种重复，白占一行高度。
// 2. **常量列隐藏**：`source` 全为 `upload` 时整列不显示（不传递信息）。
// 3. **类型用徽标**：`pdf` → `PDF`，比裸小写更易扫读。
//
// ⚠️ 测试契约（不要改）：data-test="document-table" / "document-row-<id>" /
// "document-delete-<id>" / "document-delete-confirm-<id>"。
const props = defineProps<{ documents: KnowledgeDocument[] }>();
const emit = defineEmits<{ remove: [docId: string] }>();

const confirming = ref<string | null>(null);

/** 来源列只在来源确实有差异时显示（实测全是 upload）。 */
const showSource = computed(() => shouldShowSourceColumn(props.documents));

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
        <tr class="border-b border-border text-left">
          <th scope="col" class="px-3 py-2 text-xs font-medium text-muted-foreground">标题</th>
          <th scope="col" class="w-20 px-3 py-2 text-xs font-medium text-muted-foreground">类型</th>
          <th
            v-if="showSource"
            scope="col"
            data-test="doc-col-source"
            class="w-20 px-3 py-2 text-xs font-medium text-muted-foreground"
          >
            来源
          </th>
          <th scope="col" class="w-24 px-3 py-2 text-xs font-medium text-muted-foreground">
            索引状态
          </th>
          <th scope="col" class="w-16 px-3 py-2 text-right text-xs font-medium text-muted-foreground">
            分块
          </th>
          <th
            scope="col"
            class="w-32 px-3 py-2 text-xs font-medium text-muted-foreground max-lg:hidden"
          >
            创建时间
          </th>
          <th scope="col" class="w-10 px-2 py-2">
            <span class="sr-only">操作</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="doc in documents"
          :key="doc.id"
          :data-test="`document-row-${doc.id}`"
          class="border-b border-border align-top last:border-b-0 hover:bg-muted/40"
        >
          <td class="px-3 py-2.5" data-label="标题">
            <span class="block font-medium text-foreground" :title="doc.title">
              {{ doc.title }}
            </span>
            <!-- 只在文件名与标题实质不同时显示（否则纯噪音） -->
            <span
              v-if="shouldShowOriginalName(doc)"
              :data-test="`doc-original-name-${doc.id}`"
              class="mt-0.5 block truncate text-xs text-muted-foreground"
              :title="doc.original_name ?? ''"
            >
              {{ doc.original_name }}
            </span>
          </td>
          <td class="px-3 py-2.5" data-label="类型">
            <Badge variant="outline">{{ docTypeLabel(doc.doc_type) }}</Badge>
          </td>
          <td v-if="showSource" class="px-3 py-2.5 text-muted-foreground" data-label="来源">
            {{ doc.source }}
          </td>
          <td class="px-3 py-2.5" data-label="索引状态">
            <StatusBadge :status="doc.index_status" />
            <span
              v-if="doc.index_status === 'failed'"
              class="mt-1 block text-xs text-destructive"
            >
              索引失败{{ doc.failure_reason ? `：${doc.failure_reason}` : "" }}
            </span>
          </td>
          <td
            class="px-3 py-2.5 text-right text-muted-foreground tabular-nums"
            data-label="分块"
          >
            {{ doc.chunk_count }}
          </td>
          <td
            class="px-3 py-2.5 text-xs text-muted-foreground max-lg:hidden"
            data-label="创建时间"
          >
            {{ formatDateTime(doc.created_at) }}
          </td>
          <td class="px-2 py-2.5" data-label="操作">
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
