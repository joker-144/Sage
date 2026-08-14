"""
PaperQuality — 论文成稿的确定性质量门

与审校核查员（LLM 判断）互补，这里做**不依赖 LLM 的硬校验**：
  - 大纲章节完整性（必写章节是否为空）
  - 引用标记残留（[CITE: ...] 是否全部被处理）
  - 参考文献存在性
  - 字数预算（各节是否显著短于目标）

所有检查都是纯规则，可离线单元测试；产出 QualityReport 供编排器在成稿后
反馈给用户，或作为"审校→修订二次复核"的触发依据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# [CITE: 关键词] 残留标记（引用管理员未处理干净）
_CITE_MARKER_RE = re.compile(r"\[CITE:\s*[^\]]*\]")

# 字数预算阈值：低于目标 x 倍视为"过短"
_TOO_SHORT_RATIO = 0.5


@dataclass
class QualityIssue:
    """一条质量门问题"""
    code: str               # section_missing | cite_marker_left | references_missing | section_too_short
    message: str
    section: str = ""       # 相关章节 key/title


@dataclass
class QualityReport:
    """质量门检查结果"""
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def count(self) -> int:
        return len(self.issues)

    def to_text(self) -> str:
        if self.ok:
            return "质量门检查：通过（未发现待改进项）"
        lines = [f"质量门检查：发现 {self.count} 项待改进"]
        for issue in self.issues:
            loc = f"（{issue.section}）" if issue.section else ""
            lines.append(f"- [{issue.code}] {issue.message}{loc}")
        return "\n".join(lines)


def check_section_completeness(outline: list) -> list[QualityIssue]:
    """检查必写章节是否缺失/为空。"""
    issues = []
    for sec in outline:
        content = getattr(sec, "content", "") or ""
        target = getattr(sec, "target_words", 0) or 0
        # 有字数预算（必写）但内容为空 → 缺失
        if target > 0 and not content.strip():
            issues.append(QualityIssue(
                code="section_missing",
                message="必写章节尚未撰写",
                section=getattr(sec, "title", "") or getattr(sec, "key", ""),
            ))
    return issues


def check_citation_markers(draft: str) -> list[QualityIssue]:
    """检查正文中是否残留未处理的 [CITE: ...] 标记。"""
    markers = _CITE_MARKER_RE.findall(draft or "")
    if not markers:
        return []
    return [QualityIssue(
        code="cite_marker_left",
        message=f"存在 {len(markers)} 处未处理的引用标记，如 {markers[0]}",
    )]


def check_references(draft: str, references_expected: bool = True) -> list[QualityIssue]:
    """检查参考文献章节是否存在且有内容。"""
    if not references_expected:
        return []
    text = draft or ""
    # 仅匹配真正的标题行（如 "## 参考文献" / "## References"），避免
    # "没有参考文献" 这类正文措辞被误判为有标题
    heading_re = re.compile(r"(?im)^#+\s*(参考文献|references?)\s*$")
    m = heading_re.search(text)
    if not m:
        return [QualityIssue(code="references_missing", message="缺少参考文献章节")]
    # 标题之后是否有实质内容
    if not text[m.end():].strip():
        return [QualityIssue(code="references_missing", message="参考文献章节为空")]
    return []


def check_word_budget(outline: list) -> list[QualityIssue]:
    """检查各节字数是否显著低于目标预算。"""
    issues = []
    for sec in outline:
        target = getattr(sec, "target_words", 0) or 0
        if target <= 0:
            continue
        actual = len(getattr(sec, "content", "") or "")
        if actual < target * _TOO_SHORT_RATIO:
            issues.append(QualityIssue(
                code="section_too_short",
                message=f"章节字数 {actual} 显著低于目标 {target}",
                section=getattr(sec, "title", "") or getattr(sec, "key", ""),
            ))
    return issues


def run_quality_checks(project, references_expected: bool = True) -> QualityReport:
    """对 PaperProject 运行全部确定性检查。

    Args:
        project: sage.paper_project.PaperProject 实例
        references_expected: 是否要求参考文献（默认 True，适用于完整论文）
    """
    draft = project.read_draft() if project is not None else ""
    outline = project.get_outline() if project is not None else []
    issues = []
    issues += check_section_completeness(outline)
    issues += check_citation_markers(draft)
    issues += check_references(draft, references_expected=references_expected)
    issues += check_word_budget(outline)
    return QualityReport(issues=issues)
