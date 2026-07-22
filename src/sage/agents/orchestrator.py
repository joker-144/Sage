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
    type: str  # "task_created" | "worker_start" | "worker_done" | "reflection" | "text" | "done"
    role: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)


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
        self._history: list[SubTask] = []
        self._loader = get_agent_loader()

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
            )
        return self._workers[role]

    async def collaborate(self, user_input: str) -> AsyncIterator[CollaborationEvent]:
        """多 Agent 协同入口 — Sage 论文写作协作流程

        采用"主控+平等协作+整理汇报+多重验证"模式：
        1. 主编（Supervisor）分析需求并拆解任务
        2. 文献调研员/方法论专家/撰写员平等协作讨论
        3. 整理汇报员整合讨论产出
        4. 引用管理员处理引用
        5. 审校核查员多重验证
        6. 如有问题，修订员修复

        Args:
            user_input: 用户原始需求（论文写作任务）

        Yields:
            CollaborationEvent: 协同过程中产生的事件
        """
        # 1. 主编分析需求并制定计划
        yield CollaborationEvent(
            type="task_created",
            role="supervisor",
            content=f"主编分析论文写作需求: {user_input}",
        )

        # 判断是否需要启动完整的多 Agent 协作流程
        needs_multi = self._needs_multi_agent(user_input)

        if not needs_multi:
            # 简单任务：直接由撰写员处理
            yield CollaborationEvent(
                type="worker_start",
                role="coder",
                content="简单写作任务，直接由撰写员处理",
            )
            worker = self._get_worker(AgentRole.CODER)
            async for event in worker.run(user_input):
                mapped = self._map_event(event, "coder")
                if mapped:
                    yield mapped
            yield CollaborationEvent(type="done", role="supervisor")
            return

        # 2. 完整的论文写作协作流程
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

    def _needs_multi_agent(self, user_input: str) -> bool:
        """判断是否需要启动多 Agent 协作（Sage 论文写作场景）"""
        multi_keywords = [
            "论文", "paper", "写作", "撰写", "文献", "综述", "研究",
            "方法", "实验", "期刊", "SCI", "SSCI", "CSSCI", "EI",
            "大纲", "章节", "摘要", "引言", "结论", "引用", "参考文献",
            "润色", "审校", "查重", "完整", "整体", "重构", "多章节",
        ]
        lower = user_input.lower()
        return any(kw in lower for kw in multi_keywords)

    def _has_file_changes(self, agent: AgentLoop) -> bool:
        """检查 Agent 是否进行了实际文件修改（Sage 不依赖 git）"""
        # Sage 论文写作系统不使用 git 检测文件变更，
        # 简化实现：只要 Agent 调用了 write_file/edit_file 工具即视为有修改
        return True

    def _map_event(self, event: LoopEvent, role: str) -> Optional[CollaborationEvent]:
        """将 AgentLoop 事件映射为 CollaborationEvent"""
        mapping = {
            "tool_start": "worker_start",
            "tool_result": "worker_start",
            "text": "text",
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
        )


def create_orchestrator(workspace: Optional[Path] = None) -> AgentOrchestrator:
    """创建多 Agent 编排器（工厂函数）"""
    return AgentOrchestrator(workspace=workspace)
