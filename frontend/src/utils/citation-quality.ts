/**
 * 来源质量（Issue #31 Phase 1）的纯函数工具。
 *
 * 放在 utils 而不是组件里，是因为「解析后端质量字段」与「正文角标标红」
 * 都需要独立单测：这两件事一旦悄悄退化，用户看到的是「没有撤稿警示」——
 * 比报错更危险。
 */
import type { QualityFlag, SourceType } from "@/api/types";

export const QUALITY_FLAG_LABELS: Record<QualityFlag, string> = {
  retracted: "已撤稿",
  preprint: "预印本",
  low_citation: "低被引",
};

const QUALITY_FLAG_VALUES: readonly QualityFlag[] = ["retracted", "preprint", "low_citation"];
const SOURCE_TYPE_VALUES: readonly SourceType[] = ["peer_reviewed", "preprint", "other"];

/** 把后端可能传入的任意值解析成合法标记列表（去重、保持后端顺序）。 */
export function parseQualityFlags(value: unknown): QualityFlag[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const seen = new Set<QualityFlag>();
  for (const item of value) {
    if (typeof item === "string" && (QUALITY_FLAG_VALUES as readonly string[]).includes(item)) {
      seen.add(item as QualityFlag);
    }
  }
  return [...seen];
}

/** 来源类型：非法/缺失 → null（不猜）。 */
export function parseSourceType(value: unknown): SourceType | null {
  return typeof value === "string" && (SOURCE_TYPE_VALUES as readonly string[]).includes(value)
    ? (value as SourceType)
    : null;
}

/** 从参考文献条目里收集「已撤稿」的序号（正文角标用）。 */
export function retractedIndices(entries: readonly { index: number; is_retracted?: boolean | null }[]): number[] {
  return entries.filter((entry) => entry.is_retracted === true).map((entry) => entry.index);
}

const CITATION_MARKER_RE = /\[E(\d+)\]/g;

/**
 * 把正文 HTML 里已撤稿文献的 `[En]` 角标包成红色高亮（其余标记原样保留）。
 *
 * 输入必须是 `renderMarkdown` 消毒后的 HTML：本函数只插入固定 class 的 `<span>`，
 * 不引入任何用户可控的标签/属性。序号只接受纯数字，避免把正文里的
 * `[E9](...)` 之类结构误判成引用。
 *
 * @param html 消毒后的正文 HTML
 * @param indices 已撤稿文献的序号集合
 */
export function markRetractedCitations(html: string, indices: Iterable<number>): string {
  const retracted = new Set([...indices].filter((value) => Number.isFinite(value)));
  if (!html || retracted.size === 0) {
    return html;
  }
  return html.replace(CITATION_MARKER_RE, (match, rawIndex: string) => {
    const index = Number(rawIndex);
    if (!retracted.has(index)) {
      return match;
    }
    return (
      '<span class="rounded-[2px] bg-destructive/10 px-0.5 font-semibold text-destructive"' +
      ' title="该文献已被撤稿" data-test="retracted-citation">' +
      match +
      "</span>"
    );
  });
}
