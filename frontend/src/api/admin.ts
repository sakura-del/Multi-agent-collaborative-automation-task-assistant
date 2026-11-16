import api from "./client";
import type { ByokOverview } from "./types";

/** 管理员侧 BYOK 渗透率看板（admin only）。 */
export async function getByokOverview(days: number): Promise<ByokOverview> {
  const response = await api.get<ByokOverview>("/admin/byok/overview", {
    params: { days },
  });
  return response.data;
}
