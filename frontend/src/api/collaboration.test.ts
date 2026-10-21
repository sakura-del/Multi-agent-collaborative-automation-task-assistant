import { describe, expect, it, vi } from "vitest";

import {
  acceptInvite,
  changeMemberRole,
  createAnnotation,
  createInvite,
  createWorkspace,
  deleteAnnotation,
  getWorkspace,
  leaveWorkspace,
  listAnnotations,
  listMembers,
  listParagraphMatches,
  listWorkspaces,
  removeMember,
  renameWorkspace,
  reopenAnnotation,
  resolveAnnotation,
  updateAnnotation,
} from "@/api/collaboration";

vi.mock("@/api/client", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import api from "@/api/client";

const WORKSPACE = {
  workspace_id: "w1",
  name: "研究组",
  slug: "research",
  owner_id: "u1",
  seat_limit: 5,
  seat_used: 2,
  my_role: "owner",
  created_at: "2026-10-21T00:00:00Z",
};

const ANNOTATION = {
  annotation_id: "a1",
  task_id: "t1",
  author_id: "u1",
  author_name: "导师",
  anchor_type: "task",
  anchor_key: null,
  content: "补充局限性",
  resolved_at: null,
  created_at: "2026-10-21T00:00:00Z",
};

describe("collaboration api", () => {
  it("listWorkspaces 取回列表", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: [WORKSPACE] });

    const result = await listWorkspaces();

    expect(api.get).toHaveBeenCalledWith("/workspaces");
    expect(result).toEqual([WORKSPACE]);
  });

  it("createWorkspace 提交 name/slug/seat_limit", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 201, data: WORKSPACE });

    await createWorkspace({ name: "研究组", slug: "research", seat_limit: 5 });

    expect(api.post).toHaveBeenCalledWith("/workspaces", {
      name: "研究组",
      slug: "research",
      seat_limit: 5,
    });
  });

  it("getWorkspace 用 encodeURIComponent 拼路径", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: WORKSPACE });

    await getWorkspace("w/1");

    expect(api.get).toHaveBeenCalledWith("/workspaces/w%2F1");
  });

  it("renameWorkspace 走 PATCH", async () => {
    vi.mocked(api.patch).mockResolvedValue({ status: 200, data: WORKSPACE });

    await renameWorkspace("w1", "新名字");

    expect(api.patch).toHaveBeenCalledWith("/workspaces/w1", { name: "新名字" });
  });

  it("listMembers / removeMember / changeMemberRole", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: [] });
    vi.mocked(api.delete).mockResolvedValue({ status: 204, data: null });
    vi.mocked(api.patch).mockResolvedValue({
      status: 200,
      data: { user_id: "u2", username: "b", email: "b@x", role: "viewer", joined_at: "" },
    });

    await listMembers("w1");
    await removeMember("w1", "u2");
    const changed = await changeMemberRole("w1", "u2", "viewer");

    expect(api.get).toHaveBeenCalledWith("/workspaces/w1/members");
    expect(api.delete).toHaveBeenCalledWith("/workspaces/w1/members/u2");
    expect(changed.role).toBe("viewer");
  });

  it("leaveWorkspace 走 POST", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 204, data: null });

    await leaveWorkspace("w1");

    expect(api.post).toHaveBeenCalledWith("/workspaces/w1/leave");
  });

  it("createInvite 默认 role=member", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 201,
      data: { invite_id: "i1", code: "abc123", role: "member", expires_at: "", workspace_id: "w1" },
    });

    await createInvite("w1");

    expect(api.post).toHaveBeenCalledWith("/workspaces/w1/invites", { role: "member" });
  });

  it("acceptInvite 提交 code", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 200, data: WORKSPACE });

    await acceptInvite("abc123");

    expect(api.post).toHaveBeenCalledWith("/workspaces/accept", { code: "abc123" });
  });

  it("listAnnotations 走任务子路径", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: [ANNOTATION] });

    const result = await listAnnotations("t1");

    expect(api.get).toHaveBeenCalledWith("/tasks/t1/annotations");
    expect(result[0].content).toBe("补充局限性");
  });

  it("createAnnotation 原样透传 anchor_text（指纹由服务端算）", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 201, data: ANNOTATION });

    await createAnnotation("t1", {
      anchor_type: "paragraph",
      anchor_text: "第二段。",
      content: "改这里",
    });

    expect(api.post).toHaveBeenCalledWith("/tasks/t1/annotations", {
      anchor_type: "paragraph",
      anchor_text: "第二段。",
      content: "改这里",
    });
  });

  it("updateAnnotation / resolve / reopen / delete", async () => {
    vi.mocked(api.patch).mockResolvedValue({ status: 200, data: ANNOTATION });
    vi.mocked(api.post).mockResolvedValue({ status: 200, data: ANNOTATION });
    vi.mocked(api.delete).mockResolvedValue({ status: 204, data: null });

    await updateAnnotation("t1", "a1", "新内容");
    await resolveAnnotation("t1", "a1");
    await reopenAnnotation("t1", "a1");
    await deleteAnnotation("t1", "a1");

    expect(api.patch).toHaveBeenCalledWith("/tasks/t1/annotations/a1", { content: "新内容" });
    expect(api.post).toHaveBeenCalledWith("/tasks/t1/annotations/a1/resolve");
    expect(api.post).toHaveBeenCalledWith("/tasks/t1/annotations/a1/reopen");
    expect(api.delete).toHaveBeenCalledWith("/tasks/t1/annotations/a1");
  });

  it("listParagraphMatches 取匹配状态", async () => {
    vi.mocked(api.get).mockResolvedValue({
      status: 200,
      data: [
        {
          annotation_id: "a1",
          fingerprint: "f1",
          content: "x",
          author_id: "u1",
          matched: false,
          paragraph_index: null,
          resolved: false,
        },
      ],
    });

    const result = await listParagraphMatches("t1");

    expect(api.get).toHaveBeenCalledWith("/tasks/t1/annotations/paragraph-matches");
    // matched=false 是「原文已变更」，前端必须能拿到这个状态
    expect(result[0].matched).toBe(false);
  });
});
