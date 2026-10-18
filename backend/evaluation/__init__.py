"""实验层（Plan 5 Task 5）：可复现的实验输入与配置。

与产品代码物理隔离：`app/` 只提供被测量的系统，`evaluation/` 负责
"输入冻结 → 运行 → 指标 → 报告"。实验专用开关（agent_mode / retrieval_mode 等）
只在本包的 runner 内生效，**不暴露到公开任务 API**。
"""

from evaluation.models import (
    EvaluationTopic,
    ExperimentConfig,
    HumanRating,
    TopicDomain,
)

__all__ = [
    "EvaluationTopic",
    "ExperimentConfig",
    "HumanRating",
    "TopicDomain",
]
