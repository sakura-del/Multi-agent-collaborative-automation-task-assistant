/**
 * 自定义论文抽取表预设列模板（Issue #36）。
 */

export interface ExtractionColumnSpec {
  key: string;
  label: string;
  prompt: string;
}

export interface ExtractionTemplate {
  id: string;
  label: string;
  columns: ExtractionColumnSpec[];
}

export const EXTRACTION_TEMPLATES: readonly ExtractionTemplate[] = [
  {
    id: "empirical",
    label: "实证研究对比",
    columns: [
      {
        key: "study_design",
        label: "研究设计",
        prompt: "提取实验或调查设计类型（如 RCT、队列研究、准实验）",
      },
      {
        key: "sample_size",
        label: "样本量",
        prompt: "提取总样本量 N、分组数量及受试者特征",
      },
      {
        key: "intervention",
        label: "干预/方法",
        prompt: "提取核心干预措施或实验变量设置",
      },
      {
        key: "primary_outcome",
        label: "主要结局",
        prompt: "提取主要量化指标、效应量与统计显著性",
      },
      {
        key: "limitations",
        label: "局限性",
        prompt: "提取作者明确指出的研究局限或偏倚风险",
      },
    ],
  },
  {
    id: "ml_paper",
    label: "AI / 算法论文对比",
    columns: [
      {
        key: "method",
        label: "核心方法",
        prompt: "提取提出的模型架构或核心算法创新",
      },
      {
        key: "datasets",
        label: "评测数据集",
        prompt: "提取实验使用的主要基准数据集名称",
      },
      {
        key: "baselines",
        label: "基线模型",
        prompt: "提取对比的主要基线方法",
      },
      {
        key: "metrics",
        label: "核心指标结果",
        prompt: "提取主实验的核心指标数值与相对提升",
      },
      {
        key: "code_url",
        label: "开源代码",
        prompt: "提取论文中明确给出的代码仓库或模型权重链接",
      },
    ],
  },
] as const;
