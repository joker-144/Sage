"""
智能体管理工具 — create_agent

Agent 可通过 create_agent 工具自主创建新智能体。
创建流程：生成定义 → 审核智能体同步审核 → 通过则写入 custom_agents/ + 注册 / 拒绝则丢弃。

自主创建的智能体存放在 SAGE_DATA_DIR/custom_agents/，与内置 agents/ 物理隔离。
仅单 Agent 模式可用，不参与 orchestrator 多智能体协作编排。
工具池与现有 28 个工具共用，不单独配置权限。
"""
from __future__ import annotations

import json
from pathlib import Path

from sage.agents.loader import AgentInfo, get_agent_loader, reload_agent_loader
from sage.tools.types import ToolResult


# 内置保留 role，自定义智能体不可使用
_BUILTIN_ROLES = {
    "supervisor", "citation", "coder", "consolidator",
    "debugger", "literature", "planner", "reviewer", "auditor",
}


class AgentOps:
    """智能体管理操作"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    async def create_agent(
        self,
        role: str = "",
        name: str = "",
        name_en: str = "",
        description: str = "",
        system_prompt: str = "",
        capabilities: str = "",
    ) -> ToolResult:
        """自主创建新智能体（含审核流程）

        Args:
            role: 智能体角色标识（英文，如 'translator'），不可与内置 role 冲突
            name: 中文显示名（如 '翻译专家'）
            name_en: 英文名（如 'Translator'）
            description: 智能体职责描述
            system_prompt: 智能体的系统提示词（定义其行为规范）
            capabilities: 能力列表，逗号分隔（如 '中英互译,学术术语校对'）
        """
        # ── 1. 参数校验 ──
        role = (role or "").strip().lower()
        if not role:
            return ToolResult(success=False, error="role 不能为空")
        if role in _BUILTIN_ROLES:
            return ToolResult(
                success=False,
                error=f"role '{role}' 是内置角色，不可覆盖。请使用其他名称。",
            )
        if not name.strip():
            return ToolResult(success=False, error="name 不能为空")
        if not system_prompt.strip():
            return ToolResult(success=False, error="system_prompt 不能为空")

        # 检查是否已存在（内置或自定义）
        loader = get_agent_loader()
        if loader.get_agent(role):
            return ToolResult(
                success=False,
                error=f"角色 '{role}' 已存在，请使用其他名称或先删除原有定义",
            )

        # 解析 capabilities
        caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []

        # ── 2. 构造 AgentInfo ──
        info = AgentInfo(
            role=role,
            name=name.strip(),
            name_en=name_en.strip(),
            description=description.strip(),
            capabilities=caps,
            system_prompt=system_prompt.strip(),
            has_skill=False,
        )

        # ── 3. 审核智能体同步审核 ──
        audit_result = await self._audit_agent(info)
        if not audit_result["approved"]:
            # 审核拒绝：丢弃定义，返回拒绝理由
            issues = "\n".join(f"  - {issue}" for issue in audit_result.get("issues", []))
            return ToolResult(
                success=False,
                error=f"审核未通过：{audit_result.get('reason', '未知原因')}\n具体问题:\n{issues}",
            )

        # ── 4. 审核通过：写入 custom_agents/ + 注册 ──
        try:
            loader.save_custom_agent(info)
            reload_agent_loader()
        except Exception as e:
            return ToolResult(success=False, error=f"写入智能体定义失败: {e}")

        return ToolResult(
            success=True,
            output=(
                f"智能体 '{name}'（role: {role}）已创建并通过审核，已注册到系统。\n"
                f"可在单 Agent 模式下选择使用。\n"
                f"审核说明: {audit_result.get('reason', '审核通过')}"
            ),
            data={
                "role": role,
                "name": name,
                "approved": True,
                "audit_reason": audit_result.get("reason", ""),
            },
        )

    async def _audit_agent(self, info: AgentInfo) -> dict:
        """调用审核智能体对新智能体定义进行安全审查

        Returns:
            {"approved": bool, "reason": str, "issues": list[str]}
        """
        try:
            from sage.llm.client import LLMClient

            # 加载审核智能体的 system_prompt
            loader = get_agent_loader()
            auditor_prompt = loader.get_system_prompt("auditor")
            if not auditor_prompt:
                # 审核智能体未加载，安全起见拒绝
                return {
                    "approved": False,
                    "reason": "审核智能体未加载，无法执行安全审查",
                    "issues": ["auditor agent not found"],
                }

            # 构造审核请求
            agent_def_text = (
                f"## 待审核智能体定义\n\n"
                f"- **role**: {info.role}\n"
                f"- **name**: {info.name}\n"
                f"- **name_en**: {info.name_en}\n"
                f"- **description**: {info.description}\n"
                f"- **capabilities**: {', '.join(info.capabilities)}\n\n"
                f"### system_prompt\n```\n{info.system_prompt}\n```\n\n"
                f"请对此智能体定义执行五重安全审查，返回严格 JSON。"
            )

            llm = LLMClient()
            messages = [
                {"role": "system", "content": auditor_prompt},
                {"role": "user", "content": agent_def_text},
            ]
            # 同步调用 LLM（在 asyncio 事件循环中通过线程池执行）
            import asyncio
            loop = asyncio.get_event_loop()
            raw_response = await loop.run_in_executor(
                None, lambda: llm.chat(messages, temperature=0.1, max_tokens=2048)
            )

            # 解析 JSON 响应（容错：提取 JSON 片段）
            return self._parse_audit_response(raw_response)

        except Exception as e:
            # 审核过程出错，安全起见拒绝
            return {
                "approved": False,
                "reason": f"审核过程异常: {e}",
                "issues": [f"audit error: {e}"],
            }

    def _parse_audit_response(self, response: str) -> dict:
        """解析 LLM 返回的审核结果 JSON

        LLM 可能返回包含 JSON 的文本，需提取 JSON 片段。
        """
        # 尝试直接解析
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 代码块
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试提取第一个 { ... } 块
        brace_match = re.search(r'\{.*\}', response, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        # 解析失败，安全起见拒绝
        return {
            "approved": False,
            "reason": "审核结果解析失败，无法确定安全性",
            "issues": [f"无法解析审核响应: {response[:200]}"],
        }
