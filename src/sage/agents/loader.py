"""
智能体加载器 — 从 agents/ 文件夹加载智能体定义和专属技能

每个智能体子目录包含 agent.json 定义文件，有专属技能的智能体还有 skill/ 子目录。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sage.skill_system import SkillInfo


_AGENTS_DIR = Path(__file__).resolve().parent


@dataclass
class AgentInfo:
    """智能体定义"""
    role: str
    name: str
    name_en: str
    description: str
    capabilities: list[str] = field(default_factory=list)
    system_prompt: str = ""
    has_skill: bool = False
    skill: Optional[SkillInfo] = None
    skill_prompt: str = ""

    @classmethod
    def from_json(cls, path: Path) -> "AgentInfo":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            role=data.get("role", path.parent.name),
            name=data.get("name", path.parent.name),
            name_en=data.get("name_en", ""),
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
            system_prompt=data.get("system_prompt", ""),
            has_skill=data.get("has_skill", False),
        )

    def to_dict(self) -> dict:
        result = {
            "name": f"{self.name}（{self.name_en}）" if self.name_en else self.name,
            "role": self.role,
            "description": self.description,
            "capabilities": self.capabilities,
            "has_skill": self.has_skill,
        }
        if self.skill:
            result["skill"] = {
                "name": self.skill.name,
                "version": self.skill.version,
                "description": self.skill.description,
                "capabilities": self.skill.capabilities,
                "tools": self.skill.tools,
                "trigger_conditions": self.skill.trigger_conditions,
            }
        return result


class AgentLoader:
    """加载 agents/ 目录中所有智能体定义"""

    def __init__(self, agents_dir: Optional[Path] = None):
        self.agents_dir = agents_dir or _AGENTS_DIR
        self._cache: dict[str, AgentInfo] = {}
        self._load_all()

    def _load_all(self):
        self._cache.clear()
        if not self.agents_dir.exists():
            return
        for d in sorted(self.agents_dir.iterdir()):
            if not d.is_dir():
                continue
            agent_json = d / "agent.json"
            if not agent_json.exists():
                continue
            try:
                info = AgentInfo.from_json(agent_json)
                if info.has_skill:
                    skill_dir = d / "skill"
                    skill_json = skill_dir / "skill.json"
                    if skill_json.exists():
                        info.skill = SkillInfo.from_json(skill_json)
                        info.skill_prompt = self._build_skill_prompt(info.skill)
                self._cache[info.role] = info
            except Exception:
                pass

    def _build_skill_prompt(self, skill: SkillInfo) -> str:
        caps = "\n    ".join(skill.capabilities[:5]) if skill.capabilities else "无"
        triggers = "\n    ".join(skill.trigger_conditions[:4]) if skill.trigger_conditions else "无"
        return (
            f"\n## 你的专属技能: {skill.name} (v{skill.version})\n"
            f"**描述**: {skill.description}\n"
            f"**核心能力**:\n    {caps}\n"
            f"**适用场景/调用时机**:\n    {triggers}\n"
        )

    def get_agent(self, role: str) -> Optional[AgentInfo]:
        return self._cache.get(role)

    def get_all_agents(self) -> list[AgentInfo]:
        return list(self._cache.values())

    def get_system_prompt(self, role: str) -> str:
        info = self._cache.get(role)
        if not info:
            return ""
        parts = [info.system_prompt]
        if info.skill_prompt:
            parts.append(info.skill_prompt)
        return "\n".join(parts)

    def get_all_role_info(self) -> list[dict]:
        return [info.to_dict() for info in self._cache.values()]

    def get_agent_skill(self, role: str) -> Optional[SkillInfo]:
        info = self._cache.get(role)
        return info.skill if info else None

    @classmethod
    def reload(cls) -> "AgentLoader":
        return cls()


_loader: Optional[AgentLoader] = None


def get_agent_loader() -> AgentLoader:
    global _loader
    if _loader is None:
        _loader = AgentLoader()
    return _loader


def reload_agent_loader() -> AgentLoader:
    global _loader
    _loader = AgentLoader()
    return _loader
