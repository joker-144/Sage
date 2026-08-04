"""
智能体加载器 — 从 agents/ 文件夹加载智能体定义和专属技能

每个智能体子目录包含 agent.json 定义文件，有专属技能的智能体还有 skill/ 子目录。

自主创建的智能体存放在 SAGE_DATA_DIR/custom_agents/，与内置 agents/ 物理隔离。
加载时合并两个目录，内置优先（同名 role 不被自定义覆盖）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sage.config import _get_data_dir
from sage.skill_system import SkillInfo


_AGENTS_DIR = Path(__file__).resolve().parent


def _get_custom_agents_dir() -> Path:
    """自主创建的智能体存放目录（SAGE_DATA_DIR/custom_agents/）"""
    return _get_data_dir() / "custom_agents"


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
    """加载 agents/ 目录中所有智能体定义

    同时加载 SAGE_DATA_DIR/custom_agents/ 下的自主创建智能体。
    内置智能体优先：同名 role 不被自定义覆盖。
    """

    def __init__(self, agents_dir: Optional[Path] = None):
        self.agents_dir = agents_dir or _AGENTS_DIR
        self.custom_agents_dir = _get_custom_agents_dir()
        self._cache: dict[str, AgentInfo] = {}
        self._is_custom: set[str] = set()  # 标记哪些 role 是自定义的
        self._load_all()

    def _load_all(self):
        self._cache.clear()
        self._is_custom.clear()
        # 1. 加载内置智能体
        self._load_from_dir(self.agents_dir, is_custom=False)
        # 2. 加载自定义智能体（不覆盖内置）
        self._load_from_dir(self.custom_agents_dir, is_custom=True)

    def _load_from_dir(self, directory: Path, is_custom: bool):
        """从指定目录加载智能体定义"""
        if not directory.exists():
            return
        for d in sorted(directory.iterdir()):
            if not d.is_dir():
                continue
            agent_json = d / "agent.json"
            if not agent_json.exists():
                continue
            try:
                info = AgentInfo.from_json(agent_json)
                # 内置优先：自定义不覆盖已存在的内置 role
                if is_custom and info.role in self._cache:
                    continue
                if info.has_skill:
                    skill_dir = d / "skill"
                    skill_json = skill_dir / "skill.json"
                    if skill_json.exists():
                        info.skill = SkillInfo.from_json(skill_json)
                        info.skill_prompt = self._build_skill_prompt(info.skill)
                self._cache[info.role] = info
                if is_custom:
                    self._is_custom.add(info.role)
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

    def is_custom_agent(self, role: str) -> bool:
        """判断指定 role 是否为自定义智能体"""
        return role in self._is_custom

    def get_custom_agents(self) -> list[AgentInfo]:
        """获取所有自定义智能体"""
        return [info for role, info in self._cache.items() if role in self._is_custom]

    def save_custom_agent(self, info: AgentInfo) -> Path:
        """将审核通过的自定义智能体写入 custom_agents/ 目录

        Args:
            info: 智能体定义

        Returns:
            写入的 agent.json 路径
        """
        agent_dir = self.custom_agents_dir / info.role
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_json_path = agent_dir / "agent.json"
        data = {
            "role": info.role,
            "name": info.name,
            "name_en": info.name_en,
            "description": info.description,
            "capabilities": info.capabilities,
            "system_prompt": info.system_prompt,
            "has_skill": info.has_skill,
        }
        agent_json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return agent_json_path

    def delete_custom_agent(self, role: str) -> bool:
        """删除自定义智能体

        Returns:
            True 如果删除成功，False 如果不存在或为内置
        """
        if role not in self._is_custom:
            return False
        agent_dir = self.custom_agents_dir / role
        if agent_dir.exists():
            import shutil
            shutil.rmtree(agent_dir, ignore_errors=True)
        self._cache.pop(role, None)
        self._is_custom.discard(role)
        return True

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
