"""
联网工具集 — Web 搜索 + 网页抓取

提供三种联网能力，AI 可根据场景自行选择:

  1. web_search       — DuckDuckGo 搜索（免费免配置，质量一般）
  2. web_search_pro   — Tavily AI 搜索（专为 AI 设计，质量高，需 API Key）
  3. web_fetch        — 抓取指定 URL 的网页正文

使用策略:
  - 优先使用 web_search（免费快速）
  - 当 DuckDuckGo 结果质量不高或关联性低时，AI 自动切换到 web_search_pro
  - 已知具体 URL 时使用 web_fetch 获取完整内容
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sage.tools.types import ToolResult


class WebSearchTool:
    """DuckDuckGo 搜索工具 — 免费免配置"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    async def web_search(self, query: str, max_results: int = 8) -> ToolResult:
        """使用 DuckDuckGo 进行网络搜索

        Args:
            query: 搜索关键词
            max_results: 最大返回结果数（默认 8）
        """
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return ToolResult(
                success=False,
                error="duckduckgo-search 未安装，请运行: pip install duckduckgo-search",
            )

        try:
            results_raw = DDGS().text(query, max_results=max_results)
        except Exception as e:
            return ToolResult(success=False, error=f"DuckDuckGo 搜索失败: {e}")

        if not results_raw:
            return ToolResult(
                success=True,
                data=f"未找到与 '{query}' 相关的搜索结果。建议尝试 web_search_pro 获取更精准的结果。",
            )

        lines = [f"## DuckDuckGo 搜索: {query}\n"]
        for i, r in enumerate(results_raw, 1):
            title = r.get("title", "")
            url = r.get("href") or r.get("url") or r.get("link", "")
            snippet = r.get("body") or r.get("snippet", "")
            lines.append(f"{i}. **{title}**\n   URL: {url}\n   {snippet}\n")

        return ToolResult(success=True, data="\n".join(lines))


class WebSearchProTool:
    """Tavily AI 搜索工具 — 专为 AI 设计，返回结构化高质量结果"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    async def web_search_pro(self, query: str, max_results: int = 8) -> ToolResult:
        """使用 Tavily AI 进行高质量网络搜索

        适用于 DuckDuckGo 结果质量不高时使用。

        Args:
            query: 搜索关键词
            max_results: 最大返回结果数
        """
        try:
            from sage.config import get_config
            config = get_config()
            api_key = config.tavily_api_key
        except Exception:
            api_key = ""

        if not api_key:
            return ToolResult(
                success=False,
                error="Tavily API Key 未配置。请在 .env 中设置 TAVILY_API_KEY，"
                      "或前往 https://tavily.com 获取免费 API Key（每月 1000 次免费额度）。",
            )

        try:
            import httpx
        except ImportError:
            return ToolResult(success=False, error="httpx 未安装")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                        "include_answer": True,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return ToolResult(success=False, error=f"Tavily API 错误: {e.response.status_code}")
        except Exception as e:
            return ToolResult(success=False, error=f"Tavily 搜索失败: {e}")

        # 解析结果
        lines = [f"## Tavily AI 搜索: {query}\n"]

        # Tavily 返回的 AI 摘要
        answer = data.get("answer")
        if answer:
            lines.append(f"**AI 摘要:** {answer}\n")

        results = data.get("results", [])
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")
            score = r.get("score", 0)
            # 截断过长内容
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{i}. **{title}** (相关度: {score:.2f})\n   URL: {url}\n   {content}\n")

        return ToolResult(success=True, data="\n".join(lines))


class WebFetchTool:
    """网页抓取工具 — 获取指定 URL 的网页正文"""

    # 需要排除的标签
    _REMOVE_TAGS = re.compile(
        r"<(script|style|noscript|iframe|svg|nav|footer|header)[^>]*>.*?</\1>",
        re.DOTALL | re.IGNORECASE,
    )
    # HTML 标签清理
    _TAG_CLEANER = re.compile(r"<[^>]+>")
    # 多余空白
    _WHITESPACE = re.compile(r"\s+")

    def __init__(self, workspace: Path):
        self.workspace = workspace

    async def web_fetch(self, url: str, max_length: int = 8000) -> ToolResult:
        """抓取指定 URL 的网页内容并提取正文

        Args:
            url: 要抓取的网页 URL
            max_length: 返回内容最大字符数（默认 8000）
        """
        try:
            import httpx
        except ImportError:
            return ToolResult(success=False, error="httpx 未安装")

        try:
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Sage/1.0)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
        except httpx.HTTPStatusError as e:
            return ToolResult(
                success=False,
                error=f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
            )
        except Exception as e:
            return ToolResult(success=False, error=f"抓取失败: {e}")

        # 提取页面标题
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        # 简单的 HTML → 文本转换
        # 1. 移除 script/style 等标签
        text = self._REMOVE_TAGS.sub("", html)
        # 2. 提取正文（优先 article/main/body）
        body_match = re.search(
            r"<(article|main|body)[^>]*>(.*?)</\1>",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if body_match:
            text = body_match.group(2)
        # 3. 清理所有 HTML 标签
        text = self._TAG_CLEANER.sub("", text)
        # 4. 解码常见 HTML 实体
        text = (text
                .replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&#39;", "'"))
        # 5. 压缩空白
        text = self._WHITESPACE.sub(" ", text).strip()

        if not text:
            return ToolResult(success=False, error="无法从网页提取正文内容")

        # 截断（不标记"已截断"以避免 LLM 重复抓取同一 URL）
        if len(text) > max_length:
            text = text[:max_length] + f"\n\n(共 {len(text)} 字符，已返回前 {max_length} 字符)"

        result = f"## 网页内容: {title}\nURL: {url}\n\n{text}"
        return ToolResult(success=True, data=result)
