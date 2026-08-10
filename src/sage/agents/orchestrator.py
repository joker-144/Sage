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
    type: str  # "task_created" | "worker_start" | "worker_done" | "reflection" | "text" | "reasoning" | "retry" | "done"
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

        # Step 3: 复杂任务 — 主编动态调度子智能体（按批次并行）
        try:
            # 主编生成执行计划
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content="主编正在分析需求，生成执行计划...",
            )
            plan = await self._generate_execution_plan(user_input)

            # 展示执行计划
            batches = plan.get("batches", [])
            plan_desc_parts = []
            for batch in batches:
                roles = batch.get("roles", [])
                role_labels = [_ROLE_LABELS.get(r, r) for r in roles]
                plan_desc_parts.append(f"批次{batch.get('id', '?')}: {' + '.join(role_labels)}（并行）")
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content=f"执行计划:\n" + "\n".join(plan_desc_parts),
            )

            # 按批次执行
            batch_results: dict[str, str] = {}  # 累积各角色产出，供后续批次依赖
            for batch in batches:
                roles = batch.get("roles", [])
                if not roles:
                    continue
                # 同一批次并行执行
                async for event in self._run_parallel_workers(roles, user_input, batch_results):
                    yield event

            # 最终质量检查
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
        """快速规则判断 — 基于动词+宾语模式直接匹配智能体，不确定返回 None 触发 LLM 分析

        优先级:
          1. 明确的多智能体协作任务（写一篇完整论文/多章节/开题报告等）→ complex
          2. 精细的动词+宾语模式匹配 → simple + 具体角色（如"生成目录"→coder）
          3. 问候/简单对话 → simple + general
          4. 短问题兜底匹配 → simple + 匹配角色
          5. 不确定 → None，交给 LLM 精细分析

        注意：单独的"论文"一词不再触发复杂任务，避免"生成论文目录"被误判。
        """
        lower = user_input.lower().strip()

        # ── 第一优先级：明确的多智能体协作任务 ──
        # 只有明确的"完整论文写作"才判为复杂任务，单独"论文"不触发
        complex_keywords = [
            "完整论文", "多章节", "写一篇论文", "撰写一篇论文", "帮我写论文",
            "sci ", "ssci", "cssci", "ei ", "期刊投稿", "开题报告",
            "毕业论文", "学位论文", "综述论文", "写一篇 paper", "survey paper",
        ]
        if any(kw in lower for kw in complex_keywords):
            return IntentResult(
                complexity="complex",
                role="supervisor",
                reason="包含完整论文写作关键词，需要多智能体协作",
            )

        # ── 第二优先级：精细的动词+宾语模式匹配 → 直接分配智能体 ──
        # 优先于泛化关键词匹配，确保"生成目录"等明确指令不被"文献调研"等前缀干扰
        role = self._match_role_by_patterns(user_input)
        if role is not None:
            return IntentResult(
                complexity="simple",
                role=role,
                reason=f"规则匹配到 {_ROLE_LABELS.get(role, role)} 处理",
            )

        # ── 第三优先级：问候/简单对话 ──
        simple_greetings = ["你好", "hello", "hi ", "hey", "在吗", "谢谢", "thanks"]
        if any(lower.startswith(g) for g in simple_greetings) or lower in simple_greetings:
            return IntentResult(
                complexity="simple",
                role="general",
                reason="问候或简单对话，由通用助手处理",
            )

        # ── 第四优先级：短问题兜底匹配 ──
        if len(user_input) < 30:
            fallback_role = self._match_role_by_keywords(user_input)
            return IntentResult(
                complexity="simple",
                role=fallback_role,
                reason="短问题，由匹配角色处理",
            )

        # ── 不确定 — 交给 LLM 分析 ──
        return None

    def _match_role_by_patterns(self, text: str) -> Optional[str]:
        """基于动词+宾语模式精细匹配智能体角色，无匹配返回 None

        优先级：撰写指令 > 文献检索 > 引用处理 > 审校 > 修订
        （撰写指令优先，避免用户问题中残留的"文献调研"前缀干扰真实写作意图）
        """
        lower = text.lower()

        # 撰写员(coder)：生成/写/撰写 + 目录/摘要/章节/前言/结论等
        writing_actions = ["生成", "写", "撰写", "起草", "输出", "给", "提供", "列"]
        writing_objects = [
            "目录", "摘要", "前言", "引言", "结论", "正文", "章节", "大纲",
            "框架", "abstract", "outline", "内容", "标题",
        ]
        if any(a in lower for a in writing_actions) and any(o in lower for o in writing_objects):
            return "coder"

        # 文献调研员(literature)：检索/查找/调研 + 文献/资料/相关研究
        lit_actions = ["检索", "查找", "调研", "搜索", "搜集", "收集"]
        lit_objects = ["文献", "资料", "相关研究", "相关论文", "literature"]
        if any(a in lower for a in lit_actions) and any(o in lower for o in lit_objects):
            return "literature"

        # 引用管理员(citation)：检查/格式化 + 引用格式/参考文献格式
        cite_actions = ["格式化", "规范化", "整理"]
        cite_objects = ["引用格式", "参考文献格式", "citation", "引用规范"]
        if any(a in lower for a in cite_actions) and any(o in lower for o in cite_objects):
            return "citation"

        # 审校核查员(reviewer)：审校/审查/核查/查重
        review_actions = ["审校", "审查", "核查", "查重", "检查"]
        review_objects = ["逻辑", "规范", "重复", "学术不端", "质量"]
        if any(a in lower for a in review_actions) and any(o in lower for o in review_objects):
            return "reviewer"

        # 修订员(debugger)：修改/修订/修复 + 论文/内容/段落
        revise_actions = ["修改", "修订", "修复", "调整", "润色"]
        revise_objects = ["论文", "内容", "段落", "章节", "语句"]
        if any(a in lower for a in revise_actions) and any(o in lower for o in revise_objects):
            return "debugger"

        # 无匹配
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

    # ── 动态执行计划生成（主编根据用户需求按需分配子智能体） ──

    async def _generate_execution_plan(self, user_input: str) -> dict:
        """主编用 LLM 分析用户需求，生成动态执行计划

        计划格式:
        {
            "batches": [
                {"id": 1, "roles": ["literature", "planner"], "depends_on": []},
                {"id": 2, "roles": ["coder"], "depends_on": [1]},
                {"id": 3, "roles": ["consolidator"], "depends_on": [2]},
                {"id": 4, "roles": ["citation"], "depends_on": [3]},
                {"id": 5, "roles": ["reviewer"], "depends_on": [4]}
            ]
        }

        同一 batch 内的 roles 并行执行；batch 间按 depends_on 串行。
        每个子智能体只分配需要的，不强制全流程。
        """
        system_prompt = """你是 Sage 论文写作系统的主编调度器。根据用户需求，生成动态执行计划。

可用子智能体:
- literature: 文献调研员（文献检索、综述、研究现状分析）
- planner: 方法论专家（研究方法设计、实验方案、论证框架）
- coder: 撰写员（论文各章节具体撰写）
- consolidator: 整理汇报员（整合多份产出、消除重复矛盾、统一风格）
- citation: 引用管理员（引用插入、参考文献格式化、查重）
- reviewer: 审校核查员（多重验证、逻辑核查、学术规范检查）
- debugger: 修订员（根据审校报告修复问题）

调度原则:
1. 按需分配：只分配用户需求真正需要的子智能体，不强制全流程
2. 依赖关系：
   - coder 依赖 literature 和/或 planner（需要素材才能写）
   - consolidator 依赖 coder（需要初稿才能整合）
   - citation 依赖 coder 或 consolidator（需要内容才能处理引用）
   - reviewer 依赖 coder/consolidator/citation（需要内容才能审校）
   - debugger 依赖 reviewer（需要审校报告才能修订）
3. 并行优化：无依赖的子智能体放同一批次并行（如 literature + planner 可并行）
4. 简单任务可只分配 1-2 个子智能体（如"生成目录"只需 coder）

必须输出 JSON 格式（不要任何其他内容）:
{"batches": [{"id": 1, "roles": ["角色名"], "depends_on": []}, ...]}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"用户需求: {user_input}\n\n请生成执行计划。"},
        ]

        try:
            response = await self._llm.achat_with_tools(
                messages=messages,
                tools=[],
                max_tokens=500,
            )
            content = (response.content or "").strip()
            # 容错：提取 JSON
            if "```" in content:
                import re
                m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
                if m:
                    content = m.group(1)
            if not content.startswith("{"):
                import re
                m = re.search(r"\{.*\}", content, re.DOTALL)
                if m:
                    content = m.group(0)

            plan = json.loads(content)
            # 规则校验并修正
            plan = self._validate_plan(plan)
            return plan
        except Exception as e:
            # LLM 生成失败 → 回退到经典流程（文献→方法→撰写→整理→引用→审校）
            return self._fallback_plan()

    def _validate_plan(self, plan: dict) -> dict:
        """规则校验执行计划的依赖合理性，自动修正不合理的依赖

        规则:
        - citation 必须在 coder 或 consolidator 之后
        - reviewer 必须在 coder/consolidator/citation 之后
        - debugger 必须在 reviewer 之后
        - consolidator 必须在 coder 之后
        - 每个角色只能在计划中出现一次
        """
        # 依赖规则：角色 -> 它依赖的角色（至少一个必须在更早的批次出现）
        dependency_rules = {
            "citation": ["coder", "consolidator"],      # 引用需要内容
            "reviewer": ["coder", "consolidator", "citation"],  # 审校需要内容
            "debugger": ["reviewer"],                    # 修订需要审校报告
            "consolidator": ["coder"],                   # 整理需要初稿
        }

        batches = plan.get("batches", [])
        if not batches:
            return self._fallback_plan()

        # 收集所有已分配角色（去重，重复的只保留第一个）
        seen_roles = set()
        cleaned_batches = []
        for batch in batches:
            roles = batch.get("roles", [])
            unique_roles = [r for r in roles if r not in seen_roles]
            if unique_roles:
                seen_roles.update(unique_roles)
                batch["roles"] = unique_roles
                cleaned_batches.append(batch)
        batches = cleaned_batches

        # 检查并修正依赖：如果角色依赖的角色还没出现，把它推迟到下一批
        # 记录每个角色出现的批次 id
        role_batch_map = {}
        for batch in batches:
            for role in batch.get("roles", []):
                role_batch_map[role] = batch.get("id", 0)

        # 找出违反依赖的角色
        violations = []
        for role, deps in dependency_rules.items():
            if role in role_batch_map:
                role_batch = role_batch_map[role]
                # 至少一个依赖角色必须在更早或同批出现
                dep_satisfied = False
                for dep in deps:
                    if dep in role_batch_map and role_batch_map[dep] <= role_batch:
                        dep_satisfied = True
                        break
                # 特殊：coder 没有硬依赖（可基于用户需求直接写）
                if not dep_satisfied and role != "coder":
                    violations.append((role, deps))

        # 如果有违反，重建为经典串行流程
        if violations:
            return self._fallback_plan()

        plan["batches"] = batches
        return plan

    def _fallback_plan(self) -> dict:
        """回退计划：经典串行流程（文献→方法→撰写→整理→引用→审校）"""
        return {
            "batches": [
                {"id": 1, "roles": ["literature", "planner"], "depends_on": []},
                {"id": 2, "roles": ["coder"], "depends_on": [1]},
                {"id": 3, "roles": ["consolidator"], "depends_on": [2]},
                {"id": 4, "roles": ["citation"], "depends_on": [3]},
                {"id": 5, "roles": ["reviewer"], "depends_on": [4]},
            ]
        }

    async def _run_parallel_workers(
        self,
        roles: list[str],
        user_input: str,
        batch_results: dict,
    ) -> AsyncIterator[CollaborationEvent]:
        """并行运行多个 worker，实时 yield 各 worker 的事件

        Args:
            roles: 本批次要并行执行的角色列表
            user_input: 用户原始需求
            batch_results: 前序批次的产出 {role: result_text}

        Yields:
            CollaborationEvent: 各 worker 产生的事件（交错yield）
        """
        # 为每个角色构建 prompt（包含用户需求 + 依赖的前序产出）
        async def run_single(role_name: str) -> tuple[str, list[CollaborationEvent], str]:
            """运行单个 worker，收集事件和文本输出"""
            events = []
            results = []

            # 构建依赖上下文
            dep_context = ""
            dependency_map = {
                "planner": ["literature"],
                "coder": ["literature", "planner"],
                "consolidator": ["coder"],
                "citation": ["coder", "consolidator"],
                "reviewer": ["coder", "consolidator", "citation"],
                "debugger": ["reviewer"],
            }
            deps = dependency_map.get(role_name, [])
            for dep in deps:
                if dep in batch_results:
                    dep_context += f"\n\n## {dep} 的产出\n{batch_results[dep][:2000]}\n"

            # 角色专属 prompt 引导
            role_prompts = {
                "literature": "请针对以下论文写作需求进行文献调研。输出格式: 1) 研究背景与发展脉络 2) 主要研究流派 3) 研究空白与机会 4) 关键参考文献列表（含DOI/URL）",
                "planner": "请基于以下材料设计研究方法。输出格式: 1) 研究问题与假设 2) 研究方法选型与理由 3) 实验/研究设计 4) 数据分析方法 5) 论证框架",
                "coder": "请基于以下材料撰写论文内容。要求：1) 结构完整 2) 需要引用处用 [CITE: 关键词] 标注 3) 学术语言规范",
                "consolidator": "请整合以下产出，消除重复、调和矛盾、统一风格，输出连贯完整的论文内容。",
                "citation": "请处理所有 [CITE: 关键词] 标记：1) 从文献库匹配相关文献 2) 插入规范引用 3) 格式化参考文献列表 4) 验证引用真实性 5) 标注存疑引用",
                "reviewer": "请执行四重验证: 1) 文献库验证 2) 逻辑核查 3) 外部检索验证（存疑引用）4) 学术规范检查。输出审校报告。",
                "debugger": "请根据审校报告修订论文，处理存疑引用、修复逻辑问题、调整格式。",
            }

            prompt = f"## 用户需求\n{user_input}\n{dep_context}\n\n{role_prompts.get(role_name, '')}"

            role_label = _ROLE_LABELS.get(role_name, role_name)
            events.append(CollaborationEvent(
                type="worker_start",
                role=role_name,
                content=f"{role_label}开始工作...",
            ))

            # 获取 worker 并运行
            worker = self._get_worker_by_role_name(role_name)
            if worker is None:
                events.append(CollaborationEvent(
                    type="worker_done",
                    role=role_name,
                    content=f"{role_label}角色不存在，跳过",
                ))
                return role_name, events, ""

            async for event in worker.run(prompt):
                mapped = self._map_event(event, role_name)
                if mapped:
                    events.append(mapped)
                    if mapped.type == "text":
                        results.append(mapped.content)

            text_output = "\n".join(results) if results else ""
            events.append(CollaborationEvent(
                type="worker_done",
                role=role_name,
                content=f"{role_label}完成工作",
            ))
            return role_name, events, text_output

        # 并行执行所有 worker
        tasks = [asyncio.create_task(run_single(r)) for r in roles]
        # 实时 yield 已完成 worker 的事件
        for coro in asyncio.as_completed(tasks):
            role_name, events, text_output = await coro
            for event in events:
                yield event
            # 存储结果供后续批次使用
            batch_results[role_name] = text_output

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
        # retry 事件直接透传（LLM 调用重试通知）
        if event.type == "retry":
            return CollaborationEvent(
                type="retry",
                role=role,
                content=event.content,
                metadata=event.tool_args or {},
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
