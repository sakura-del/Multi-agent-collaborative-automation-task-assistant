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

/** 从参考文献条目里收集「已撤稿」的序号（正文角标标红用）。 */
export function retractedIndices(
  entries: readonly {
    index: number;
    is_retracted?: boolean | null;
    quality_flags?: readonly string[] | null;
  }[],
): number[] {
  return entries
    .filter(
      (entry) =>
        entry.is_retracted === true || Boolean(entry.quality_flags?.includes("retracted")),
    )
    .map((entry) => entry.index);
}

const CITATION_MARKER_RE = /\[E(\d+)\]/g;

export interface CitationMarkerOptions {
  /** 已撤稿文献的序号集合：这些角标用红色强警示样式 */
  retracted?: Iterable<number>;
}

/**
 * 把正文 HTML 里的 `[En]` 角标换成可点击按钮（Issue #31 Phase 2）。
 *
 * 输入必须是 `renderMarkdown` 消毒后的 HTML：本函数只插入固定 class 的
 * `<button>`，不引入任何用户可控的标签/属性（序号只接受纯数字）。
 * 点击由 `TaskResult` 的事件委托处理，打开右侧「来源核验」抽屉。
 *
 * 为什么是按钮而不是 span：键盘用户要能聚焦并用 Enter 打开；
 * 已撤稿角标复用同一元素、只换配色，避免「红角标不可点、可点角标不红」。
 *
 * @param html 消毒后的正文 HTML
 * @param options 撤稿序号集合
 */
export function decorateCitationMarkers(html: string, options: CitationMarkerOptions = {}): string {
  const retracted = new Set([...options.retracted ?? []].filter((value) => Number.isFinite(value)));
  if (!html) {
    return html;
  }
  return html.replace(CITATION_MARKER_RE, (match, rawIndex: string) => {
    const index = Number(rawIndex);
    const isRetracted = retracted.has(index);
    const classes =
      "citation-marker inline cursor-pointer rounded-[2px] px-0.5 font-semibold transition-colors " +
      (isRetracted ? "bg-destructive/10 text-destructive" : "text-primary hover:bg-primary/10");
    const title = isRetracted
      ? "该文献已被撤稿：点击查看来源与论断核验"
      : `查看 [E${index}] 的来源与论断核验`;
    const testAttr = isRetracted
      ? ' data-test="retracted-citation"'
      : ' data-test="citation-marker"';
    return (
      `<button type="button" class="${classes}" data-cite="${index}" title="${title}"` +
      `${testAttr}>${match}</button>`
    );
  });
}
