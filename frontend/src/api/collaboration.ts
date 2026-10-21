import api from "./client";
import type {
  Annotation,
  AnnotationAnchorType,
  ParagraphMatch,
  Workspace,
  WorkspaceInvite,
  WorkspaceMember,
  WorkspaceRole,
} from "./types";

// 团队工作区 + 席位 + 导师批注 API（Phase 2）
// Bearer 鉴权由 client.ts 的请求拦截器统一注入。

// ===== 工作区 =====

/** 我参与的工作区列表 */
export async function listWorkspaces(): Promise<Workspace[]> {
  const response = await api.get<Workspace[]>("/workspaces");
  return response.data;
}

/** 创建工作区（创建者自动成为 owner，占一个席位） */
export async function createWorkspace(payload: {
  name: string;
  slug: string;
  seat_limit?: number;
}): Promise<Workspace> {
  const response = await api.post<Workspace>("/workspaces", payload);
  return response.data;
}

/** 工作区详情（非成员返回 404） */
export async function getWorkspace(workspaceId: string): Promise<Workspace> {
  const response = await api.get<Workspace>(`/workspaces/${encodeURIComponent(workspaceId)}`);
  return response.data;
}

/** 改名（仅 owner） */
export async function renameWorkspace(workspaceId: string, name: string): Promise<Workspace> {
  const response = await api.patch<Workspace>(
    `/workspaces/${encodeURIComponent(workspaceId)}`,
    { name }
  );
  return response.data;
}

// ===== 成员（席位） =====

export async function listMembers(workspaceId: string): Promise<WorkspaceMember[]> {
  const response = await api.get<WorkspaceMember[]>(
    `/workspaces/${encodeURIComponent(workspaceId)}/members`
  );
  return response.data;
}

/** 移除成员（admin+；不能移除 owner，不能操作同级或更高角色） */
export async function removeMember(workspaceId: string, userId: string): Promise<void> {
  await api.delete(
    `/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`
  );
}

/** 改成员角色（仅 owner；不能设为 owner） */
export async function changeMemberRole(
  workspaceId: string,
  userId: string,
  role: Exclude<WorkspaceRole, "owner">
): Promise<WorkspaceMember> {
  const response = await api.patch<WorkspaceMember>(
    `/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
    { role }
  );
  return response.data;
}

/** 主动退出（owner 不能退出） */
export async function leaveWorkspace(workspaceId: string): Promise<void> {
  await api.post(`/workspaces/${encodeURIComponent(workspaceId)}/leave`);
}

// ===== 邀请 =====

/** 生成一次性邀请码（admin+）。码只在这次返回，之后无法再取。 */
export async function createInvite(
  workspaceId: string,
  role: Exclude<WorkspaceRole, "owner"> = "member"
): Promise<WorkspaceInvite> {
  const response = await api.post<WorkspaceInvite>(
    `/workspaces/${encodeURIComponent(workspaceId)}/invites`,
    { role }
  );
  return response.data;
}

/** 接受邀请码加入（席位满返回 409） */
export async function acceptInvite(code: string): Promise<Workspace> {
  const response = await api.post<Workspace>("/workspaces/accept", { code });
  return response.data;
}

// ===== 批注 =====

/** 列出任务全部批注（含已解决） */
export async function listAnnotations(taskId: string): Promise<Annotation[]> {
  const response = await api.get<Annotation[]>(
    `/tasks/${encodeURIComponent(taskId)}/annotations`
  );
  return response.data;
}

/**
 * 新建批注。
 *
 * `anchor_type="paragraph"` 时传 `anchor_text`（段落原文），**指纹由服务端算**
 * —— 前后端各算一份迟早口径不一致（空白归一化、换行风格）。
 */
export async function createAnnotation(
  taskId: string,
  payload: {
    anchor_type: AnnotationAnchorType;
    content: string;
    anchor_text?: string;
    anchor_key?: string;
  }
): Promise<Annotation> {
  const response = await api.post<Annotation>(
    `/tasks/${encodeURIComponent(taskId)}/annotations`,
    payload
  );
  return response.data;
}

/** 修改批注正文（仅作者本人） */
export async function updateAnnotation(
  taskId: string,
  annotationId: string,
  content: string
): Promise<Annotation> {
  const response = await api.patch<Annotation>(
    `/tasks/${encodeURIComponent(taskId)}/annotations/${encodeURIComponent(annotationId)}`,
    { content }
  );
  return response.data;
}

/** 标记已解决（幂等；不删除批注） */
export async function resolveAnnotation(
  taskId: string,
  annotationId: string
): Promise<Annotation> {
  const response = await api.post<Annotation>(
    `/tasks/${encodeURIComponent(taskId)}/annotations/${encodeURIComponent(annotationId)}/resolve`
  );
  return response.data;
}

/** 重新打开批注 */
export async function reopenAnnotation(
  taskId: string,
  annotationId: string
): Promise<Annotation> {
  const response = await api.post<Annotation>(
    `/tasks/${encodeURIComponent(taskId)}/annotations/${encodeURIComponent(annotationId)}/reopen`
  );
  return response.data;
}

/** 删除批注（本人可删；他人批注需 admin+） */
export async function deleteAnnotation(taskId: string, annotationId: string): Promise<void> {
  await api.delete(
    `/tasks/${encodeURIComponent(taskId)}/annotations/${encodeURIComponent(annotationId)}`
  );
}

/** 段落批注的锚点匹配状态（matched=false 表示原文已变更） */
export async function listParagraphMatches(taskId: string): Promise<ParagraphMatch[]> {
  const response = await api.get<ParagraphMatch[]>(
    `/tasks/${encodeURIComponent(taskId)}/annotations/paragraph-matches`
  );
  return response.data;
}
