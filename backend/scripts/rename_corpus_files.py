"""把语料 PDF 重命名为「可读标题 + arXiv id」，并同步更新清单中的 local_path。

命名规则（便于人工入库时一眼认出内容）：
    <序号>_<短标题>__<arXiv-id>.pdf
例：01_AR-RAG__2506.06962v3.pdf

只改文件名与清单里的 local_path，不动 sha256 / 记录顺序 / 其它字段，
因此重命名后 `python -m evaluation.validate` 仍然通过。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CORPORA = BACKEND / "evaluation" / "datasets" / "corpora"
DEMO = BACKEND / "demo_data" / "literature_review"

# 短标题：人工挑选，比机器截断的 title 更易读（与清单顺序一一对应）
SHORT_TITLES: dict[str, list[str]] = {
    "ai": [
        "AR-RAG_Autoregressive_Retrieval",
        "Ragas_RAG_Evaluation",
        "MultiAgent_Collaborative_RAG",
        "ContextAware_Cognitive_Augmentation",
        "Factually_Wearable_FactCheck",
        "Automated_Literature_Review_NLP_LLM",
        "AI_Augment_Human_Performed",
        "Conventions_MultiAgent_Action_Space",
        "FAIR-RAG_Faithful_Adaptive_Refinement",
        "EVOR_Evolving_Retrieval_CodeGen",
    ],
    "software_engineering": [
        "JaCoText_Java_CodeText_Generation",
        "Future_Scientific_Publishing_AutoGen",
        "DeepCodeSeek_Realtime_API_Retrieval",
        "ChatUniTest_LLM_Test_Generation",
        "Automating_Code_Review",
        "LLM_Unit_Test_Java_Assessment",
        "Automated_Literature_Review_NLP_LLM",
        "EVOR_Evolving_Retrieval_CodeGen",
        "Reformulation_Automated_Planning_SLR",
        "DefectFocused_Code_Review",
    ],
    "education_technology": [
        "Learner_Control_Explainable_Analytics",
        "Tutoring_Systems_Systematic_Review",
        "LLM_Assess_Tutor_Performance",
        "Language_Alignment_Nash_Learning",
        "Online_Learning_Fairness_Regularizers",
        "Analytics_SelfRegulated_Learning",
        "RLHF_Blender_Interactive_Interface",
        "Doer_Effect_Beyond_WEIRD",
        "LAVA_Learning_Analytics_Visual",
        "Tutor_Equity_Training_GenAI",
    ],
}

# Plan 3 演示语料（10 篇，与 ai 领域前 10 篇同源）
DEMO_TITLES = SHORT_TITLES["ai"]


def _arxiv_id(source_id: str) -> str:
    """从 source_id（如 2506.06962v3）取稳定 id 片段。"""
    match = re.search(r"(\d{4}\.\d{4,5}v\d+)", source_id)
    return match.group(1) if match else source_id.replace("/", "_")


def rename_corpus(domain: str, titles: list[str], *, dry_run: bool = False) -> int:
    manifest_path = CORPORA / f"{domain}.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files_dir = CORPORA / f"{domain}-files"
    records = data["records"]
    if len(titles) < len(records):
        raise SystemExit(f"{domain}: 标题数量不足（{len(titles)} < {len(records)}）")

    for index, record in enumerate(records):
        old_name = record["local_path"].split("/")[-1]
        old_path = files_dir / old_name
        new_name = f"{index + 1:02d}_{titles[index]}__{_arxiv_id(record['source_id'])}.pdf"
        new_path = files_dir / new_name
        record["local_path"] = f"{domain}-files/{new_name}"
        if dry_run:
            print(f"  {old_name}  ->  {new_name}")
            continue
        if old_path.is_file() and not new_path.exists():
            old_path.rename(new_path)

    if not dry_run:
        manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return len(records)


def rename_demo(*, dry_run: bool = False) -> int:
    manifest_path = DEMO / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files_dir = DEMO / "files"
    records = data["records"]

    for index, record in enumerate(records):
        old_name = record["local_path"].split("/")[-1]
        old_path = files_dir / old_name
        new_name = f"{index + 1:02d}_{DEMO_TITLES[index]}__{_arxiv_id(record['source_id'])}.pdf"
        new_path = files_dir / new_name
        record["local_path"] = f"files/{new_name}"
        if dry_run:
            print(f"  {old_name}  ->  {new_name}")
            continue
        if old_path.is_file() and not new_path.exists():
            old_path.rename(new_path)

    if not dry_run:
        manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return len(records)


def main() -> int:
    print("=== 实验语料重命名 ===")
    for domain, titles in SHORT_TITLES.items():
        print(f"[{domain}]")
        count = rename_corpus(domain, titles)
        print(f"  已处理 {count} 个文件\n")
    print("=== Plan 3 演示语料重命名 ===")
    count = rename_demo()
    print(f"  已处理 {count} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
