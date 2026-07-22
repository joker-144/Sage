"""
文件操作工具 — read_file, write_file, edit_file (diff), list_dir

所有路径自动限制在 workspace 内，防止目录穿越。
edit_file 是核心新增能力：通过搜索-替换实现精准 diff 编辑，而非整文件重写。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sage.tools.types import ToolResult


class FileOps:
    """文件操作工具 — 所有路径限制在 workspace 内"""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def _resolve(self, path: str) -> Path:
        """安全解析路径，防止目录穿越"""
        clean = Path(path).as_posix().lstrip("/")
        resolved = (self.workspace / clean).resolve()
        # 使用 relative_to 做更可靠的边界检查（兼容 Windows 大小写差异）
        try:
            resolved.relative_to(self.workspace)
        except ValueError:
            raise ValueError(f"路径越界: {path}")
        return resolved

    async def read_file(self, path: str, start_line: int = 0, end_line: int = 0) -> ToolResult:
        """读取文件完整内容。默认读取整个文件，无需传 start_line/end_line。仅在文件超过 2000 行时允许分段读取。"""
        try:
            target = self._resolve(path)
            if not target.exists():
                return ToolResult(success=False, error=f"文件不存在: {path}")
            content = target.read_text(encoding="utf-8")
            if start_line > 0 or end_line > 0:
                lines = content.splitlines()
                start = (start_line - 1) if start_line > 0 else 0
                end = end_line if end_line > 0 else len(lines)
                # 带行号输出
                numbered = []
                for i in range(start, min(end, len(lines))):
                    numbered.append(f"{i + 1}→{lines[i]}")
                content = "\n".join(numbered)
            return ToolResult(success=True, data=content)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def write_file(self, path: str, content: str) -> ToolResult:
        """创建或覆写文件（自动创建父目录）"""
        try:
            target = self._resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolResult(success=True, data=f"已写入: {path} ({len(content)} 字符)")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def edit_file(self, path: str, old_str: str, new_str: str) -> ToolResult:
        """diff 编辑 — 搜索替换

        old_str 必须是文件中唯一匹配的文本片段。
        若匹配多处，返回错误要求提供更多上下文。
        """
        try:
            target = self._resolve(path)
            if not target.exists():
                return ToolResult(success=False, error=f"文件不存在: {path}")
            content = target.read_text(encoding="utf-8")

            if old_str not in content:
                return ToolResult(success=False, error="old_str 未在文件中找到匹配")
            if content.count(old_str) > 1:
                return ToolResult(
                    success=False,
                    error="old_str 匹配多处，请提供更多上下文以唯一匹配",
                )

            new_content = content.replace(old_str, new_str, 1)
            target.write_text(new_content, encoding="utf-8")
            return ToolResult(success=True, data=f"已编辑: {path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def list_dir(self, path: str = ".") -> ToolResult:
        """列出目录内容"""
        try:
            target = self._resolve(path)
            if not target.is_dir():
                return ToolResult(success=False, error=f"不是目录: {path}")

            items = []
            for item in sorted(target.iterdir()):
                prefix = "[DIR] " if item.is_dir() else "[FILE]"
                items.append(f"{prefix} {item.name}")

            return ToolResult(success=True, data="\n".join(items))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
