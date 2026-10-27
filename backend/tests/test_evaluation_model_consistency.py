"""实验画像记录的模型必须与实际执行模型一致。

## 为什么需要这条测试（2026-10-27）

`evaluation/models.py` 里 `FROZEN_MODEL = "gpt-6-luna"`，四份画像 yaml 也都写
`model: gpt-6-luna`。这个字段会被写进每个 run 的 `config.json`，是论文里
「用什么模型跑的」的唯一记录。

但**实际执行不走这个字段** —— `scripts/run_experiments.py` 用
`get_settings().llm`（当前是 `workbuddy-ai/deepseek-v4.1-flash`）。
也就是说：**画像说 gpt-6-luna，实际跑 DeepSeek**。

`gpt-6-luna` 已在 2026-10-27 被端点下架，不可能真的用它跑。
如果就这么跑 60 个 run，产出的 `config.json` 全是错的 ——
论文写「用 gpt-6-luna 实验」而实际不是，这是**编造实验条件**。

## 断言什么

`FROZEN_MODEL` 必须与**运行时实际配置的模型**一致。
这样换模型时（像今天）会强制同步画像，而不是静默漂移。
"""

from __future__ import annotations

from pathlib import Path

import yaml


def test_frozen_model_matches_runtime_config() -> None:
    """FROZEN_MODEL 必须等于 config/default.yaml 的 llm.default_model。"""
    from evaluation.models import FROZEN_MODEL

    repo_root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load((repo_root / "config" / "default.yaml").read_text(encoding="utf-8"))
    runtime_model = data["llm"]["default_model"]

    assert FROZEN_MODEL == runtime_model, (
        f"实验画像记录的模型 {FROZEN_MODEL!r} 与运行时配置 {runtime_model!r} 不一致。\n"
        "画像里的 model 会写进每个 run 的 config.json（论文记录实验条件用），"
        "而实际执行走 settings.llm.default_model —— 两者必须一致，"
        "否则产出的实验元数据是假的。"
    )


def test_all_profiles_use_frozen_model() -> None:
    """四份画像 yaml 的 model 必须都等于 FROZEN_MODEL。"""
    from evaluation.models import FROZEN_MODEL

    configs_dir = Path(__file__).resolve().parents[1] / "evaluation" / "configs"
    files = ["baseline_a.yaml", "baseline_b.yaml", "baseline_c.yaml", "full.yaml"]

    for name in files:
        data = yaml.safe_load((configs_dir / name).read_text(encoding="utf-8"))
        assert (
            data["model"] == FROZEN_MODEL
        ), f"{name} 的 model 是 {data['model']!r}，与 FROZEN_MODEL={FROZEN_MODEL!r} 不一致"
