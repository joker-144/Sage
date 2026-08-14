"""
PaperProject — 论文写作共享草稿文档

解决多智能体协作中"前序产出被截断为 2000 字符残片"导致的全文一致性缺失问题。

设计目标:
  1. 大纲（章节树 + 各节目标字数）结构化持久化
  2. 每个角色 worker 的完整产出作为"素材"保存，不再截断
  3. 论文正文按大纲章节分节存储，下游可读全文或指定章节
  4. 最终渲染为工作空间根目录 paper.md，供用户查阅与导出

存储布局:
  - 运行态: 内存中的 outline / material / sections
  - 持久化: <workspace>/.sage/paper_project.json（结构化状态）
  - 成稿:   <workspace>/paper.md（最终渲染的 markdown）
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 大纲章节 key 与展示标题的规范映射（缺省 IMRaD 结构）
DEFAULT_OUTLINE = [
    {"key": "abstract", "title": "摘要", "target_words": 300},
    {"key": "introduction", "title": "引言", "target_words": 800},
    {"key": "related_work", "title": "相关工作", "target_words": 1000},
    {"key": "methodology", "title": "研究方法", "target_words": 1500},
    {"key": "experiment", "title": "实验与结果", "target_words": 2000},
    {"key": "discussion", "title": "讨论", "target_words": 1000},
    {"key": "conclusion", "title": "结论", "target_words": 500},
    {"key": "references", "title": "参考文献", "target_words": 0},
]

# 角色产出 → 素材 key（全文保存，供下游 worker 读取）
ROLE_MATERIAL_KEY = {
    "literature": "literature_review",
    "planner": "methodology_design",
    "coder": "draft",
    "consolidator": "draft",
    "debugger": "draft",
    "citation": "references",
    "reviewer": "review_report",
}


def estimate_paper_cost(outline: list["PaperSection"]) -> dict:
    """根据大纲目标字数预估全文规模与 LLM 成本（生成前反馈给用户）。

    Returns:
        {"total_target_words", "section_count", "est_output_tokens", "est_llm_calls"}
    """
    total_words = sum(s.target_words for s in outline)
    section_count = len([s for s in outline if s.target_words > 0])
    # 中文场景粗估：生成侧约 1 字符 ≈ 1 token
    est_output_tokens = total_words
    # LLM 调用次数粗估：每章撰写 + 大纲/计划/整理/引用/审校/修订等固定开销
    est_llm_calls = section_count + 6
    return {
        "total_target_words": total_words,
        "section_count": section_count,
        "est_output_tokens": est_output_tokens,
        "est_llm_calls": est_llm_calls,
    }


@dataclass
class PaperSection:
    """论文大纲中的一个章节"""
    key: str                     # 稳定标识，如 "introduction"
    title: str                   # 展示标题，如 "引言"
    target_words: int = 0        # 目标字数预算（0 = 不限制）
    content: str = ""            # 本节草稿内容

    @property
    def word_count(self) -> int:
        return len(self.content)


class PaperProject:
    """共享草稿文档 — 编排器内部唯一事实来源"""

    def __init__(
        self,
        workspace: Path,
        meta_path: Optional[Path] = None,
        draft_path: Optional[Path] = None,
    ):
        self.workspace = Path(workspace)
        self.outline: list[PaperSection] = []
        self.material: dict[str, str] = {}
        # 元数据与成稿路径可注入（默认 .sage/paper_project.json 与 paper.md）
        self._meta_path = Path(meta_path) if meta_path else self.workspace / ".sage" / "paper_project.json"
        self._draft_path = Path(draft_path) if draft_path else self.workspace / "paper.md"

    # ── 大纲 ──

    def set_outline(self, sections: list[dict]):
        """设置大纲。sections 为 [{"key","title","target_words"}]，缺省字段自动补。

        缺 key 时用 title 派生 key；缺 title 时用 key 兜底；重复 key 去重。
        """
        normalized = []
        seen = set()
        for s in sections:
            key = str(s.get("key", "")).strip()
            title = str(s.get("title", "")).strip()
            if not key:
                key = self._slugify(title) if title else ""
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(PaperSection(
                key=key,
                title=title or key,
                target_words=int(s.get("target_words", 0) or 0),
                content="",
            ))
        # 保留已存在的章节内容（大纲重设时不清空正文）
        old_content = {sec.key: sec.content for sec in self.outline}
        self.outline = normalized
        for sec in self.outline:
            sec.content = old_content.get(sec.key, "")
        return self.outline

    def get_outline(self) -> list[PaperSection]:
        return self.outline

    def outline_text(self) -> str:
        """大纲的紧凑文本表示，用于注入 worker prompt"""
        if not self.outline:
            return ""
        lines = ["论文大纲:"]
        for sec in self.outline:
            wc = f"（约 {sec.target_words} 字）" if sec.target_words else ""
            status = "已写" if sec.content else "待写"
            lines.append(f"- {sec.title} [{sec.key}] {wc} — {status}")
        return "\n".join(lines)

    def outline_progress(self) -> str:
        """大纲完成度摘要（已写/待写）"""
        if not self.outline:
            return "（未设置大纲）"
        done = sum(1 for s in self.outline if s.content.strip())
        return f"大纲进度: {done}/{len(self.outline)} 个章节已写"

    def missing_sections(self) -> list["PaperSection"]:
        """返回尚未撰写（有字数预算但内容为空）的章节。"""
        return [s for s in self.outline if s.target_words > 0 and not s.content.strip()]

    def missing_sections_text(self) -> str:
        """未完成章节的文本表示，供续写 prompt 引用。"""
        missing = self.missing_sections()
        if not missing:
            return "（所有章节均已完成）"
        lines = []
        for s in missing:
            wc = f"（约 {s.target_words} 字）" if s.target_words else ""
            lines.append(f"- {s.title} {wc}")
        return "\n".join(lines)

    # ── 素材（角色完整产出） ──

    def store_material(self, role: str, content: str):
        """保存角色的完整产出（不截断）。"""
        key = ROLE_MATERIAL_KEY.get(role, role)
        self.material[key] = content or ""

    def get_material(self, role: str) -> str:
        key = ROLE_MATERIAL_KEY.get(role, role)
        return self.material.get(key, "")

    def material_for(self, roles: list[str]) -> str:
        """拼接多个角色的完整产出，供下游 worker 读取全文。"""
        parts = []
        for role in roles:
            content = self.get_material(role)
            if content:
                parts.append(f"## {role} 的完整产出\n{content}")
        return "\n\n".join(parts)

    # ── 章节正文 ──

    def fill_section(self, key: str, content: str):
        """按 key 写入章节正文（key 不存在于大纲时自动补一节）。"""
        content = content or ""
        for sec in self.outline:
            if sec.key == key:
                sec.content = content
                return
        # 大纲外的 key → 追加
        self.outline.append(PaperSection(key=key, title=key, content=content))

    def get_section(self, key: str) -> str:
        for sec in self.outline:
            if sec.key == key:
                return sec.content
        return ""

    def parse_draft_to_sections(self, markdown: str):
        """把带 `## 标题` 的 markdown 草稿按标题拆分，填入匹配的大纲章节。

        匹配规则: 标题包含大纲章节的 title 或 key；无法匹配的内容保留为
        "正文其他部分" 章节。这样撰写员按大纲输出后，正文即被结构化存储。
        """
        if not markdown:
            return
        blocks = re.split(r"(?m)^(#{2,4})\s+(.+?)\s*$", markdown)
        # blocks[0] 是第一个标题之前的内容
        preamble = blocks[0].strip()
        if preamble:
            self.fill_section("_preamble", preamble)
        i = 1
        while i + 1 < len(blocks):
            title = blocks[i + 1].strip()
            body = blocks[i + 2].strip()
            i += 3
            key = self._match_section_key(title)
            if key is None:
                key = self._slugify(title)
                self._ensure_section(key, title)
            self.fill_section(key, body)

    def _match_section_key(self, title: str) -> Optional[str]:
        t = title.lower()
        for sec in self.outline:
            if sec.title and (sec.title in title or title in sec.title):
                return sec.key
            if sec.key and sec.key.lower() in t:
                return sec.key
        return None

    @staticmethod
    def _slugify(title: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", "_", title).strip("_")
        return slug or "section"

    def _ensure_section(self, key: str, title: str):
        if not any(s.key == key for s in self.outline):
            self.outline.append(PaperSection(key=key, title=title, content=""))

    # ── 全文与成稿 ──

    def read_draft(self) -> str:
        """渲染当前完整草稿（大纲顺序 + 未匹配的附加章节）"""
        parts = []
        for sec in self.outline:
            if sec.content.strip():
                parts.append(f"## {sec.title}\n\n{sec.content.strip()}")
        return "\n\n".join(parts)

    def draft_word_count(self) -> int:
        return sum(s.word_count for s in self.outline)

    # ── 持久化 ──

    def save(self):
        """保存结构化状态到 .sage/paper_project.json（失败不影响流程）"""
        try:
            self._meta_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "outline": [asdict(s) for s in self.outline],
                "material": self.material,
            }
            self._meta_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("PaperProject 状态保存失败: %s", e)

    def finalize(self) -> Path:
        """渲染最终草稿到 workspace/paper.md 并返回路径"""
        draft = self.read_draft()
        try:
            self._draft_path.write_text(draft + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning("paper.md 写入失败: %s", e)
        self.save()
        return self._draft_path

    def export_latex(self, out_path=None, title: str = "") -> Path:
        """把当前草稿导出为 LaTeX 文件（默认 workspace/paper.tex），返回路径。"""
        from sage.paper_export import to_latex
        latex = to_latex(self.read_draft(), title=title or self._draft_path.stem)
        out = Path(out_path) if out_path else self.workspace / "paper.tex"
        out.write_text(latex, encoding="utf-8")
        return out

    def clear(self):
        self.outline = []
        self.material = {}

    def load(self) -> bool:
        """从磁盘加载上次草稿状态（跨会话持久草稿）。

        优先读结构化状态 .sage/paper_project.json；缺失或损坏时回退到解析
        paper.md（把带 `## 标题` 的正文按章节拆分）。

        Returns:
            True 表示成功加载到历史草稿，False 表示无历史可加载。
        """
        if self._meta_path.exists():
            try:
                payload = json.loads(self._meta_path.read_text(encoding="utf-8"))
                outline_raw = payload.get("outline") or []
                self.outline = [
                    PaperSection(
                        key=str(s.get("key", "")),
                        title=str(s.get("title", "")),
                        target_words=int(s.get("target_words", 0) or 0),
                        content=str(s.get("content", "") or ""),
                    )
                    for s in outline_raw
                    if isinstance(s, dict) and (s.get("key") or s.get("title"))
                ]
                self.material = dict(payload.get("material") or {})
                if self.outline or self.material:
                    return True
            except Exception as e:
                logger.warning("PaperProject 状态加载失败，尝试从 paper.md 回退: %s", e)

        # 回退：无结构化状态时，从 paper.md 解析正文
        if self._draft_path.exists():
            try:
                md = self._draft_path.read_text(encoding="utf-8")
                if md.strip():
                    self.parse_draft_to_sections(md)
                    return True
            except Exception as e:
                logger.warning("从 paper.md 回退加载失败: %s", e)
        return False
