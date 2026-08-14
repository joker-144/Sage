"""
PaperData — 论文数据占位的确定性扫描

配合"数据接入"策略：撰写阶段对实验数据/统计结果/具体数值一律用【数据】占位
（禁止编造），成稿后扫描这些占位，供编排器调用 LLM 生成"数据处理与来源建议"。

本模块只做确定性扫描，不依赖 LLM，可离线单元测试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 数据占位标记
DATA_PLACEHOLDER = "【数据】"

# 占位符前后保留的上下文字符数（用于展示该位置在说什么）
_CONTEXT_CHARS = 60


@dataclass
class DataPlaceholder:
    """一处数据占位"""
    index: int       # 全文中第几处（从 1 开始）
    section: str     # 所在章节标题（由最近的 ## 标题推断）
    context: str     # 占位符前后文（截断）


def find_data_placeholders(draft: str) -> list[DataPlaceholder]:
    """扫描草稿中所有【数据】占位，返回其位置与上下文。

    按 markdown 二级标题切块，占位归属于其上方最近的章节标题。
    """
    results: list[DataPlaceholder] = []
    current_section = "（正文开头）"

    for block in re.split(r"\n(?=#{2,4}\s)", draft or ""):
        lines = block.split("\n", 1)
        first = lines[0] if lines else ""
        heading_match = re.match(r"#{2,4}\s+(.+?)\s*$", first)
        if heading_match:
            current_section = heading_match.group(1).strip()
            body = lines[1] if len(lines) > 1 else ""
        else:
            body = block

        for m in re.finditer(re.escape(DATA_PLACEHOLDER), body):
            start = max(0, m.start() - _CONTEXT_CHARS)
            end = min(len(body), m.end() + _CONTEXT_CHARS)
            context = body[start:end].strip().replace("\n", " ")
            results.append(DataPlaceholder(
                index=len(results) + 1,
                section=current_section,
                context=context,
            ))
    return results


def format_placeholders(placeholders: list[DataPlaceholder]) -> str:
    """把占位列表格式化为文本，供 LLM 生成建议时引用。"""
    if not placeholders:
        return "（无【数据】占位）"
    lines = []
    for p in placeholders:
        lines.append(f"[{p.index}] 章节「{p.section}」：…{p.context}…")
    return "\n".join(lines)
