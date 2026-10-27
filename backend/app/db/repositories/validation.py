"""校验结果 Repository：把校验 Agent 的结论写入 `validation_result` 表。

## 为什么需要它（2026-10-27）

`validation_result` 表自建库起就存在（含 `passed` / `score` / `rule_results` /
`llm_report` / `report` 五列），但**全 `app/` 里没有任何代码实例化
`ValidationResult`** —— 它是张空表。校验结论只被塞进
`subtask.output_data` 的 JSON，没有任何可查询的结构。

后果（都是实测踩到的）：

| 场景 | 影响 |
| --- | --- |
| 排查「为什么转人工」 | 只能逐个任务拉 API 快照，无法按失败原因聚合 |
| 统计校验通过率 | 做不到（`passed` 列形同虚设） |
| 量化缺陷影响面 | 做不到（review 长度上限缺陷就因此无法量化） |

## 三个必须注意的语义

1. **`score` 要归一化**：契约 `ValidationReport.score` 是 **0–100**，
   而列注释写的是 **0–1**。直接把 85.5 存进去会超出列语义，
   下游按 0–1 解读时得到「8550% 通过率」。
2. **缺省从严**：报告形状不对（空 dict / 缺 `passed`）时按 `passed=False`、
   `score=0.0` 落库 —— 宁可留下「有失败」，也不要伪造「通过」。
3. **只 flush 不 commit**：与 `EventRepository` 一致，事务由调用方控制。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.db.models.log import ValidationResult
from app.db.models.task import Subtask
from app.db.repositories.base import BaseRepository

# 契约评分上限（`ValidationReport.score` 是 0–100）
_CONTRACT_SCORE_MAX = 100.0


def normalize_score(raw: Any) -> float:
    """把契约的 0–100 分归一化到列的 0–1 语义，并夹紧边界。

    非数字 / 无法解析一律返回 0.0（从严：不伪造「高分通过」）。
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(value / _CONTRACT_SCORE_MAX, 1.0))


class ValidationRepository(BaseRepository[ValidationResult]):
    """`validation_result` 的写入。"""

    model = ValidationResult

    async def save(
        self,
        subtask_id: UUID,
        report: dict[str, Any] | None,
    ) -> ValidationResult | None:
        """把一份校验报告写入数据库；subtask 不存在时返回 None。

        报告形状参照 `ValidationReport`：
        `{passed, score, checks, retryable, feedback}`，
        可选 `semantic_check`（LLM 自检结论）与 `rule_check`（规则明细）。

        只 flush 不 commit。
        """
        subtask = await self.db.get(Subtask, subtask_id)
        if subtask is None:
            return None

        body = report if isinstance(report, dict) else {}
        checks = body.get("checks")
        rule_results = checks if isinstance(checks, list) else []

        semantic = body.get("semantic_check")
        llm_report = semantic if isinstance(semantic, dict) and semantic else None

        feedback = body.get("feedback")
        row = ValidationResult(
            subtask_id=subtask_id,
            # 缺省从严：报告缺失/形状不对时记为「未通过」，不伪造通过
            passed=body.get("passed") is True,
            score=normalize_score(body.get("score")),
            rule_results=rule_results,
            llm_report=llm_report,
            report=str(feedback) if feedback is not None else None,
            validated_at=datetime.now(UTC),
        )
        self.db.add(row)
        await self.db.flush()
        return row
