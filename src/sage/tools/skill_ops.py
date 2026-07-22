"""
技能管理工具 — list_skills、load_skill、install_skill

Agent 可通过这些工具检索、加载、安装技能。
安装技能使用内置的 SkillHubClient（HTTP 直接调用远程服务），
不依赖外部 skillhub CLI。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from sage.skill_hub_client import SkillHubClient
from sage.skill_system import SkillLoader, get_skills_dir
from sage.tools.types import ToolResult


class SkillOps:
    """技能管理操作"""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.loader = SkillLoader()
        self.hub = SkillHubClient()

    async def list_skills(self) -> ToolResult:
        """列出当前所有技能"""
        skills = self.loader.list_all()
        if not skills:
            return ToolResult(success=True, data="skills 目录为空，暂无技能。")
        lines = ["## 当前已安装技能\n"]
        for name, skill in skills.items():
            caps = ", ".join(skill.capabilities)
            lines.append(f"- **{name}** ({skill.version}): {skill.description}")
            lines.append(f"  能力: {caps}")
        return ToolResult(success=True, data="\n".join(lines))

    async def load_skill(self, name: str = "") -> ToolResult:
        """加载指定技能的详细信息"""
        if name:
            skill = self.loader.get_by_dir_name(name)
            if not skill:
                return ToolResult(success=False, error=f"技能 '{name}' 不存在。可用的: {list(self.loader.list_all().keys())}")
            caps = "\n".join(f"- {c}" for c in skill.capabilities)
            tools = "\n".join(f"- {t}" for t in skill.tools)
            return ToolResult(success=True, data=(
                f"## {skill.name} (v{skill.version})\n\n"
                f"{skill.description}\n\n"
                f"### 核心能力\n{caps}\n\n"
                f"### 关联工具\n{tools}"
            ))
        else:
            return await self.list_skills()

    async def install_skill(self, name: str = "") -> ToolResult:
        """从远程 SkillHub 安装技能到本地 .agent/skills/ 目录

        使用内置 SkillHubClient 直接通过 HTTP 下载技能 zip 包并解压。
        不依赖任何外部 CLI。

        Args:
            name: 技能 slug（如 code-reviewer、self-improving）
        """
        if not name:
            return ToolResult(success=False, error="请提供要安装的技能名称")

        skills_dir = get_skills_dir()
        skills_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 内置客户端：远程下载并安装
            result = await self.hub.download_and_install(
                slug=name,
                target_dir=skills_dir,
                force=False,
            )

            if not result.get("success"):
                return ToolResult(
                    success=False,
                    error=result.get("error", "未知错误"),
                )

            # 重新加载技能清单
            from sage.skill_system import SkillLoader
            SkillLoader.reload()

            skill_json = result.get("skill_json", {})
            skill_name = skill_json.get("name", name)
            version = skill_json.get("version", "1.0.0")
            desc = skill_json.get("description", "")

            return ToolResult(
                success=True,
                data=(
                    f"技能 '{skill_name}' 安装成功！\n"
                    f"版本: v{version}\n"
                    f"描述: {desc}\n"
                    f"安装路径: {result.get('path', '')}\n\n"
                    f"现在可以使用 load_skill 工具加载它，或通过 list_skills 查看所有已安装技能。"
                ),
            )

        except Exception as e:
            return ToolResult(success=False, error=f"安装过程异常: {e}")

    async def search_remote_skills(self, query: str = "", limit: int = 10) -> ToolResult:
        """搜索远程技能库

        Args:
            query: 搜索关键词
            limit: 返回数量
        """
        try:
            results = await self.hub.search(query=query, limit=limit)
            if not results:
                return ToolResult(
                    success=True,
                    data=f"未找到与 '{query}' 相关的技能。",
                )

            lines = [f"## 远程技能搜索结果: {query or '全部'}\n"]
            for r in results:
                lines.append(f"- **{r.slug}** (v{r.version or '?'})")
                if r.name and r.name != r.slug:
                    lines.append(f"  名称: {r.name}")
                if r.author:
                    lines.append(f"  作者: {r.author}")
                if r.description:
                    lines.append(f"  描述: {r.description[:200]}")
                if r.tags:
                    lines.append(f"  标签: {', '.join(r.tags[:5])}")
                lines.append("")

            lines.append("使用 install_skill 工具 + 上面的 slug 安装技能。")
            return ToolResult(success=True, data="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"搜索失败: {e}")
