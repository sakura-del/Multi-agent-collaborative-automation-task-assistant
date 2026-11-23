/**
 * 落地页可交互工作台预览的示范数据（显式标注为示范内容，不冒充真实任务状态）。
 */

export type DemoVerdict = "include" | "maybe" | "exclude";
export type DemoCslStyle = "gbt7714" | "apa" | "ieee" | "nature";

export interface DemoScreeningPaper {
  id: string;
  score: number;
  title: string;
  venue: string;
  year: number;
  source: "arXiv" | "PubMed" | "OpenAlex" | "Semantic Scholar";
  reason: string;
  initialVerdict: DemoVerdict;
}

export interface DemoMatrixRow {
  id: string;
  title: string;
  year: number;
  source: "arXiv" | "PubMed" | "OpenAlex" | "Semantic Scholar";
  method: { text: string; quote: string; section: string };
  sample: { text: string; quote: string; section: string };
  finding: { text: string; quote: string; section: string };
  limitation: { text: string; quote: string; section: string };
}

export interface DemoCitation {
  index: number;
  title: string;
  authors: string;
  venue: string;
  year: number;
  doi: string;
  source: string;
  quote: string;
  locator: string;
  formatted: Record<DemoCslStyle, string>;
}

export const DEMO_SCREENING_PAPERS: DemoScreeningPaper[] = [
  {
    id: "p1",
    score: 96,
    title: "Evaluating Faithfulness and Citation Grounding in Retrieval-Augmented Literature Synthesis",
    venue: "ACL Findings",
    year: 2025,
    source: "arXiv",
    reason: "直接提出综述场景下的引文忠实度与证据覆盖率双指标基准，与核心研究问题高度契合。",
    initialVerdict: "include",
  },
  {
    id: "p2",
    score: 92,
    title: "Automated PRISMA 2020 Screening via Multi-Agent Deliberation and Active Prioritization",
    venue: "Journal of Biomedical Informatics",
    year: 2025,
    source: "PubMed",
    reason: "验证了多智能体协同初筛在系统综述中的召回率（97.4%）与人工复核工作量降幅。",
    initialVerdict: "include",
  },
  {
    id: "p3",
    score: 84,
    title: "Cross-Database Deduplication and Metadata Reconciliation for Scholarly Knowledge Graphs",
    venue: "Scientometrics",
    year: 2024,
    source: "OpenAlex",
    reason: "聚焦跨库题录对齐与 DOI 消歧，可作为方法学背景引用，待全文评估实验细节。",
    initialVerdict: "maybe",
  },
  {
    id: "p4",
    score: 71,
    title: "Prompt Engineering Patterns for General-Purpose Conversational Question Answering",
    venue: "IEEE Access",
    year: 2024,
    source: "Semantic Scholar",
    reason: "仅讨论开放域对话提示词，未涉及学术引用核验或系统综述流程，建议排除。",
    initialVerdict: "exclude",
  },
];

export const DEMO_AGENT_STAGES = [
  { id: "planner", label: "Planner 拆解提纲", status: "done" as const },
  { id: "executor", label: "Executor 多源提取", status: "done" as const },
  { id: "reviewer", label: "Reviewer 交叉审稿中", status: "active" as const },
  { id: "validator", label: "Validator 引文核验", status: "pending" as const },
];

