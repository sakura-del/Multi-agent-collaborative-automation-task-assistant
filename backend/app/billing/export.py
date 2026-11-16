"""用量账单 CSV 生成（Issue #15 P2 分账导出）。

## 为什么一份 CSV 同时放汇总与明细

- 用户要能直接打开看「平台用了多少 / 自己的 key 用了多少」→ 需要汇总；
- 对账要能追到每个任务 → 需要明细。

拆成两个文件会让「下载完发现数字对不上」变成常态。用 `类型` 列区分
`汇总` / `明细`，Excel 里一眼能分，脚本也能按列筛选。

## CSV 注入防护（不是可选项）

任务标题是用户输入。`=cmd|' /C calc'!A0` 这类值在 Excel 打开时会当公式执行。
所有字符串单元格：以 `= + - @`、制表符或回车开头时，前面加一个单引号。

## 编码

UTF-8 **带 BOM**：Excel 在中文 Windows 上不认无 BOM 的 UTF-8，会显示乱码。
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

#: CSV 表头（顺序即列顺序）。
HEADERS: tuple[str, ...] = (
    "类型",
    "周期",
    "模型来源",
    "任务数",
    "Token数",
    "任务ID",
    "创建时间",
    "标题",
    "状态",
    "厂商",
    "模型",
    "凭据",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """把任意值转成不会在 Excel 里被当公式执行的字符串。"""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _created_at(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def build_usage_csv(period_key: str, summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """生成账单文本（含 BOM）。`summary` 形如 `model_usage`，`rows` 为任务明细。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADERS)

    for source, label in (("platform", "平台模型"), ("byok", "自带模型"), ("total", "合计")):
        bucket = summary[source]
        writer.writerow(
            [
                "汇总",
                safe_cell(period_key),
                label,
                bucket["tasks"],
                bucket["tokens"],
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )

    for row in rows:
        writer.writerow(
            [
                "明细",
                safe_cell(period_key),
                "自带模型" if row["is_byok"] else "平台模型",
                "",
                row["tokens"],
                safe_cell(row["task_id"]),
                _created_at(row["created_at"]),
                safe_cell(row["title"]),
                safe_cell(row["status"]),
                safe_cell(row["provider"]),
                safe_cell(row["model"]),
                safe_cell(row["credential_name"]),
            ]
        )

    return "\ufeff" + buffer.getvalue()
