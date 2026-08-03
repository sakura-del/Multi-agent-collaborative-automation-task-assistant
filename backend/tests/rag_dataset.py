"""RAG 检索质量标准测试集：30 条 query-doc 对，覆盖项目核心知识域。

用途：
- 检索质量回归测试：每次改动检索组件后运行，确保质量不退化
- A/B 对比实验：对比不同分块策略、嵌入模型、权重配比的效果
- 验收集验收：满足"Top-5 中至少 3 条相关"的验收标准

文档主题分布：
- RAG 检索增强（5 篇）
- 多智能体协作（5 篇）
- DAG 引擎与调度（5 篇）
- 重试与恢复（3 篇）
- 缓存与索引（4 篇）

每条 query 标注 expected_doc_ids（相关文档 ID 列表），用于计算 Recall@K / Precision@K / MRR。
"""

from __future__ import annotations

# ===== 标准文档集（10 篇，覆盖项目核心知识域）=====

STANDARD_DOCUMENTS: list[dict] = [
    {
        "doc_id": "doc-rag-overview",
        "text": (
            "RAG（检索增强生成）通过向量检索从知识库召回相关文档片段，"
            "并将其作为上下文注入 LLM 提示词，从而提升回答的事实准确性。"
            "RAG 的核心流程包括文档解析、分块、嵌入、向量索引、混合检索与重排。"
            "相比纯参数化模型，RAG 能利用外部知识库减少幻觉，适合企业知识问答场景。"
        ),
    },
    {
        "doc_id": "doc-rag-chunking",
        "text": (
            "智能文档分块是 RAG 流水线的关键环节。"
            "递归字符分块按分隔符层级切分，保留语义连贯性，是默认策略。"
            "语义分块按文档结构（标题/段落）切分，保留章节完整性。"
            "滑动窗口分块固定窗口加重叠，适合需要上下文连续性的场景。"
            "chunk_size 与 chunk_overlap 是影响检索精度的关键参数。"
        ),
    },
    {
        "doc_id": "doc-rag-hybrid",
        "text": (
            "混合检索引擎融合向量检索与 BM25 关键词检索的结果。"
            "向量检索基于语义相似度，能理解同义词与语义关系。"
            "BM25 基于关键词字面匹配，对专有名词与精确查询更有效。"
            "RRF（Reciprocal Rank Fusion）算法按排名融合两路结果，"
            "无需得分归一化，对排名而非得分敏感，鲁棒性强。"
        ),
    },
    {
        "doc_id": "doc-rag-reranker",
        "text": (
            "重排模型对混合检索 Top-K 结果进行精排，提升检索精度。"
            "BGE-Reranker 是交叉编码器，将 query 与 document 拼接输入模型，"
            "直接输出相关性得分，比双塔模型的余弦相似度更精确。"
            "重排计算成本较高，因此仅对 Top-K（通常 10-20 条）结果精排。"
            "重排后得分归一化到 0-1 区间，便于下游阈值过滤。"
        ),
    },
    {
        "doc_id": "doc-rag-cache",
        "text": (
            "检索缓存层基于 Redis 缓存高频查询结果，降低重复检索延迟。"
            "缓存键由 query 与 top_k 的 MD5 哈希生成，避免长字符串键。"
            "TTL 自动过期机制保证缓存数据新鲜度，默认 300 秒。"
            "Redis 不可用时降级为进程内字典缓存，保证服务可用性。"
            "缓存命中率目标 80% 以上，可显著降低检索延迟。"
        ),
    },
    {
        "doc_id": "doc-agent-overview",
        "text": (
            "多智能体协作系统由 Planner、Executor、Validator、Reviewer 四个 Agent 组成。"
            "Planner 负责任务拆解与 DAG 生成，Executor 调用 LLM 执行子任务，"
            "Validator 进行规则校验，Reviewer 收集日志与反馈。"
            "Agent 间通过消息协议通信，实现拆解-执行-校验-复审的闭环。"
        ),
    },
    {
        "doc_id": "doc-agent-comm",
        "text": (
            "Agent 通信层基于消息协议实现智能体间协作。"
            "消息结构包含 sender、receiver、message_type、payload、trace_id。"
            "支持同步请求-回复与异步事件广播两种模式。"
            "消息队列保证顺序性与可靠性，trace_id 贯穿调用链便于追踪。"
        ),
    },
    {
        "doc_id": "doc_dag_engine",
        "text": (
            "DAG 引擎基于 networkx 实现任务依赖图管理。"
            "支持节点添加、边添加、循环依赖检测、拓扑排序、子图提取。"
            "添加边时即时检测循环，若形成环则自动回滚并抛出 DAGCycleError。"
            "拓扑排序确定任务执行顺序，无依赖的任务可并行调度。"
        ),
    },
    {
        "doc_id": "doc_scheduler",
        "text": (
            "任务调度器按拓扑序将子任务分发给对应 Agent 执行。"
            "调度策略综合紧急度、依赖深度与 Agent 负载进行优先级排序。"
            "支持动态负载均衡，避免单个 Agent 任务堆积。"
            "独立子任务可并行调度，缩短整体任务完成时间。"
        ),
    },
    {
        "doc_id": "doc_retry_recovery",
        "text": (
            "自动重试机制采用指数退避策略（1s 3s 9s，最多 3 次）。"
            "每次重试自动切换策略：调整温度参数、修改 Prompt、更换工具。"
            "重试耗尽后任务状态转为 WAITING_HUMAN 等待人工干预。"
            "断点续传通过任务状态持久化实现，服务重启后可恢复未完成任务。"
        ),
    },
]


