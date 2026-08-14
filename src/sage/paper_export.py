"""
PaperExport — 论文成稿导出（LaTeX / Word）

提供基础 markdown → LaTeX / Word 转换：
  - to_latex: 纯文本转换，产出可编译的 article + ctex 文档
  - to_docx: 需要 python-docx（可选依赖 [paper]）

导出的 markdown 通常来自 PaperProject.read_draft()（最终 paper.md）。
"""
from __future__ import annotations

import re
from pathlib import Path

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_CITE_RE = re.compile(r"\[CITE:\s*([^\]]*)\]")


def _inline(md: str) -> str:
    """行内元素转换：加粗/斜体/引用标记。"""
    md = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", md)
    md = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\\textit{\1}", md)
    md = _CITE_RE.sub(r"\\cite{\1}", md)
    return md


def _escape_plain(text: str) -> str:
    """转义 LaTeX 特殊字符（用于标题等纯文本，不用于已含命令的文本）。"""
    for ch in ("\\", "{", "}", "&", "%", "$", "#", "_", "~", "^"):
        text = text.replace(ch, "\\" + ch)
    return text


def _iter_blocks(draft: str):
    """把 markdown 拆为 (kind, text) 块。kind: h1/h2/h3/para/bullet"""
    blocks = []
    for raw in (draft or "").split("\n"):
        line = raw.rstrip()
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            kind = "h1" if level == 1 else ("h2" if level == 2 else "h3")
            blocks.append((kind, m.group(2).strip()))
        elif line.strip().startswith("- "):
            blocks.append(("bullet", line.strip()[2:]))
        elif line.strip():
            blocks.append(("para", line.strip()))
    return blocks


def to_latex(draft: str, title: str = "") -> str:
    """把 markdown 草稿转换为可编译的 LaTeX 文档（article + ctex 中文支持）。"""
    body = []
    in_list = False
    for raw in (draft or "").split("\n"):
        line = raw.rstrip()
        m = _HEADING_RE.match(line)
        if m:
            if in_list:
                body.append("\\end{itemize}")
                in_list = False
            level = len(m.group(1))
            heading = _inline(m.group(2).strip())
            cmd = {1: "\\section", 2: "\\subsection"}.get(level, "\\subsubsection")
            body.append(f"{cmd}{{{heading}}}")
            continue
        if line.strip().startswith("- "):
            if not in_list:
                body.append("\\begin{itemize}")
                in_list = True
            body.append("  \\item " + _inline(line.strip()[2:]))
            continue
        if not line.strip():
            if in_list:
                body.append("\\end{itemize}")
                in_list = False
            continue
        if in_list:
            body.append("\\end{itemize}")
            in_list = False
        body.append(_inline(line))
    if in_list:
        body.append("\\end{itemize}")

    content = "\n".join(body)
    doc_title = _escape_plain(title or "论文")
    return (
        "\\documentclass{article}\n"
        "\\usepackage[UTF8]{ctex}\n"
        f"\\title{{{doc_title}}}\n"
        "\\author{}\n"
        "\\begin{document}\n"
        "\\maketitle\n\n"
        f"{content}\n\n"
        "\\end{document}\n"
    )


def to_docx(draft: str, out_path) -> Path:
    """把 markdown 草稿导出为 Word 文档（需要 python-docx 可选依赖）。"""
    try:
        from docx import Document
    except ImportError as e:
        raise RuntimeError(
            "Word 导出需要 python-docx，请安装: pip install 'sage-paper[paper]'"
        ) from e
    doc = Document()
    for kind, text in _iter_blocks(draft):
        if kind == "h1":
            doc.add_heading(text, level=1)
        elif kind == "h2":
            doc.add_heading(text, level=2)
        elif kind == "h3":
            doc.add_heading(text, level=3)
        elif kind == "bullet":
            doc.add_paragraph(text, style="List Bullet")
        else:
            doc.add_paragraph(text)
    out = Path(out_path)
    doc.save(str(out))
    return out
