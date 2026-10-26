/**
 * 团队标识（slug）推导（2026-10-26 团队页重设计）。
 *
 * ## 为什么抽成纯函数
 *
 * 这是本次修的两个真实缺陷的核心逻辑，且边界情况多（中文、全角符号、
 * 超长、已有连字符）。放在视图里只能靠挂载组件 + 模拟事件来测，
 * 而事件时序恰恰是缺陷的成因（见下），纯函数能直接测。
 *
 * ## 缺陷背景
 *
 * 原先派生挂在 `@input="syncSlug"` 上，`syncSlug` 读 `newName` 这个 ref。
 * 浏览器的执行顺序是 **模板 `@input` 监听器先于 `v-model` 的写入**
 * （模板事件在 `mountElement` 阶段注册，`vModelText` 指令的监听在
 * `created` 钩子里更晚），所以 `syncSlug` 读到的是**上一个字符**：
 *
 * - Playwright `fill()` 一次写入整段并只发一次 `input` → 读到空串 → slug 为空
 *   → 点创建报「请填写团队名与标识」（实测截图确认）；
 * - 真实逐字输入 → slug 永远少最后一个字符（实测单测断言 `'ab' !== 'abc'`）。
 *
 * 修法：改用 `watch(newName)`，ref 更新之后才推导，与事件顺序解耦。
 */

/** slug 允许的最大长度（与后端 `String(64)` 契约一致）。 */
export const SLUG_MAX_LENGTH = 64;

/**
 * 把团队名推导成合法 slug。
 *
 * 规则：小写 → 非 `[a-z0-9]` 折叠为 `-` → 压缩连续 `-` → 去首尾 `-` → 截断。
 *
 * @returns 合法 slug；**无法推导时返回空串**（纯中文名会得到空串，
 *          由调用方决定兜底策略）
 */
export function deriveSlug(name: string): string {
  return (name ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX_LENGTH)
    .replace(/-+$/g, "");
}

/**
 * 兜底 slug：推导为空时使用（纯中文团队名走这里）。
 *
 * ## 为什么必须有兜底
 *
 * 用户填「巡检课题组」→ 推导结果为空 → 创建直接失败。而「标识」是 URL 用的
 * 技术字段，普通用户不该被迫自己想一个英文名。
 *
 * 生成 `team-<6 位 base36>`：满足 `^[a-z0-9-]+$`，足够区分，且用户仍可手动改。
 * 不用纯随机数是为了让测试可断言前缀与格式（而不是断言具体值）。
 */
export function fallbackSlug(): string {
  return `team-${Math.random().toString(36).slice(2, 8).padEnd(6, "0")}`;
}

/**
 * 推导 slug，必要时兜底。这是视图直接用的入口。
 */
export function deriveSlugOrFallback(name: string): string {
  return deriveSlug(name) || fallbackSlug();
}
