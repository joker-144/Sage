"""
弹性重试与错误处理 — Retry & Resilience

参考生产级系统设计，提供:
- 指数退避重试
- 断路器模式
- 超时控制
- 错误分类与降级策略
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class ErrorSeverity(Enum):
    """错误严重级别"""
    TRANSIENT = "transient"    # 瞬时错误（网络超时、速率限制），可重试
    RETRYABLE = "retryable"    # 可重试错误（服务暂时不可用）
    PERMANENT = "permanent"    # 永久错误（参数错误、权限不足），不重试
    FATAL = "fatal"            # 致命错误（API Key 无效），立即终止


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3
    base_delay: float = 1.0       # 基础延迟（秒）
    max_delay: float = 30.0       # 最大延迟（秒）
    backoff_multiplier: float = 2.0  # 退避倍数
    jitter: bool = True           # 是否添加随机抖动


@dataclass
class CircuitBreakerState:
    """断路器状态"""
    failure_count: int = 0
    last_failure_time: float = 0.0
    state: str = "closed"  # closed | open | half_open
    failure_threshold: int = 5
    recovery_timeout: float = 60.0  # 恢复超时（秒）


class CircuitBreaker:
    """断路器 — 防止级联失败

    状态机:
      closed → (failures >= threshold) → open
      open → (timeout elapsed) → half_open
      half_open → (success) → closed | (failure) → open
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.state = CircuitBreakerState(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )

    @property
    def is_open(self) -> bool:
        """断路器是否开启（拒绝请求）"""
        if self.state.state == "closed":
            return False
        if self.state.state == "half_open":
            return False
        # open 状态 → 检查是否可以进入 half_open
        elapsed = time.time() - self.state.last_failure_time
        if elapsed >= self.state.recovery_timeout:
            self.state.state = "half_open"
            return False
        return True

    def record_success(self):
        """记录成功"""
        if self.state.state == "half_open":
            self.state.state = "closed"
        self.state.failure_count = 0

    def record_failure(self):
        """记录失败"""
        self.state.failure_count += 1
        self.state.last_failure_time = time.time()
        if self.state.failure_count >= self.state.failure_threshold:
            self.state.state = "open"

    def reset(self):
        """重置断路器"""
        self.state.failure_count = 0
        self.state.state = "closed"


class RetryExhaustedError(Exception):
    """重试耗尽异常"""
    pass


class CircuitBreakerOpenError(Exception):
    """断路器开启异常"""
    pass


def classify_error(error: Exception) -> ErrorSeverity:
    """分类错误严重级别"""
    error_str = str(error).lower()

    # 速率限制
    if any(kw in error_str for kw in ("rate limit", "too many requests", "429")):
        return ErrorSeverity.TRANSIENT

    # 网络/超时错误
    if any(kw in error_str for kw in ("timeout", "connection", "network", "timed out")):
        return ErrorSeverity.RETRYABLE

    # 服务暂时不可用
    if any(kw in error_str for kw in ("503", "502", "unavailable", "overloaded")):
        return ErrorSeverity.RETRYABLE

    # 认证/权限
    if any(kw in error_str for kw in ("401", "403", "unauthorized", "invalid api key", "authentication")):
        return ErrorSeverity.FATAL

    # 参数错误
    if any(kw in error_str for kw in ("400", "invalid", "参数")):
        return ErrorSeverity.PERMANENT

    return ErrorSeverity.RETRYABLE  # 默认可重试


def retry_with_backoff(
    config: Optional[RetryConfig] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
):
    """异步重试装饰器（指数退避 + 断路器）

    用法:
        @retry_with_backoff(RetryConfig(max_retries=3))
        async def my_func():
            ...

    ⚠️ 必须带括号调用；误写为 @retry_with_backoff（不带括号）会抛 TypeError。
    """
    if callable(config):
        raise TypeError(
            "retry_with_backoff 必须带括号调用，例如 @retry_with_backoff()\n"
            "请改为 @retry_with_backoff(RetryConfig(max_retries=3))"
        )
    cfg = config or RetryConfig()

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None

            for attempt in range(cfg.max_retries + 1):
                # 断路器检查
                if circuit_breaker and circuit_breaker.is_open:
                    raise CircuitBreakerOpenError(
                        f"断路器开启，拒绝 {func.__name__} 调用"
                    )

                try:
                    result = await func(*args, **kwargs)
                    if circuit_breaker:
                        circuit_breaker.record_success()
                    return result
                except Exception as e:
                    last_error = e
                    severity = classify_error(e)

                    if circuit_breaker:
                        circuit_breaker.record_failure()

                    # 最后一次尝试 → 抛出
                    if attempt >= cfg.max_retries:
                        raise RetryExhaustedError(
                            f"{func.__name__} 重试 {cfg.max_retries} 次后仍失败: {e}"
                        ) from e

                    # 永久/致命错误 → 不重试
                    if severity in (ErrorSeverity.PERMANENT, ErrorSeverity.FATAL):
                        raise

                    # 计算延迟
                    delay = min(
                        cfg.base_delay * (cfg.backoff_multiplier ** attempt),
                        cfg.max_delay,
                    )
                    if cfg.jitter:
                        delay *= (0.5 + random.random())

                    if on_retry:
                        on_retry(attempt + 1, e)

                    await asyncio.sleep(delay)

            raise last_error  # pygame: should never reach

        return wrapper  # type: ignore[return-value]
    return decorator


def retry_sync(
    func: Callable,
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs,
) -> Any:
    """同步重试函数（简单版，用于非异步场景）

    Args:
        func: 要重试的函数
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）

    Returns:
        函数结果
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            severity = classify_error(e)
            if attempt >= max_retries or severity in (ErrorSeverity.PERMANENT, ErrorSeverity.FATAL):
                raise
            time.sleep(base_delay * (2 ** attempt))

    raise last_error  # type: ignore


@dataclass
class OperationMetrics:
    """操作指标"""
    operation: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    total_time_ms: float = 0.0
    circuit_breaker_trips: int = 0


class ResilienceTracker:
    """韧性追踪器 — 记录各操作的重试和故障统计"""

    def __init__(self):
        self._metrics: dict[str, OperationMetrics] = {}

    def record(self, operation: str, success: bool, time_ms: float = 0.0):
        """记录一次操作"""
        if operation not in self._metrics:
            self._metrics[operation] = OperationMetrics(operation=operation)
        m = self._metrics[operation]
        m.attempts += 1
        if success:
            m.successes += 1
        else:
            m.failures += 1
        m.total_time_ms += time_ms

    def get_stats(self) -> dict[str, dict]:
        """获取统计信息"""
        return {
            op: {
                "attempts": m.attempts,
                "successes": m.successes,
                "failures": m.failures,
                "success_rate": f"{(m.successes / m.attempts * 100):.1f}%" if m.attempts > 0 else "N/A",
                "avg_time_ms": f"{m.total_time_ms / m.attempts:.0f}" if m.attempts > 0 else "N/A",
                "circuit_breaker_trips": m.circuit_breaker_trips,
            }
            for op, m in self._metrics.items()
        }


# 全局弹性追踪器
_global_tracker = ResilienceTracker()


def get_resilience_tracker() -> ResilienceTracker:
    return _global_tracker
