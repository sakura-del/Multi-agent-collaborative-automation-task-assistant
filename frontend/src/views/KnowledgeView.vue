<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import DocumentTable from "@/components/knowledge/DocumentTable.vue";
import DocumentUpload from "@/components/knowledge/DocumentUpload.vue";
import { useKnowledgeStore } from "@/stores/knowledge";

// 知识库页面：一个上传工具 + 一张无边框文档表 + 一行统计
const store = useKnowledgeStore();
const actionError = ref<string | null>(null);

const statsLine = computed(() => {
  const stats = store.stats;
  if (!stats) {
    return "";
  }
  return `${stats.total_docs} 篇文档 · ${stats.total_chunks} 个分块`;
});

onMounted(() => {
  void refresh();
});

async function refresh() {
  actionError.value = null;
  try {
    await store.loadAll();
  } catch (error) {
    actionError.value = (error as Error).message;
  }
}

async function handleRemove(docId: string) {
  actionError.value = null;
  try {
    await store.remove(docId);
  } catch (error) {
    actionError.value = (error as Error).message;
  }
}
</script>

<template>
  <section>
    <header class="mb-4">
      <h1 class="text-lg font-semibold tracking-tight">知识库</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        {{ statsLine || "支持 pdf / docx / md / txt，单文件 ≤ 30 MB" }}
      </p>
    </header>

    <ErrorBanner :message="actionError" />

    <DocumentUpload @uploaded="refresh" />

    <p v-if="store.loading && !store.documents.length" class="text-sm text-muted-foreground">
      加载中…
    </p>
    <EmptyState
      v-else-if="!store.documents.length"
      testid="knowledge-empty"
      title="知识库还是空的"
      description="上传一篇文档（pdf / docx / md / txt）即可用于文献综述的证据检索。"
    />
    <DocumentTable v-else :documents="store.documents" @remove="handleRemove" />
  </section>
</template>
