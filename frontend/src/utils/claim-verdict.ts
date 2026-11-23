/**
 * 论断核验（Issue #31 Phase 2）的展示映射与载荷解析。
 *
 * `review_report.claim_checks` 是 JSON 透传字段：旧任务没有、坏的条目可能缺键，
 * 所以渲染前必须逐条校验。判定值来自后端 Literal，未知值一律丢弃——
 * 不把「看不懂的判定」伪装成 supported。
 */
import type { ClaimCheckResult, ClaimVerdict } from "@/api/types";

export const CLAIM_VERDICT_LABELS: Record<ClaimVerdict, string> = {
  supported: "支持",
  weakly_supported: "弱支持",
  overstated: "夸大",
  misrepresented: "曲解",
  contradicted: "矛盾",
  unverifiable: "无法核验",
};

/** 徽章配色：只有 contradicted 用强警示，其余按严重度递减 */
export const CLAIM_VERDICT_TONES: Record<ClaimVerdict, string> = {
  supported: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  weakly_supported: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  overstated: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  misrepresented: "border-destructive/30 bg-destructive/10 text-destructive",
  contradicted: "border-destructive/40 bg-destructive/15 text-destructive",
  unverifiable: "border-border bg-muted text-muted-foreground",
};

const CLAIM_VERDICTS = Object.keys(CLAIM_VERDICT_LABELS) as ClaimVerdict[];

function isVerdict(value: unknown): value is ClaimVerdict {
  return typeof value === "string" && (CLAIM_VERDICTS as readonly string[]).includes(value);
}

/** 解析 `review_report.claim_checks`：非法条目逐条丢弃，不猜测、不伪造。 */
export function parseClaimChecks(value: unknown): ClaimCheckResult[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const checks: ClaimCheckResult[] = [];
  for (const item of value) {
    if (typeof item !== "object" || item === null) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const claimId = record.claim_id;
    const sentence = record.sentence_text;
    if (typeof claimId !== "string" || !claimId || typeof sentence !== "string" || !sentence) {
      continue;
    }
    if (!isVerdict(record.verdict)) {
      continue;
    }
    const refs = Array.isArray(record.cited_ref_ids)
      ? record.cited_ref_ids.filter((ref): ref is string => typeof ref === "string")
      : [];
    const confidence =
      typeof record.confidence === "number" && Number.isFinite(record.confidence)
        ? Math.min(1, Math.max(0, record.confidence))
        : 0;
    checks.push({
      claim_id: claimId,
      sentence_text: sentence,
      cited_ref_ids: refs,
      verdict: record.verdict,
      confidence,
      rationale: typeof record.rationale === "string" ? record.rationale : "",
      evidence_quote: typeof record.evidence_quote === "string" ? record.evidence_quote : null,
      suggested_fix: typeof record.suggested_fix === "string" ? record.suggested_fix : null,
      evidence_scope:
        record.evidence_scope === "full_text" || record.evidence_scope === "abstract_only"
          ? record.evidence_scope
          : null,
      evidence_passage:
        typeof record.evidence_passage === "string" ? record.evidence_passage : null,
    });
  }
  return checks;
}

/** 判定严重度排序：抽屉里先展示最需要用户处理的论断。 */
export function claimSeverity(verdict: ClaimVerdict): number {
  return {
    contradicted: 0,
    misrepresented: 1,
    overstated: 2,
    weakly_supported: 3,
    unverifiable: 4,
    supported: 5,
  }[verdict];
}
