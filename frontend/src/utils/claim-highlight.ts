/**
 * 原文片段高亮（Issue #31 Phase 2）。
 *
 * 后端返回的 `evidence_quote` 是模型复述出来的「精确子句」——它可能：
 * - 与 `evidence_passage` 的空白/换行不一致（模型把两行拼成一行）；
 * - 大小写不同（英文摘要）；
 * - 根本不在片段里（模型改写或引错）。
 *
 * 所以匹配必须「忽略多余空白 + 忽略大小写」；找不到时**不标红、也不报错**，
 * 只把片段原样展示——把改写过的句子标成原文，比不标更误导。
 */

const MARK_CLASS = "rounded-[2px] bg-amber-200/70 px-0.5 text-inherit dark:bg-amber-300/30";

/** HTML 转义：片段来自 LLM/外部文献，绝不能直接拼进 innerHTML。 */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

interface CollapsedText {
  text: string;
  /** 折叠后第 i 个字符在原串中的下标；用于把匹配区间映射回原文 */
  map: number[];
}

/** 折叠连续空白为单个空格，并记录每个字符的原串下标。 */
function collapseWithMap(text: string): CollapsedText {
  let out = "";
  const map: number[] = [];
  let pendingSpace = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (/\s/.test(char)) {
      pendingSpace = out.length > 0;
      continue;
    }
    if (pendingSpace) {
      out += " ";
      map.push(i);
      pendingSpace = false;
    }
    out += char;
    map.push(i);
  }
  return { text: out, map };
}

export interface HighlightResult {
  /** 转义后的 HTML；match=true 时含 `<mark data-test="claim-highlight">` */
  html: string;
  /** 是否真的在片段里找到了引文（找不到时调用方应给出「未在片段中定位」提示） */
  matched: boolean;
}

/**
 * 在 `passage` 里高亮 `quote`（忽略空白差异与大小写）。
 *
 * @param passage 原文片段（后端 `evidence_passage`）
 * @param quote 需要高亮的精确子句（后端 `evidence_quote`）
 */
export function highlightQuote(passage: string, quote: string | null | undefined): HighlightResult {
  if (!passage) {
    return { html: "", matched: false };
  }
  const haystack = collapseWithMap(passage);
  const needle = quote ? collapseWithMap(quote).text : "";
  if (!needle) {
    return { html: escapeHtml(passage), matched: false };
  }

  const index = haystack.text.toLowerCase().indexOf(needle.toLowerCase());
  if (index < 0) {
    return { html: escapeHtml(passage), matched: false };
  }
  const start = haystack.map[index];
  const end = haystack.map[index + needle.length - 1] + 1;

  const before = escapeHtml(passage.slice(0, start));
  const middle = escapeHtml(passage.slice(start, end));
  const after = escapeHtml(passage.slice(end));
  return {
    html: `${before}<mark class="${MARK_CLASS}" data-test="claim-highlight">${middle}</mark>${after}`,
    matched: true,
  };
}
