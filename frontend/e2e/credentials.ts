/**
 * E2E 账号凭据统一入口（Issue #10）。
 *
 * 2026-11-11 安全排查发现：`demo` / `e2efull` 的口令硬编码在十几个 spec 里，
 * 等于把生产 admin 的口令随仓库一起公开。这里改成"只从环境变量读"：
 *
 * - 缺失时**立即抛错**并给出怎么设置，而不是静默回退到某个默认口令
 *   （默认口令等于把泄露重新写回仓库）；
 * - 账号名不是秘密，允许默认值，但要能被环境变量覆盖（本地库与生产库账号可能不同）。
 *
 * 用法：
 *   E2E_DEMO_PASSWORD=xxx E2E_SECONDARY_PASSWORD=yyy npx playwright test
 */

function required(name: string, hint: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `缺少环境变量 ${name}。${hint}\n` +
        "示例：E2E_DEMO_PASSWORD=xxx E2E_SECONDARY_PASSWORD=yyy npm run test:e2e",
    );
  }
  return value;
}

/** 演示账号（生产上是 admin，口令见部署环境 /opt/veripaper/.demo-password）。 */
export const E2E_DEMO_ACCOUNT = process.env.E2E_DEMO_ACCOUNT ?? "demo";

/** 第二个账号：用于跨用户隔离、免费额度用尽（1/1）等场景。 */
export const E2E_SECONDARY_ACCOUNT = process.env.E2E_SECONDARY_ACCOUNT ?? "e2efull";

/** 文献综述主流程账号（Pro 套餐，避免与免费版用尽态账号冲突）。 */
export const E2E_REVIEW_ACCOUNT = process.env.E2E_REVIEW_ACCOUNT ?? "e2ereview";

/** 断线重连专用账号（Pro 套餐，独立任务列表隔离）。 */
export const E2E_RECONNECT_ACCOUNT = process.env.E2E_RECONNECT_ACCOUNT ?? "e2ereconn";

export function demoPassword(): string {
  return required("E2E_DEMO_PASSWORD", "演示账号口令保存在部署环境的 600 文件里。");
}

export function secondaryPassword(): string {
  return required("E2E_SECONDARY_PASSWORD", "第二个账号需要先在目标库注册并设置口令。");
}
