import type { WorkflowPreset } from "@/api/types";

/**
 * 工作流预设唯一配置源（Issue #32）。
 *
 * 表单创建页（TaskCreateForm）与侧栏全局入口（GlobalPromptEntry）共用这份定义，
 * 避免两处维护不同的默认参数或文案导致行为漂移。
 */
export interface WorkflowPresetOption {
  id: WorkflowPreset;
  label: string;
  shortLabel: string;
  description: string;
  topicPlaceholder: string;
  promptPlaceholder: string;
  /** 是否出现在侧栏极简一句话入口（需要大段粘贴草稿的预设默认不出现在侧栏） */
  allowGlobalPrompt?: boolean;
  defaults: {
    maxDocuments: number;
    verifyReferences: boolean;
  };
}

export const WORKFLOW_PRESETS: readonly WorkflowPresetOption[] = [
  {
    id: "literature_review",
    label: "文献综述",
    shortLabel: "文献综述",
    description: "按主题系统检索文献、抽取证据并撰写带可回溯引用的综述正文。",
    topicPlaceholder: "例如：检索增强生成与多智能体协作的质量评估",
    promptPlaceholder: "一句话开始一篇综述…",
    allowGlobalPrompt: true,
    defaults: {
      maxDocuments: 20,
      verifyReferences: false,
    },
  },
  {
    id: "research_gaps",
    label: "研究空白分析",
    shortLabel: "研究空白",
    description: "重点挖掘证据薄弱点、结论冲突与未充分研究的问题，辅助选题与立项。",
    topicPlaceholder: "例如：多模态大语言模型在医学影像诊断中的研究空白与争议",
    promptPlaceholder: "输入研究方向，挖掘尚未解决的研究空白…",
    allowGlobalPrompt: true,
    defaults: {
      maxDocuments: 25,
      verifyReferences: true,
    },
  },
  {
    id: "verify_citations",
    label: "验证引用",
    shortLabel: "验证引用",
    description: "粘贴已有草稿或勾选知识库文档，直接核验参考文献真实性、撤稿风险与论断支撑度。",
    topicPlaceholder: "例如：论文初稿第 2 章引用核验（留空将自动命名）",
    promptPlaceholder: "粘贴待核验草稿…",
    allowGlobalPrompt: false,
    defaults: {
      maxDocuments: 20,
      verifyReferences: true,
    },
  },
] as const;

export const GLOBAL_PROMPT_PRESETS: readonly WorkflowPresetOption[] =
  WORKFLOW_PRESETS.filter((preset) => preset.allowGlobalPrompt !== false);

export const DEFAULT_WORKFLOW_PRESET: WorkflowPreset = "literature_review";

export function getWorkflowPreset(id?: string | null): WorkflowPresetOption {
  return (
    WORKFLOW_PRESETS.find((preset) => preset.id === id) ?? WORKFLOW_PRESETS[0]
  );
}
