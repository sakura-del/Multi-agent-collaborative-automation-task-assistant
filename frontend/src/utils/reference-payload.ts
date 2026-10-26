import type { ImportedReferenceItem } from "@/api/types";

/**
 * 导入预览条目 → `CslItem` 形态（保存到引用库时用）。
 *
 * ## 为什么需要这个转换
 *
 * `POST /references/import` 返回的是**扁平展示结构**（`ImportedReferenceItem`）：
 * `authors` 是 `string[]`、`year` 而不是 `issued_year`。而
 * `POST /reference-library` 接收的是 **`CslItem` 形态**（`extra="forbid"`）：
 * `author` 是 `CslName[]`、字段名是 `issued_year`。
 *
 * 两者形状不同是**有意的**：导入响应面向展示（前端直接渲染），
 * 引用库面向存储（与 CSL 管线同构，渲染/导出可直接 `from_csl_json`）。
 * 但这就要求中间有一次显式转换 —— 不能把展示结构直接丢给保存接口。
 *
 * ## 这个函数必须保住的两件事
 *
 * 1. **`id` 不能为空**：`CslItem.id` 是必填，且是引用库的幂等键（`ref_id`）。
 *    导入器总会给出（DOI 优先，否则标题派生），但类型上是 `string | null`，
 *    所以这里要兜底 —— 用 `title` 派生一个稳定值。
 * 2. **字段名映射准确**：`year` → `issued_year`、`authors` → `author`。
 *    写错会被后端 `extra="forbid"` 直接拒（这比静默丢字段好，但仍应写对）。
 */

/** 作者字符串 → `CslName`：`"Family, Given"` 拆分；无逗号时整串当 family。 */
export function toCslName(raw: string): { family: string; given: string } {
  const text = raw.trim();
  if (!text) {
    return { family: "", given: "" };
  }
  const comma = text.indexOf(",");
  if (comma >= 0) {
    return {
      family: text.slice(0, comma).trim(),
      given: text.slice(comma + 1).trim(),
    };
  }
  return { family: text, given: "" };
}

/** 没有 id 时用题名派生稳定标识（与后端导入器的 `title:` 规则同风格）。 */
function fallbackId(item: ImportedReferenceItem): string {
  const basis = item.title.trim() || item.doi || "untitled";
  // 只保留可读字符，避免把标点带进 ref_id
  const slug = basis
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
  return `title:${slug || "untitled"}`;
}

export function toCslItemPayload(item: ImportedReferenceItem): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    id: item.id?.trim() || fallbackId(item),
    type: item.type || "article-journal",
    title: item.title || "",
    author: item.authors.map(toCslName).filter((name) => name.family || name.given),
  };

  // 只写有值的字段：`CslItem` 里这些是可选的，写 null 也能过，
  // 但少写能减少 payload 体积，且与后端导入器的输出风格一致。
  if (item.year !== null && item.year !== undefined) {
    payload.issued_year = item.year;
  }
  for (const key of ["container_title", "volume", "issue", "page", "publisher", "doi", "url"] as const) {
    const value = item[key];
    if (value !== null && value !== undefined && value !== "") {
      payload[key] = value;
    }
  }
  return payload;
}

/**
 * 批量转换。**跳过没有题名的条目**：`CslItem.title` 必填，
 * 且没有题名的文献对用户没有意义 —— 与其发一个注定被拒的条目，
 * 不如在这里过滤掉（调用方据返回长度差告知用户）。
 */
export function toCslItemPayloads(
  items: ImportedReferenceItem[]
): Record<string, unknown>[] {
  return items
    .filter((item) => (item.title ?? "").trim().length > 0)
    .map(toCslItemPayload);
}