export const DEMO_MATRIX_ROWS: DemoMatrixRow[] = [
  {
    id: "m1",
    title: "Faithfulness & Citation Grounding in RAG Synthesis",
    year: 2025,
    source: "arXiv",
    method: {
      text: "双阶段检索 + 句级证据对齐验证器",
      quote: "We introduce a two-stage hybrid retriever paired with a sentence-level entailment verifier that binds every synthesized claim to explicit source spans.",
      section: "Section 3.2 · Architecture",
    },
    sample: {
      text: "420 篇计算机与生物医学综述样本",
      quote: "The evaluation benchmark spans 420 peer-reviewed survey papers balanced across computer science (210) and biomedicine (210).",
      section: "Section 4.1 · Benchmark",
    },
    finding: {
      text: "幻觉引用率由 18.6% 降至 1.2%",
      quote: "Enforcing post-hoc Crossref verification alongside span grounding reduced unverified or fabricated citations from 18.6% down to 1.2%.",
      section: "Section 5.1 · Main Results",
    },
    limitation: {
      text: "未覆盖非英文区域性期刊元数据",
      quote: "Metadata reconciliation currently focuses on English-indexed venues and does not yet cover regional non-English citation databases.",
      section: "Section 6 · Limitations",
    },
  },
  {
    id: "m2",
    title: "Multi-Agent Deliberation for PRISMA 2020 Screening",
    year: 2025,
    source: "PubMed",
    method: {
      text: "三角色辩论初筛 + 主动学习排序",
      quote: "Three specialized screening agents independently assess inclusion criteria before resolving boundary cases via structured deliberation.",
      section: "Methods · Multi-Agent Protocol",
    },
    sample: {
      text: "12 项临床系统综述，共 14,380 条题录",
      quote: "We retrospectively evaluated the protocol across 12 published clinical systematic reviews comprising 14,380 candidate abstracts.",
      section: "Materials · Datasets",
    },
    finding: {
      text: "达到 97.4% 召回率，节省 68% 审阅工时",
      quote: "At the 95% recall target, human workload decreased by 68.2% while achieving an empirical recall of 97.4%.",
      section: "Results · Screening Efficiency",
    },
    limitation: {
      text: "全文 PDF 复杂表格解析偶发漏检",
      quote: "Outcome extraction from multi-page rotated clinical tables remains susceptible to layout fragmentation.",
      section: "Discussion · Error Analysis",
    },
  },
  {
    id: "m3",
    title: "Cross-Database Scholarly Metadata Reconciliation",
    year: 2024,
    source: "OpenAlex",
    method: {
      text: "DOI 规范化 + 标题语义近似去重",
      quote: "Candidate records are canonicalized via DOI normalization followed by title-author trigram and dense embedding deduplication.",
      section: "Section 2.3 · Deduplication Pipeline",
    },
    sample: {
      text: "跨越 4 个开放学术库的 28 万条记录",
      quote: "Experiments cover 280,000 scholarly records harvested simultaneously from arXiv, PubMed, OpenAlex, and Semantic Scholar.",
      section: "Section 3 · Corpus Construction",
    },
    finding: {
      text: "跨库去重 F1 达 99.1%，保留完整溯源链",
      quote: "Hybrid canonicalization achieves 99.1% deduplication F1 while preserving provenance pointers to every originating repository.",
      section: "Section 4.2 · Accuracy",
    },
    limitation: {
      text: "预印本与正式发表版偶发作者序差异",
      quote: "Author list revisions between arXiv preprints and final journal versions occasionally lower string-match confidence.",
      section: "Section 5 · Edge Cases",
    },
  },
];

export const DEMO_CSL_OPTIONS: { value: DemoCslStyle; label: string }[] = [
  { value: "gbt7714", label: "GB/T 7714-2025" },
  { value: "apa", label: "APA 7th" },
  { value: "ieee", label: "IEEE" },
  { value: "nature", label: "Nature" },
];

export const DEMO_CITATIONS: DemoCitation[] = [
  {
    index: 1,
    title: "Evaluating Faithfulness and Citation Grounding in Retrieval-Augmented Literature Synthesis",
    authors: "Lin Y, Chen W, Patel R",
    venue: "Findings of the Association for Computational Linguistics (ACL)",
    year: 2025,
    doi: "10.18653/v1/2025.findings-acl.418",
    source: "Crossref · OpenAlex 双库核验",
    quote: "在系统性文献综述场景中，引入句级证据绑定与外部题录核验后，模型生成文本的不可追溯引用率由 18.6% 显著下降至 1.2%，同时保持了完整的论点覆盖度。",
    locator: "Section 5.1, Table 3 · p. 1142",
    formatted: {
      gbt7714: "[1] LIN Y, CHEN W, PATEL R. Evaluating faithfulness and citation grounding in retrieval-augmented literature synthesis[C]//Findings of the Association for Computational Linguistics: ACL 2025. 2025: 1138-1151. DOI:10.18653/v1/2025.findings-acl.418.",
      apa: "Lin, Y., Chen, W., & Patel, R. (2025). Evaluating faithfulness and citation grounding in retrieval-augmented literature synthesis. In Findings of the Association for Computational Linguistics: ACL 2025 (pp. 1138–1151). https://doi.org/10.18653/v1/2025.findings-acl.418",
      ieee: "[1] Y. Lin, W. Chen, and R. Patel, \"Evaluating faithfulness and citation grounding in retrieval-augmented literature synthesis,\" in Findings Assoc. Comput. Linguistics (ACL), 2025, pp. 1138–1151, doi: 10.18653/v1/2025.findings-acl.418.",
      nature: "1. Lin, Y., Chen, W. & Patel, R. Evaluating faithfulness and citation grounding in retrieval-augmented literature synthesis. Findings Assoc. Comput. Linguist. 1138–1151 (2025).",
    },
  },
  {
    index: 2,
    title: "Automated PRISMA 2020 Screening via Multi-Agent Deliberation and Active Prioritization",
    authors: "Zhou H, Miller K, Sato T",
    venue: "Journal of Biomedical Informatics",
    year: 2025,
    doi: "10.1016/j.jbi.2025.104612",
    source: "PubMed · Crossref 双库核验",
    quote: "跨 12 项临床系统综述的回溯评估表明，基于多智能体分工与主动学习优先排序的 PRISMA 2020 初筛流程在 97.4% 召回率下将人工审阅工时减少了 68.2%。",
    locator: "Results, Section 3.3 · p. 7",
    formatted: {
      gbt7714: "[2] ZHOU H, MILLER K, SATO T. Automated PRISMA 2020 screening via multi-agent deliberation and active prioritization[J]. Journal of Biomedical Informatics, 2025, 162: 104612. DOI:10.1016/j.jbi.2025.104612.",
      apa: "Zhou, H., Miller, K., & Sato, T. (2025). Automated PRISMA 2020 screening via multi-agent deliberation and active prioritization. Journal of Biomedical Informatics, 162, 104612. https://doi.org/10.1016/j.jbi.2025.104612",
      ieee: "[2] H. Zhou, K. Miller, and T. Sato, \"Automated PRISMA 2020 screening via multi-agent deliberation and active prioritization,\" J. Biomed. Inform., vol. 162, Art. no. 104612, 2025, doi: 10.1016/j.jbi.2025.104612.",
      nature: "2. Zhou, H., Miller, K. & Sato, T. Automated PRISMA 2020 screening via multi-agent deliberation and active prioritization. J. Biomed. Inform. 162, 104612 (2025).",
    },
  },
];

