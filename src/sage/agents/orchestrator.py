"""
多 Agent 协同编排器 — Sage 论文写作系统（主控+平等协作+整理汇报+多重验证）

核心设计:
- 主编 Agent (Supervisor/Orchestrator): 分析写作需求 → 拆解任务 → 调度子智能体 → 质量把关
- 平等协作子智能体:
  - 文献调研员 (Literature): 文献检索、综述、研究现状分析
  - 方法论专家 (Methodology/Planner): 研究方法设计、实验方案、论证框架
  - 撰写员 (Writer/Coder): 论文各章节具体撰写
- 整理汇报员 (Consolidator): 整合讨论产出，消除重复矛盾
- 引用管理员 (Citation): 引用插入、参考文献格式化、查重
- 审校核查员 (Verifier/Reviewer): 多重验证（文献库+逻辑+外部检索+学术规范）
- 修订员 (Reviser/Debugger): 根据审校报告修复问题

工作流程:
  1. 主编接收用户写作需求，拆解为子任务
  2. 文献调研员检索文献，方法论专家设计方法，撰写员撰写内容（平等协作讨论）
  3. 整理汇报员整合各子智能体产出，形成连贯论文
  4. 引用管理员处理引用与格式化
  5. 审校核查员执行多重验证，生成审校报告
  6. 如有问题，修订员修复，再交审校核查员重检
  7. 最终结果输出给用户

与单 Agent Loop 的关系:
  Orchestrator 内部使用 AgentLoop 作为 Worker 的执行引擎，
  每个 Worker 是独立的 AgentLoop 实例，拥有自己的上下文和工具访问权限。

智能体定义从 agents/ 文件夹的 agent.json 加载（见 loader.py），
专属技能自动注入到 Worker 的 system prompt。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from sage.agent.loop import AgentLoop, LoopEvent
from sage.config import get_config
from sage.agents.loader import get_agent_loader
from sage.llm.client import LLMClient


class AgentRole(Enum):
    """Agent 角色（Sage 论文写作系统）

    保留原有角色值用于向后兼容，同时新增 Sage 专用角色。
    原有角色在 Sage 中映射为：
      SUPERVISOR → 主编/Orchestrator
      PLANNER → 方法论专家/Methodology
      CODER → 撰写员/Writer
      REVIEWER → 审校核查员/Verifier
      DEBUGGER → 修订员/Reviser
    """
    # 原有角色（向后兼容）
    SUPERVISOR = "supervisor"
    PLANNER = "planner"
    CODER = "coder"
    REVIEWER = "reviewer"
    DEBUGGER = "debugger"
    # Sage 新增角色
    LITERATURE = "literature"      # 文献调研员
    CITATION = "citation"          # 引用管理员
    CONSOLIDATOR = "consolidator"  # 整理汇报员


@dataclass
class SubTask:
    """子任务定义"""
    id: str
    role: AgentRole
    description: str
    context: str = ""
    dependencies: list[str] = field(default_factory=list)
    result: str = ""
    status: str = "pending"


@dataclass
class CollaborationEvent:
    """协同事件（供 CLI/Web 展示）"""
    type: str  # "task_created" | "worker_start" | "worker_done" | "reflection" | "text" | "reasoning" | "done"
    role: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)
    tokens: dict = None  # 该轮 LLM 调用的 token 用量（用于工具卡片显示）

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = {}


@dataclass
class IntentResult:
    """意图分析结果"""
    complexity: str  # "simple" | "complex"
    role: str        # AgentRole.value 或 "general"（通用Agent）
    reason: str = ""


# 角色中文名映射（供事件展示）
_ROLE_LABELS = {
    "supervisor": "主编", "planner": "方法论专家", "coder": "撰写员",
    "reviewer": "审校核查员", "debugger": "修订员",
    "literature": "文献调研员", "citation": "引用管理员", "consolidator": "整理汇报员",
    "general": "通用助手",
}


class AgentOrchestrator:
    """多 Agent 协同编排器

    每个 Worker Agent 是独立的 AgentLoop 实例，
    拥有独立的上下文和角色化的 System Prompt（从 agents/{role}/agent.json 加载）。
    """

    @classmethod
    def get_all_role_info(cls) -> list[dict]:
        """获取所有 Agent 角色的信息（用于 API 动态展示）"""
        return get_agent_loader().get_all_role_info()

    def __init__(self, workspace: Optional[Path] = None):
        config = get_config()
        self.workspace = workspace or config.workspace
        self._workers: dict[AgentRole, AgentLoop] = {}
        self._general_worker: Optional[AgentLoop] = None  # 通用 Agent（不绑定角色）
        self._history: list[SubTask] = []
        self._loader = get_agent_loader()
        self._llm = LLMClient()  # 用于意图分析（不带工具的快速调用）

    def _get_worker(self, role: AgentRole) -> AgentLoop:
        """获取或创建 Worker Agent

        system prompt 从 agents/{role}/agent.json 加载，并自动注入专属技能。
        """
        if role not in self._workers:
            prompt = self._loader.get_system_prompt(role.value)
            if not prompt:
                # 回退：loader 中没有定义时使用空字符串
                prompt = ""
            self._workers[role] = AgentLoop(
                workspace=self.workspace,
                system_prompt=prompt,
                writing_mode=True,
            )
        return self._workers[role]

    def _get_general_worker(self) -> AgentLoop:
        """获取通用 Agent（不绑定特定角色 prompt）

        用于意图分析判定为简单任务且无匹配角色时的兜底处理。
        """
        if self._general_worker is None:
            # 不传 system_prompt，AgentLoop 会使用默认的通用 system prompt
            self._general_worker = AgentLoop(workspace=self.workspace, writing_mode=True)
        return self._general_worker

    def _get_worker_by_role_name(self, role_name: str) -> Optional[AgentLoop]:
        """根据角色名获取 Worker，'general' 返回通用 Agent，未知角色返回 None"""
        if role_name == "general":
            return self._get_general_worker()
        try:
            role = AgentRole(role_name)
            return self._get_worker(role)
        except ValueError:
            return None

    async def collaborate(self, user_input: str) -> AsyncIterator[CollaborationEvent]:
        """写作模式入口 — 智能选择流程

        流程:
          1. 意图分析（规则快速判断 + 不确定时 LLM 精细分析）
          2. 简单任务 → 根据意图选择匹配角色 Agent 或通用 Agent
          3. 复杂任务 → 完整多智能体协作流程（主编→文献→方法→撰写→整理→引用→审校→修订）

        Args:
            user_input: 用户原始需求

        Yields:
            CollaborationEvent: 协同过程中产生的事件
        """
        # Step 1: 意图分析
        yield CollaborationEvent(
            type="task_created",
            role="supervisor",
            content=f"正在分析用户意图: {user_input[:100]}",
        )

        intent = await self._analyze_intent(user_input)

        role_label = _ROLE_LABELS.get(intent.role, intent.role)
        yield CollaborationEvent(
            type="reflection",
            role="supervisor",
            content=f"意图分析结果: {'复杂任务(多智能体协作)' if intent.complexity == 'complex' else '简单任务'} → {role_label}。理由: {intent.reason}",
        )

        # Step 2: 简单任务 — 路由到匹配角色 Agent 或通用 Agent
        if intent.complexity == "simple":
            worker = self._get_worker_by_role_name(intent.role)
            if worker is None:
                # 未知角色兜底为通用 Agent
                worker = self._get_general_worker()
                intent.role = "general"
            yield CollaborationEvent(
                type="worker_start",
                role=intent.role,
                content=f"由 {role_label} 处理简单任务",
            )
            async for event in worker.run(user_input):
                mapped = self._map_event(event, intent.role)
                if mapped:
                    yield mapped
            yield CollaborationEvent(type="done", role="supervisor")
            return

        # Step 3: 复杂任务 — 完整多智能体协作流程
        try:
            # Step 1: 文献调研
            yield CollaborationEvent(type="worker_start", role="literature", content="文献调研员开始检索相关文献...")
            literature_result = await self._run_worker(AgentRole.LITERATURE, (
                f"请针对以下论文写作需求进行文献调研:\n{user_input}\n\n"
                "输出格式: 1) 研究背景与发展脉络 2) 主要研究流派 3) 研究空白与机会 4) 关键参考文献列表（含DOI/URL）"
            ))
            yield CollaborationEvent(type="worker_done", role="literature", content=literature_result[:500])

            # Step 2: 方法论设计
            yield CollaborationEvent(type="worker_start", role="planner", content="方法论专家设计研究方案...")
            methodology_result = await self._run_worker(AgentRole.PLANNER, (
                f"## 文献调研结果\n{literature_result}\n\n"
                f"## 用户需求\n{user_input}\n\n"
                "请基于文献调研结果设计研究方法。输出格式: 1) 研究问题与假设 2) 研究方法选型与理由 3) 实验/研究设计 4) 数据分析方法 5) 论证框架"
            ))
            yield CollaborationEvent(type="worker_done", role="planner", content=methodology_result[:500])

            # Step 3: 平等协作讨论（文献调研员+方法论专家+撰写员）
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content="启动平等协作讨论：文献调研员、方法论专家、撰写员交叉补充观点...",
            )
            # 撰写员基于文献和方法设计开始写作
            yield CollaborationEvent(type="worker_start", role="coder", content="撰写员基于文献和方法设计撰写论文...")
            writer_prompt = (
                f"## 文献调研结果\n{literature_result}\n\n"
                f"## 研究方法设计\n{methodology_result}\n\n"
                f"## 用户需求\n{user_input}\n\n"
                "请基于以上材料撰写论文内容。要求：1) 结构完整（摘要/引言/相关工作/方法/实验/讨论/结论）2) 需要引用处用 [CITE: 关键词] 标注 3) 学术语言规范"
            )
            coder = self._get_worker(AgentRole.CODER)
            writer_results = []
            async for event in coder.run(writer_prompt):
                mapped = self._map_event(event, "coder")
                if mapped:
                    yield mapped
                    if mapped.type == "text":
                        writer_results.append(mapped.content)
            draft_content = "\n".join(writer_results) if writer_results else ""
            yield CollaborationEvent(type="worker_done", role="coder", content="初稿撰写完成")

            # Step 4: 整理汇报员整合内容
            if draft_content:
                yield CollaborationEvent(type="worker_start", role="consolidator", content="整理汇报员整合论文内容...")
                consolidated = await self._run_worker(AgentRole.CONSOLIDATOR, (
                    f"## 文献调研产出\n{literature_result[:1500]}\n\n"
                    f"## 方法设计产出\n{methodology_result[:1500]}\n\n"
                    f"## 撰写员初稿\n{draft_content[:3000]}\n\n"
                    "请整合以上内容，消除重复、调和矛盾、统一风格，输出连贯完整的论文内容。"
                ))
                yield CollaborationEvent(type="worker_done", role="consolidator", content="内容整合完成")

                # Step 5: 引用管理员处理引用
                yield CollaborationEvent(type="worker_start", role="citation", content="引用管理员处理引用与格式化...")
                citation_result = await self._run_worker(AgentRole.CITATION, (
                    f"## 整合后的论文内容\n{consolidated[:3000]}\n\n"
                    "请处理所有 [CITE: 关键词] 标记：1) 从文献库匹配相关文献 2) 插入规范引用 3) 格式化参考文献列表 4) 验证引用真实性 5) 标注存疑引用"
                ))
                yield CollaborationEvent(type="worker_done", role="citation", content=citation_result[:500])

                # Step 6: 审校核查员多重验证
                yield CollaborationEvent(type="worker_start", role="reviewer", content="审校核查员执行多重验证...")
                review = await self._run_worker(AgentRole.REVIEWER, (
                    f"## 论文内容（含引用）\n{consolidated[:2000]}\n\n"
                    f"## 引用处理结果\n{citation_result[:1500]}\n\n"
                    "请执行四重验证: 1) 文献库验证 2) 逻辑核查 3) 外部检索验证（存疑引用）4) 学术规范检查。输出审校报告。"
                ))
                yield CollaborationEvent(type="worker_done", role="reviewer", content=review[:500])

                # 如果审校发现严重问题，触发修订
                if "严重" in review or "存疑" in review or "CRITICAL" in review:
                    yield CollaborationEvent(
                        type="reflection",
                        role="supervisor",
                        content="审校发现问题，触发修订员修复...",
                    )
                    yield CollaborationEvent(type="worker_start", role="debugger", content="修订员根据审校报告修复问题...")
                    fix_prompt = (
                        f"## 审校报告\n{review}\n\n"
                        f"## 论文内容\n{consolidated[:3000]}\n\n"
                        "请根据审校报告修订论文，处理存疑引用、修复逻辑问题、调整格式。"
                    )
                    debugger = self._get_worker(AgentRole.DEBUGGER)
                    async for event in debugger.run(fix_prompt):
                        mapped = self._map_event(event, "debugger")
                        if mapped:
                            yield mapped
                    yield CollaborationEvent(type="worker_done", role="debugger", content="修订完成")

            # Step 7: 最终一致性检查
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content="主编执行最终质量检查...",
            )

        except Exception as e:
            yield CollaborationEvent(
                type="worker_done",
                role="supervisor",
                content=f"协同过程出错: {e}",
                metadata={"error": str(e)},
            )

        yield CollaborationEvent(type="done", role="supervisor")

    async def _run_worker(self, role: AgentRole, prompt: str) -> str:
        """运行一个 Worker 并收集文本输出

        若 Worker 仅调工具未输出文本，生成有意义的工具调用摘要。
        """
        worker = self._get_worker(role)
        results = []
        tool_names = []
        async for event in worker.run(prompt):
            if event.type == "text":
                results.append(event.content)
            elif event.type == "tool_start" and event.tool_name:
                tool_names.append(event.tool_name)

        if results:
            return "\n".join(results)
        if tool_names:
            return f"[工具调用摘要] Worker 完成 {len(tool_names)} 次工具调用: {', '.join(tool_names[:10])}"
        return ""

    async def _analyze_intent(self, user_input: str) -> IntentResult:
        """意图分析 — 规则快速判断 + 不确定时 LLM 精细分析

        Returns:
            IntentResult: 含 complexity(simple/complex)、role(角色名)、reason(判断理由)
        """
        # 第一层：快速规则判断（明显简单/复杂直接返回，避免 LLM 调用延迟）
        quick = self._quick_classify(user_input)
        if quick is not None:
            return quick

        # 第二层：LLM 精细意图分析（规则无法确定时调用）
        try:
            return await self._analyze_intent_with_llm(user_input)
        except Exception as e:
            # LLM 分析失败时降级为复杂任务（多智能体兜底，确保不漏）
            return IntentResult(
                complexity="complex",
                role="supervisor",
                reason=f"意图分析 LLM 调用失败，降级为复杂任务: {e}",
            )

    def _quick_classify(self, user_input: str) -> Optional[IntentResult]:
        """快速规则判断 — 明确的简单/复杂任务直接返回，不确定返回 None 触发 LLM 分析

        判断规则:
          - 明确复杂：包含论文写作核心关键词（论文/综述/完整论文/多章节/SCI 等）
          - 明确简单：短问题（<30字）且不含写作关键词（问候/解释/单条指令）
          - 其他：返回 None，交给 LLM 精细分析
        """
        lower = user_input.lower().strip()

        # 明确复杂任务关键词（完整论文写作、多章节、学术写作）
        complex_keywords = [
            "论文", "paper", "综述", "survey", "完整论文", "多章节",
            "sci ", "ssci", "cssci", "ei ", "期刊投稿", "开题报告",
            "毕业论文", "学位论文", "写一篇", "撰写一篇", "帮我写论文",
        ]
        if any(kw in lower for kw in complex_keywords):
            return IntentResult(
                complexity="complex",
                role="supervisor",
                reason="包含完整论文写作关键词，需要多智能体协作",
            )

        # 明确简单任务 — 短问题且无写作关键词
        # 问候/解释/单条指令/概念查询
        simple_greetings = ["你好", "hello", "hi ", "hey", "在吗", "谢谢", "thanks"]
        if any(lower.startswith(g) for g in simple_greetings) or lower in simple_greetings:
            return IntentResult(
                complexity="simple",
                role="general",
                reason="问候或简单对话，由通用助手处理",
            )

        # 短问题（<30字）且不含写作/研究相关词 — 视为简单任务
        writing_hint_words = ["写作", "撰写", "论文", "研究", "方法", "实验", "引用", "参考文献"]
        if len(user_input) < 30 and not any(w in user_input for w in writing_hint_words):
            # 根据问题特征匹配角色
            role = self._match_role_by_keywords(user_input)
            return IntentResult(
                complexity="simple",
                role=role,
                reason="短问题且无写作关键词，由匹配角色处理",
            )

        # 不确定 — 交给 LLM 分析
        return None

    def _match_role_by_keywords(self, text: str) -> str:
        """根据问题关键词匹配最合适的角色，无匹配返回 'general'"""
        lower = text.lower()
        # 文献调研相关
        if any(w in lower for w in ["文献", "检索", "综述", "查找资料", "相关研究", "literature", "search"]):
            return "literature"
        # 方法论相关
        if any(w in lower for w in ["方法", "实验设计", "研究方法", "方案", "methodology", "design"]):
            return "planner"
        # 撰写相关
        if any(w in lower for w in ["写", "撰写", "润色", "改写", "段落", "write", "draft"]):
            return "coder"
        # 审校相关
        if any(w in lower for w in ["审校", "检查", "审查", "验证", "逻辑", "review", "check"]):
            return "reviewer"
        # 修订相关
        if any(w in lower for w in ["修改", "修订", "修复", "改", "fix", "revise"]):
            return "debugger"
        # 引用相关
        if any(w in lower for w in ["引用", "参考文献", "格式化", "citation", "reference"]):
            return "citation"
        # 整理相关
        if any(w in lower for w in ["整合", "整理", "合并", "统一", "consolidate"]):
            return "consolidator"
        # 无匹配 — 通用 Agent
        return "general"

    async def _analyze_intent_with_llm(self, user_input: str) -> IntentResult:
        """使用 LLM 进行精细意图分析

        通过一次不带工具的 LLM 调用，输出结构化 JSON 结果：
        {complexity, role, reason}

        - complexity: "simple"（单Agent可处理） | "complex"（需多智能体协作）
        - role: simple 时选择最匹配的角色；complex 时为 "supervisor"
        - reason: 判断理由（展示给用户）
        """
        system_prompt = """你是 Sage 论文写作系统的意图分析器。分析用户输入，判断任务复杂度并选择最合适的处理方式。

