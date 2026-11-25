import {
  E2E_DEMO_ACCOUNT,
  E2E_RECONNECT_ACCOUNT,
  E2E_REVIEW_ACCOUNT,
  E2E_SECONDARY_ACCOUNT,
  demoPassword,
  secondaryPassword,
} from "../credentials";

/**
 * 统一解析 API 基地址：
 * 1. 优先读取显式环境变量 E2E_API_BASE；
 * 2. 默认返回相对路径 `/api/v1`，由 Playwright 的 `baseURL` + 同源代理自动路由。
 */
export function apiBase(): string {
  const explicit = process.env.E2E_API_BASE?.trim();
  if (explicit) return explicit.replace(/\/+$/, "");
  return "/api/v1";
}

export interface E2ECredentials {
  account: string;
  password: string;
}

export function demoCreds(): E2ECredentials {
  return { account: E2E_DEMO_ACCOUNT, password: demoPassword() };
}

export function secondaryCreds(): E2ECredentials {
  return { account: E2E_SECONDARY_ACCOUNT, password: secondaryPassword() };
}

export function reviewCreds(): E2ECredentials {
  return { account: E2E_REVIEW_ACCOUNT, password: secondaryPassword() };
}

export function reconnectCreds(): E2ECredentials {
  return { account: E2E_RECONNECT_ACCOUNT, password: secondaryPassword() };
}
