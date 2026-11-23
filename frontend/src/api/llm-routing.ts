import api from "./client";

// 节点级模型路由（Issue #22 延伸：混合路由的用户级配置）。
//
// 语义：
// - 六个节点类型（planner/search/extract/write/validate/review）各可绑定一把凭据；
// - 未配置的节点 = 跟随任务勾选的模型（默认行为不变）；
// - 后端执行时按「用户路由 > 平台路由 > 任务勾选」的优先级解析。

/** 可路由的节点类型与展示名（顺序即 UI 展示顺序） */
export const ROUTABLE_NODE_TYPES: { value: string; label: string; hint: string }[] = [
  { value: "planner", label: "规划", hint: "任务大纲与子任务拆解，对逻辑能力要求最高" },
  { value: "search", label: "检索", hint: "文献检索结果汇总（不走 LLM 时自动跳过）" },
  { value: "extract", label: "证据抽取", hint: "长输入的文献证据提取，吃上下文窗口" },
  { value: "write", label: "正文写作", hint: "综述章节撰写，输出质量敏感" },
  { value: "validate", label: "校验", hint: "逐节点质量校验，JSON 短输出" },
  { value: "review", label: "复盘", hint: "终稿摘要、局限性与统计" },
];

export interface NodeRouteItem {
  node_type: string;
  credential_id: string;
  credential_name: string;
  model: string;
  provider: string;
}

export interface NodeRoutingState {
  routes: NodeRouteItem[];
}

/** 列出我的节点路由 */
export async function listNodeRouting(): Promise<NodeRouteItem[]> {
  const response = await api.get<NodeRoutingState>("/llm-routing");
  return response.data.routes;
}

/** 全量替换：条目缺失/credential_id 为 null 的节点 = 移除路由（跟随任务勾选） */
export async function updateNodeRouting(
  routes: { node_type: string; credential_id: string | null }[],
): Promise<NodeRouteItem[]> {
  const response = await api.put<NodeRoutingState>("/llm-routing", { routes });
  return response.data.routes;
}