export const TRUST_SOURCES = [
  "arXiv",
  "PubMed",
  "OpenAlex",
  "Semantic Scholar",
  "Crossref",
  "PRISMA 2020",
  "CSL 1.0",
] as const;

export interface DemoFullReport {
  taskId: string;
  title: string;
  model: string;
  durationText: string;
  searchedCount: number;
  includedCount: number;
  verification: {
    verified: number;
    partial: number;
    dubious: number;
    unverified: number;
  };
  sections: {
    heading: string;
    paragraphs: string[];
  }[];
}

/** 真实示范任务脱敏快照：用于落地页「阅读示范报告」右侧抽屉展示 */
export const DEMO_FULL_REPORT: DemoFullReport = {
  taskId: "demo-rag-faithfulness-2025",
  title: "检索增强生成（RAG）在学术文献综述中的引文忠实度与自动化筛选研究进展",
  model: "DeepSeek-V4.1 · 混合路由",
  durationText: "11 分 42 秒",
  searchedCount: 1248,
  includedCount: 18,
  verification: {
    verified: 16,
    partial: 2,
    dubious: 0,
    unverified: 0,
  },
  sections: [
    {
      heading: "摘要",
      paragraphs: [
        "本综述系统梳理了 2023—2025 年间检索增强生成（RAG）技术在学术文献综述自动化中的应用进展，重点评估引文忠实度约束机制与 PRISMA 2020 标准化筛选流程的协同效能。基于 arXiv、PubMed、OpenAlex 与 Semantic Scholar 四个数据库共检索 1,248 篇候选文献，经跨库 DOI 去重与双阶段纳排后最终纳入 18 篇核心研究。",
      ],
    },
    {
      heading: "1. 引言与研究动机",
      paragraphs: [
        "通用大语言模型在长文本综述生成中普遍存在“幻觉引用”问题，即生成不存在的题录或将真实文献错配至无关论断。现有研究表明，仅依赖参数化记忆的生成方式在学术写作场景中的引用错误率高达 18% 以上 [1]。因此，如何将每一句生成论断严格锚定至可回溯的原始文献片段，成为严肃科研工作台的核心工程命题。",
      ],
    },
    {
      heading: "2. 多智能体 PRISMA 2020 筛选架构",
      paragraphs: [
        "在系统综述的数据准备阶段，传统人工初筛面临候选集庞大与标准漂移的挑战。通过引入分工明确的多智能体审议协议与主动学习优先级排序，系统能够在保持 97.4% 召回率的同时将人工审阅工时降低 68.2% [2]。每条纳入或排除决策均附带可审计的理由摘要，满足 PRISMA 2020 透明报告规范。",
      ],
    },
    {
      heading: "3. 证据绑定与跨库引文核验",
      paragraphs: [
        "在成稿阶段，四阶段协同流水线（Planner → Executor → Reviewer → Validator）先基于结构化提取矩阵组织论点，再由独立核验器向 Crossref 与 OpenAlex 发起 DOI 与题录一致性校验 [1]。实证结果显示，引入后置跨库核验与句级证据绑定后，不可追溯引用率降至 1.2%，显著提升了综述产出的学术可信度。",
      ],
    },
  ],
};
