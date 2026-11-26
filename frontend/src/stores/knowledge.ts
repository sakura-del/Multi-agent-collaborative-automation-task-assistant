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
      // 网络中断或超时时，服务端可能已落库成功；静默刷新列表避免前端仍显示 0 篇诱导重复上传
      try {
        const [list, summary] = await Promise.all([knowledgeApi.list(), knowledgeApi.stats()]);
        documents.value = list.documents;
        stats.value = summary;
      } catch {
        // 静默同步失败时保留原始上传错误
      }
      error.value = (caught as Error).message;
      throw caught;
    } finally {
      uploading.value = false;
      uploadProgress.value = 0;
    }
  }

  /**
   * 批量有限并发上传（默认并发 2），单篇失败不中断其余文档，全部完成后统一刷新一次列表。
   */
  async function uploadBatch(
    items: Array<{ id: string; file: File; title: string }>,
    strategy: ChunkStrategy = "recursive",
    options?: {
      concurrency?: number;
      onProgress?: (id: string, percent: number) => void;
      onItemDone?: (id: string, ok: boolean, errorMessage?: string) => void;
    },
  ): Promise<{ succeeded: number; failed: number }> {
    if (!items.length) return { succeeded: 0, failed: 0 };
    uploading.value = true;
    uploadProgress.value = 0;
    error.value = null;

    const concurrency = Math.max(1, Math.min(options?.concurrency ?? 2, 4));
    let cursor = 0;
    let succeeded = 0;
    let failed = 0;

    async function worker() {
      while (cursor < items.length) {
        const current = items[cursor++];
        try {
          await knowledgeApi.upload(current.file, current.title, strategy, (percent) => {
            options?.onProgress?.(current.id, percent);
          });
          succeeded += 1;
          options?.onItemDone?.(current.id, true);
        } catch (caught) {
          failed += 1;
          options?.onItemDone?.(current.id, false, (caught as Error).message || "上传失败");
        } finally {
          uploadProgress.value = Math.round(((succeeded + failed) / items.length) * 100);
        }
      }
    }

    try {
      await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, () => worker()));
      if (succeeded > 0) {
        await loadAll();
      }
      return { succeeded, failed };
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
    uploadBatch,
    remove,
  };
});
