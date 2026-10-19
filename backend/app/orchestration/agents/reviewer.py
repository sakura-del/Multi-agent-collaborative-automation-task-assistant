"""复盘 Agent：日志收集 → 错误归因 → 性能统计。

职责：
1. 日志收集：汇总全链路执行日志（成功/失败/重试）
2. 错误归因：LLM 驱动的根因分析，定位失败原因
3. 性能统计：成功率/重试次数/平均耗时等指标

设计要点：
- 日志通过 Agent 间消息异步采集，不阻塞主流程
- LLM 归因可降级：无 LLM 时返回规则统计，保证可用
- 引用核验（Issue #3 / 计划 §2.6）可选注入：注入 ``reference_verifier`` 时核验参考文献
  并把统计写进 ``stats["reference_verification"]``；**未注入时行为与既有版本完全一致**
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.llm.models import Message, MessageRole
from app.orchestration.base_agent import BaseAgent
from app.orchestration.messages import AgentMessage
from app.tools.literature.models import (
    ReferenceCandidate,
    ReferenceVerdict,
    ReferenceVerification,
)

if TYPE_CHECKING:  # 仅类型检查期导入：运行期按需懒加载，避免编排层强依赖知识库服务包
    from app.knowledge.services.reference_verifier import ReferenceVerifier

logger = get_logger(__name__)

#: 判定态 → 中文标签（与 `citation_validator` 的报告口径保持一致）
_VERDICT_LABELS: tuple[tuple[str, str], ...] = (
    ("verified", "已验证"),
    ("metadata_mismatch", "元数据不符"),
    ("not_found", "未找到"),
    ("unverified", "未核验"),
)

#: 核验统计章节标题：正文已有该章节时（复盘产物 → 导出）不得重复追加
_REFERENCE_SECTION = "## 引用核验"

#: `verification_reason` 截断长度：异常信息可能很长，避免污染 bibliography
_MAX_REASON_LENGTH = 200


def _verification_mark(status: Any) -> str:
    """参考文献行的核验标记；无核验信息（老数据 / 未注入核验器）时返回空串。"""
    if not isinstance(status, str) or not status:
        return ""
    return f"（核验：{dict(_VERDICT_LABELS).get(status, status)}）"


def _summary_count(summary: Mapping[str, Any], key: str) -> int:
    """统计值容错：非整数字段按 0 处理，避免脏 stats 让报告组装失败。"""
    value = summary.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _verification_line(summary: Mapping[str, Any]) -> str:
    """`## 引用核验` 章节正文（复盘与导出共用同一展示口径）。"""
    counts = " / ".join(
        f"{label} {_summary_count(summary, verdict)} 条" for verdict, label in _VERDICT_LABELS
    )
    switch = "开启" if summary.get("intercepted") else "关闭"
    return (
        f"引用核验：{counts} / 已拦截 {_summary_count(summary, 'dropped')} 条；拦截开关：{switch}"
    )


def _contributes_to_body(result: Any) -> bool:
    """该节点内容是否应进入复盘正文（Issue #7）。

    `extract` / `write` 是作者正文，进正文；
    `search`（检索结果列表）、`validate` / `review`（依赖输出的机械汇总，
    而汇总里又已包含上游节点全文）不进正文——否则正文大面积重复
    （生产实测：57 段里 26 段重复，`## 参考文献` 出现 3 次）。

    `node_type` 缺失（历史持久化负载）时返回 True：无法判定角色就不擅自丢内容，
    保持既有行为。显式 `content_role` 优先于类型推断。
    """
    role = str(getattr(result, "content_role", "") or "")
    if role == "aggregated":
        return False
    if role == "authored":
        return True
    node_type = str(getattr(result, "node_type", "") or "")
    if not node_type:
        return True
    return node_type in {"extract", "write"}


def _assemble_body(body_parts: list[str]) -> str:
    """拼接正文并去除**完全重复的段落**（Issue #7）。

    多个作者节点之间仍可能复述同一段内容（如 extract 与 write 都写了同一段背景），
    按「首次出现顺序」保留一次即可。只去重完全相同的段落（按空白归一后比较），
    不做模糊合并，避免把不同内容误删。

    同时剔除作者正文里**自带的** `## 参考文献` / `## 局限性` / `## 引用核验` 章节：
    这些章节由复盘统一生成，节点自带的版本会造成同一章节出现多次
    （生产实测：`## 参考文献` 出现 3 次）。只删这三个受管章节，其余内容原样保留。
    """
    paragraphs: list[str] = []
    seen: set[str] = set()
    skipping = False
    for part in body_parts:
        for block in part.split("\n\n"):
            stripped = block.strip()
            if not stripped:
                continue
            heading = _managed_section_heading(stripped)
            if heading is not None:
                # 进入受管章节：其内容由复盘统一生成，正文里整体丢弃
                skipping = True
                continue
            if stripped.startswith("#"):
                # 任何其他标题都结束受管章节的跳过状态
                skipping = False
            if skipping:
                continue
            key = re.sub(r"\s+", " ", stripped)
            if key in seen:
                continue
            seen.add(key)
            paragraphs.append(stripped)
    return "\n\n".join(paragraphs)


#: 由复盘统一生成、节点正文不得自带的章节标题
_MANAGED_SECTIONS = ("参考文献", "局限性", "引用核验")


def _managed_section_heading(block: str) -> str | None:
    """若该块是受管章节标题（`## 参考文献` 等），返回章节名；否则 None。"""
    match = re.match(r"^#{1,3}\s+(.+?)\s*$", block)
    if match is None:
        return None
    title = match.group(1).strip()
    for name in _MANAGED_SECTIONS:
        if title == name or title.startswith(f"{name}：") or title.startswith(f"{name}:"):
            return name
    return None


def _enrich_reference_entry(entry: dict[str, Any], artifact: Mapping[str, Any]) -> None:
    """把检索结果元数据补进参考文献条目（Issue #4 / 计划 §1.5）。

    设计约束：
    - **只在缺键时补**，不覆盖 reviewer 已有取值；
    - 条目若来自证据片段（没有作者）而同 URL 有真实检索记录，则用**真实题名**替换摘录标题
      （否则 CSL 渲染出的参考文献会把 60 字摘录当题名）；
    - `language` 必须落库：缺失会让逐条 locale 退化（英文条目被渲染成「等」）。
    仅依赖 `app.tools.csl.models` 的纯函数，不触发任何网络。
    """
    from app.tools.csl.models import detect_language, parse_year

    title = str(artifact.get("title") or "").strip()
    authors = [str(item).strip() for item in (artifact.get("authors") or []) if str(item).strip()]

    if title and not entry.get("authors") and entry.get("title") != title:
        entry["title"] = title
    if authors and not entry.get("authors"):
        entry["authors"] = authors
    if entry.get("year") is None:
        year = parse_year(artifact.get("published_at") or artifact.get("issued"))
        if year is not None:
            entry["year"] = year
    if not entry.get("language"):
        entry["language"] = detect_language(
            title=str(entry.get("title") or title),
            authors=[str(item) for item in (entry.get("authors") or authors)],
        )
    for key in ("doi", "DOI"):
        value = artifact.get(key)
        if value and not entry.get("doi"):
            entry["doi"] = str(value)
            break
    if artifact.get("type") and not entry.get("type"):
        entry["type"] = str(artifact["type"])


_ATTRIBUTION_PROMPT = """请分析以下任务执行日志，定位失败根因并给出改进建议。

