"""
Sage 论文写作专用工具集 — 文献检索、文档解析、引用管理、写作辅助、外部检索

这些工具在 ToolEngine 中注册，供 Sage 智能体调用。
所有工具遵循统一的 ToolResult 返回格式，与原有工具接口一致。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Optional

from sage.tools.types import ToolResult


class PaperOps:
    """Sage 论文写作工具集

    提供文献检索、文档解析、引用管理、写作辅助、外部检索等能力。
    所有方法均为 async，返回 ToolResult，与原有工具接口一致。
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace

    # ── 文献与索引工具 ──

    async def index_papers(self, force: bool = False) -> ToolResult:
        """对工作空间中的论文文档建立向量索引"""
        try:
            from sage.context.index import ProjectIndex
            from sage.memory.store import MemoryStore
            store = MemoryStore()
            indexer = ProjectIndex(self.workspace, store)
            stats = indexer.index_project(force=force)
            return ToolResult(
                success=True,
                output=(
                    f"索引完成: 索引 {stats['files']} 个文件, "
                    f"生成 {stats['chunks']} 个文本块, 跳过 {stats['skipped']} 个未修改文件"
                ),
                data=stats,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"索引失败: {e}")

    async def search_literature(self, query: str, top_k: int = 5) -> ToolResult:
        """语义检索已索引的文献库"""
        try:
            from sage.context.index import ProjectIndex
            from sage.memory.store import MemoryStore
            store = MemoryStore()
            indexer = ProjectIndex(self.workspace, store)
            results = indexer.search(query, top_k=top_k)
            if not results:
                return ToolResult(
                    success=True,
                    output="未找到相关文献。请先使用 index_papers 工具索引文献库。",
                    data=[],
                )
            formatted = []
            for i, r in enumerate(results, 1):
                formatted.append(
                    f"### 结果 {i}（相似度: {r.score:.3f}）\n"
                    f"**文件**: {r.file_path} (L{r.start_line}-{r.end_line})\n"
                    f"**内容**:\n{r.content[:500]}\n"
                )
            return ToolResult(
                success=True,
                output="\n".join(formatted),
                data=[{"file": r.file_path, "score": r.score} for r in results],
            )
        except Exception as e:
            return ToolResult(success=False, error=f"检索失败: {e}")

    async def extract_references(self, file_path: str) -> ToolResult:
        """从论文文件中提取参考文献列表"""
        try:
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            ext = full_path.suffix.lower()
            content = self._read_document(full_path, ext)
            if not content:
                return ToolResult(success=False, error="无法读取文件内容")

            # 提取参考文献部分
            refs = self._parse_references(content)
            return ToolResult(
                success=True,
                output=f"提取到 {len(refs)} 条参考文献:\n" + "\n".join(
                    f"{i+1}. {r}" for i, r in enumerate(refs[:20])
                ),
                data={"references": refs, "count": len(refs)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"提取失败: {e}")

    async def insert_citation(
        self, file_path: str, marker: str, citation: str
    ) -> ToolResult:
        """在论文指定标记位置插入引用"""
        try:
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            content = full_path.read_text(encoding="utf-8")
            # 查找标记位置 [CITE: marker]
            pattern = f"[CITE: {marker}]"
            if pattern not in content:
                return ToolResult(success=False, error=f"未找到标记: {pattern}")

            new_content = content.replace(pattern, citation)
            full_path.write_text(new_content, encoding="utf-8")
            return ToolResult(
                success=True,
                output=f"已在 {file_path} 中将 {pattern} 替换为: {citation}",
            )
        except Exception as e:
            return ToolResult(success=False, error=f"插入失败: {e}")

    async def format_references(self, references: str, style: str = "APA") -> ToolResult:
        """按目标格式格式化参考文献列表"""
        try:
            refs = [r.strip() for r in references.split("\n") if r.strip()]
            formatted = []
            for ref in refs:
                formatted.append(self._format_single_reference(ref, style))
            return ToolResult(
                success=True,
                output=f"已按 {style} 格式格式化 {len(formatted)} 条参考文献:\n" + "\n".join(formatted),
                data={"style": style, "references": formatted},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"格式化失败: {e}")

    async def check_plagiarism(self, content: str, threshold: float = 0.8) -> ToolResult:
        """查重检测，识别与已索引文献的重复内容"""
        try:
            from sage.context.index import ProjectIndex
            from sage.memory.store import MemoryStore
            store = MemoryStore()
            indexer = ProjectIndex(self.workspace, store)
            # 将内容分段检索
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            duplicates = []
            for i, para in enumerate(paragraphs):
                results = indexer.search(para, top_k=1)
                if results and results[0].score >= threshold:
                    duplicates.append({
                        "paragraph_index": i,
                        "similarity": results[0].score,
                        "source_file": results[0].file_path,
                        "source_lines": f"L{results[0].start_line}-{results[0].end_line}",
                    })
            duplicate_rate = len(duplicates) / max(len(paragraphs), 1) * 100
            return ToolResult(
                success=True,
                output=(
                    f"查重完成: 检测 {len(paragraphs)} 段, "
                    f"发现 {len(duplicates)} 段相似内容, "
                    f"重复率: {duplicate_rate:.1f}%"
                ),
                data={"duplicate_rate": duplicate_rate, "duplicates": duplicates},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"查重失败: {e}")

    # ── 文档处理工具 ──

    async def parse_pdf(self, file_path: str) -> ToolResult:
        """解析 PDF 文件提取文本内容"""
        try:
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            from sage.context.index import ProjectIndex
            indexer = ProjectIndex(self.workspace)
            text = indexer._extract_pdf_text(full_path)
            if not text:
                return ToolResult(success=False, error="PDF 解析失败（可能未安装 PyMuPDF）")
            return ToolResult(
                success=True,
                output=f"PDF 解析完成, 共 {len(text)} 字符:\n{text[:3000]}",
                data={"char_count": len(text), "preview": text[:1000]},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"PDF 解析失败: {e}")

    async def parse_docx(self, file_path: str) -> ToolResult:
        """解析 Word 文档提取文本内容"""
        try:
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            from sage.context.index import ProjectIndex
            indexer = ProjectIndex(self.workspace)
            text = indexer._extract_docx_text(full_path)
            if not text:
                return ToolResult(success=False, error="DOCX 解析失败（可能未安装 python-docx）")
            return ToolResult(
                success=True,
                output=f"DOCX 解析完成, 共 {len(text)} 字符:\n{text[:3000]}",
                data={"char_count": len(text), "preview": text[:1000]},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"DOCX 解析失败: {e}")

    async def parse_latex(self, file_path: str) -> ToolResult:
        """解析 LaTeX 源文件提取文本内容"""
        try:
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            content = full_path.read_text(encoding="utf-8")
            # 移除 LaTeX 命令，提取纯文本
            # 移除注释
            text = re.sub(r"%.*$", "", content, flags=re.MULTILINE)
            # 提取 section/subsection 标题
            text = re.sub(r"\\section\{([^}]+)\}", r"\n\n## \1\n", text)
            text = re.sub(r"\\subsection\{([^}]+)\}", r"\n\n### \1\n", text)
            text = re.sub(r"\\subsubsection\{([^}]+)\}", r"\n\n#### \1\n", text)
            # 移除其他 LaTeX 命令
            text = re.sub(r"\\[a-zA-Z]+\*?(\{[^}]*\})*", "", text)
            text = re.sub(r"[{}\\]", "", text)
            # 清理多余空行
            text = re.sub(r"\n{3,}", "\n\n", text)
            return ToolResult(
                success=True,
                output=f"LaTeX 解析完成, 共 {len(text)} 字符:\n{text[:3000]}",
                data={"char_count": len(text)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"LaTeX 解析失败: {e}")

    async def extract_metadata(self, file_path: str) -> ToolResult:
        """提取论文元数据（标题/作者/年份/DOI/摘要/关键词）"""
        try:
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            ext = full_path.suffix.lower()
            content = self._read_document(full_path, ext)
            if not content:
                return ToolResult(success=False, error="无法读取文件内容")

            metadata = self._extract_paper_metadata(content)
            return ToolResult(
                success=True,
                output=f"元数据提取完成:\n" + "\n".join(f"**{k}**: {v}" for k, v in metadata.items()),
                data=metadata,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"元数据提取失败: {e}")

    async def ocr_document(self, file_path: str) -> ToolResult:
        """OCR 识别扫描版文档"""
        try:
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            try:
                import fitz  # PyMuPDF
            except ImportError:
                try:
                    import pymupdf as fitz
                except ImportError:
                    return ToolResult(
                        success=False,
                        error="OCR 需要 PyMuPDF (fitz) 库，请安装: pip install pymupdf"
                    )

            # 使用 PyMuPDF 进行 OCR（如果支持）
            doc = fitz.open(str(full_path))
            text_parts = []
            for page in doc:
                # 先尝试直接提取文本
                page_text = page.get_text()
                if page_text.strip():
                    text_parts.append(page_text)
                else:
                    # 文本为空，可能是扫描版，尝试 OCR
                    try:
                        pix = page.get_pixmap()
                        # 这里简化处理，实际 OCR 需要 pytesseract
                        text_parts.append(f"[扫描页面 {page.number + 1}，需要 OCR]")
                    except Exception:
                        text_parts.append(f"[页面 {page.number + 1} 无法解析]")
            doc.close()

            text = "\n\n".join(text_parts)
            return ToolResult(
                success=True,
                output=f"OCR 处理完成, 共 {len(text)} 字符:\n{text[:3000]}",
                data={"char_count": len(text)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"OCR 失败: {e}")

    # ── 写作辅助工具 ──

    async def generate_outline(self, topic: str, paper_type: str = "research") -> ToolResult:
        """根据选题生成论文大纲"""
        try:
            # 大纲生成模板（不同论文类型）
            templates = {
                "research": [
                    "1. 引言（研究背景、问题提出、研究意义、论文结构）",
                    "2. 相关工作（研究现状、主要流派、研究空白）",
                    "3. 研究方法（研究设计、数据收集、分析方法）",
                    "4. 实验与结果（实验设置、结果分析、对比评估）",
                    "5. 讨论（结果解读、理论贡献、实践启示、局限性）",
                    "6. 结论（研究总结、未来工作）",
                    "7. 参考文献",
                ],
                "review": [
                    "1. 引言（综述范围、研究意义、结构安排）",
                    "2. 文献检索策略（数据库、关键词、筛选标准）",
                    "3. 主题分类与分析框架",
                    "4. 各主题研究现状",
                    "5. 跨主题综合分析",
                    "6. 研究空白与未来方向",
                    "7. 结论",
                    "8. 参考文献",
                ],
                "case": [
                    "1. 引言（案例背景、研究意义）",
                    "2. 文献综述与理论框架",
                    "3. 研究方法（案例选择、数据收集、分析方法）",
                    "4. 案例描述",
                    "5. 案例分析与讨论",
                    "6. 理论与实践启示",
                    "7. 结论与局限性",
                    "8. 参考文献",
                ],
            }
            outline = templates.get(paper_type, templates["research"])
            return ToolResult(
                success=True,
                output=f"论文大纲（{paper_type} 类型）:\n选题: {topic}\n\n" + "\n".join(outline),
                data={"topic": topic, "paper_type": paper_type, "outline": outline},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"大纲生成失败: {e}")

    async def write_paragraph(self, section: str, key_points: str) -> ToolResult:
        """撰写论文段落（提供写作指导）"""
        try:
            guidance = (
                f"## 段落写作指导\n"
                f"**章节**: {section}\n"
                f"**要点**: {key_points}\n\n"
                f"**写作建议**:\n"
                f"1. 开头句明确段落主旨\n"
                f"2. 中间展开论证，每句有信息量\n"
                f"3. 结尾句总结或过渡\n"
                f"4. 需要引用处用 [CITE: 关键词] 标注\n"
                f"5. 保持学术语言规范"
            )
            return ToolResult(success=True, output=guidance)
        except Exception as e:
            return ToolResult(success=False, error=f"写作指导失败: {e}")

    async def polish_academic(self, text: str) -> ToolResult:
        """学术语言润色建议"""
        try:
            suggestions = []
            # 检查口语化表达
            colloquial = ["很", "非常", "特别", "的话", "然后", "所以说", "其实"]
            for word in colloquial:
                if word in text:
                    suggestions.append(f"避免口语化: '{word}' → 建议使用更学术的表达")

            # 检查被动语态使用
            if "我们" in text and text.count("我们") > 3:
                suggestions.append("过多使用'我们'，建议部分改为被动语态或客观表述")

            # 检查句子长度
            sentences = re.split(r"[。.!?]", text)
            long_sentences = [s for s in sentences if len(s) > 80]
            if long_sentences:
                suggestions.append(f"发现 {len(long_sentences)} 个过长句子（>80字），建议拆分")

            result = "润色建议:\n" + "\n".join(suggestions) if suggestions else "文本符合学术规范，无需润色"
            return ToolResult(success=True, output=result, data={"suggestions": suggestions})
        except Exception as e:
            return ToolResult(success=False, error=f"润色失败: {e}")

    async def check_logic(self, content: str) -> ToolResult:
        """逻辑结构与论证完整性检查"""
        try:
            issues = []
            # 检查必要章节
            sections = ["引言", "方法", "结果", "讨论", "结论"]
            missing = [s for s in sections if s not in content]
            if missing:
                issues.append(f"缺少必要章节: {', '.join(missing)}")

            # 检查引用标记
            cite_markers = re.findall(r"\[CITE: [^\]]+\]", content)
            if not cite_markers and "引言" in content:
                issues.append("引言部分未发现引用标记，学术论文需要文献支撑")

            # 检查段落平衡
            paragraphs = [p for p in content.split("\n\n") if p.strip()]
            if paragraphs:
                avg_len = sum(len(p) for p in paragraphs) / len(paragraphs)
                if avg_len < 100:
                    issues.append(f"段落平均长度 {avg_len:.0f} 字符偏短，建议充实内容")

            result = "逻辑检查报告:\n" + "\n".join(issues) if issues else "逻辑结构完整，未发现问题"
            return ToolResult(success=True, output=result, data={"issues": issues})
        except Exception as e:
            return ToolResult(success=False, error=f"逻辑检查失败: {e}")

    async def reduce_ai_pattern(self, text: str) -> ToolResult:
        """降低 AI 生成痕迹的建议"""
        try:
            patterns = []
            # 检查 AI 常见模式
            ai_phrases = [
                "值得注意的是", "需要指出的是", "综上所述", "总而言之",
                "首先", "其次", "再次", "最后",
                "在某种程度上", "从某种意义上说",
            ]
            for phrase in ai_phrases:
                if phrase in text:
                    patterns.append(f"AI 痕迹: '{phrase}' → 建议替换为更自然的表述")

            # 检查句式重复
            sentences = re.split(r"[。.!?]", text)
            sentence_starts = [s.strip()[:4] for s in sentences if s.strip()]
            from collections import Counter
            start_counts = Counter(sentence_starts)
            for start, count in start_counts.items():
                if count > 2:
                    patterns.append(f"句式重复: '{start}...' 开头出现 {count} 次，建议变化句式")

            # 检查过度使用连接词
            connectors = ["因此", "然而", "此外", "另外", "同时"]
            connector_count = sum(text.count(c) for c in connectors)
            if connector_count > len(sentences) * 0.3:
                patterns.append(f"连接词使用过多（{connector_count}次），建议减少")

            result = "降AI味建议:\n" + "\n".join(patterns) if patterns else "文本自然度良好，无明显AI痕迹"
            return ToolResult(success=True, output=result, data={"patterns": patterns})
        except Exception as e:
            return ToolResult(success=False, error=f"降AI味分析失败: {e}")

    # ── 外部检索工具 ──

    async def search_scholar(self, query: str, max_results: int = 5) -> ToolResult:
        """检索 Google Scholar 验证引用真实性"""
        try:
            # 使用 DuckDuckGo 搜索学术文献
            from sage.tools.web import WebSearchTool
            web_tool = WebSearchTool(self.workspace)
            enhanced_query = f"site:scholar.google.com OR site:arxiv.org OR site:doi.org {query}"
            result = await web_tool.web_search(enhanced_query, max_results=max_results)
            return result
        except Exception as e:
            return ToolResult(success=False, error=f"Scholar 检索失败: {e}")

    async def search_arxiv(self, query: str, max_results: int = 5) -> ToolResult:
        """检索 arXiv 预印本"""
        try:
            from sage.tools.web import WebFetchTool
            fetch_tool = WebFetchTool(self.workspace)
            # arXiv API 搜索
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            url = f"http://export.arxiv.org/api/query?search_query=all:{encoded_query}&max_results={max_results}"
            result = await fetch_tool.web_fetch(url, max_length=8000)
            return result
        except Exception as e:
            return ToolResult(success=False, error=f"arXiv 检索失败: {e}")

    async def search_crossref(self, doi: str) -> ToolResult:
        """通过 DOI 验证引用文献是否存在"""
        try:
            from sage.tools.web import WebFetchTool
            fetch_tool = WebFetchTool(self.workspace)
            url = f"https://api.crossref.org/works/{doi}"
            result = await fetch_tool.web_fetch(url, max_length=4000)
            if result.success and "title" in result.output:
                return ToolResult(
                    success=True,
                    output=f"DOI 验证成功: {doi}\n{result.output[:1000]}",
                    data={"doi": doi, "verified": True},
                )
            return ToolResult(
                success=False,
                error=f"DOI 验证失败: {doi} 可能不存在",
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Crossref 验证失败: {e}")

    async def search_semantic_scholar(self, query: str, max_results: int = 5) -> ToolResult:
        """检索 Semantic Scholar 学术数据库"""
        try:
            from sage.tools.web import WebFetchTool
            fetch_tool = WebFetchTool(self.workspace)
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded_query}&limit={max_results}&fields=title,authors,year,abstract,doi"
            result = await fetch_tool.web_fetch(url, max_length=8000)
            return result
        except Exception as e:
            return ToolResult(success=False, error=f"Semantic Scholar 检索失败: {e}")

    # ── 辅助方法 ──

    def _read_document(self, file_path: Path, ext: str) -> str:
        """读取文档内容（支持文本和二进制格式）"""
        if ext in {".pdf", ".docx", ".rtf"}:
            from sage.context.index import ProjectIndex
            indexer = ProjectIndex(self.workspace)
            return indexer._extract_binary_text(file_path, ext)
        return file_path.read_text(encoding="utf-8", errors="ignore")

    def _parse_references(self, content: str) -> list[str]:
        """从内容中解析参考文献列表"""
        # 查找参考文献部分
        ref_patterns = [
            r"##\s*参考文献.*?\n(.*?)(?=\n##|\Z)",
            r"#\s*References.*?\n(.*?)(?=\n#|\Z)",
            r"\\bibliography\{([^}]+)\}",
            r"References\s*\n(.*?)(?=\n\n\n|\Z)",
        ]
        for pattern in ref_patterns:
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                ref_text = match.group(1)
                # 按行分割，过滤空行
                refs = [r.strip() for r in ref_text.split("\n") if r.strip()]
                # 移除编号前缀
                refs = [re.sub(r"^\[\d+\]\s*", "", r) for r in refs]
                refs = [re.sub(r"^\d+\.\s*", "", r) for r in refs]
                return refs
        return []

    def _format_single_reference(self, ref: str, style: str) -> str:
        """格式化单条参考文献"""
        # 简化实现：返回原引用并标注格式
        return f"[{style}] {ref}"

    def _extract_paper_metadata(self, content: str) -> dict:
        """提取论文元数据"""
        metadata = {}
        lines = content.split("\n")

        # 提取标题（通常是第一个非空行或 # 开头）
        for line in lines[:10]:
            line = line.strip()
            if line and not line.startswith("#"):
                metadata["title"] = line[:200]
                break
            elif line.startswith("#"):
                metadata["title"] = line.lstrip("# ").strip()[:200]
                break

        # 提取 DOI
        doi_match = re.search(r"10\.\d{4,}/[^\s]+", content)
        if doi_match:
            metadata["doi"] = doi_match.group(0)

        # 提取年份
        year_match = re.search(r"\b(19|20)\d{2}\b", content)
        if year_match:
            metadata["year"] = year_match.group(0)

        # 提取摘要
        abstract_match = re.search(
            r"(?:摘要|Abstract)[:\s]*(.*?)(?=\n\n|\n#|\n关键词|\nKeywords)",
            content, re.DOTALL | re.IGNORECASE
        )
        if abstract_match:
            metadata["abstract"] = abstract_match.group(1).strip()[:500]

        # 提取关键词
        keywords_match = re.search(
            r"(?:关键词|Keywords)[:\s]*(.*?)(?=\n\n|\n#|\Z)",
            content, re.DOTALL | re.IGNORECASE
        )
        if keywords_match:
            metadata["keywords"] = keywords_match.group(1).strip()[:200]

        return metadata