可用角色:
- literature: 文献调研员（文献检索、综述、研究现状分析）
- planner: 方法论专家（研究方法设计、实验方案、论证框架）
- coder: 撰写员（论文各章节具体撰写）
- reviewer: 审校核查员（多重验证、逻辑核查、学术规范检查）
- debugger: 修订员（根据审校报告修复问题）
- citation: 引用管理员（引用插入、参考文献格式化、查重）
- consolidator: 整理汇报员（整合产出、消除重复矛盾）
- general: 通用助手（不属于上述角色的简单问答/解释/通用任务）
- supervisor: 主编（复杂任务需多智能体协作时使用）

判断规则:
1. 复杂任务(complex): 需要完整论文写作、多章节撰写、系统性研究、多步骤协作的任务 → role="supervisor"
2. 简单任务(simple): 单一角色可完成的任务（如查询文献、润色段落、检查引用格式、解释概念等） → role=最匹配的角色

必须输出 JSON 格式（不要任何其他内容）:
{"complexity": "simple" 或 "complex", "role": "角色名", "reason": "简短理由(不超过30字)"}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"用户输入: {user_input}\n\n请分析意图并输出 JSON。"},
        ]

        # 限制 max_tokens 避免浪费（意图分析结果很短）
        response = await self._llm.achat_with_tools(
            messages=messages,
            tools=[],
            max_tokens=200,
        )

        content = (response.content or "").strip()
        # 容错：提取 JSON（LLM 可能包裹在 ```json ... ``` 中）
        if "```" in content:
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if m:
                content = m.group(1)
        # 尝试从原文中提取 JSON
        if not content.startswith("{"):
            import re
            m = re.search(r"\{[^{}]*\}", content, re.DOTALL)
            if m:
                content = m.group(0)

        try:
            data = json.loads(content)
            complexity = data.get("complexity", "simple")
            role = data.get("role", "general")
            reason = data.get("reason", "")

            # 校验 complexity 值
            if complexity not in ("simple", "complex"):
                complexity = "simple"
            # 校验 role 值
            valid_roles = {"literature", "planner", "coder", "reviewer", "debugger",
                           "citation", "consolidator", "general", "supervisor"}
            if role not in valid_roles:
                role = "general"
            # complex 任务强制 role=supervisor
            if complexity == "complex":
                role = "supervisor"

            return IntentResult(complexity=complexity, role=role, reason=reason)
        except (json.JSONDecodeError, KeyError):
            # JSON 解析失败 — 降级为简单任务通用 Agent
            return IntentResult(
                complexity="simple",
                role="general",
                reason="意图分析结果解析失败，由通用助手处理",
            )

    def _has_file_changes(self, agent: AgentLoop) -> bool:
        """检查 Agent 是否进行了实际文件修改（Sage 不依赖 git）"""
        # Sage 论文写作系统不使用 git 检测文件变更，
        # 简化实现：只要 Agent 调用了 write_file/edit_file 工具即视为有修改
        return True

    def _map_event(self, event: LoopEvent, role: str) -> Optional[CollaborationEvent]:
        """将 AgentLoop 事件映射为 CollaborationEvent"""
        # reasoning 事件直接透传（模型思考内容）
        if event.type == "reasoning":
            return CollaborationEvent(
                type="reasoning",
                role=role,
                content=event.content,
            )
        # text 事件直接透传
        if event.type == "text":
            return CollaborationEvent(
                type="text",
                role=role,
                content=event.content,
            )
        mapping = {
            "tool_start": "worker_start",
            "tool_result": "worker_start",
            "error": "worker_done",
        }
        mapped_type = mapping.get(event.type, "worker_start")
        if mapped_type == "worker_start" and event.type == "tool_result":
            return None
        return CollaborationEvent(
            type=mapped_type,
            role=role,
            content=event.content,
            metadata={"tool": event.tool_name, "args": event.tool_args} if event.tool_name else {},
            tokens=event.tokens or {},
        )


def create_orchestrator(workspace: Optional[Path] = None) -> AgentOrchestrator:
    """创建多 Agent 编排器（工厂函数）"""
    return AgentOrchestrator(workspace=workspace)
