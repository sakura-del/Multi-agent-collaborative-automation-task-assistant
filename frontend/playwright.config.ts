import { defineConfig, devices } from "@playwright/test";

// 浏览器级验收：默认打本机 80 端口的 Compose 入口（nginx 同时托管前端与 /api）。
// 覆盖：E2E_BASE_URL=http://10.0.10.2:48080 npm run test:e2e
// 不在配置里安装浏览器依赖；CI 由显式步骤执行 `npx playwright install --with-deps chromium`。
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "e2e/.artifacts",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
    actionTimeout: 15_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // Plan 4 Exit Gate：桌面与移动两种视口都要能走完整流程
      name: "mobile-chrome",
      use: { ...devices["Pixel 5"] },
    },
  ],
});
