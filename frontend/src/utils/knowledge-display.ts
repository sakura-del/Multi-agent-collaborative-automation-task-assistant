import type { KnowledgeDocument } from "@/api/types";

/**
 * 知识库页的展示逻辑（2026-10-23 重设计）。
 *
 * 这些判断放在纯函数里，而不是写在模板表达式中：
 * - 逻辑有真实边界情况（扩展名大小写、`.markdown` 双后缀、同名不同格式）；
 * - 纯函数能直接测，不必挂载组件。
 */

/** 支持的文档类型 → 展示用标签。 */
const TYPE_LABELS: Record<string, string> = {
  pdf: "PDF",
  docx: "DOCX",
  md: "Markdown",
  markdown: "Markdown",
  txt: "TXT",
};

/** `pdf` → `PDF`，未知类型原样大写。 */
export function docTypeLabel(docType: string): string {
  const key = (docType ?? "").trim().toLowerCase();
  return TYPE_LABELS[key] ?? key.toUpperCase();
}

/**
 * 第二行（原始文件名）是否值得显示。
 *
 * ## 为什么需要
 *
 * 实测：`title` 基本等于 `original_name` 去掉扩展名，例如
 *
 * ```
 * 10_EVOR_Evolving_Retrieval_CodeGen_2402.12317v2          ← title
 * 10_EVOR_Evolving_Retrieval_CodeGen_2402.12317v2.pdf      ← original_name
 * ```
 *
 * 第二行只是多了一个扩展名 —— 纯噪音，占掉一行高度。
 *
 * 判定规则：把 `original_name` 去掉扩展名后与 `title` 比较（忽略大小写与
 * 首尾空白）。相同 → 不显示。
 */
export function shouldShowOriginalName(doc: KnowledgeDocument): boolean {
  const original = (doc.original_name ?? "").trim();
  if (!original) {
    return false;
  }
  const withoutExt = original.replace(/\.[^./\\]+$/, "").trim();
  return withoutExt.toLowerCase() !== (doc.title ?? "").trim().toLowerCase();
}

/**
 * 是否显示「来源」列。
 *
 * 当所有文档来源相同（实测全部是 `upload`）时，这一列不传递任何信息，
 * 白占宽度。只在来源确实有差异时显示。
 */
export function shouldShowSourceColumn(documents: KnowledgeDocument[]): boolean {
  if (documents.length === 0) {
    return false;
  }
  const first = (documents[0].source ?? "").trim().toLowerCase();
  return documents.some((item) => (item.source ?? "").trim().toLowerCase() !== first);
}

/** 该文档是否需要用户处理（索引失败 = 要重新上传）。 */
export function needsAttention(doc: KnowledgeDocument): boolean {
  return doc.index_status === "failed";
}
