"""
可观测性监控 — Observability & Monitoring

参考 Langfuse/Coval 设计，提供:
- 请求追踪 (Tracing)
- Token 用量统计
- 工具调用耗时
- 错误率统计
- 会话指标

不依赖外部服务，数据存储在本地 SQLite + 结构化日志。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TraceSpan:
    """追踪 span"""
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    name: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    status: str = "ok"  # ok | error
    metadata: dict = field(default_factory=dict)
    observations: list[dict] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "metadata": self.metadata,
        }


@dataclass
class ToolCallRecord:
    """工具调用记录"""
    tool_name: str
    args: dict
    result_summary: str = ""
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""
    timestamp: float = 0.0


@dataclass
class SessionMetrics:
    """会话指标"""
    session_id: str
    request_count: int = 0
    tool_call_count: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_duration_ms: float = 0.0
    error_count: int = 0
    llm_call_count: int = 0
    tool_success_rate: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_duration_ms / max(self.request_count, 1)

    @property
    def avg_tokens_per_request(self) -> int:
        return (self.total_tokens_in + self.total_tokens_out) // max(self.request_count, 1)


class Observability:
    """可观测性中心 — 单例模式（线程安全）

    提供:
    - Span 追踪
    - Token 统计
    - 工具调用统计
    - 会话指标
    """

    _instance: Optional["Observability"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._traces: list[TraceSpan] = []
        self._sessions: dict[str, SessionMetrics] = {}
        self._tool_calls: list[ToolCallRecord] = []
        # 保留最近 1000 条记录防止内存膨胀
        self._MAX_TRACES = 1000
        self._MAX_TOOL_CALLS = 5000
        self._initialized = True

    # ── 追踪 ──

    @contextmanager
    def trace(self, name: str, metadata: Optional[dict] = None):
        """同步追踪上下文管理器"""
        trace_id = str(uuid.uuid4())[:8]
        span = TraceSpan(
            trace_id=trace_id,
            span_id=str(uuid.uuid4())[:8],
            name=name,
            start_time=time.time(),
            metadata=metadata or {},
        )
        try:
            yield span
            span.status = "ok"
        except Exception as e:
            span.status = "error"
            span.metadata["error"] = str(e)
            raise
        finally:
            span.end_time = time.time()
            self._traces.append(span)
            if len(self._traces) > self._MAX_TRACES:
                self._traces = self._traces[-self._MAX_TRACES:]

    @asynccontextmanager
    async def async_trace(self, name: str, metadata: Optional[dict] = None):
        """异步追踪上下文管理器"""
        trace_id = str(uuid.uuid4())[:8]
        span = TraceSpan(
            trace_id=trace_id,
            span_id=str(uuid.uuid4())[:8],
            name=name,
            start_time=time.time(),
            metadata=metadata or {},
        )
        try:
            yield span
            span.status = "ok"
        except Exception as e:
            span.status = "error"
            span.metadata["error"] = str(e)
            raise
        finally:
            span.end_time = time.time()
            self._traces.append(span)
            if len(self._traces) > self._MAX_TRACES:
                self._traces = self._traces[-self._MAX_TRACES:]

    # ── 工具调用记录 ──

    def record_tool_call(
        self,
        tool_name: str,
        args: dict,
        duration_ms: float,
        success: bool,
        result_summary: str = "",
        error: str = "",
    ):
        """记录工具调用"""
        record = ToolCallRecord(
            tool_name=tool_name,
            args=self._truncate_args(args),
            duration_ms=duration_ms,
            success=success,
            result_summary=result_summary[:200],
            error=error,
            timestamp=time.time(),
        )
        self._tool_calls.append(record)
        if len(self._tool_calls) > self._MAX_TOOL_CALLS:
            self._tool_calls = self._tool_calls[-self._MAX_TOOL_CALLS:]

    def _truncate_args(self, args: dict, max_len: int = 300) -> dict:
        """截断过长的参数值"""
        truncated = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > max_len:
                truncated[k] = v[:max_len] + "..."
            else:
                truncated[k] = v
        return truncated

    # ── 会话指标 ──

    def init_session(self, session_id: str):
        """初始化会话指标"""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionMetrics(session_id=session_id)

    def record_llm_call(self, session_id: str, tokens_in: int, tokens_out: int, duration_ms: float):
        """记录 LLM 调用"""
        s = self._sessions.get(session_id)
        if s:
            s.llm_call_count += 1
            s.total_tokens_in += tokens_in
            s.total_tokens_out += tokens_out
            s.total_duration_ms += duration_ms

    def record_request(self, session_id: str, duration_ms: float, error: bool = False):
        """记录用户请求"""
        s = self._sessions.get(session_id)
        if s:
            s.request_count += 1
            s.total_duration_ms += duration_ms
            if error:
                s.error_count += 1

    def record_tool_metrics(self, session_id: str, count: int = 1):
        """记录工具调用次数"""
        s = self._sessions.get(session_id)
        if s:
            s.tool_call_count += count
            total = s.tool_call_count
            errors = len([t for t in self._tool_calls[-100:] if not t.success])
            s.tool_success_rate = ((total - errors) / total * 100) if total > 0 else 100.0

    # ── 统计报告 ──

    def get_session_report(self, session_id: str) -> dict:
        """获取会话报告"""
        s = self._sessions.get(session_id)
        if not s:
            return {"error": "会话不存在"}
        return {
            "session_id": s.session_id,
            "requests": s.request_count,
            "tool_calls": s.tool_call_count,
            "llm_calls": s.llm_call_count,
            "tokens_in": s.total_tokens_in,
            "tokens_out": s.total_tokens_out,
            "total_tokens": s.total_tokens_in + s.total_tokens_out,
            "total_duration_ms": round(s.total_duration_ms, 0),
            "avg_latency_ms": round(s.avg_latency_ms, 1),
            "avg_tokens_per_request": s.avg_tokens_per_request,
            "error_count": s.error_count,
            "tool_success_rate": round(s.tool_success_rate, 1),
        }

    def get_recent_tool_calls(self, limit: int = 20) -> list[dict]:
        """获取最近工具调用"""
        return [
            {
                "tool": r.tool_name,
                "duration_ms": round(r.duration_ms, 1),
                "success": r.success,
                "error": r.error[:100] if r.error else "",
            }
            for r in self._tool_calls[-limit:]
        ]

    def get_recent_traces(self, limit: int = 10) -> list[dict]:
        """获取最近追踪 span"""
        return [t.to_dict() for t in self._traces[-limit:]]

    def get_global_stats(self) -> dict:
        """全局统计"""
        tool_success = sum(1 for t in self._tool_calls if t.success)
        tool_total = max(len(self._tool_calls), 1)
        return {
            "total_traces": len(self._traces),
            "total_tool_calls": len(self._tool_calls),
            "tool_success_rate": round(tool_success / tool_total * 100, 1),
            "active_sessions": len(self._sessions),
        }


def get_observability() -> Observability:
    """获取可观测性单例"""
    return Observability()