执行日志（JSON）：
{logs}

请以 JSON 格式返回（仅输出 JSON）：
{{
  "root_cause": "根因分析（一句话）",
  "failure_pattern": "失败模式分类（如 超时/依赖缺失/逻辑错误）",
  "improvement": "改进建议",
  "confidence": 0.0-1.0
}}
"""


class ReviewerAgent(BaseAgent):
    """复盘 Agent：收集执行日志，归因错误，生成性能统计报告。"""

    def __init__(
        self,
        *,
        reference_verifier: ReferenceVerifier | None = None,
        **kwargs: Any,
    ) -> None:
        """构造复盘 Agent。

        ``reference_verifier`` 为可选注入：为 ``None``（默认）时复盘行为与既有版本逐字一致，
        既不核验也不产生 ``stats["reference_verification"]``。
        """
        super().__init__(**kwargs)
        self.reference_verifier = reference_verifier
        # 执行日志缓冲：按 trace_id 聚合，避免跨任务混淆
        self._logs: dict[str, list[dict[str, Any]]] = {}

    @property
    def agent_type(self) -> str:
        return "reviewer"

    async def _handle_message(self, message: AgentMessage) -> dict[str, Any] | None:
        """接收并缓存执行日志/错误消息。

        复盘 Agent 被动采集，消息驱动而非主动拉取（解耦）。
        """
        trace_id = message.metadata.get("trace_id", "default")
        entry = {
            "from_agent": message.from_agent,
            "message_type": message.message_type.value,
            "content": message.content,
            "timestamp": message.timestamp.isoformat(),
        }
        self._logs.setdefault(trace_id, []).append(entry)
        return None

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行复盘：文献场景基于持久化结果组装；否则走日志统计 → 归因。

        input_data 需包含 trace_id（指定要复盘的任务链路）。
        """
        if isinstance(input_data.get("results"), list):
            return await self._build_literature_review(input_data)

        trace_id = input_data.get("trace_id", "default")

        # 步骤1：收集该链路日志
        logs = self._logs.get(trace_id, [])
        if not logs:
            # 兼容直接传入 logs 的场景（如测试）
            logs = input_data.get("logs", [])

        # 步骤2：性能统计
        stats = self._compute_stats(logs)

        # 步骤3：错误归因（仅在有失败时执行）
        root_cause: dict[str, Any] = {}
        if stats["error_count"] > 0:
            root_cause = await self._attribute_errors(logs)

        report = {
            "trace_id": trace_id,
            "summary": self._build_summary(stats, root_cause),
            "stats": stats,
            "root_cause": root_cause,
            "log_count": len(logs),
        }
        logger.info(
            "reviewer_done",
            trace_id=trace_id,
            total_logs=len(logs),
            error_count=stats["error_count"],
        )
        return report

    @staticmethod
    def _normalize_validations(payloads: list[Any]) -> list[Any]:
        """把已持久化的校验结果规范为 `ValidationReport`。

        通用（非文献）校验器会额外返回 rule_check / semantic_check / issues 等键，
        这里只取 ValidationReport 字段；无法解析的条目直接跳过，避免复盘整体失败。
        """
        from app.orchestration.contracts import ValidationReport

        known_fields = set(ValidationReport.model_fields)
        normalized: list[Any] = []
        for item in payloads:
            if isinstance(item, ValidationReport):
                normalized.append(item)
                continue
            if not isinstance(item, dict):
                continue
            try:
                normalized.append(
                    ValidationReport.model_validate(
                        {key: value for key, value in item.items() if key in known_fields}
                    )
                )
            except Exception:
                logger.warning("reviewer_validation_skipped")
        return normalized

    async def _build_literature_review(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """文献场景复盘：只消费已持久化的 ExecutionResult / ValidationReport / 事件统计。

        不调用 arXiv、RAG 或文件工具；LLM 仅用于润色摘要与局限性（无 LLM 时纯规则组装，
        结果保持不变）。
        """
        results_payload = input_data.get("results") or []
        if not results_payload:
            raise ValueError("复盘缺少节点结果")
        from app.orchestration.contracts import ExecutionResult

        results = [ExecutionResult.model_validate(item) for item in results_payload]
        validations = self._normalize_validations(input_data.get("validations") or [])
        topic = str(input_data.get("topic") or "文献综述")
        event_stats = dict(input_data.get("event_stats") or {})

        body_parts: list[str] = []
        citations: dict[str, dict[str, Any]] = {}
        for result in results:
            content = (result.content or "").strip()
            if content and _contributes_to_body(result):
                body_parts.append(content)
            for item in result.evidence:
                url = str(item.source_url) if item.source_url else ""
                if url and url not in citations:
                    citations[url] = {
                        "title": item.excerpt[:60],
                        "source_url": url,
                        "doc_id": str(item.doc_id) if item.doc_id else None,
                        "chunk_id": str(item.chunk_id) if item.chunk_id else None,
                    }
            for artifact in result.artifacts:
                url = str(artifact.get("source_url") or "")
                if url and url not in citations:
                    citations[url] = {
                        "title": str(artifact.get("title") or artifact.get("source_id") or ""),
                        "source_url": url,
                        "source_id": artifact.get("source_id"),
                    }
                if url:
                    # Issue #4：补齐 CSL 渲染所需的元数据（作者/年份/语言/DOI/类型）
                    _enrich_reference_entry(citations[url], artifact)

        bibliography = [
            {"index": index, **entry} for index, entry in enumerate(citations.values(), start=1)
        ]
        # Issue #3：核验最终 bibliography（导出报告展示的就是这份列表）
        bibliography, verification_summary = await self._verify_bibliography(bibliography)
        scores = self._literature_scores(validations)
        issues = [
            check.message
            for validation in validations
            for check in validation.checks
            if not check.passed
        ] + [validation.feedback for validation in validations if validation.feedback]

        abstract = self._first_paragraph(body_parts[0] if body_parts else "")
        limitations: list[str] = [
            validation.feedback for validation in validations if validation.feedback
        ]

        if self.llm is not None:
            polished = await self._polish_literature_review(
                topic, results, validations, event_stats
            )
            abstract = str(polished.get("abstract") or abstract)
            refined = polished.get("limitations")
            if isinstance(refined, list) and refined:
                limitations = [str(item) for item in refined]

        final_markdown = self._compose_review_markdown(
            topic=topic,
            abstract=abstract,
            body=_assemble_body(body_parts),
            limitations=limitations or ["（无）"],
            bibliography=bibliography,
            reference_verification=verification_summary,
        )
        stats = {
            "node_count": len(results),
            "event_count": int(event_stats.get("event_count", 0)),
            "total_tokens": sum(
                result.prompt_tokens + result.completion_tokens for result in results
            ),
            "documents": len(citations),
            "validations": len(validations),
            "title": topic,
            "abstract": abstract,
            "limitations": limitations,
        }
        if verification_summary is not None:
            stats["reference_verification"] = verification_summary
        logger.info(
            "reviewer_literature_done",
            topic=topic,
            nodes=len(results),
            citations=len(citations),
        )
        return {
            "summary": f"共 {len(results)} 个节点完成，引用 {len(citations)} 条文献。",
            "final_markdown": final_markdown,
            "bibliography": bibliography,
            "scores": scores,
            "issues": issues,
            "stats": stats,
        }

    async def _verify_bibliography(
        self, bibliography: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """核验最终 bibliography，返回（拦截后的条目, ``stats`` 汇总）。

        语义（计划 §2.6）：
        - 候选由 ``candidates_from_bibliography`` 从**最终 bibliography** 构造
          （导出报告展示的就是这份列表）；
        - 核验器抛异常时降级为「全部未核验」：既不因此让复盘失败，也不据此剔除任何条目；
        - ``intercept_unverified`` 打开时只剔除 ``blocked_keys``（元数据不符 / 未找到），
          ``unverified`` 一律保留（外部源故障不得惩罚/误伤）；
        - 拦截后重新连续编号（index 从 1 开始），剔除条数记入 ``dropped``；
        - 未注入核验器时原样返回，且不产生任何新 stats 键（旧行为完全不变）。
        """
        verifier = self.reference_verifier
        if verifier is None:
            return bibliography, None

        # 方法内导入：编排层不静态依赖知识库服务包，测试注入 fake 即可，无需真实客户端
        from app.knowledge.services.reference_verifier import (
            blocked_keys,
            candidates_from_bibliography,
            summarize_verifications,
        )

        candidates: list[ReferenceCandidate] = []
        keys: list[str | None] = []
        for entry in bibliography:
            # 逐条构造：既复用冻结的候选口径，又能把结论精确映射回对应条目
            built = candidates_from_bibliography([entry])
            candidate = built[0] if built else None
            keys.append(candidate.key if candidate is not None else None)
            if candidate is not None:
                candidates.append(candidate)

        try:
            verifications = await verifier.verify_many(candidates)
        except Exception as exc:
            # 核验失败绝不能让复盘整体失败：降级为「全部未核验」，且一条都不拦截
            logger.warning("reviewer_reference_verification_failed", error=str(exc))
            verifications = [
                self._degraded_verification(candidate, exc) for candidate in candidates
            ]

        # 缺 `intercept_unverified` 属性（最小替身/误注入）时按「开关关闭」处理
        intercept = bool(getattr(verifier, "intercept_unverified", False))
        dropped = 0
        if intercept:
            blocked = blocked_keys(verifications)
            kept = [
                (entry, key)
                for entry, key in zip(bibliography, keys, strict=True)
                if key is None or key not in blocked
            ]
            dropped = len(bibliography) - len(kept)
            bibliography = self._reindex_bibliography([entry for entry, _ in kept])
            keys = [key for _, key in kept]

        by_key = {item.reference_key: item for item in verifications}
        for entry, key in zip(bibliography, keys, strict=True):
            verification = by_key.get(key) if key is not None else None
            if verification is None:
                continue
            entry["verification_status"] = verification.verdict.value
            entry["verification_reason"] = verification.reason

        summary = summarize_verifications(verifications, dropped=dropped, intercepted=intercept)
        return bibliography, summary.model_dump()

    @staticmethod
    def _degraded_verification(
        candidate: ReferenceCandidate, error: Exception
    ) -> ReferenceVerification:
        """核验过程异常 → 降级为「未核验」结论（外部源故障不得惩罚、不得拦截）。"""
        return ReferenceVerification(
            reference_key=candidate.key,
            title=candidate.title,
            verdict=ReferenceVerdict.UNVERIFIED,
            reason=f"核验过程异常，降级为未核验：{error}"[:_MAX_REASON_LENGTH],
        )

    @staticmethod
    def _reindex_bibliography(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """拦截后重新连续编号（index 从 1 开始），并保持 index 在首键位置。"""
        reindexed: list[dict[str, Any]] = []
        for position, entry in enumerate(entries, start=1):
            rest = {key: value for key, value in entry.items() if key != "index"}
            reindexed.append({"index": position, **rest})
        return reindexed

    async def _polish_literature_review(
        self,
        topic: str,
        results: list[Any],
        validations: list[Any],
        event_stats: dict[str, Any],
    ) -> dict[str, Any]:
        """可选 LLM 润色：失败时静默回退到确定性组装。"""
        # 方法内导入：避免 agents 包与 workflows 包互相导入
        from app.orchestration.workflows.prompts import render_reviewer_prompt

        llm = self.llm
        if llm is None:  # 无 LLM 时直接跳过润色（调用方已做确定性组装）
            return {}

        prompt = render_reviewer_prompt(
            topic=topic,
            results_json=json.dumps(
                [item.model_dump(mode="json") for item in results], ensure_ascii=False
            ),
            validations_json=json.dumps(
                [item.model_dump(mode="json") for item in validations], ensure_ascii=False
            ),
            event_stats_json=json.dumps(event_stats, ensure_ascii=False),
        )
        try:
            response = await llm.chat(
                [
                    Message(
                        role=MessageRole.SYSTEM,
                        content="你是文献综述复盘专家，只输出 JSON。",
                    ),
                    Message(role=MessageRole.USER, content=prompt),
                ],
                temperature=0.2,
            )
            payload = json.loads(response.content or "{}")
        except Exception as exc:
            logger.warning("reviewer_polish_failed", error=str(exc))
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _literature_scores(validations: list[Any]) -> dict[str, float]:
        if not validations:
            return {"citation_traceability": 0.0, "quality": 0.0}
        mean = sum(item.score for item in validations) / len(validations) / 100.0
        score = round(mean, 4)
        return {"citation_traceability": score, "quality": score}

    @staticmethod
    def _first_paragraph(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                return stripped[:300]
        return ""

    @staticmethod
    def _compose_review_markdown(
        *,
        topic: str,
        abstract: str,
        body: str,
        limitations: list[str],
        bibliography: list[dict[str, Any]],
        reference_verification: dict[str, Any] | None = None,
    ) -> str:
        """组装复盘 Markdown；注入核验统计时追加 `## 引用核验` 章节与逐行核验标记。"""
        parts = [f"# {topic}"]
        if abstract:
            parts.append(f"## 摘要\n{abstract}")
        if body:
            parts.append(body)
        parts.append("## 局限性\n" + "\n".join(f"- {item}" for item in limitations))
        if bibliography:
            lines = []
            for entry in bibliography:
                title = entry.get("title") or entry.get("source_id") or "未命名文献"
                url = entry.get("source_url") or ""
                mark = _verification_mark(entry.get("verification_status"))
                lines.append(f"- [{entry['index']}] {title} — {url}".rstrip(" —") + mark)
            parts.append("## 参考文献\n" + "\n".join(lines))
        if reference_verification is not None:
            parts.append(f"{_REFERENCE_SECTION}\n{_verification_line(reference_verification)}")
        return "\n\n".join(parts)

    def _compute_stats(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        """从日志列表计算性能统计指标。"""
        total = len(logs)
        # 按消息类型统计
        by_type: dict[str, int] = {}
        error_count = 0
        agent_set: set[str] = set()

        for entry in logs:
            msg_type = entry.get("message_type", "unknown")
            by_type[msg_type] = by_type.get(msg_type, 0) + 1
            if msg_type == "error":
                error_count += 1
            agent_set.add(entry.get("from_agent", "unknown"))

        success_count = by_type.get("result", 0)
        # 成功率：成功结果数 / (成功+失败)，无数据时为 0
        total_attempts = success_count + error_count
        success_rate = (success_count / total_attempts) if total_attempts > 0 else 0.0

        return {
            "total_logs": total,
            "by_type": by_type,
            "success_count": success_count,
            "error_count": error_count,
            "success_rate": round(success_rate, 4),
            "agents_involved": sorted(agent_set),
        }

    async def _attribute_errors(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        """LLM 驱动的错误根因分析。

        无 LLM 时降级为规则归因：提取首个错误的 error_type。
        """
        if self.llm is None:
            return self._rule_based_attribution(logs)

        # 仅传入错误相关日志，减少 token 消耗（计算优化）
        error_logs = [e for e in logs if e.get("message_type") == "error"]
        response = await self.llm.chat(
            [
                Message(role=MessageRole.SYSTEM, content="你是根因分析专家，只输出 JSON。"),
                Message(
                    role=MessageRole.USER,
                    content=_ATTRIBUTION_PROMPT.format(
                        logs=json.dumps(error_logs, ensure_ascii=False)
                    ),
                ),
            ],
            temperature=0.3,
        )
        try:
            return json.loads(response.content or "{}")
        except json.JSONDecodeError:
            logger.warning("reviewer_attribution_parse_failed")
            return self._rule_based_attribution(logs)

    @staticmethod
    def _rule_based_attribution(logs: list[dict[str, Any]]) -> dict[str, Any]:
        """规则归因降级：提取首个错误类型作为根因。"""
        for entry in logs:
            if entry.get("message_type") == "error":
                content = entry.get("content", {})
                return {
                    "root_cause": content.get("error", "未知错误"),
                    "failure_pattern": content.get("error_type", "UNKNOWN"),
                    "improvement": "建议检查错误来源并重试",
                    "confidence": 0.5,
                }
        return {
            "root_cause": "无错误日志",
            "failure_pattern": "NONE",
            "improvement": "",
            "confidence": 1.0,
        }

    @staticmethod
    def _build_summary(stats: dict[str, Any], root_cause: dict[str, Any]) -> str:
        """生成人类可读的复盘摘要。"""
        rate = stats["success_rate"] * 100
        if stats["error_count"] == 0:
            return f"任务执行成功，成功率 {rate:.1f}%，共 {stats['total_logs']} 条日志。"
        cause = root_cause.get("root_cause", "未定位")
        return (
            f"任务存在异常，成功率 {rate:.1f}%，" f"错误 {stats['error_count']} 次，根因：{cause}。"
        )

    async def collect_from(self, source: Callable[[], Awaitable[list[dict[str, Any]]]]) -> None:
        """从外部数据源（如数据库日志表）异步采集日志。

        阶段4+ 可用此方法从 DB 拉取历史日志做批量复盘。
        """
        logs = await source()
        for entry in logs:
            trace_id = entry.get("trace_id", "default")
            self._logs.setdefault(trace_id, []).append(entry)
