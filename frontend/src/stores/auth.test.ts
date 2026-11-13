import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { authApi } from "@/api/auth";
import type { TokenOut, UserOut } from "@/api/types";
import { useAuthStore } from "@/stores/auth";

// 只 mock authApi，不触碰 Axios 内部实现
vi.mock("@/api/auth", async () => {
  const actual = await vi.importActual<typeof import("@/api/auth")>("@/api/auth");
  return {
    ...actual,
    authApi: {
      register: vi.fn(),
      login: vi.fn(),
      refresh: vi.fn(),
      me: vi.fn(),
    },
  };
});

const user: UserOut = {
  id: "11111111-1111-1111-1111-111111111111",
  username: "ylin",
  email: "ylin@example.com",
  role: "user",
  is_active: true,
  created_at: "2026-10-17T10:00:00Z",
};

const tokens: TokenOut = {
  access_token: "access-new",
  refresh_token: "refresh-new",
  token_type: "bearer",
  expires_in: 900,
};

describe("auth store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  it("persists both tokens on login", async () => {
    vi.mocked(authApi.login).mockResolvedValue(tokens);
    vi.mocked(authApi.me).mockResolvedValue(user);

    const store = useAuthStore();
    await store.login({ account: "ylin", password: "secret" });

    expect(localStorage.getItem("access_token")).toBe(tokens.access_token);
    expect(localStorage.getItem("refresh_token")).toBe(tokens.refresh_token);
    expect(store.user?.username).toBe("ylin");
    expect(store.authenticated).toBe(true);
  });

  it("bootstraps the current user from a stored access token", async () => {
    localStorage.setItem("access_token", "stored-access");
    vi.mocked(authApi.me).mockResolvedValue(user);

    const store = useAuthStore();
    await store.bootstrap();

    expect(authApi.me).toHaveBeenCalledTimes(1);
    expect(store.user?.id).toBe(user.id);
    expect(store.ready).toBe(true);
  });

  it("marks ready without calling the API when no token is stored", async () => {
    const store = useAuthStore();
    await store.bootstrap();

    expect(authApi.me).not.toHaveBeenCalled();
    expect(store.ready).toBe(true);
    expect(store.authenticated).toBe(false);
  });

  it("rotates both tokens on refresh", async () => {
    vi.mocked(authApi.refresh).mockResolvedValue(tokens);
    const store = useAuthStore();
    localStorage.setItem("refresh_token", "old");

    await store.refresh();

    expect(authApi.refresh).toHaveBeenCalledWith("old");
    expect(localStorage.getItem("access_token")).toBe(tokens.access_token);
    expect(localStorage.getItem("refresh_token")).toBe(tokens.refresh_token);
  });

  it("clears both tokens and the user on logout", async () => {
    localStorage.setItem("access_token", "a");
    localStorage.setItem("refresh_token", "r");
    const store = useAuthStore();

    store.logout();

    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
    expect(store.user).toBeNull();
    expect(store.authenticated).toBe(false);
  });

  it("clears the session when refresh fails", async () => {
    vi.mocked(authApi.refresh).mockRejectedValue(new Error("refresh expired"));
    localStorage.setItem("access_token", "a");
    localStorage.setItem("refresh_token", "r");
    const store = useAuthStore();

    await expect(store.refresh()).rejects.toThrow("refresh expired");

    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
    expect(store.user).toBeNull();
  });

  it("register 成功后不再自动登录（邮箱验证门禁，2026-11-13）", async () => {
    vi.mocked(authApi.register).mockResolvedValue({
      id: "11111111-1111-1111-1111-111111111111",
      username: "ylin",
      email: "ylin@example.com",
      role: "user",
      is_active: true,
      created_at: "2026-11-13T00:00:00Z",
    });
    const store = useAuthStore();

    await store.register({ username: "ylin", email: "ylin@example.com", password: "x" });

    // 关键：login 不得被调用——未验证邮箱没有资格拿令牌
    expect(authApi.login).not.toHaveBeenCalled();
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(store.authenticated).toBe(false);
  });

  it("surfaces register errors without persisting tokens", async () => {
    vi.mocked(authApi.register).mockRejectedValue(new Error("用户名已存在"));
    const store = useAuthStore();

    await expect(
      store.register({ username: "ylin", email: "ylin@example.com", password: "x" })
    ).rejects.toThrow("用户名已存在");

    expect(localStorage.getItem("access_token")).toBeNull();
    expect(store.authenticated).toBe(false);
  });
});
