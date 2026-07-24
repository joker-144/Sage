"""
技能系统 — 统一管理所有 Agent 技能

.agent/
└── skills/
    ├── manifest.json  ← 技能聚合清单（自动生成，供 AI 快速浏览所有技能信息）
    ├── planner/       ← 规划Agent 技能
    ├── coder/         ← 编码Agent 技能
    ├── reviewer/      ← 审查Agent 技能
    └── (下载的新技能也放在这里)

每个技能目录包含:
  - skill.json  技能元数据（名称、版本、能力、工具列表、调用时机）
  - SKILL.md    技能详细说明（可选）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# skills 目录路径
# 开发模式: 项目根目录下的 .agent/skills/
# 打包模式: 用户数据目录 SAGE_DATA_DIR/.agent/skills/（可写），首次自动从 _MEIPASS 复制默认技能
if getattr(sys, 'frozen', False):
    import shutil
    # 用户数据目录中的技能目录（可写）
    _data_dir_s = Path(os.environ.get("SAGE_DATA_DIR", "")) if os.environ.get("SAGE_DATA_DIR") else Path(sys._MEIPASS)
    _AGENT_DIR = _data_dir_s / '.agent'
    _SKILLS_DIR = _AGENT_DIR / 'skills'
    # 首次运行时，从 _MEIPASS 复制默认技能到用户数据目录
    _packaged_skills = Path(sys._MEIPASS) / '.agent' / 'skills'
    if _packaged_skills.exists() and not _SKILLS_DIR.exists():
        try:
            _SKILLS_DIR.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(_packaged_skills), str(_SKILLS_DIR), dirs_exist_ok=True)
        except Exception:
            pass
else:
    _AGENT_DIR = Path(__file__).resolve().parent.parent.parent / '.agent'
    _SKILLS_DIR = _AGENT_DIR / 'skills'


def get_skills_dir() -> Path:
    """获取 skills 目录路径（供 CLI/skillhub 使用）
    
    返回相对于项目根目录的相对路径，保证项目移植后可用。
    """
    return _SKILLS_DIR


class SkillInfo:
    """技能元数据"""

    def __init__(self, name: str, version: str, description: str,
                 capabilities: list[str], tools: list[str],
                 trigger_conditions: list[str] | None = None):
        self.name = name
        self.version = version
        self.description = description
        self.capabilities = capabilities
        self.tools = tools
        self.trigger_conditions = trigger_conditions or []

    @classmethod
    def from_json(cls, path: Path) -> "SkillInfo":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=data.get("name", path.parent.name),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
            tools=data.get("tools", []),
            trigger_conditions=data.get("trigger_conditions", []),
        )


# 智能体专属技能角色 — 这些技能已移至 agents/{role}/skill/，不再从通用 skills/ 加载
_AGENT_SKILL_ROLES = {"planner", "coder", "reviewer"}


class SkillLoader:
    """加载 skills 目录中所有技能

    注意：planner/coder/reviewer 已移至 agents/{role}/skill/，
    作为智能体专属技能由 AgentLoader 加载，不再出现在通用技能列表中。
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or _SKILLS_DIR
        # 初始化时自动同步 manifest，让 AI 和前端始终有最新技能清单
        self.sync_manifest()

    def list_all(self) -> dict[str, SkillInfo]:
        """扫描 skills 目录，返回 {agent_name: SkillInfo}

        自动排除已移至 agents/ 的智能体专属技能（planner/coder/reviewer）。
        """
        result = {}
        if not self.skills_dir.exists():
            return result

        for d in sorted(self.skills_dir.iterdir()):
            if not d.is_dir():
                continue
            # 排除已迁移到 agents/ 的智能体专属技能
            if d.name.lower() in _AGENT_SKILL_ROLES:
                continue
            skill_json = d / "skill.json"
            if skill_json.exists():
                try:
                    skill = SkillInfo.from_json(skill_json)
                    result[d.name] = skill
                except Exception:
                    pass
        return result

    def get_skill(self, agent_name: str) -> Optional[SkillInfo]:
        return self.list_all().get(agent_name)

    def get_by_dir_name(self, dir_name: str) -> Optional[SkillInfo]:
        """按目录名获取技能（供下载后直接使用）"""
        skill_json = self.skills_dir / dir_name / "skill.json"
        if skill_json.exists():
            try:
                return SkillInfo.from_json(skill_json)
            except Exception:
                pass
        return None

    def format_for_prompt(self) -> str:
        """生成供 Agent system prompt 使用的技能描述（含调用时机，减少 AI 重复查 list_skills）"""
        skills = self.list_all()
        if not skills:
            return ""

        lines = [
            "\n## 可用技能清单\n",
            "以下技能已安装，AI 可根据用户需求直接调用对应技能，无需先通过 list_skills 查询。",
        ]
        for agent_name, skill in skills.items():
            caps = "\n    " + "\n    ".join(skill.capabilities[:5]) if skill.capabilities else ""
            triggers = "\n    " + "\n    ".join(skill.trigger_conditions[:4]) if skill.trigger_conditions else ""
            lines.append(
                f"\n### {agent_name} (v{skill.version})\n"
                f"**描述**: {skill.description}\n"
                f"**核心能力**:{caps}\n"
                f"**适用场景/调用时机**:{triggers}"
            )
        lines.append("")
        return "\n".join(lines) + "\n"

    def generate_manifest(self) -> dict:
        """生成技能聚合清单（manifest.json 的内容）

        此清单供 AI 快速浏览所有技能信息，避免反复调用 list_skills。
        """
        skills = self.list_all()
        if not skills:
            return {"skills": [], "count": 0, "summary": "暂无已安装技能"}

        entries = []
        for agent_name, skill in skills.items():
            entries.append({
                "agent_name": agent_name,
                "name": skill.name,
                "version": skill.version,
                "description": skill.description,
                "capabilities": skill.capabilities,
                "tools": skill.tools,
                "trigger_conditions": skill.trigger_conditions,
            })

        summary = f"已安装 {len(entries)} 个技能: {', '.join(e['agent_name'] for e in entries)}"
        return {"skills": entries, "count": len(entries), "summary": summary}

    def sync_manifest(self):
        """将 manifest 写入磁盘，供前端和 API 直接加载"""
        manifest = self.generate_manifest()
        manifest_path = self.skills_dir / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def reload(cls) -> "SkillLoader":
        """重新加载技能（安装/卸载后调用），自动同步 manifest"""
        loader = cls()
        loader.sync_manifest()
        return loader
