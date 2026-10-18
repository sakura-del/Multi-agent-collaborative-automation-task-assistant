import api from "./client";
import type {
  ChunkStrategy,
  KnowledgeDocument,
  KnowledgeDocumentList,
  KnowledgeSearchInput,
  KnowledgeSearchOut,
  KnowledgeStats,
} from "./types";

// 知识库 API：multipart 上传（边界由 Axios 生成，客户端不写 content-type）
export const knowledgeApi = {
  upload: (
    file: File,
    title: string,
    strategy: ChunkStrategy = "recursive",
    onProgress?: (percent: number) => void
  ) => {
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    form.append("strategy", strategy);
    return api
      .post<KnowledgeDocument>("/knowledge/documents", form, {
        onUploadProgress: (event) => {
          const total = event.total ?? 0;
          if (onProgress && total > 0) {
            onProgress(Math.round(((event.loaded ?? 0) / total) * 100));
          }
        },
      })
      .then((response) => response.data);
  },

  list: () =>
    api.get<KnowledgeDocumentList>("/knowledge/documents").then((response) => response.data),

  remove: (docId: string) =>
    api.delete<KnowledgeDocument>(`/knowledge/documents/${docId}`).then((response) => response.data),

  stats: () => api.get<KnowledgeStats>("/knowledge/stats").then((response) => response.data),

  search: (payload: KnowledgeSearchInput) =>
    api.post<KnowledgeSearchOut>("/knowledge/search", payload).then((response) => response.data),
};

export default knowledgeApi;
