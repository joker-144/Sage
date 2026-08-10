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

    def _get_store(self):
        """获取索引存储：优先使用工作空间独立的 WorkspaceStore

        如果工作空间有 .sage/index.db，使用 WorkspaceStore（工作空间隔离）；
        否则回退到全局 MemoryStore（向后兼容旧索引数据）。
        这样切换工作空间后，检索的确实是对应工作空间的索引数据。
        """
        ws_db = self.workspace / ".sage" / "index.db"
        if ws_db.exists():
            from sage.workspace_manager import WorkspaceStore
            return WorkspaceStore(db_path=str(ws_db))
        from sage.memory.store import MemoryStore
        return MemoryStore()

    # ── 文献与索引工具 ──

    async def index_papers(self, force: bool = False) -> ToolResult:
        """对工作空间中的论文文档建立向量索引"""
        try:
            import asyncio
            from sage.context.index import ProjectIndex
            store = self._get_store()
            indexer = ProjectIndex(self.workspace, store)
            # 使用 to_thread 避免阻塞事件循环（索引涉及 embedding 计算，CPU 密集）
            stats = await asyncio.to_thread(indexer.index_project, force=force)
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

    async def search_literature(self, query: str, top_k: Optional[int] = None) -> ToolResult:
        """语义检索已索引的文献库

        二阶段检索：bi-encoder 召回（阈值 0.3 过滤）+ cross-encoder 重排。
        返回结果包含完整来源信息（标题/作者/年份/DOI/页码），供智能体标注引用。

        top_k 为 None 时按工作空间索引级别自动选择（standard=5, premium=10）。
        """
        try:
            import asyncio
            from sage.context.index import ProjectIndex
            store = self._get_store()
            indexer = ProjectIndex(self.workspace, store)
            # 使用 to_thread 避免阻塞事件循环（embedding 计算是 CPU 密集型）
            results = await asyncio.to_thread(indexer.search, query, top_k=top_k, threshold=0.3)
            if not results:
                return ToolResult(
                    success=True,
                    output="未找到相关文献。请先使用 index_papers 工具索引文献库，或当前问题与文献库内容相关度不足。",
                    data=[],
                )
            formatted = []
            for i, r in enumerate(results, 1):
                # 组装来源行（仅展示存在的字段）
                source_parts = []
                if r.title:
                    source_parts.append(f"标题: {r.title}")
                if r.authors:
                    source_parts.append(f"作者: {r.authors}")
                if r.year:
                    source_parts.append(f"年份: {r.year}")
                if r.doi:
                    source_parts.append(f"DOI: {r.doi}")
                page_info = ""
                if r.page_start is not None and r.page_end is not None:
                    page_info = f" (P{r.page_start}-{r.page_end})"
                elif r.page_start is not None:
                    page_info = f" (P{r.page_start})"
                source_line = " | ".join(source_parts) if source_parts else "来源信息缺失"
                formatted.append(
                    f"### 结果 {i}（相关度: {r.score:.3f}）\n"
                    f"**来源**: {source_line}{page_info}\n"
                    f"**文件**: {r.file_path} (L{r.start_line}-{r.end_line})\n"
                    f"**内容**:\n{r.content[:500]}\n"
                )
            return ToolResult(
                success=True,
                output="\n".join(formatted),
                data=[
                    {
                        "file": r.file_path,
                        "score": r.score,
                        "title": r.title,
                        "authors": r.authors,
                        "year": r.year,
                        "doi": r.doi,
                        "page_start": r.page_start,
                        "page_end": r.page_end,
                    }
                    for r in results
                ],
            )
        except Exception as e:
            return ToolResult(success=False, error=f"检索失败: {e}")

    async def extract_references(self, file_path: str) -> ToolResult:
        """从论文文件中提取参考文献列表"""
        try:
            import asyncio
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            ext = full_path.suffix.lower()
            # 使用 to_thread 避免阻塞事件循环（PDF 解析是 CPU/IO 密集型）
            content = await asyncio.to_thread(self._read_document, full_path, ext)
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
        """解析 PDF 文件提取文本内容

        解析后自动查询维普/万方/CrossRef 认证元数据：
        - 补充缺失的元数据字段（期刊名、卷期、页码等）
        - 校验已有字段（如纠正期刊名/栏目名混淆）
        """
        try:
            import asyncio
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            from sage.context.index import ProjectIndex
            indexer = ProjectIndex(self.workspace)
            # 使用 to_thread 避免阻塞事件循环（PDF 解析是 CPU/IO 密集型）
            text = await asyncio.to_thread(indexer._extract_pdf_text, full_path)
            if not text:
                return ToolResult(success=False, error="PDF 解析失败（可能未安装 PyMuPDF）")

            # 提取本地元数据
            local_metadata = self._extract_paper_metadata(text)
            title = self._clean_title(local_metadata.get("title", ""))

            # 自动查询外部源认证元数据（最佳努力，不阻塞 PDF 解析结果）
            verified_metadata = None
            source = ""
            discrepancies = []
            if title:
                try:
                    verified_metadata, source = await asyncio.wait_for(
                        self._verify_metadata_online(title), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    pass

            # 合并元数据
            merged_metadata, discrepancies = self._merge_metadata(
                local_metadata, verified_metadata
            )

            # 构建输出
            output_parts = [f"PDF 解析完成, 共 {len(text)} 字符"]

            if verified_metadata:
                output_parts.append(self._format_verified_metadata(verified_metadata, source))

            if discrepancies:
                output_parts.append("## 差异校正")
                output_parts.extend(discrepancies)

            output_parts.append(f"\n## 内容预览（开头）\n{text[:2000]}")
            # 同时展示文末部分（参考文献通常位于文末），让 AI 能直接看到
            if len(text) > 4000:
                output_parts.append(f"\n## 内容预览（文末）\n{text[-2000:]}")

            return ToolResult(
                success=True,
                output="\n".join(output_parts),
                data={
                    "char_count": len(text),
                    "preview": text[:1000],
                    "tail_preview": text[-1000:] if len(text) > 1000 else "",
                    "metadata": merged_metadata,
                    "verified": verified_metadata is not None,
                    "source": source,
                    "discrepancies": discrepancies,
                },
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
            import asyncio
            full_path = self.workspace / file_path
            if not full_path.exists():
                return ToolResult(success=False, error=f"文件不存在: {file_path}")

            ext = full_path.suffix.lower()
            # 使用 to_thread 避免阻塞事件循环（PDF 解析是 CPU/IO 密集型）
            content = await asyncio.to_thread(self._read_document, full_path, ext)
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
        """OCR 识别扫描版文档

        使用 RapidOCR (ONNX Runtime) 识别扫描版 PDF。
        对于有文本层的原生 PDF 页面，优先直接提取文本；文本层为空的页面才调用 OCR。
        """
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

            # 延迟加载 RapidOCR
            try:
                from rapidocr_onnxruntime import RapidOCR
                ocr_engine = RapidOCR()
            except ImportError:
                return ToolResult(
                    success=False,
                    error="OCR 需要 rapidocr-onnxruntime 库，请安装: pip install rapidocr-onnxruntime"
                )
            except Exception as e:
                return ToolResult(success=False, error=f"OCR 引擎初始化失败: {e}")

            doc = fitz.open(str(full_path))
            text_parts = []
            ocr_page_count = 0
            try:
                for page in doc:
                    # 先尝试直接提取文本层
                    page_text = page.get_text()
                    if page_text.strip():
                        text_parts.append(page_text)
                    else:
                        # 文本层为空 — 调用 RapidOCR
                        try:
                            pix = page.get_pixmap(dpi=200)
                            img_bytes = pix.tobytes("png")
                            result, _ = ocr_engine(img_bytes)
                            if result:
                                lines = [item[1] for item in result if item and len(item) >= 2]
                                ocr_text = "\n".join(lines)
                                text_parts.append(ocr_text)
                                ocr_page_count += 1
                            else:
                                text_parts.append(f"[页面 {page.number + 1} OCR 无识别结果]")
                        except Exception as e:
                            text_parts.append(f"[页面 {page.number + 1} OCR 失败: {e}]")
            finally:
                doc.close()

            text = "\n\n".join(text_parts)
            return ToolResult(
                success=True,
                output=f"OCR 处理完成, 共 {len(text)} 字符 (其中 {ocr_page_count} 页使用 OCR):\n{text[:3000]}",
                data={"char_count": len(text), "ocr_pages": ocr_page_count},
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

    # ── 中文论文元数据认证工具 ──

    async def search_cnki(self, title: str) -> ToolResult:
        """搜索中文学术数据库认证论文元数据

        搜索顺序: 维普 → 万方 → CrossRef
        用于验证论文的期刊名、作者、年份、卷期、页码、DOI 等元数据，
        特别适用于纠正 PDF 提取中常见的期刊名/栏目名混淆问题。
        """
        clean_title = self._clean_title(title)
        if not clean_title:
            return ToolResult(success=False, error="标题为空，无法搜索")

        metadata, source = await self._verify_metadata_online(clean_title)
        if metadata:
            return ToolResult(
                success=True,
                output=self._format_verified_metadata(metadata, source),
                data={**metadata, "source": source},
            )
        return ToolResult(
            success=False,
            error=f"未能从维普/万方/CrossRef 找到与 '{clean_title}' 匹配的论文",
        )

    async def _verify_metadata_online(self, title: str) -> tuple[Optional[dict], str]:
        """在线认证论文元数据（维普 → 万方 → CrossRef）

        Returns: (metadata, source) 或 (None, "")
        """
        if not title:
            return None, ""

        # 1. 维普
        try:
            result = await asyncio.wait_for(self._search_cqvip(title), timeout=15.0)
            if result:
                return result, "维普"
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass

        # 2. 万方
        try:
            result = await asyncio.wait_for(self._search_wanfang(title), timeout=15.0)
            if result:
                return result, "万方"
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass

        # 3. CrossRef
        try:
            result = await asyncio.wait_for(self._search_crossref_by_title(title), timeout=15.0)
            if result:
                return result, "CrossRef"
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass

        return None, ""

    async def _search_cqvip(self, title: str) -> Optional[dict]:
        """通过 DuckDuckGo site 搜索维普获取论文元数据"""
        return await self._search_chinese_db(title, "cqvip.com", "维普")

    async def _search_wanfang(self, title: str) -> Optional[dict]:
        """通过 DuckDuckGo site 搜索万方获取论文元数据"""
        return await self._search_chinese_db(title, "wanfangdata.com.cn", "万方")

    async def _search_chinese_db(self, title: str, site: str, db_name: str) -> Optional[dict]:
        """通过 DuckDuckGo 搜索指定学术数据库获取论文元数据

        Args:
            title: 论文标题
            site: 学术数据库域名（如 cqvip.com）
            db_name: 数据库名称（用于标记来源）
        """
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return None

        try:
            loop = asyncio.get_running_loop()
            # 先用 site 限定搜索，再用标题+数据库名搜索
            query = f"site:{site} {title}"
            results = await loop.run_in_executor(
                None, lambda: DDGS().text(query, max_results=5)
            )
            # site 搜索无结果时回退到标题+数据库名
            if not results:
                query = f'"{title}" {db_name}'
                results = await loop.run_in_executor(
                    None, lambda: DDGS().text(query, max_results=5)
                )
        except Exception:
            return None

        if not results:
            return None

        # 从搜索结果中解析元数据（同时验证标题相似度）
        best_metadata = None
        best_score = 0.0
        SIMILARITY_THRESHOLD = 0.3
        for r in results:
            result_title = r.get("title", "")
            body = r.get("body", "")
            # 标题相似度检查（搜索结果标题与原始标题的重叠度）
            score = self._title_similarity(title, result_title)
            if score < SIMILARITY_THRESHOLD:
                continue
            text = f"{result_title} {body}"
            metadata = self._parse_journal_metadata_from_text(text)
            if metadata and metadata.get("journal") and score > best_score:
                best_metadata = metadata
                best_score = score

        return best_metadata

    async def _search_crossref_by_title(self, title: str) -> Optional[dict]:
        """通过标题搜索 CrossRef API 获取论文元数据

        请求多条候选结果，用标题相似度筛选最匹配的项，
        避免中文标题被 CrossRef 匹配到完全不相关的英文文献。
        """
        try:
            import httpx
            import urllib.parse

            encoded_title = urllib.parse.quote(title)
            # 请求 5 条候选，用于相似度筛选
            url = f"https://api.crossref.org/works?query.bibliographic={encoded_title}&rows=5"

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Sage/1.0 (mailto:sage@example.com)"},
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()

            items = data.get("message", {}).get("items", [])
            if not items:
                return None

            # 相似度阈值：低于此值视为不匹配（防止返回不相关结果如法律案例）
            SIMILARITY_THRESHOLD = 0.3
            best_metadata = None
            best_score = 0.0

            for item in items:
                titles = item.get("title", [])
                if not titles:
                    continue
                candidate_title = titles[0]
                score = self._title_similarity(title, candidate_title)
                if score < SIMILARITY_THRESHOLD:
                    continue
                if score <= best_score:
                    continue

                metadata = {}
                # 期刊名
                container_titles = item.get("container-title", [])
                if container_titles:
                    metadata["journal"] = container_titles[0]
                # 标题
                metadata["title"] = candidate_title
                # 作者
                authors = item.get("author", [])
                if authors:
                    author_names = []
                    for a in authors:
                        given = a.get("given", "")
                        family = a.get("family", "")
                        name = f"{family}{given}".strip() if family or given else ""
                        if name:
                            author_names.append(name)
                    if author_names:
                        metadata["authors"] = ", ".join(author_names)
                # 年份
                date_parts = item.get("published", {}).get("date-parts", [[]])
                if date_parts and date_parts[0]:
                    metadata["year"] = str(date_parts[0][0])
                # 卷期页
                if item.get("volume"):
                    metadata["volume"] = item["volume"]
                if item.get("issue"):
                    metadata["issue"] = item["issue"]
                if item.get("page"):
                    metadata["pages"] = item["page"]
                # DOI
                if item.get("DOI"):
                    metadata["doi"] = item["DOI"]
                # 摘要（CrossRef 摘要可能含 XML 标签）
                if item.get("abstract"):
                    abstract = re.sub(r"<[^>]+>", "", item["abstract"])
                    metadata["abstract"] = abstract[:500]
                # 关键词
                subjects = item.get("subject", [])
                if subjects:
                    metadata["keywords"] = ", ".join(subjects)

                # 必须有期刊名才算认证成功
                if metadata.get("journal"):
                    best_metadata = metadata
                    best_score = score

            return best_metadata
        except Exception:
            return None

    def _parse_journal_metadata_from_text(self, text: str) -> Optional[dict]:
        """从搜索结果文本中解析期刊元数据

        中文期刊搜索结果的常见格式：
        - 期刊名: 《乡村科技》 或 期刊：乡村科技
        - 年份: 2023年 或 2023
        - 期号: 第X期
        - 页码: X-Y
        """
        metadata = {}

        # 期刊名: 优先匹配 《》中的内容
        journal_match = re.search(r"《([^》]+)》", text)
        if journal_match:
            candidate = journal_match.group(1).strip()
            # 排除标题被误识别为期刊名的情况
            if len(candidate) <= 30 and not candidate.startswith("基于"):
                metadata["journal"] = candidate

        # 年份
        year_match = re.search(r"\b(19|20)\d{2}\b", text)
        if year_match:
            metadata["year"] = year_match.group(0)

        # 期号
        issue_match = re.search(r"第\s*(\d+)\s*期", text)
        if issue_match:
            metadata["issue"] = issue_match.group(1)

        # 卷号
        vol_match = re.search(r"第\s*(\d+)\s*卷", text)
        if vol_match:
            metadata["volume"] = vol_match.group(1)

        # 页码
        page_match = re.search(r"页?\s*(\d+)\s*[-–]\s*(\d+)", text)
        if page_match:
            metadata["pages"] = f"{page_match.group(1)}-{page_match.group(2)}"

        # DOI
        doi_match = re.search(r"10\.\d{4,}/[^\s)】]+", text)
        if doi_match:
            metadata["doi"] = doi_match.group(0)

        # 作者（尝试匹配 作者: 或 作者： 后面的内容）
        author_match = re.search(r"作者[:：]\s*([^\n,，|]{2,50})", text)
        if author_match:
            metadata["authors"] = author_match.group(1).strip()

        return metadata if metadata.get("journal") else None

    def _clean_title(self, title: str) -> str:
        """清理从 PDF 提取的标题"""
        if not title:
            return ""
        # 移除多余空白和换行
        title = re.sub(r"\s+", " ", title).strip()
        # 移除文件扩展名
        title = re.sub(r"\.(pdf|PDF)$", "", title)
        # 移除可能的编号前缀
        title = re.sub(r"^[\d\W]+", "", title).strip()
        # 限制长度
        if len(title) > 100:
            title = title[:100]
        return title

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        """计算两个标题的相似度（0~1），支持中英文混合

        使用 difflib.SequenceMatcher 做整体比率，同时计算字符级重叠率，
        取较高值以更好地处理中英文标题的匹配。
        """
        if not a or not b:
            return 0.0
        a_lower = a.lower().strip()
        b_lower = b.lower().strip()
        if a_lower == b_lower:
            return 1.0
        # SequenceMatcher 比率
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, a_lower, b_lower).ratio()
        # 字符重叠率（中文友好：计算共有字符占较短标题的比例）
        a_chars = set(a_lower.replace(" ", ""))
        b_chars = set(b_lower.replace(" ", ""))
        if a_chars and b_chars:
            overlap = len(a_chars & b_chars) / max(len(a_chars), len(b_chars))
        else:
            overlap = 0.0
        return max(ratio, overlap)

    def _format_verified_metadata(self, metadata: dict, source: str) -> str:
        """格式化认证后的元数据为可读文本"""
        lines = [f"## 论文元数据认证（来源: {source}）"]

        field_names = {
            "title": "标题",
            "journal": "期刊名",
            "authors": "作者",
            "year": "年份",
            "volume": "卷号",
            "issue": "期号",
            "pages": "页码",
            "doi": "DOI",
            "abstract": "摘要",
            "keywords": "关键词",
        }

        for key, label in field_names.items():
            val = metadata.get(key)
            if val:
                lines.append(f"**{label}**: {val}")

        return "\n".join(lines)

    def _merge_metadata(
        self, local: dict, verified: Optional[dict]
    ) -> tuple[dict, list[str]]:
        """合并本地提取和外部认证的元数据

        策略: 优先使用外部认证结果（特别是期刊名），补充本地提取的字段。
        同时生成差异报告，标注本地与外部不一致的字段。

        Returns: (合并后的元数据, 差异报告列表)
        """
        if not verified:
            return local, []

        result = {}
        discrepancies = []
        all_keys = set(local.keys()) | set(verified.keys())

        for key in all_keys:
            local_val = local.get(key)
            verified_val = verified.get(key)

            if verified_val:
                result[key] = verified_val
                # 检查差异（仅在本地和外部都有值且不一致时报告）
                if local_val and str(local_val).strip() != str(verified_val).strip():
                    discrepancies.append(
                        f"  {key}: 本地='{local_val}' → 认证='{verified_val}'"
                    )
            else:
                result[key] = local_val

        return result, discrepancies

    # ── 辅助方法 ──

    def _read_document(self, file_path: Path, ext: str) -> str:
        """读取文档内容（支持文本和二进制格式）"""
        if ext in {".pdf", ".docx", ".rtf"}:
            from sage.context.index import ProjectIndex
            indexer = ProjectIndex(self.workspace)
            return indexer._extract_binary_text(file_path, ext)
        return file_path.read_text(encoding="utf-8", errors="ignore")

    def _parse_references(self, content: str) -> list[str]:
        """从内容中解析参考文献列表

        支持多种标题格式：
          - Markdown 标题：## 参考文献 / # References
          - LaTeX：\\bibliography{...}
          - 纯文本（PDF 提取的常见形式）：参考文献 / 参考文献：（无 # 前缀，可带冒号）
          - 英文变体：REFERENCES / Bibliography / 文献
        参考文献位于文末，匹配后取到文档结尾的内容。
        支持全角方括号编号：［1］［2］...
        兼容双栏排版 PDF 导致的文本顺序混乱（表格/图表可能插入在参考文献条目中间）。
        """
        # 查找参考文献部分（按匹配优先级排序）
        ref_patterns = [
            # Markdown 标题（保留原逻辑）
            r"##\s*参考文献[:：]?.*?\n(.*?)(?=\n##\s|\Z)",
            r"#\s*References[:：]?.*?\n(.*?)(?=\n#\s|\Z)",
            # LaTeX bibliography
            r"\\bibliography\{([^}]+)\}",
            # 纯文本标题（PDF 提取的常见形式，无 # 前缀，支持半角/全角冒号）
            # 中文"参考文献"作为独立行/标题，后接参考文献条目直到文档结尾
            r"(?:^|\n)\s*参\s*考\s*文\s*献\s*[:：]?\s*\n+(.*)",
            # 英文 References 作为独立行/标题（全大写或首字母大写）
            r"(?:^|\n)\s*References[:：]?\s*\n+(.*)",
            r"(?:^|\n)\s*REFERENCES[:：]?\s*\n+(.*)",
            r"(?:^|\n)\s*Bibliography[:：]?\s*\n+(.*)",
            # 原宽松匹配（保留向后兼容）
            r"References\s*\n(.*?)(?=\n\n\n|\Z)",
        ]
        for pattern in ref_patterns:
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                ref_text = match.group(1)
                # 按行分割，收集所有看起来像参考文献条目的行
                # 编号模式：半角 [1] 或全角 ［1］
                num_pattern = re.compile(r"^[\[［]\s*(\d+)\s*[\]］]")
                lines = [r.strip() for r in ref_text.split("\n")]

                # 收集编号的参考文献条目（以 [1] / ［1］ 开头）
                numbered_refs = {}  # {序号: 条目文本}
                current_num = None
                # 跳过干扰模式（遇到表格/图表后，跳过内容直到下一个参考文献条目）
                skip_until_next_ref = False
                # 记录遇到的非参考文献内容（英文摘要等）
                stop_triggered = False
                stop_markers = [
                    "Abstract:", "Key words:", "Keywords:", "作者简介",
                    "基金项目", "致谢", "Acknowledgements", "附录", "Appendix",
                ]

                for line in lines:
                    if not line:
                        continue

                    # 检查是否是新的参考文献条目
                    m = num_pattern.match(line)
                    if m:
                        current_num = int(m.group(1))
                        numbered_refs[current_num] = line
                        skip_until_next_ref = False
                        continue

                    # 检查是否是表格/图表标题行
                    is_table_fig = bool(re.match(r"^(?:表|图|Table|Figure|Fig\.)\s*\d+", line))
                    if is_table_fig:
                        skip_until_next_ref = True
                        continue

                    # 检查是否应该停止（遇到了参考文献之后的章节，如英文摘要）
                    line_lower = line.lower()
                    is_stop = False
                    for marker in stop_markers:
                        if marker.lower() in line_lower and len(line) < 200:
                            is_stop = True
                            break
                    # 额外判断：如果行很长且是英文标题（通常在参考文献之后）
                    if not is_stop and re.match(r"^[A-Z][a-zA-Z\s,]+$", line) and len(line) > 30 and numbered_refs:
                        is_stop = True
                    if is_stop:
                        # 如果已经收集到了一些参考文献，就停止；否则继续
                        if numbered_refs:
                            stop_triggered = True
                            break
                        else:
                            continue

                    # 如果处于跳过干扰模式，跳过该行
                    if skip_until_next_ref:
                        # 但检查是否是页码/页眉（如 http://www... 或年份卷期号）
                        if re.match(r"^https?://", line) or re.match(r"^\d{4}年\d+月", line):
                            continue
                        # 检查是否是明显的页码（·149 这样的）
                        if re.match(r"^·\s*\d+\s*$", line):
                            continue
                        continue

                    # 追加到当前条目
                    if current_num is not None and len(line) >= 5:
                        numbered_refs[current_num] += " " + line

                if not numbered_refs:
                    continue

                # 按序号排序
                refs = []
                for num in sorted(numbered_refs.keys()):
                    ref = numbered_refs[num]
                    # 移除编号前缀
                    ref = re.sub(r"^[\[［]\s*\d+\s*[\]］]\s*", "", ref)
                    # 过滤过短条目
                    if len(ref) >= 10:
                        refs.append(ref)

                if refs:
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
