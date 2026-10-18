import { ref } from "vue";
import { defineStore } from "pinia";

import { knowledgeApi } from "@/api/knowledge";
import type { ChunkStrategy, KnowledgeDocument, KnowledgeStats } from "@/api/types";

// 知识库状态：文档列表 + 统计 + 上传进度（视图不直接调用 Axios）
export const useKnowledgeStore = defineStore("knowledge", () => {
  const documents = ref<KnowledgeDocument[]>([]);
  const stats = ref<KnowledgeStats | null>(null);
  const loading = ref(false);
  const uploading = ref(false);
  const uploadProgress = ref(0);
  const error = ref<string | null>(null);

  async function loadAll(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const [list, summary] = await Promise.all([knowledgeApi.list(), knowledgeApi.stats()]);
      documents.value = list.documents;
      stats.value = summary;
    } catch (caught) {
      error.value = (caught as Error).message;
      throw caught;
    } finally {
      loading.value = false;
    }
  }

  async function upload(file: File, title: string, strategy: ChunkStrategy = "recursive") {
    uploading.value = true;
    uploadProgress.value = 0;
    error.value = null;
    try {
      const created = await knowledgeApi.upload(file, title, strategy, (percent) => {
        uploadProgress.value = percent;
      });
      await loadAll();
      return created;
    } catch (caught) {
      error.value = (caught as Error).message;
      throw caught;
    } finally {
      uploading.value = false;
      uploadProgress.value = 0;
    }
  }

  async function remove(docId: string): Promise<void> {
    error.value = null;
    try {
      await knowledgeApi.remove(docId);
      await loadAll();
    } catch (caught) {
      error.value = (caught as Error).message;
      throw caught;
    }
  }

  return {
    documents,
    stats,
    loading,
    uploading,
    uploadProgress,
    error,
    loadAll,
    upload,
    remove,
  };
});
