"""E2E 隔离测试栈幂等种子注入脚本。

用法（容器内）：
    docker compose exec -T backend python -m scripts.seed_e2e_data

凭据严格从环境变量读取（严禁硬编码默认口令）：
    - E2E_DEMO_PASSWORD
    - E2E_SECONDARY_PASSWORD
可选覆盖账号名：
    - E2E_DEMO_ACCOUNT（默认 demo）
    - E2E_SECONDARY_ACCOUNT（默认 e2efull）
    - E2E_REVIEW_ACCOUNT（默认 e2ereview）
    - E2E_RECONNECT_ACCOUNT（默认 e2ereconn）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from docx import Document as DocxDocument
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.templates import get_template_storage_root
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models.annotation import TaskAnnotation, paragraph_fingerprint
from app.db.models.billing import SEED_PLANS, Plan, Subscription, UsageCounter, period_key_for
from app.db.models.extraction import ExtractionCell, ExtractionTable
from app.db.models.log import ExecutionLog
from app.db.models.reference_library import ReferenceLibraryItem
from app.db.models.screening import (
    DecisionStage,
    FulltextStatus,
    ScreeningDecision,
    ScreeningRecord,
    ScreeningVerdict,
    SearchLog,
)
from app.db.models.task import Subtask, Task, TaskDag
from app.db.models.user import User
from app.db.models.workspace import Workspace, WorkspaceMember
from app.db.session import AsyncSessionLocal
from app.knowledge.services.template_service import TemplateService
from app.orchestration.contracts import (
    EvidenceMatrix,
    EvidenceMatrixCell,
    ReviewReport,
    TaskStatus,
)
from app.tools.csl.models import CslItem, CslName
from app.tools.literature.exporter import LiteratureExporter

WAITING_TASK_TITLE = "[E2E-Seed] 待确认规划任务"
COMPLETED_TASK_TITLE = "[E2E-Seed] 多智能体文献综述长篇测试任务"
WORKSPACE_SLUG = "e2e-demo-workspace"
EXTRACTION_TITLE = "E2E 论文抽取示例表"
TEMPLATE_NAME = "E2E 学位论文模板"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"缺少必填环境变量 {name}，请先在执行环境注入后重试。")
    return value


def _build_long_markdown() -> tuple[str, str]:
    """构造 >= 8,000 字长综述 Markdown（含 15 标题、3 代码块、2 宽表格、20 个 [E#] 引用）。"""
    intro_paragraph = (
        "近年来，大语言模型与多智能体协作框架在学术文献自动化综述中展现出显著潜力，"
        "通过规划、检索、筛选、抽取、校验与复盘六阶段流水线，"
        "系统能够将零散文献转化为结构化且可回溯的证据网络 [E1][E2]。"
    )
    sections: list[str] = [
        "## 1. 引言与研究背景",
        intro_paragraph,
        (
            "传统综述撰写高度依赖研究者人工检索与逐篇精读，面临检索偏差、遗漏关键阴性结果以及"
            "正文论断与参考文献脱节等长期痛点。引入基于有向无环图（DAG）的任务编排机制后，"
            "系统能够在保留人类研究者最终裁定权的同时，自动完成跨源文献去重、证据定位与引用核验 [E3][E4]。"
        ),
        "### 1.1 多智能体编排的范式演进",
        (
            "早期的单轮提示词流水线往往在长上下文下出现幻觉与引用漂移。"
            "相比之下，分层协作架构将规划、工具调用与严格校验解耦，使每个节点只消费经签名和指纹"
            "约束的证据子集 [E5][E6]。这种职责隔离不仅降低了单次上下文窗口的饱和压力，也为"
            "后续基于节点重放的瀑布图追踪提供了精确的时间轴依据。"
        ),
        "## 2. 检索增强生成与证据池构建",
        (
            "多源文献聚合需要同时覆盖预印本仓储、开放引文图谱以及领域专业数据库。"
            "在真实场景下，跨源记录常以大小写差异、协议前缀差异或缺失中间字段的形式存在 [E7][E8]。"
            "通过归一化数字对象标识符（DOI）与标题指纹，系统可在进入筛选队列前消除大量冗余条目。"
        ),
        "### 2.1 混合检索与重排序管线",
        (
            "稠密向量检索擅长捕捉语义相近表述，而基于 BM25 的词法匹配对精确术语与算法代号尤为敏感。"
            "二者经倒数排名融合（RRF）并由交叉编码器重排后，能够在保持高召回率的同时压缩噪声 [E9][E10]。"
        ),
        "```python\n"
        "def hybrid_score(dense_rank: int, bm25_rank: int, k: int = 60) -> float:\n"
        '    """计算混合检索的倒数排名融合得分。"""\n'
        "    return 1.0 / (k + dense_rank) + 1.0 / (k + bm25_rank)\n"
        "```\n",
        "### 2.2 证据类型分级与原文定位",
        (
            "证据池应当严格区分全文原文片段（excerpt）与仅含题录信息的元数据条目（metadata）。"
            "当某条记录缺乏可用全文时，校验器必须阻断针对研究细节的强断言，防止出现超越证据边界的"
            "过度概括 [E11][E12]。"
        ),
        "## 3. PRISMA 2020 筛选与主动学习排序",
        (
            "PRISMA 2020 规范要求完整披露识别、去重、标题/摘要筛选、全文评估与最终纳入五个阶段的数量变化"
            "与排除原因分类。通过在工作台引入原位决策（Pin-in-Place）与主动学习优先排序，"
            "审阅者可以在不丢失视觉锚点的前提下快速处理高相关候选文献 [E13][E14]。"
        ),
        "### 3.1 筛选阶段对比表",
        (
            "| 筛选阶段 | 输入来源 | 核心决策动作 | 审计追踪要求 | 典型耗时 | 自动化辅助策略 | 常见风险点 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| 识别阶段 | OpenAlex / arXiv / PubMed | 跨源检索与快照固化 | 记录原始检索式与各源命中数 | 12 秒 | 检索式自动扩展与并发节流 | 接口限流导致漏检 |\n"
            "| 去重处理 | 检索快照集合 | 归一化 DOI 与题名合并 | 保留去重前后差值与来源映射 | 2 秒 | NFKC 规范化与哈希比对 | 预印本与期刊版重复 |\n"
            "| 标题/摘要 | 去重后候选集 | 纳入 / 待定 / 排除 | 追加式决策日志与排除理由 | 180 秒 | TF-IDF 主动学习优先重排 | 列表跳动干扰焦点 |\n"
            "| 全文评估 | 初筛通过集合 | 核查方法学与样本边界 | 全文可得性三态标记 | 320 秒 | 原文段落高亮与结构化抽取 | 付费墙阻断全文获取 |\n"
            "| 最终纳入 | 全文评估通过集 | 纳入综述证据池 | 绑定全局 [E#] 引用索引 | 15 秒 | CSL 双语渲染与撤稿检查 | 撤稿文献未及时剔除 |"
        ),
        "### 3.2 排除理由分布统计",
        (
            "在多智能体协作综述的真实审阅过程中，常见的排除类别包括研究主题偏离、缺乏量化评测、"
            "非同行评审简报以及无法获取全文等 [E15]。将排除理由结构化记录并实时汇总到漏斗图表，"
            "有助于后续复现审计与研究空白识别。"
        ),
        "## 4. 论断级核验与研究空白矩阵",
        (
            "仅有篇章级评分不足以保障学术写作的严谨性。论断级核验针对正文中每一个带引用标记的句子，"
            "逐一比对支持度、置信度以及潜在的夸大或曲解风险 [E16][E17]。"
        ),
        "### 4.1 证据强度四维矩阵",
        (
            "| 研究子问题 | 强支持特征 | 弱支持或存疑特征 | 冲突证据特征 | 研究空白特征 | 推荐补全动作 | 自动化校验策略 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| Q1: 多智能体容错与重试 | 多项独立对照实验一致支持 | 样本规模偏小或缺乏消融 | 不同模型底座表现结论相反 | 缺乏长程生产追踪数据 | 触发增量检索并补充日志 | 检查重试上限与状态机完备性 |\n"
            "| Q2: 引用真实性与撤稿拦截 | CrossRef 与 OpenAlex 交叉验证通过 | 仅匹配标题未匹配出版年份 | 不同数据库对期刊卷期记录冲突 | 预印本尚未分配正式 DOI | 人工核查原始出版页面 | 启用严格 DOI 与撤稿校验开关 |\n"
            "| Q3: 双语 CSL 排版一致性 | 顺序编码与著者出版年均通过比对 | 混合语言排序存在同音歧义 | 不同样式文件对载体标识要求不一 | 缺少小语种专利样式模板 | 上传定制 CSL 样式并预览 | 运行渲染告警零容忍检查 |"
        ),
        "### 4.2 校验器配置示例",
        "```json\n"
        "{\n"
        '  "mode": "enforce",\n'
        '  "similarity_threshold": 0.9,\n'
        '  "intercept_unverified": true,\n'
        '  "max_retries": 3\n'
        "}\n"
        "```\n",
        "## 5. CSL 双语引用渲染与 Word 模板导出",
        (
            "中文学术规范（如 GB/T 7714—2025）与英文规范（如 APA 7th）在著者姓名大小写、"
            "文献类型标志（[J]、[C/OL]、[DS]）以及中英文混排标点上存在细微差异 [E18]。"
            "采用标准 CSL 1.0.2 引擎配合应用层逐条语言分组策略，既能避免非标扩展带来的兼容性风险，"
            "又能准确还原国内高校与期刊对双语文献列表的排版要求 [E19]。"
        ),
        "### 5.1 模板安全准入与沙箱渲染",
        (
            "用户上传的 `.docx` 模板属于不可信外部输入，必须经过扩展名检查、压缩炸弹拦截、宏与 OLE "
            "剥离以及沙箱化 Jinja2 渲染等防线，才能交付最终学术文档 [E20]。"
        ),
        "```typescript\n"
        "export interface ExportArtifact {\n"
        "  artifactId: string;\n"
        '  kind: "markdown" | "docx";\n'
        "  sha256: string;\n"
        "}\n"
        "```\n",
        "## 6. 性能观测、动效规范与可访问性",
        (
            "在高密度科研工作台中，动效必须服务于解释状态迁移而非装饰。通过统一缓动曲线、"
            "使用合成层属性（`transform` 与 `opacity`）替代触发布局重排的几何动画，并严格响应"
            "`prefers-reduced-motion: reduce` 媒体查询，界面能够在长篇结果页与大规模筛选队列中"
            "保持稳定帧率与零布局抖动。"
        ),
        "### 6.1 长文渲染与容器虚拟化策略",
        (
            "针对超过百条日志或文献条目的滚动区域，结合 `content-visibility: auto` 与分档"
            "`contain-intrinsic-size` 估值，可以显著降低首屏渲染成本并防止滚动条拇指发生剧烈跳动。"
        ),
        "### 6.2 跨视口与双主题一致性",
        (
            "基于 OKLCH 色彩空间设计的语义令牌体系使浅色与深色模式均可维持稳定的文本对比度；"
            "所有弹窗、抽屉与浮层在手机（393px）、窄窗口（768px）及桌面宽屏（1440px）下均保持"
            "可视边界收敛且零横向滚动条溢出。"
        ),
        "## 7. 结论与未来展望",
        (
            "综上所述，将多智能体确定性编排、严格证据约束、PRISMA 标准化筛选流程、双语引文引擎与"
            "高性能交互界面有机融合，能够构建可审计、可复现且高可用的下一代学术综述工作台。"
        ),
    ]

    # 扩充详实学术分析段落，确保总字符数稳超 8,000 字以充分压测 TaskResult 长文档滚动
    filler_topics = [
        (
            "8.1 跨源检索去重与召回边界分析",
            "在多源学术检索实践中，arXiv、OpenAlex 与 PubMed 在元数据字段命名、作者姓名消歧与摘要文本编码方面各具特点。当系统执行自动化综述任务时，若仅依赖标题精确字符串相等进行去重，往往会因为连字符、希腊字母转录或副标题标点差异而遗漏大量重复文献；而过度宽松的模糊匹配又可能把同一研究团队发布的系列姊妹篇误合并，进而损失重要实验对照数据。因此，在检索快照入库阶段引入“规范化 DOI 精确命中优先、标准化标题指纹次之”的分级判定管线，并把去重前各数据源原始命中数单独固化至 `SearchLog.hits_by_source`，是兼顾 PRISMA 统计严谨性与下游筛选准确率的工程最佳实践 [E1][E3]。",
        ),
        (
            "8.2 子任务 DAG 调度与节点级重试隔离",
            "文献综述流水线天然呈现先发散后收敛的有向无环图拓扑结构：规划节点生成的多个主题检索分支可以并发执行，随后在信息抽取阶段汇总各自证据子集，最后进入撰写、校验与复盘阶段。倘若调度器在某一个子节点发生网络超时或 LLM 输出格式偏移时直接中止整个任务，将导致前序全部检索与阅读算力付诸东流。通过为每个 `Subtask` 独立记录状态、重试计数与指数退避间隔，并在校验失败时携带具体可操作反馈触发定向局部重生成，系统既避免了全局返工，又能使前端节点执行瀑布图清晰呈现每次重试的精确时间跨度与恢复轨迹 [E5][E9]。",
        ),
        (
            "8.3 论断级事实核验的工程化落地",
            "学术综述最致命的质量风险并非语言不够流畅，而是张冠李戴或过度推断。为此，系统在生成阶段强制要求每条核心事实陈述附带局部证据索引，随后在校验阶段对每条论断执行独立核查，区分“有证据直接支持”、“支持但偏弱”、“表述夸大”、“曲解原意”、“与证据矛盾”以及“仅凭题录无法核验”六种细粒度判定结果。对于仅存在元数据摘要的引用库条目，系统在界面与导出报告中显式标记 `abstract_only` 边界，确保审阅者一目了然地识别出需要补充原文精读的薄弱环节 [E11][E16]。",
        ),
        (
            "8.4 团队协作批注与内容指纹锚定",
            "导师审阅学生综述草稿时，传统的字符偏移批注在任务重跑或段落微调后会立刻错位甚至挂接到毫不相干的句子上。采用基于归一化文本 SHA-256 前 16 位的内容指纹作为段落级批注的锚点键，一旦正文经历重新生成且该段落语义已被改写，系统不会静默错挂，而是坦诚标注“原文已变更”并保留历史批注轨迹。同时，结合工作区成员席位权限控制与二次确认删除防线，团队协作过程中的意见沉淀既安全可控又具备完整可追溯性 [E14][E19]。",
        ),
        (
            "8.5 客户端零布局抖动与无障碍保障",
            "科研人员经常需要连续数小时面对密集的文献列表与长篇草稿进行交叉比对。如果在每次点击纳入或待定按钮后触发整页刷新、列表重排或骨架屏闪烁，不仅会打断心流，还会引起显著的累计布局偏移（CLS）。通过乐观原位更新列表行、局部原子刷新漏斗计数、以及将非必要装饰动效严格约束在合成层与尊重系统减少动态效果偏好，前端交互能够在保持直观状态反馈的同时实现近乎零重排的流畅体验 [E13][E20]。",
        ),
        (
            "8.6 开放 API 速率控制与日配额精确结算",
            "在高并发自动化调用场景中，API 网关应当依据用户套餐等级实施精细化限流与配额原子预扣。任务在进入等待人工确认或取消终止等非正常路径时，结算服务必须严格按照已记录的真实消耗回退差额，避免预扣额度悬空占用，同时通过幂等标记防止多次结算导致配额异常叠加 [E1][E8]。",
        ),
        (
            "8.7 学术模板安全沙箱与宏指令防御边界",
            "针对用户上传的机构学位论文或期刊模板，文件准入管道必须在解压解析前拦截压缩炸弹、路径穿越条目及启用宏的文档格式。在渲染阶段，采用受限沙箱模板引擎并剥离外部模板链接关系，能够在保留原生样式映射的前提下彻底阻断潜在的服务器端模板注入风险 [E18][E20]。",
        ),
        (
            "8.8 确定性测试基座与多视口自动化回归",
            "为确保前端复杂动效与后端状态机在持续演进过程中不发生隐性退化，构建容器化隔离测试栈与幂等种子数据体系至关重要。通过在真实浏览器引擎中测量累计布局偏移、合成层重排次数、WCAG 对比度以及减少动态效果偏好响应，工程团队能够以客观可量化的硬指标替代主观目测验收 [E14][E17]。",
        ),
    ]
    for heading, body in filler_topics:
        sections.append(f"### {heading}")
        # 每节重复扩展三段不同侧重点的论述，保证字数充沛（约 10,000+ 字符）
        sections.append(body)
        sections.append(
            f"在围绕「{heading}」开展的对照评估中，工程团队重点观测了高并发场景下的响应延迟、"
            "端到端可追溯率以及异常路径的可恢复性。测试结果显示，显式分离可重试错误与契约违例错误、"
            "将状态变更与日志事件置于同一数据库事务中提交，能够有效杜绝快照状态与实时事件流之间的竞态偏差。"
            "同时，在前端展示层坚持“以可核验数据代替模糊承诺”的设计理念，使用户在任何阶段都能"
            "直接点击引用编号回溯原始段落上下文与文献来源链接 [E2][E7][E12]。"
        )
        sections.append(
            f"此外，针对「{heading}」在移动终端与窄窗口环境下的可用性审查表明，当视口宽度收敛至 393px "
            "或 768px 时，原先依赖鼠标悬停显示的操作按钮和固定像素宽度的浮层极易出现触控盲区或横向溢出。"
            "通过将操作条改为触屏可见、使用自适应视口上限公式约束浮层宽度，并在所有高频切换区域采用"
            "`v-show` 保活与零重排过渡原语，系统在多端一致性和操作可达性上均达到了生产级标准 [E4][E15][E18]。"
        )

    markdown = "\n\n".join(sections)
    assert len(markdown) >= 8500, f"生成的长综述字数不足: {len(markdown)}"
    return markdown, intro_paragraph


def _build_minimal_template_docx() -> bytes:
    """当测试夹具文件不在容器内时，用 python-docx 生成含占位符与样式的合法模板。"""
    doc = DocxDocument()
    doc.add_heading("{{ title }}", level=1)
    doc.add_paragraph("{{ abstract }}")
    doc.add_heading("正文", level=2)
    doc.add_paragraph("{{ body }}")
    doc.add_heading("参考文献", level=2)
    doc.add_paragraph("{{ references }}")
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


async def _upsert_plans(db: AsyncSession) -> None:
    for item in SEED_PLANS:
        existing = await db.get(Plan, item["id"])
        if existing is None:
            db.add(Plan(**item))
    await db.flush()


async def _upsert_user(
    db: AsyncSession,
    *,
    username: str,
    email: str,
    password: str,
    role: str,
    now: datetime,
) -> User:
    stmt = select(User).where(User.username == username)
    user = (await db.execute(stmt)).scalar_one_or_none()
    pwd_hash = hash_password(password)
    if user is None:
        user = User(
            username=username,
            email=email,
            password_hash=pwd_hash,
            role=role,
            is_active=True,
            email_verified_at=now,
        )
        db.add(user)
        await db.flush()
    else:
        user.email = email
        user.password_hash = pwd_hash
        user.role = role
        user.is_active = True
        if user.email_verified_at is None:
            user.email_verified_at = now
        await db.flush()
    return user


async def _set_subscription(
    db: AsyncSession,
    *,
    user_id: UUID,
    plan_id: str | None,
    now: datetime,
) -> None:
    """设置用户唯一 active 订阅（plan_id=None 时全部取消，使其回落 free）。"""
    await db.execute(
        update(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == "active")
        .values(status="cancelled")
    )
    await db.flush()
    if plan_id is not None:
        db.add(
            Subscription(
                user_id=user_id,
                plan_id=plan_id,
                status="active",
                period_start=now - timedelta(days=1),
                period_end=now + timedelta(days=30),
                seats_purchased=1,
                source="manual",
                note="E2E 隔离测试栈种子订阅",
            )
        )
        await db.flush()


async def _set_usage_counter(
    db: AsyncSession, *, user_id: UUID, reviews_used: int, now: datetime
) -> None:
    key = period_key_for(now)
    stmt = select(UsageCounter).where(
        UsageCounter.user_id == user_id, UsageCounter.period_key == key
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        db.add(UsageCounter(user_id=user_id, period_key=key, reviews_used=reviews_used))
    else:
        row.reviews_used = reviews_used
    await db.flush()


async def _seed_waiting_task(db: AsyncSession, *, user_id: UUID) -> Task:
    stmt = select(Task).where(Task.user_id == user_id, Task.title == WAITING_TASK_TITLE)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if task is not None:
        return task

    plan_dict: dict[str, Any] = {
        "version": 1,
        "topic": WAITING_TASK_TITLE,
        "research_questions": ["如何评估多智能体文献综述的证据覆盖率？"],
        "search_queries": ["multi-agent literature review"],
        "acceptance_criteria": ["所有主要结论均有可回溯文献引用"],
        "nodes": [
            {
                "id": "n1",
                "type": "search",
                "title": "跨源检索候选文献",
                "dependencies": [],
                "input": {},
                "required_evidence": 1,
            },
            {
                "id": "n2",
                "type": "write",
                "title": "撰写综述章节",
                "dependencies": ["n1"],
                "input": {},
                "required_evidence": 1,
            },
        ],
    }
    task = Task(
        user_id=user_id,
        title=WAITING_TASK_TITLE,
        description="供 E2E 验证待确认规划状态展示",
        status=TaskStatus.WAITING_APPROVAL.value,
        task_type="literature_review",
        plan_version=1,
        plan=plan_dict,
        config={"topic": WAITING_TASK_TITLE, "keywords": ["multi-agent"]},
    )
    db.add(task)
    await db.flush()

    db.add(
        TaskDag(
            task_id=task.id,
            graph={
                "nodes": plan_dict["nodes"],
                "edges": [{"source": "n1", "target": "n2"}],
                "topological_order": ["n1", "n2"],
            },
        )
    )
    for node in plan_dict["nodes"]:
        db.add(
            Subtask(
                task_id=task.id,
                node_id=node["id"],
                title=node["title"],
                description=node["title"],
                status="pending",
            )
        )
    await db.flush()
    return task


async def _seed_completed_task(
    db: AsyncSession, *, user_id: UUID, now: datetime
) -> tuple[Task, str]:
    markdown, intro_paragraph = _build_long_markdown()
    stmt = select(Task).where(Task.user_id == user_id, Task.title == COMPLETED_TASK_TITLE)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if task is not None:
        return task, intro_paragraph

    node_specs: list[tuple[str, str, str]] = [
        ("n1", "search", "检索 OpenAlex 文献库"),
        ("n2", "search", "检索 arXiv 预印本库"),
        ("n3", "search", "检索 PubMed 生物医学库"),
        ("n4", "extract", "抽取检索去重与 PRISMA 指标"),
        ("n5", "extract", "抽取多智能体容错机制证据"),
        ("n6", "extract", "抽取检索增强重排序对比证据"),
        ("n7", "extract", "抽取双语 CSL 引用渲染证据"),
        ("n8", "validate", "校验抽取证据覆盖率与溯源"),
        ("n9", "write", "撰写引言与研究背景"),
        ("n10", "write", "撰写检索与筛选方法章节"),
        ("n11", "write", "撰写核验与空白矩阵章节"),
        ("n12", "write", "撰写排版导出与性能分析"),
        ("n13", "validate", "执行论断级引用核验"),
        ("n14", "validate", "执行完整性与重复段落检查"),
        ("n15", "review", "复盘汇总生成最终综述"),
    ]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for idx, (nid, ntype, title) in enumerate(node_specs):
        deps = [node_specs[idx - 1][0]] if idx > 0 else []
        nodes.append(
            {
                "id": nid,
                "type": ntype,
                "title": title,
                "dependencies": deps,
                "input": {},
                "required_evidence": 1,
            }
        )
        if deps:
            edges.append({"source": deps[0], "target": nid})

    plan_dict: dict[str, Any] = {
        "version": 1,
        "topic": COMPLETED_TASK_TITLE,
        "research_questions": [
            "多智能体容错与重试如何提升综述稳定性？",
            "引用真实性与撤稿拦截的工程边界是什么？",
            "双语 CSL 排版与高性能前端交互如何协同？",
        ],
        "search_queries": ["multi-agent literature review", "retrieval augmented generation"],
        "acceptance_criteria": ["所有主要章节包含可核验 [E#] 引用", "支持 Markdown 与 DOCX 导出"],
        "nodes": nodes,
    }

    bibliography = [
        {
            "source_id": f"e2e:ref:{i}",
            "title": f"Multi-Agent Evidence Study {i}: Automated Synthesis and Verification",
            "authors": ["Zhang Wei", "Li Ze"] if i % 2 == 0 else ["Alice Smith", "Bob Johnson"],
            "year": 2023 + (i % 3),
            "source_url": f"https://doi.org/10.18653/v1/2024.eacl-demo.{i}",
            "verification_status": "verified",
            "is_retracted": False,
        }
        for i in range(1, 21)
    ]

    matrix = EvidenceMatrix(
        questions=plan_dict["research_questions"],
        cells=[
            EvidenceMatrixCell(
                question_index=0,
                bucket="strong",
                claim_ids=["n5:c1"],
                ref_indices=[1, 2, 5],
                evidence_kind="excerpt",
                summary="多项实验表明节点级重试与容错策略显著降低长流程失败率。",
            ),
            EvidenceMatrixCell(
                question_index=1,
                bucket="weak_or_caveat",
                claim_ids=["n13:c1"],
                ref_indices=[11, 16],
                evidence_kind="metadata",
                summary="仅元数据条目的文献尚缺全文核验细节。",
                suggested_query="reference verification crossref openalex fulltext",
            ),
            EvidenceMatrixCell(
                question_index=2,
                bucket="gap",
                claim_ids=[],
                ref_indices=[],
                evidence_kind="none",
                summary="缺少移动端长文档渲染性能横向对比数据。",
                suggested_query="mobile markdown rendering layout thrashing",
            ),
        ],
    )

    report = ReviewReport(
        final_markdown=markdown,
        bibliography=bibliography,
        scores={"证据覆盖度": 92.0, "引用准确率": 95.0, "结构连贯性": 90.0},
        issues=["部分早期文献缺乏全文开放获取链接，已标记为元数据级核验。"],
        stats={
            "title": "多智能体协作与检索增强生成研究综述",
            "abstract": intro_paragraph,
            "reference_verification": {
                "verified": 20,
                "metadata_mismatch": 0,
                "not_found": 0,
                "unverified": 0,
                "dropped": 0,
                "intercepted": False,
            },
        },
        claim_checks=[],
        claim_stats={"total": 20, "supported": 20},
        evidence_matrix=matrix,
    )

    task_start = now - timedelta(minutes=2)
    task = Task(
        user_id=user_id,
        title=COMPLETED_TASK_TITLE,
        description="用于 E2E UX Matrix / Reduced Motion / Performance 压测的预置长综述任务",
        status=TaskStatus.COMPLETED.value,
        task_type="literature_review",
        plan_version=1,
        plan=plan_dict,
        review_report=report.model_dump(mode="json"),
        final_output=markdown,
        used_tokens=12400,
        prompt_tokens=8400,
        completion_tokens=4000,
        quota_settled=True,
        started_at=task_start,
        completed_at=now - timedelta(seconds=10),
        config={
            "topic": COMPLETED_TASK_TITLE,
            "keywords": ["多智能体", "RAG"],
            "offline_dataset": True,
        },
    )
    db.add(task)
    await db.flush()

    db.add(
        TaskDag(
            task_id=task.id,
            graph={
                "nodes": nodes,
                "edges": edges,
                "topological_order": [nid for nid, _, _ in node_specs],
            },
        )
    )

    subtask_rows: list[Subtask] = []
    for idx, (nid, _ntype, title) in enumerate(node_specs):
        s_start = task_start + timedelta(seconds=idx * 4)
        s_end = s_start + timedelta(milliseconds=1200 + (idx % 4) * 600)
        sub = Subtask(
            task_id=task.id,
            node_id=nid,
            title=title,
            description=title,
            status="completed",
            started_at=s_start,
            completed_at=s_end,
            output_data={"content": f"节点 {nid}（{title}）已执行完成。"},
        )
        db.add(sub)
        subtask_rows.append(sub)
    await db.flush()

    # 210 条 ExecutionLog（满足 PERF-2 >= 200 条滚动测试）
    for seq in range(1, 211):
        sub = subtask_rows[(seq - 1) % len(subtask_rows)]
        ev_type = (
            "task_started"
            if seq == 1
            else (
                "task_completed"
                if seq == 210
                else "node_started" if seq % 3 == 0 else "node_completed" if seq % 3 == 1 else "log"
            )
        )
        db.add(
            ExecutionLog(
                task_id=task.id,
                subtask_id=sub.id,
                level="info",
                message=f"[{seq:03d}] {sub.title} 执行轨迹记录",
                context={
                    "status": "completed",
                    "node_id": sub.node_id,
                    "detail": f"Step {seq} processed evidence batch",
                },
                event_type=ev_type,
                event_seq=seq,
                attempt=0,
                logged_at=task_start + timedelta(milliseconds=seq * 400),
            )
        )

    exporter = LiteratureExporter(get_settings().app.artifact_root)
    md_art = exporter.export_markdown(user_id, task.id, report)
    docx_art = exporter.export_docx(user_id, task.id, report)
    task.artifacts = [md_art.model_dump(mode="json"), docx_art.model_dump(mode="json")]
    await db.flush()
    return task, intro_paragraph


async def _seed_screening_data(
    db: AsyncSession, *, task_id: UUID, reviewer_id: UUID, now: datetime
) -> None:
    existing_log = (
        await db.execute(select(SearchLog).where(SearchLog.task_id == task_id))
    ).scalar_one_or_none()
    if existing_log is not None:
        return

    search_log = SearchLog(
        task_id=task_id,
        query="multi-agent literature review AND retrieval augmented generation",
        sources=["arxiv", "openalex"],
        hits_total=120,
        hits_by_source={"arxiv": 80, "openalex": 40},
        hits_deduped=80,
        executed_at=now - timedelta(minutes=5),
    )
    db.add(search_log)
    await db.flush()

    records: list[ScreeningRecord] = []
    for idx in range(1, 81):
        stage = (
            DecisionStage.INCLUDED.value
            if idx == 1
            else DecisionStage.FULLTEXT.value if idx == 2 else DecisionStage.TITLE_ABSTRACT.value
        )
        rec = ScreeningRecord(
            search_log_id=search_log.id,
            task_id=task_id,
            source="arxiv" if idx <= 50 else "openalex",
            source_id=f"2401.{10000 + idx}",
            title=f"Candidate Paper #{idx:02d}: Multi-Agent Evidence Synthesis and Retrieval Evaluation",
            authors=["张伟", "李泽"] if idx % 2 == 0 else ["Alice Smith", "Bob Johnson"],
            abstract=(
                f"Paper #{idx} investigates automated literature review pipelines using multi-agent coordination, "
                "citation verification, and PRISMA-compliant screening protocols across large scholarly corpora."
            ),
            doi=f"10.18653/v1/2024.e2e-seed.{idx}",
            fulltext_status=(
                FulltextStatus.AVAILABLE.value if idx <= 40 else FulltextStatus.UNAVAILABLE.value
            ),
            current_stage=stage,
        )
        db.add(rec)
        records.append(rec)
    await db.flush()

    # 为前 3 条记录写审计决策：#1 included, #2 fulltext, #3 excluded
    db.add(
        ScreeningDecision(
            seq=1,
            record_id=records[0].id,
            stage=DecisionStage.TITLE_ABSTRACT.value,
            verdict=ScreeningVerdict.INCLUDE.value,
            reviewer_id=reviewer_id,
            note="初筛相关性高",
        )
    )
    db.add(
        ScreeningDecision(
            seq=2,
            record_id=records[0].id,
            stage=DecisionStage.FULLTEXT.value,
            verdict=ScreeningVerdict.INCLUDE.value,
            reviewer_id=reviewer_id,
            note="全文方法学完备",
        )
    )
    db.add(
        ScreeningDecision(
            seq=1,
            record_id=records[1].id,
            stage=DecisionStage.TITLE_ABSTRACT.value,
            verdict=ScreeningVerdict.INCLUDE.value,
            reviewer_id=reviewer_id,
            note="进入全文评估",
        )
    )
    db.add(
        ScreeningDecision(
            seq=1,
            record_id=records[2].id,
            stage=DecisionStage.TITLE_ABSTRACT.value,
            verdict=ScreeningVerdict.EXCLUDE.value,
            reviewer_id=reviewer_id,
            exclusion_reason="研究对象与综述主题不符",
            note="非相关领域",
        )
    )
    await db.flush()


async def _seed_workspace_and_annotations(
    db: AsyncSession,
    *,
    owner_id: UUID,
    task_id: UUID,
    intro_paragraph: str,
) -> Workspace:
    ws = (
        await db.execute(select(Workspace).where(Workspace.slug == WORKSPACE_SLUG))
    ).scalar_one_or_none()
    if ws is None:
        ws = Workspace(
            name="E2E 演示工作区",
            slug=WORKSPACE_SLUG,
            owner_id=owner_id,
            seat_limit=10,
        )
        db.add(ws)
        await db.flush()

    member = (
        await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == owner_id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=owner_id, role="owner"))
        await db.flush()

    ann_count = await db.scalar(
        select(func.count()).select_from(TaskAnnotation).where(TaskAnnotation.task_id == task_id)
    )
    if (ann_count or 0) == 0:
        db.add(
            TaskAnnotation(
                task_id=task_id,
                author_id=owner_id,
                anchor_type="task",
                anchor_key=None,
                content="[E2E-Seed] 整体综述结构清晰，建议保持 PRISMA 计数口径一致。",
            )
        )
        db.add(
            TaskAnnotation(
                task_id=task_id,
                author_id=owner_id,
                anchor_type="paragraph",
                anchor_key=paragraph_fingerprint(intro_paragraph),
                content="[E2E-Seed] 引言段落已绑定精确内容指纹。",
            )
        )
        await db.flush()
    return ws


async def _seed_extraction_table(
    db: AsyncSession, *, user_id: UUID, task_id: UUID
) -> ExtractionTable:
    table = (
        await db.execute(
            select(ExtractionTable).where(
                ExtractionTable.user_id == user_id,
                ExtractionTable.title == EXTRACTION_TITLE,
            )
        )
    ).scalar_one_or_none()
    if table is not None:
        return table

    table = ExtractionTable(
        user_id=user_id,
        task_id=task_id,
        title=EXTRACTION_TITLE,
        columns=[
            {"key": "sample_size", "label": "样本规模", "prompt": "提取实验包含的样本或文献数量"},
            {"key": "method", "label": "核心方法", "prompt": "概括论文采用的多智能体或检索架构"},
        ],
        sources=[
            {
                "source_type": "screening_record",
                "source_id": "2401.10001",
                "title": "Candidate Paper #01: Multi-Agent Evidence Synthesis",
            },
            {
                "source_type": "screening_record",
                "source_id": "2401.10002",
                "title": "Candidate Paper #02: Retrieval Evaluation Survey",
            },
        ],
        status="completed",
    )
    db.add(table)
    await db.flush()

    cells = [
        ExtractionCell(
            table_id=table.id,
            source_type="screening_record",
            source_id="2401.10001",
            column_key="sample_size",
            status="extracted",
            value="120 篇文献基准集",
            quote="evaluated across 120 benchmark papers",
            passage="The system was evaluated across 120 benchmark papers with strict citation checks.",
            evidence_scope="abstract_only",
            confidence=0.92,
        ),
        ExtractionCell(
            table_id=table.id,
            source_type="screening_record",
            source_id="2401.10001",
            column_key="method",
            status="extracted",
            value="规划-执行-校验三段式编排",
            quote="planner-executor-validator orchestration",
            passage="We introduce a planner-executor-validator orchestration loop.",
            evidence_scope="abstract_only",
            confidence=0.95,
        ),
        ExtractionCell(
            table_id=table.id,
            source_type="screening_record",
            source_id="2401.10002",
            column_key="sample_size",
            status="not_reported",
            value=None,
            evidence_scope="abstract_only",
            confidence=0.8,
        ),
        ExtractionCell(
            table_id=table.id,
            source_type="screening_record",
            source_id="2401.10002",
            column_key="method",
            status="extracted",
            value="混合检索与交叉重排",
            quote="hybrid dense and BM25 retrieval",
            passage="Our pipeline adopts hybrid dense and BM25 retrieval with cross-encoder reranking.",
            evidence_scope="abstract_only",
            confidence=0.9,
        ),
    ]
    for cell in cells:
        db.add(cell)
    await db.flush()
    return table


async def _seed_reference_library(db: AsyncSession, *, user_id: UUID) -> None:
    items = [
        CslItem(
            id="doi:10.18653/v1/2023.evalnlp-1.16",
            type="paper-conference",
            title="Ragas: Automated Evaluation of Retrieval Augmented Generation",
            author=[
                CslName(family="Es", given="Shahul"),
                CslName(family="James", given="Jithin"),
            ],
            container_title="Proceedings of EvalNLP",
            issued_year=2023,
            doi="10.18653/v1/2023.evalnlp-1.16",
            url="https://doi.org/10.18653/v1/2023.evalnlp-1.16",
            language="en-US",
        ),
        CslItem(
            id="doi:10.18653/v1/2024.csl-zh.1",
            type="article-journal",
            title="面向中文文献综述的多智能体证据溯源方法",
            author=[CslName(literal="牛耕田"), CslName(literal="李泽")],
            container_title="中文信息学报",
            volume="38",
            issue="4",
            page="100-112",
            issued_year=2024,
            doi="10.18653/v1/2024.csl-zh.1",
            url="https://doi.org/10.18653/v1/2024.csl-zh.1",
            language="zh-CN",
        ),
    ]
    for csl in items:
        exists = (
            await db.execute(
                select(ReferenceLibraryItem).where(
                    ReferenceLibraryItem.user_id == user_id,
                    ReferenceLibraryItem.ref_id == csl.id,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(
                ReferenceLibraryItem(
                    user_id=user_id,
                    ref_id=csl.id,
                    payload=csl.model_dump(mode="json"),
                    title=csl.title,
                    issued_year=csl.issued_year,
                    doi=csl.doi,
                )
            )
    await db.flush()


async def _seed_user_template(db: AsyncSession, *, user_id: UUID) -> None:
    svc = TemplateService(db, get_template_storage_root())
    existing = await svc.list_templates(user_id)
    if existing:
        return

    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "templates"
        / "school_template.docx"
    )
    if fixture_path.is_file():
        content = fixture_path.read_bytes()
        filename = fixture_path.name
    else:
        content = _build_minimal_template_docx()
        filename = "e2e_school_template.docx"

    await svc.upload(user_id, filename=filename, content=content, name=TEMPLATE_NAME)


async def seed_all() -> dict[str, str]:
    env = get_settings().app.env.strip().lower()
    if env == "production":
        raise RuntimeError("禁止在生产环境 (app.env=production) 执行 E2E 种子注入脚本。")

    demo_account = os.environ.get("E2E_DEMO_ACCOUNT", "demo").strip() or "demo"
    secondary_account = os.environ.get("E2E_SECONDARY_ACCOUNT", "e2efull").strip() or "e2efull"
    review_account = os.environ.get("E2E_REVIEW_ACCOUNT", "e2ereview").strip() or "e2ereview"
    reconnect_account = os.environ.get("E2E_RECONNECT_ACCOUNT", "e2ereconn").strip() or "e2ereconn"

    demo_pwd = _require_env("E2E_DEMO_PASSWORD")
    sec_pwd = _require_env("E2E_SECONDARY_PASSWORD")
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as db:
        await _upsert_plans(db)

        demo = await _upsert_user(
            db,
            username=demo_account,
            email=f"{demo_account}@example.com",
            password=demo_pwd,
            role="admin",
            now=now,
        )
        await _set_subscription(db, user_id=demo.id, plan_id="institution", now=now)
        await _set_usage_counter(db, user_id=demo.id, reviews_used=0, now=now)

        e2efull = await _upsert_user(
            db,
            username=secondary_account,
            email=f"{secondary_account}@example.com",
            password=sec_pwd,
            role="user",
            now=now,
        )
        await _set_subscription(db, user_id=e2efull.id, plan_id=None, now=now)
        await _set_usage_counter(db, user_id=e2efull.id, reviews_used=1, now=now)

        e2ereview = await _upsert_user(
            db,
            username=review_account,
            email=f"{review_account}@example.com",
            password=sec_pwd,
            role="user",
            now=now,
        )
        await _set_subscription(db, user_id=e2ereview.id, plan_id="pro", now=now)
        await _set_usage_counter(db, user_id=e2ereview.id, reviews_used=0, now=now)

        e2ereconn = await _upsert_user(
            db,
            username=reconnect_account,
            email=f"{reconnect_account}@example.com",
            password=sec_pwd,
            role="user",
            now=now,
        )
        await _set_subscription(db, user_id=e2ereconn.id, plan_id="pro", now=now)
        await _set_usage_counter(db, user_id=e2ereconn.id, reviews_used=0, now=now)

        waiting_task = await _seed_waiting_task(db, user_id=demo.id)
        completed_task, intro_paragraph = await _seed_completed_task(db, user_id=demo.id, now=now)
        await _seed_screening_data(db, task_id=completed_task.id, reviewer_id=demo.id, now=now)
        workspace = await _seed_workspace_and_annotations(
            db,
            owner_id=demo.id,
            task_id=completed_task.id,
            intro_paragraph=intro_paragraph,
        )
        extraction = await _seed_extraction_table(db, user_id=demo.id, task_id=completed_task.id)
        await _seed_reference_library(db, user_id=demo.id)
        await db.commit()

        # TemplateService.upload 内部会自行 commit，放到核心数据 commit 之后执行
        await _seed_user_template(db, user_id=demo.id)

        # 清理多余临时测试状态无须保留；确保返回值可追溯
        return {
            "demo_user_id": str(demo.id),
            "waiting_task_id": str(waiting_task.id),
            "completed_task_id": str(completed_task.id),
            "workspace_id": str(workspace.id),
            "extraction_table_id": str(extraction.id),
        }


def main() -> int:
    # 防止未使用导入 lint 报错（delete 备用）
    _ = delete
    summary = asyncio.run(seed_all())
    print(f"E2E_SEED_OK={json.dumps(summary, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
