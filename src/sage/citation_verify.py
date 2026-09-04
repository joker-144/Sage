"""
CitationVerify — 参考文献真实性硬校验（不依赖 LLM 的确定性步骤）

编造引用是学术写作系统最致命的失信点。本模块对成稿的参考文献章节逐条
执行真实性验证：

  1. 条目含 DOI → 直接请求 CrossRef /works/{doi} 解析（404 = 条目不存在）
  2. 无 DOI → 取条目文本（启发式提取标题或整条书目信息）查询 CrossRef
     bibliographic 检索，用标题相似度判定匹配强度
  3. CrossRef 未命中且条目含中文 → 回退维普/万方认证链（复用
     PaperOps._verify_metadata_online，中文文献 CrossRef 覆盖有限）

产出 ReferenceVerifyReport（含逐条状态与汇总），供：
  - 编排器成稿后自动执行并把报告存入 PaperProject（见 orchestrator）
  - 审阅工作台按需重新验证（见 api.py 的 /api/review/verify-references）

状态语义:
  verified      — 外部数据库命中该条目（DOI 解析成功或高相似度匹配）
  suspicious    — 低相似度匹配，可能是转述/格式差异，也可能不是同一篇文献
  not_found     — 各数据源均未找到
  network_error — 网络失败（不作为"编造"依据）
  unverified    — 条目过短/无法提取查询文本，跳过验证
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 参考文献章节标题（与 paper_quality.check_references 的匹配规则保持一致）
_REFERENCE_HEADING_RE = re.compile(r"(?im)^#+\s*(参考文献|references?)\s*$")

# 条目起始行：[1] / 1. / 1、/ 1) / - （纯列表项）
_ENTRY_START_RE = re.compile(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.、)]|-)\s+")

# DOI（CrossRef 风格），末尾截掉常见标点
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)

# GB/T 7714 文献类型标记：作者. 标题[J]. 期刊, 年份
_GBT_TITLE_RE = re.compile(r"[.．]\s*(.+?)\[[JMCDSRP]\]", re.IGNORECASE)

# "标题 (2019). 期刊" 风格 — 取括号年份之前的文本
_TITLE_BEFORE_YEAR_RE = re.compile(r"(.{4,}?)\s*\(\d{4}[a-z]?\)")

# APA 风格："Author (2019). Title. Journal" — 取括号年份之后、句号之前的文本
_APA_TITLE_RE = re.compile(r"\(\d{4}[a-z]?\)[.．]\s*(.+?)[.．]")

# 引号包裹的标题（中英文引号）
_QUOTED_TITLE_RE = re.compile(r"[“\"'](.+?)[”\"']")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 标题相似度阈值：>= VERIFIED 视为命中；>= SUSPICIOUS 视为存疑
_SIMILARITY_VERIFIED = 0.6
_SIMILARITY_SUSPICIOUS = 0.35

# 条目最短长度：过短的行（如孤立年份/页码）跳过验证
_MIN_ENTRY_LEN = 12


@dataclass
class ReferenceEntryResult:
    """一条参考文献的验证结果"""
    index: int                      # 在参考文献列表中的序号（1 起）
    raw: str                        # 原始条目文本（截断存储）
    doi: str = ""                   # 条目中提取到的 DOI
    status: str = "unverified"      # verified | suspicious | not_found | network_error | unverified
    matched_title: str = ""         # 命中的外部文献标题
    matched_doi: str = ""           # 命中的外部文献 DOI
    score: float = 0.0              # 标题相似度（0~1）
    source: str = ""                # 验证来源：CrossRef | DOI | 维普 | 万方
    message: str = ""               # 补充说明


@dataclass
class ReferenceVerifyReport:
    """参考文献验证报告"""
    entries: list[ReferenceEntryResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def verified_count(self) -> int:
        return sum(1 for e in self.entries if e.status == "verified")

    @property
    def suspicious_count(self) -> int:
        return sum(1 for e in self.entries if e.status == "suspicious")

    @property
    def not_found_count(self) -> int:
        return sum(1 for e in self.entries if e.status == "not_found")

    @property
    def network_error_count(self) -> int:
        return sum(1 for e in self.entries if e.status == "network_error")

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "verified": self.verified_count,
            "suspicious": self.suspicious_count,
            "not_found": self.not_found_count,
            "network_error": self.network_error_count,
            "entries": [asdict(e) for e in self.entries],
        }

    def to_text(self) -> str:
        if not self.total:
            return "引用验证：参考文献章节为空，无可验证条目"
        lines = [
            f"引用真实性验证：共 {self.total} 条，"
            f"已验证 {self.verified_count}，存疑 {self.suspicious_count}，"
            f"未找到 {self.not_found_count}"
            + (f"，网络失败 {self.network_error_count}" if self.network_error_count else "")
        ]
        for e in self.entries:
            if e.status == "verified":
                continue
            snippet = e.raw[:60] + ("..." if len(e.raw) > 60 else "")
            lines.append(f"- [{e.status}] [{e.index}] {snippet}")
            if e.message:
                lines.append(f"  {e.message}")
        return "\n".join(lines)


def split_reference_entries(draft: str) -> list[tuple[int, str]]:
    """从成稿中拆出参考文献条目。

    Returns:
        [(序号, 条目文本), ...]，序号取自 [n] 编号（无编号时按出现顺序递增）。
        找不到参考文献章节时返回空列表。
    """
    text = draft or ""
    m = _REFERENCE_HEADING_RE.search(text)
    if not m:
        return []

    # 章节内容到下一个 markdown 标题或文末为止
    rest = text[m.end():]
    next_heading = re.search(r"(?m)^#+\s", rest)
    body = rest[next_heading.start():] if next_heading else rest

    entries: list[tuple[int, str]] = []
    current_no = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        start = _ENTRY_START_RE.match(stripped)
        if start:
            explicit = start.group(1) or start.group(2)
            if explicit:
                current_no = int(explicit)
            else:
                current_no += 1
            entry_text = stripped[start.end():].strip()
            entries.append((current_no, entry_text))
        elif entries:
            # 续行：并入上一条（GB/T 长条目常折行）
            prev_no, prev_text = entries[-1]
            entries[-1] = (prev_no, f"{prev_text} {stripped}")
    # 过滤孤立短行（如被拆开的空白占位）
    return [(no, t) for no, t in entries if t]


def extract_doi(text: str) -> str:
    """提取条目中的 DOI（去掉尾部标点）"""
    m = _DOI_RE.search(text or "")
    if not m:
        return ""
    return m.group(0).rstrip(".,;，。；)")


def guess_title(text: str) -> str:
    """启发式提取条目中的标题（用于展示与相似度比对）。

    依次尝试 GB/T 7714 标记、APA 括号年份、引号包裹；均失败时返回
    去掉编号/DOI 的整条文本（CrossRef 的 bibliographic 查询支持整条书目串）。
    """
    text = (text or "").strip()
    # GB/T 类型标记最明确，直接采信；其余风格同时尝试，取最长的候选
    m = _GBT_TITLE_RE.search(text)
    if m and len(m.group(1).strip()) >= 4:
        return m.group(1).strip()
    candidates = [
        pat.search(text).group(1).strip()
        for pat in (_TITLE_BEFORE_YEAR_RE, _APA_TITLE_RE, _QUOTED_TITLE_RE)
        if pat.search(text) and len(pat.search(text).group(1).strip()) >= 4
    ]
    if candidates:
        return max(candidates, key=len)
    cleaned = extract_doi(text)
    if cleaned:
        text = text.replace(cleaned, " ").strip()
    return text


def _title_similarity(a: str, b: str) -> float:
    """标题相似度（0~1）— 与 PaperOps._title_similarity 同算法，避免循环导入时复制于此"""
    if not a or not b:
        return 0.0
    from difflib import SequenceMatcher
    a_lower, b_lower = a.lower().strip(), b.lower().strip()
    if a_lower == b_lower:
        return 1.0
    ratio = SequenceMatcher(None, a_lower, b_lower).ratio()
    a_chars = set(a_lower.replace(" ", ""))
    b_chars = set(b_lower.replace(" ", ""))
    overlap = len(a_chars & b_chars) / max(len(a_chars), len(b_chars)) if a_chars and b_chars else 0.0
    return max(ratio, overlap)


async def _resolve_doi(doi: str, timeout: float) -> tuple[str, str, float]:
    """通过 CrossRef 解析 DOI。

    Returns:
        (status, matched_title, score)：status 为 verified / not_found / network_error
    """
    import httpx
    url = f"https://api.crossref.org/works/{doi}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url, headers={"User-Agent": "Sage/1.0 (mailto:sage@example.com)"}
            )
        if resp.status_code == 200:
            titles = resp.json().get("message", {}).get("title", [])
            return "verified", (titles[0] if titles else ""), 1.0
        if resp.status_code == 404:
            return "not_found", "", 0.0
        return "network_error", "", 0.0
    except Exception as e:
        logger.debug("DOI 解析失败 %s: %s", doi, e)
        return "network_error", "", 0.0


async def _crossref_bibliographic(query: str, timeout: float) -> list[dict]:
    """CrossRef bibliographic 检索，返回候选 [{title, doi}]"""
    import httpx
    import urllib.parse
    url = f"https://api.crossref.org/works?query.bibliographic={urllib.parse.quote(query)}&rows=5"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url, headers={"User-Agent": "Sage/1.0 (mailto:sage@example.com)"}
            )
            if resp.status_code != 200:
                return []
            items = resp.json().get("message", {}).get("items", [])
        candidates = []
        for item in items:
            titles = item.get("title") or []
            if titles:
                candidates.append({"title": titles[0], "doi": item.get("DOI", "")})
        return candidates
    except Exception as e:
        logger.debug("CrossRef bibliographic 检索失败: %s", e)
        return []


async def _verify_chinese_db(title: str, workspace) -> tuple[str, str]:
    """中文文献回退认证链（维普 → 万方 → CrossRef），复用 PaperOps。

    Returns:
        (source, matched_title)；未命中返回 ("", "")
    """
    try:
        from pathlib import Path
        from sage.tools.paper_ops import PaperOps
        ops = PaperOps(Path(workspace) if workspace else Path("."))
        metadata, source = await asyncio.wait_for(
            ops._verify_metadata_online(title), timeout=30.0
        )
        if metadata:
            return source, metadata.get("title", "")
    except Exception as e:
        logger.debug("中文数据库认证失败: %s", e)
    return "", ""


async def verify_reference_entry(
    entry_text: str,
    workspace=None,
    timeout: float = 15.0,
    use_chinese_dbs: bool = True,
) -> ReferenceEntryResult:
    """验证单条参考文献（见模块 docstring 的状态语义）"""
    result = ReferenceEntryResult(index=0, raw=(entry_text or "")[:200])
    if len((entry_text or "").strip()) < _MIN_ENTRY_LEN:
        result.message = "条目过短，跳过验证"
        return result

    # 1) DOI 直接解析
    doi = extract_doi(entry_text)
    result.doi = doi
    if doi:
        status, title, _ = await _resolve_doi(doi, timeout)
        if status == "verified":
            result.status = "verified"
            result.source = "DOI"
            result.matched_title = title
            result.matched_doi = doi
            return result
        if status == "not_found":
            result.status = "not_found"
            result.message = f"DOI {doi} 在 CrossRef 不存在"
            return result
        # network_error → 继续尝试标题检索兜底

    # 2) 标题/书目串检索
    query = guess_title(entry_text)
    if not query:
        result.message = "无法提取查询文本"
        return result
    candidates = await _crossref_bibliographic(query, timeout)
    best_score, best = 0.0, None
    for cand in candidates:
        score = _title_similarity(query, cand["title"])
        if score > best_score:
            best_score, best = score, cand
    if best_score >= _SIMILARITY_VERIFIED and best:
        result.status = "verified"
        result.source = "CrossRef"
        result.matched_title = best["title"]
        result.matched_doi = best.get("doi", "")
        result.score = round(best_score, 3)
        return result

    # 3) 中文文献回退认证链（CrossRef 对中文文献覆盖有限）
    if use_chinese_dbs and _CJK_RE.search(entry_text):
        source, matched_title = await _verify_chinese_db(query, workspace)
        if source:
            result.status = "verified"
            result.source = source
            result.matched_title = matched_title
            result.score = round(
                _title_similarity(query, matched_title) if matched_title else 1.0, 3
            )
            return result

    if best_score >= _SIMILARITY_SUSPICIOUS and best:
        result.status = "suspicious"
        result.source = "CrossRef"
        result.matched_title = best["title"]
        result.matched_doi = best.get("doi", "")
        result.score = round(best_score, 3)
        result.message = f"相似度 {best_score:.2f} 偏低，请人工核对"
    elif candidates or doi:
        result.status = "not_found"
        result.message = "各数据源均未找到该条目"
    else:
        result.status = "network_error"
        result.message = "网络请求失败，无法验证"
    return result


async def verify_references(
    draft: str,
    workspace=None,
    max_entries: int = 60,
    concurrency: int = 3,
    progress: Optional[Callable[..., None]] = None,
) -> ReferenceVerifyReport:
    """验证成稿参考文献的真实性（并发受限，失败不抛异常）

    Args:
        draft: 成稿 markdown 全文
        workspace: 工作空间路径（中文数据库回退链使用）
        max_entries: 最多验证条数（防止异常稿件打爆外部 API）
        concurrency: 并发请求数
        progress: 可选进度回调 fn(message, done, total)
    """
    report = ReferenceVerifyReport()
    entries = split_reference_entries(draft)[:max_entries]
    if not entries:
        return report

    semaphore = asyncio.Semaphore(max(1, concurrency))
    done_count = 0

    async def _verify_one(no: int, text: str) -> ReferenceEntryResult:
        nonlocal done_count
        async with semaphore:
            try:
                res = await verify_reference_entry(text, workspace=workspace)
            except Exception as e:
                logger.warning("参考文献验证异常 [%s]: %s", no, e)
                res = ReferenceEntryResult(index=no, raw=text[:200], status="network_error")
            res.index = no
            done_count += 1
            if progress:
                progress(f"已验证 {done_count}/{len(entries)} 条", done_count, len(entries))
            return res

    report.entries = list(await asyncio.gather(
        *(_verify_one(no, text) for no, text in entries)
    ))
    # 按序号排序输出
    report.entries.sort(key=lambda e: e.index)
    return report