# ===== 标准查询集（30 条 query，标注相关文档）=====

STANDARD_QUERIES: list[dict] = [
    # RAG 基础概念（6 条）
    {
        "query": "什么是 RAG 检索增强生成",
        "expected_doc_ids": ["doc-rag-overview"],
    },
    {
        "query": "RAG 的核心流程包含哪些步骤",
        "expected_doc_ids": ["doc-rag-overview"],
    },
    {
        "query": "RAG 如何减少大模型的幻觉",
        "expected_doc_ids": ["doc-rag-overview"],
    },
    # 分块策略（5 条）
    {
        "query": "文档分块有哪些策略",
        "expected_doc_ids": ["doc-rag-chunking"],
    },
    {
        "query": "递归字符分块是怎么工作的",
        "expected_doc_ids": ["doc-rag-chunking"],
    },
    {
        "query": "语义分块和滑动窗口分块的区别",
        "expected_doc_ids": ["doc-rag-chunking"],
    },
    {
        "query": "chunk_size 和 chunk_overlap 参数",
        "expected_doc_ids": ["doc-rag-chunking"],
    },
    {
        "query": "如何选择分块策略",
        "expected_doc_ids": ["doc-rag-chunking"],
    },
    # 混合检索（5 条）
    {
        "query": "混合检索引擎如何工作",
        "expected_doc_ids": ["doc-rag-hybrid"],
    },
    {
        "query": "向量检索和 BM25 检索的区别",
        "expected_doc_ids": ["doc-rag-hybrid"],
    },
    {
        "query": "RRF 融合算法原理",
        "expected_doc_ids": ["doc-rag-hybrid"],
    },
    {
        "query": "语义相似度检索",
        "expected_doc_ids": ["doc-rag-hybrid", "doc-rag-overview"],
    },
    {
        "query": "关键词匹配检索",
        "expected_doc_ids": ["doc-rag-hybrid"],
    },
    # 重排（4 条）
    {
        "query": "重排模型的作用是什么",
        "expected_doc_ids": ["doc-rag-reranker"],
    },
    {
        "query": "BGE-Reranker 交叉编码器",
        "expected_doc_ids": ["doc-rag-reranker"],
    },
    {
        "query": "为什么只对 Top-K 结果重排",
        "expected_doc_ids": ["doc-rag-reranker"],
    },
    {
        "query": "重排得分归一化",
        "expected_doc_ids": ["doc-rag-reranker"],
    },
    # 缓存（3 条）
    {
        "query": "检索缓存如何降低延迟",
        "expected_doc_ids": ["doc-rag-cache"],
    },
    {
        "query": "Redis 缓存键如何生成",
        "expected_doc_ids": ["doc-rag-cache"],
    },
    {
        "query": "缓存命中率目标",
        "expected_doc_ids": ["doc-rag-cache"],
    },
    # 多智能体（4 条）
    {
        "query": "多智能体系统有哪些 Agent",
        "expected_doc_ids": ["doc-agent-overview"],
    },
    {
        "query": "Planner 和 Executor 的职责",
        "expected_doc_ids": ["doc-agent-overview"],
    },
    {
        "query": "Agent 间如何通信",
        "expected_doc_ids": ["doc-agent-comm"],
    },
    {
        "query": "消息协议包含哪些字段",
        "expected_doc_ids": ["doc-agent-comm"],
    },
    # DAG 与调度（4 条）
    {
        "query": "DAG 引擎如何检测循环依赖",
        "expected_doc_ids": ["doc_dag_engine"],
    },
    {
        "query": "拓扑排序的作用",
        "expected_doc_ids": ["doc_dag_engine", "doc_scheduler"],
    },
    {
        "query": "调度器如何分发子任务",
        "expected_doc_ids": ["doc_scheduler"],
    },
    {
        "query": "并行调度无依赖任务",
        "expected_doc_ids": ["doc_scheduler"],
    },
    # 重试与恢复（3 条）
    {
        "query": "指数退避重试策略",
        "expected_doc_ids": ["doc_retry_recovery"],
    },
    {
        "query": "重试耗尽后怎么处理",
        "expected_doc_ids": ["doc_retry_recovery"],
    },
    {
        "query": "断点续传如何实现",
        "expected_doc_ids": ["doc_retry_recovery"],
    },
]


def load_standard_dataset() -> tuple[list[dict], list[dict]]:
    """加载标准测试集。

    返回：
        (documents, queries)
        - documents: 文档列表，每项含 doc_id 与 text
        - queries: 查询列表，每项含 query 与 expected_doc_ids
    """
    return STANDARD_DOCUMENTS, STANDARD_QUERIES


def compute_recall_at_k(
    retrieved_doc_ids: list[str],
    relevant_doc_ids: list[str],
    k: int = 5,
) -> float:
    """计算 Recall@K：前 K 条结果中相关文档的召回率。

    Recall@K = |相关文档 ∩ 前 K 条结果| / |相关文档|
    """
    if not relevant_doc_ids:
        return 0.0
    top_k = retrieved_doc_ids[:k]
    hits = len(set(top_k) & set(relevant_doc_ids))
    return hits / len(relevant_doc_ids)


def compute_mrr(
    retrieved_doc_ids: list[str],
    relevant_doc_ids: list[str],
) -> float:
    """计算 MRR（平均倒数排名）：第一个相关文档的倒数排名。

    MRR = 1 / rank_of_first_relevant
    """
    for i, doc_id in enumerate(retrieved_doc_ids, start=1):
        if doc_id in relevant_doc_ids:
            return 1.0 / i
    return 0.0
