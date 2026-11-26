import api from "./client";
import type { WorkflowPreset } from "./types";

// 意图分类（Issue #8 P1 / Issue #32 Phase 1）：通用入口一句话 → 意图 + 预设 + 结构化参数。
// 后端 fail-open：平台模型不可用时回落 literature_review——前端不需要特殊处理。

export interface IntentResult {
  intent: "literature_review" | "other";
  topic: string;
  keywords: string[];
  source: "llm" | "fallback";
  workflow_preset?: WorkflowPreset;
}

export async function classifyIntent(text: string): Promise<IntentResult> {
  const response = await api.post<IntentResult>("/tasks/classify-intent", { text });
  return response.data;
}

export interface KeywordSuggestion {
  term: string;
  lang: "zh" | "en";
}

export interface KeywordSuggestResult {
  suggestions: KeywordSuggestion[];
  source: "llm" | "fallback";
}

export async function suggestKeywords(payload: {
  topic: string;
  output_language?: "zh" | "en";
  existing_keywords?: string[];
  max_keywords?: number;
}): Promise<KeywordSuggestResult> {
  const response = await api.post<KeywordSuggestResult>("/tasks/suggest-keywords", payload);
  return response.data;
}
