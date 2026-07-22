"""工具类型定义 — ToolResult 等共享类型"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """工具操作统一返回格式

    字段约定:
      - success: 是否成功
      - output: 面向 LLM 的可读文本（人类可读的格式化结果，字符串）
      - data: 结构化数据（供 API/前端使用的 dict/list，可为字符串）
      - error: 失败时的错误信息
      - metadata: 额外元数据（任意结构）
    """
    success: bool
    output: str = ""
    data: Any = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """供 AgentLoop 展示的摘要"""
        if self.success:
            # 优先使用 output（可读文本），其次 data（若是字符串）
            if self.output:
                text = self.output
            elif isinstance(self.data, str):
                text = self.data
            else:
                text = str(self.data) if self.data else ""
            return text[:200] + "..." if len(text) > 200 else text
        return f"错误: {self.error}"
