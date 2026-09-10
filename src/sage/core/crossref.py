"""CrossRef API 共享客户端 — polite pool (mailto) + 全局限速 + 429/5xx 退避重试

集中原先散落在 citation_verify.py（_resolve_doi / _crossref_bibliographic）与
tools/paper_ops.py（_search_crossref_by_title / search_crossref）四处、彼此重复的
CrossRef HTTP 请求逻辑，统一提供三项能力（P1-2）：

  1. polite pool：User-Agent 携带 mailto。CrossRef 对提供联系方式的客户端分配
     "礼貌池"，更快更稳定、更不易被限流。可用环境变量 SAGE_CROSSREF_MAILTO
     覆盖为真实可达邮箱（默认占位 sage@example.com）。
  2. 全局限速：跨所有调用点强制最小请求间隔，避免批量验证参考文献时突发请求触发 429。
  3. 退避重试：429 / 5xx / 网络错误复用 core.resilience.retry_with_backoff 指数退避；
     404 等确定性响应不重试（classify_error 对 "404" 会 fallback 为可重试，故此处
     显式只对 429/5xx 抛异常分流，避免对不存在的 DOI 做无意义重试）。

依赖方向：core.crossref → core.resilience（仅 stdlib + httpx），不反向依赖
tools/ 或 citation_verify，无循环导入风险。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

from sage.core.resilience import RetryConfig, retry_with_backoff

logger = logging.getLogger(__name__)

# CrossRef API 基址
BASE_URL = "https://api.crossref.org"

# polite pool 默认联系方式（占位）。生产环境建议用环境变量替换为真实可达邮箱，
# CrossRef 据此分配"礼貌池"，显著提升稳定性并降低被限流概率。
DEFAULT_MAILTO = "sage@example.com"

# 全局最小请求间隔（秒）——限速，避免突发请求触发 429。
# CrossRef 礼貌池上限约 50 req/s，此处保守取 5 req/s（0.2s 间隔）：
# 批量验证 20 条参考文献仅额外增加约 4s，几乎无感，却能规避限流。置 0 可关闭。
MIN_REQUEST_INTERVAL = 0.2

# 退避重试配置：429/5xx/网络错误最多重试 2 次，基础延迟 1s，指数退避 + jitter。
# 该对象被 retry_with_backoff 装饰器持有引用，测试可就地调小 base_delay 加速。
_RETRY_CFG = RetryConfig(
    max_retries=2, base_delay=1.0, max_delay=8.0,
    backoff_multiplier=2.0, jitter=True,
)

# 测试可注入 httpx.MockTransport；生产为 None（使用默认网络传输）。
_transport: Optional[httpx.AsyncBaseTransport] = None

# 全局限速状态：跨所有调用点共享（模块级单例），用 Lock 串行化并强制最小间隔。
_rate_lock = asyncio.Lock()
_last_request_ts = 0.0


def _user_agent() -> str:
    """构造携带 mailto 的 User-Agent（进入 CrossRef 礼貌池）。

    每次调用读取环境变量，便于运行时 / 测试覆盖 SAGE_CROSSREF_MAILTO。
    """
    mailto = os.environ.get("SAGE_CROSSREF_MAILTO", DEFAULT_MAILTO)
    return f"Sage/1.0 (academic writing assistant; mailto:{mailto})"


async def _throttle() -> None:
    """全局限速：确保相邻 CrossRef 请求间隔 >= MIN_REQUEST_INTERVAL。

    持锁期间 sleep，使并发调用点排队而非同时突发。MIN_REQUEST_INTERVAL<=0 时关闭。
    """
    global _last_request_ts
    if MIN_REQUEST_INTERVAL <= 0:
        return
    async with _rate_lock:
        now = time.monotonic()
        wait = MIN_REQUEST_INTERVAL - (now - _last_request_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_ts = time.monotonic()


@retry_with_backoff(_RETRY_CFG)
async def _fetch(url: str, timeout: float) -> tuple[int, Optional[dict]]:
    """实际发起请求（受 retry_with_backoff 保护：429/5xx/网络错误退避重试）。"""
    await _throttle()
    client_kwargs: dict = {"timeout": timeout}
    if _transport is not None:
        client_kwargs["transport"] = _transport
    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.get(url, headers={"User-Agent": _user_agent()})

    # 仅对 429 / 5xx 抛出，交由 retry_with_backoff 按 classify_error 退避重试。
    # 404 等确定性响应不抛：classify_error("404 ...") 会 fallback 为可重试，
    # 若抛出会对"不存在的 DOI"做无意义重试。
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    if resp.status_code != 200:
        return resp.status_code, None
    try:
        return 200, resp.json()
    except ValueError as e:  # 响应体非法 JSON
        logger.debug("CrossRef 响应 JSON 解析失败 %s: %s", url, e)
        return 200, None


async def crossref_get(path: str, *, timeout: float = 15.0) -> tuple[int, Optional[dict]]:
    """GET 一个 CrossRef 端点，带 polite-pool mailto + 限速 + 429/5xx 退避重试。

    Args:
        path: base 之后的路径，如 "works/10.1000/xyz" 或
              "works?query.bibliographic=...&rows=5"；也接受完整 http(s) URL。
        timeout: 单次请求超时（秒）。

    Returns:
        (status_code, data):
          - status_code: HTTP 状态码（200/404/...）；网络错误 / 重试耗尽时为 0。
          - data: status_code==200 时为解析后的 JSON dict，否则为 None。
        调用方据此保留各自的 200/404/其它 语义（verified / not_found / network_error）。
    """
    url = path if path.startswith("http") else f"{BASE_URL}/{path.lstrip('/')}"
    try:
        return await _fetch(url, timeout)
    except Exception as e:  # RetryExhaustedError 或未被识别的网络异常
        logger.debug("CrossRef 请求失败（已退避重试）%s: %s", url, e)
        return 0, None
