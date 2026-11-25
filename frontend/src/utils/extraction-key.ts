/**
 * 确定性派生抽取列技术标识（B3：系统能判断的，不留给用户）。
 *
 * 规则：
 * 1. 对 `label.trim().toLowerCase()` 提取 ASCII 字母/数字/下划线片段；
 * 2. 若清洗结果以 `[a-z]` 开头且长度 >= 2，截断至 40 字符并去尾下划线作为基础标识；
 * 3. 否则（如纯中文、空串或数字开头）使用 `label.trim()` 的 FNV-1a 32 位哈希生成 `col_<6hex>`；
 * 4. 若与 `existingKeys` 冲突，依次附加 `_2`, `_3`… 并确保总长 <= 48 且满足 `^[a-z][a-z0-9_]{0,47}$`。
 */
export function deriveExtractionKey(
  label: string,
  existingKeys: Iterable<string> = [],
): string {
  const used = new Set(
    Array.from(existingKeys, (k) => k.trim().toLowerCase()).filter(Boolean),
  );
  const trimmed = label.trim();
  const ascii = trimmed
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");

  let base: string;
  if (/^[a-z][a-z0-9_]*$/.test(ascii) && ascii.length >= 2) {
    const sliced = ascii.slice(0, 40).replace(/_+$/g, "");
    base = sliced.length >= 2 ? sliced : ascii.slice(0, 40);
  } else {
    let hash = 0x811c9dc5;
    for (let i = 0; i < trimmed.length; i += 1) {
      hash ^= trimmed.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193);
    }
    const hex = (hash >>> 0).toString(16).padStart(8, "0").slice(0, 6);
    base = `col_${hex}`;
  }

  if (!used.has(base)) {
    return base;
  }

  let index = 2;
  while (true) {
    const suffix = `_${index}`;
    const prefix = base.slice(0, 48 - suffix.length).replace(/_+$/g, "");
    const candidate = `${prefix || "col"}${suffix}`;
    if (!used.has(candidate)) {
      return candidate;
    }
    index += 1;
  }
}
