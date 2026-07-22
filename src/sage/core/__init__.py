"""
核心基础设施层 — 弹性、可观测、协议标准化

参考 2026 六层架构体系第四层（工具与集成层）和第六层（运维与治理层）
"""

from sage.core.resilience import (
    CircuitBreaker,
    ErrorSeverity,
    classify_error,
    get_resilience_tracker,
    retry_with_backoff,
)
from sage.core.observability import (
    Observability,
    SessionMetrics,
    ToolCallRecord,
    TraceSpan,
    get_observability,
)
from sage.core.mcp import (
    LocalToolBridge,
    MCPRegistry,
    MCPResource,
    MCPToolSchema,
    get_mcp_registry,
)

__all__ = [
    # resilience
    "CircuitBreaker",
    "ErrorSeverity",
    "classify_error",
    "get_resilience_tracker",
    "retry_with_backoff",
    # observability
    "Observability",
    "SessionMetrics",
    "ToolCallRecord",
    "TraceSpan",
    "get_observability",
    # mcp
    "LocalToolBridge",
    "MCPRegistry",
    "MCPResource",
    "MCPToolSchema",
    "get_mcp_registry",
]
