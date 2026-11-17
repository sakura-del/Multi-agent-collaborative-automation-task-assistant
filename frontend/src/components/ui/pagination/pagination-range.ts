/**
 * 分页窗口算法（照搬 beUI paginationRange）。
 *
 * ## 为什么要保证长度恒定
 *
 * 页码窗口移动时如果长度变化（比如从 `1 2 3 … 20` 变成 `1 … 5 6 7 … 20`），
 * 整行宽度会跳，鼠标下的按钮会瞬移。这里固定返回
 * `boundaries*2 + siblings*2 + 3` 项：数字位 + 两个省略号位互补，
 * 翻页时只有内容变、宽度不变。
 */
export type PaginationItem = number | "ellipsis-start" | "ellipsis-end";

function range(start: number, end: number): number[] {
  const out: number[] = [];
  for (let i = start; i <= end; i += 1) out.push(i);
  return out;
}

export function paginationRange(
  page: number,
  totalPages: number,
  siblings = 1,
  boundaries = 1,
): PaginationItem[] {
  const total = Math.max(1, Math.floor(totalPages));
  const current = Math.min(total, Math.max(1, Math.round(page)));
  const slots = boundaries * 2 + siblings * 2 + 3;

  // 页数装得下就全列出来，不出现省略号
  if (total <= slots) {
    return range(1, total);
  }

  const left = Math.max(current - siblings, boundaries);
  const right = Math.min(current + siblings, total - boundaries);

  const showStart = left > boundaries + 2;
  const showEnd = right < total - (boundaries + 1);

  if (!showStart && showEnd) {
    const end = siblings * 2 + boundaries + 2;
    return [...range(1, end), "ellipsis-end", ...range(total - boundaries + 1, total)];
  }

  if (showStart && !showEnd) {
    const start = total - (boundaries + 1 + siblings * 2);
    return [...range(1, boundaries), "ellipsis-start", ...range(start, total)];
  }

  return [
    ...range(1, boundaries),
    "ellipsis-start",
    ...range(left, right),
    "ellipsis-end",
    ...range(total - boundaries + 1, total),
  ];
}
