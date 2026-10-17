import { afterEach } from "vitest";

// 每个用例结束后清空本地存储，避免 token 在用例之间泄漏
afterEach(() => {
  localStorage.clear();
});
