/**
 * 漏斗数据模型（照搬 beUI `buildFunnel` 的语义，去掉装饰性字段）。
 *
 * ## 为什么不直接按数量画宽
 *
 * PRISMA 的「识别」可能是 165 条，而「纳入」只有 1 条。若按绝对数量映射宽度，
 * 最后一段会细到看不见、也无法判断「相对上一阶段掉了多少」。所以同时给三个数：
 * - `proportion`：相对首段（画漏斗宽度）
 * - `conversion`：相对首段（总体转化，看全局流失）
 * - `stepConversion`：相对上一段（看**这一步**流失了多少，才是可行动的）
 */

export interface FunnelStageInput {
  id: string;
  label: string;
  value: number;
  /** 本阶段排除数（PRISMA 的 excluded），可选。 */
  excluded?: number;
}

export interface FunnelRow extends FunnelStageInput {
  /** 相对首段的比例 0–1，用于梯形宽度。 */
  proportion: number;
  /** 相对首段的转化率（百分比，保留 1 位）。 */
  conversion: number;
  /** 相对上一段的阶段转化率；首段为 100。上一段为 0 时记 0。 */
  stepConversion: number;
}

export interface FunnelModel {
  rows: FunnelRow[];
  /** 末段 / 首段（百分比，保留 1 位）；首段为 0 时记 0。 */
  overall: number;
}

const round1 = (value: number): number => Math.round(value * 10) / 10;

export function buildFunnel(stages: readonly FunnelStageInput[]): FunnelModel {
  // 去重 id + 丢弃非法值：NaN 会让整条 SVG path 变成 "M NaN"，整张图直接消失
  const seen = new Set<string>();
  const valid: FunnelStageInput[] = [];
  for (const stage of stages) {
    if (seen.has(stage.id)) continue;
    if (!Number.isFinite(stage.value) || stage.value < 0) continue;
    seen.add(stage.id);
    valid.push(stage);
  }

  const first = valid[0]?.value ?? 0;
  const rows: FunnelRow[] = valid.map((stage, index) => {
    const previous = index === 0 ? stage.value : valid[index - 1].value;
    return {
      ...stage,
      proportion: first > 0 ? stage.value / first : 0,
      conversion: first > 0 ? round1((stage.value / first) * 100) : 0,
      stepConversion: index === 0 ? 100 : previous > 0 ? round1((stage.value / previous) * 100) : 0,
    };
  });

  const last = valid.length > 0 ? valid[valid.length - 1].value : 0;
  return { rows, overall: first > 0 ? round1((last / first) * 100) : 0 };
}
