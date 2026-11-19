import api from "./client";

// 意图分类（Issue #8 P1）：通用入口一句话 → 意图 + 结构化参数。
// 后端 fail-open：平台模型不可用时回落 literature_review——前端不需要特殊处理。

export interface IntentResult {
  intent: "literature_review" | "other";
  topic: string;
  keywords: string[];
  source: "llm" | "fallback";
}

export async function classifyIntent(text: string): Promise<IntentResult> {
  const response = await api.post<IntentResult>("/tasks/classify-intent", { text });
  return response.data;
}
