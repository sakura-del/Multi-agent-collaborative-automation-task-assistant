import { describe, expect, it } from "vitest";

import { router } from "@/router";

describe("router 权限边界（Issue #62）", () => {
  it("内部设计预览页 /design/timeline 不得对匿名用户公开，且仅限管理员访问", () => {
    const resolved = router.resolve("/design/timeline");
    expect(resolved.name).toBe("design-timeline");
    expect(resolved.meta.public).not.toBe(true);
    expect(resolved.meta.requiresAdmin).toBe(true);
  });
});
