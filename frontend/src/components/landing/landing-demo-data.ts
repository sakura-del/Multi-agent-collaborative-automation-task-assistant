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
