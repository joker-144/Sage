"""
工具引擎 — 注册、schema 生成、执行调度（Sage 论文写作系统）

每个工具定义为标准 OpenAI function calling schema，
LLM 可以在推理过程中自主决定调用哪个工具、传递什么参数。

工具执行流程:
  LLM 返回 tool_calls → ToolEngine.execute() → 查找注册的工具 → 调用 → 返回 ToolResult

Sage 系统工具分为:
  - 文件操作（通用）: read_file/write_file/edit_file/list_dir
  - 文献索引（Sage）: index_papers/search_literature/extract_references/insert_citation/format_references/check_plagiarism
  - 文档处理（Sage）: parse_pdf/parse_docx/parse_latex/extract_metadata/ocr_document
  - 写作辅助（Sage）: generate_outline/write_paragraph/polish_academic/check_logic/reduce_ai_pattern
  - 外部检索（Sage）: search_scholar/search_arxiv/search_crossref/search_semantic_scholar
  - 技能与网络（通用）: list_skills/load_skill/install_skill/search_remote_skills/web_search/web_search_pro/web_fetch
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Awaitable

from sage.tools.types import ToolResult
from sage.tools.file_ops import FileOps
from sage.tools.paper_ops import PaperOps
from sage.tools.skill_ops import SkillOps
from sage.tools.web import WebSearchTool, WebSearchProTool, WebFetchTool


@dataclass
class ToolDef:
    """工具定义 — 函数 + JSON schema"""
    func: Callable[..., Awaitable[ToolResult]]
    schema: dict[str, Any]


class ToolEngine:
    """工具引擎 — 注册、schema 生成、执行调度"""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._tools: dict[str, ToolDef] = {}
        self._register_defaults()

    def _register_defaults(self):
        """注册内置工具（Sage 论文写作系统）"""
        file_ops = FileOps(self.workspace)
        paper_ops = PaperOps(self.workspace)

        # 文件操作（通用）
        self.register("read_file", file_ops.read_file, READ_FILE_SCHEMA)
        self.register("write_file", file_ops.write_file, WRITE_FILE_SCHEMA)
        self.register("edit_file", file_ops.edit_file, EDIT_FILE_SCHEMA)
        self.register("list_dir", file_ops.list_dir, LIST_DIR_SCHEMA)

        # 文献与索引（Sage 专用）
        self.register("index_papers", paper_ops.index_papers, INDEX_PAPERS_SCHEMA)
        self.register("search_literature", paper_ops.search_literature, SEARCH_LITERATURE_SCHEMA)
        self.register("extract_references", paper_ops.extract_references, EXTRACT_REFERENCES_SCHEMA)
        self.register("insert_citation", paper_ops.insert_citation, INSERT_CITATION_SCHEMA)
        self.register("format_references", paper_ops.format_references, FORMAT_REFERENCES_SCHEMA)
        self.register("check_plagiarism", paper_ops.check_plagiarism, CHECK_PLAGIARISM_SCHEMA)

        # 文档处理（Sage 专用）
        self.register("parse_pdf", paper_ops.parse_pdf, PARSE_PDF_SCHEMA)
        self.register("parse_docx", paper_ops.parse_docx, PARSE_DOCX_SCHEMA)
        self.register("parse_latex", paper_ops.parse_latex, PARSE_LATEX_SCHEMA)
        self.register("extract_metadata", paper_ops.extract_metadata, EXTRACT_METADATA_SCHEMA)
        self.register("ocr_document", paper_ops.ocr_document, OCR_DOCUMENT_SCHEMA)

        # 写作辅助（Sage 专用）
        self.register("generate_outline", paper_ops.generate_outline, GENERATE_OUTLINE_SCHEMA)
        self.register("write_paragraph", paper_ops.write_paragraph, WRITE_PARAGRAPH_SCHEMA)
        self.register("polish_academic", paper_ops.polish_academic, POLISH_ACADEMIC_SCHEMA)
        self.register("check_logic", paper_ops.check_logic, CHECK_LOGIC_SCHEMA)
        self.register("reduce_ai_pattern", paper_ops.reduce_ai_pattern, REDUCE_AI_PATTERN_SCHEMA)

        # 外部检索（Sage 专用）
        self.register("search_scholar", paper_ops.search_scholar, SEARCH_SCHOLAR_SCHEMA)
        self.register("search_arxiv", paper_ops.search_arxiv, SEARCH_ARXIV_SCHEMA)
        self.register("search_crossref", paper_ops.search_crossref, SEARCH_CROSSREF_SCHEMA)
        self.register("search_semantic_scholar", paper_ops.search_semantic_scholar, SEARCH_SEMANTIC_SCHOLAR_SCHEMA)

        # 技能管理（通用）
        skill_ops = SkillOps(self.workspace)
        self.register("list_skills", skill_ops.list_skills, LIST_SKILLS_SCHEMA)
        self.register("load_skill", skill_ops.load_skill, LOAD_SKILL_SCHEMA)
        self.register("install_skill", skill_ops.install_skill, INSTALL_SKILL_SCHEMA)
        self.register("search_remote_skills", skill_ops.search_remote_skills, SEARCH_REMOTE_SKILLS_SCHEMA)

        # 联网工具（通用）
        web_search = WebSearchTool(self.workspace)
        web_search_pro = WebSearchProTool(self.workspace)
        web_fetch = WebFetchTool(self.workspace)
        self.register("web_search", web_search.web_search, WEB_SEARCH_SCHEMA)
        self.register("web_search_pro", web_search_pro.web_search_pro, WEB_SEARCH_PRO_SCHEMA)
        self.register("web_fetch", web_fetch.web_fetch, WEB_FETCH_SCHEMA)

    def register(self, name: str, func: Callable[..., Awaitable[ToolResult]], schema: dict[str, Any]):
        """注册工具"""
        self._tools[name] = ToolDef(func=func, schema=schema)

    def get_schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的 JSON schema（供 LLM function calling 使用）"""
        return [t.schema for t in self._tools.values()]

    async def execute(self, tool_call) -> ToolResult:
        """执行 LLM 请求的工具调用

        Args:
            tool_call: 包含 id, name, arguments 的工具调用对象
                       (sage.llm.client.ToolCall 或 OpenAI tool_call 对象)
        """
        name = tool_call.name if hasattr(tool_call, "name") else tool_call.function.name
        arguments = (
            tool_call.arguments
            if hasattr(tool_call, "arguments")
            else json.loads(tool_call.function.arguments)
        )

        if name not in self._tools:
            return ToolResult(success=False, error=f"未知工具: {name}")

        tool = self._tools[name]
        try:
            return await tool.func(**arguments)
        except TypeError as e:
            return ToolResult(success=False, error=f"参数错误: {e}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ── 文件操作工具 Schema（通用）──

READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取指定文件的内容。支持通过行号范围读取部分内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径（相对 workspace）"},
                "start_line": {"type": "integer", "description": "起始行号（从 1 开始），默认从头读", "default": 0},
                "end_line": {"type": "integer", "description": "结束行号，默认读到末尾", "default": 0},
            },
            "required": ["path"],
        },
    },
}

WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "创建或覆写文件。如果文件已存在会被覆盖，父目录自动创建。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径（相对 workspace）"},
                "content": {"type": "string", "description": "文件完整内容"},
            },
            "required": ["path", "content"],
        },
    },
}

EDIT_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "通过搜索-替换的方式编辑文件的指定部分。"
            "old_str 必须是文件中唯一匹配的文本片段，否则会报错要求提供更多上下文。"
            "相比 write_file 整文件重写，edit_file 只修改需要改的部分，节省 token 且更安全。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要编辑的文件路径（相对 workspace）"},
                "old_str": {"type": "string", "description": "要替换的原文（必须精确匹配文件中的内容）"},
                "new_str": {"type": "string", "description": "替换后的新文本"},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
}

LIST_DIR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "列出指定目录下的文件和子目录。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径（相对 workspace），默认为根目录", "default": "."},
            },
            "required": [],
        },
    },
}


# ── 文献索引工具 Schema（Sage 专用）──

INDEX_PAPERS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "index_papers",
        "description": (
            "对工作空间中的论文文档建立向量索引。"
            "支持 PDF/Word/Markdown/LaTeX/BibTeX 等格式。"
            "导入新论文后自动调用此工具建立索引。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "description": "是否强制重新索引所有文件", "default": False},
            },
        },
    },
}

SEARCH_LITERATURE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_literature",
        "description": (
            "语义检索已索引的文献库，查找相关研究内容。"
            "使用自然语言查询，返回最相关的文献片段。"
            "首次使用前需调用 index_papers 建立索引。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询（自然语言描述要查找的文献内容）"},
                "top_k": {"type": "integer", "description": "返回结果数量", "default": 5},
            },
            "required": ["query"],
        },
    },
}

EXTRACT_REFERENCES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "extract_references",
        "description": "从论文文件中提取参考文献列表。支持 Markdown/LaTeX/Word/PDF 格式。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "论文文件路径（相对 workspace）"},
            },
            "required": ["file_path"],
        },
    },
}

INSERT_CITATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "insert_citation",
        "description": "在论文指定标记位置插入引用。标记格式为 [CITE: 关键词]。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "论文文件路径（相对 workspace）"},
                "marker": {"type": "string", "description": "引用标记关键词（[CITE: 标记] 中的标记部分）"},
                "citation": {"type": "string", "description": "要插入的引用文本（如 (作者, 年份)）"},
            },
            "required": ["file_path", "marker", "citation"],
        },
    },
}

FORMAT_REFERENCES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "format_references",
        "description": "按目标期刊要求格式化参考文献列表。支持 APA/MLA/GB-T7714/Vancouver/Chicago/IEEE 格式。",
        "parameters": {
            "type": "object",
            "properties": {
                "references": {"type": "string", "description": "参考文献列表（每行一条）"},
                "style": {"type": "string", "description": "引用格式: APA/MLA/GB-T7714/Vancouver/Chicago/IEEE", "default": "APA"},
            },
            "required": ["references"],
        },
    },
}

CHECK_PLAGIARISM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_plagiarism",
        "description": "查重检测，识别与已索引文献库的重复内容。返回重复率和重复段落详情。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要检测的论文内容"},
                "threshold": {"type": "number", "description": "相似度阈值（0-1），超过此值视为重复", "default": 0.8},
            },
            "required": ["content"],
        },
    },
}


# ── 文档处理工具 Schema（Sage 专用）──

PARSE_PDF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_pdf",
        "description": "解析 PDF 文件提取文本内容。需要安装 PyMuPDF (pip install pymupdf)。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "PDF 文件路径（相对 workspace）"},
            },
            "required": ["file_path"],
        },
    },
}

PARSE_DOCX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_docx",
        "description": "解析 Word 文档提取文本内容。需要安装 python-docx (pip install python-docx)。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Word 文件路径（相对 workspace）"},
            },
            "required": ["file_path"],
        },
    },
}

PARSE_LATEX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_latex",
        "description": "解析 LaTeX 源文件提取纯文本内容，保留章节结构。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "LaTeX 文件路径（相对 workspace）"},
            },
            "required": ["file_path"],
        },
    },
}

EXTRACT_METADATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "extract_metadata",
        "description": "提取论文元数据（标题/作者/年份/DOI/摘要/关键词）。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "论文文件路径（相对 workspace）"},
            },
            "required": ["file_path"],
        },
    },
}

OCR_DOCUMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ocr_document",
        "description": "OCR 识别扫描版文档。需要安装 PyMuPDF。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "扫描版文档路径（相对 workspace）"},
            },
            "required": ["file_path"],
        },
    },
}


# ── 写作辅助工具 Schema（Sage 专用）──

GENERATE_OUTLINE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_outline",
        "description": "根据选题生成论文大纲。支持研究论文/综述论文/案例研究等类型。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "论文选题/主题"},
                "paper_type": {"type": "string", "description": "论文类型: research/review/case", "default": "research"},
            },
            "required": ["topic"],
        },
    },
}

WRITE_PARAGRAPH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_paragraph",
        "description": "提供论文段落写作指导，包括结构建议和写作要点。",
        "parameters": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "章节名称（如引言/方法/结果）"},
                "key_points": {"type": "string", "description": "段落要点（用逗号分隔）"},
            },
            "required": ["section", "key_points"],
        },
    },
}

POLISH_ACADEMIC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "polish_academic",
        "description": "学术语言润色建议，检查口语化表达、被动语态、句子长度等。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要润色的文本"},
            },
            "required": ["text"],
        },
    },
}

CHECK_LOGIC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_logic",
        "description": "逻辑结构与论证完整性检查，检查必要章节、引用标记、段落平衡等。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要检查的论文内容"},
            },
            "required": ["content"],
        },
    },
}

REDUCE_AI_PATTERN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reduce_ai_pattern",
        "description": "降低 AI 生成痕迹的建议，识别 AI 常见表述模式、句式重复、连接词过多等问题。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要分析的文本"},
            },
            "required": ["text"],
        },
    },
}


# ── 外部检索工具 Schema（Sage 专用）──

SEARCH_SCHOLAR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_scholar",
        "description": "检索学术数据库验证引用真实性。通过 Google Scholar/arXiv/DOI 搜索学术文献。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询（论文标题/作者/关键词）"},
                "max_results": {"type": "integer", "description": "最大返回结果数", "default": 5},
            },
            "required": ["query"],
        },
    },
}

SEARCH_ARXIV_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_arxiv",
        "description": "检索 arXiv 预印本数据库，获取最新研究论文。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "max_results": {"type": "integer", "description": "最大返回结果数", "default": 5},
            },
            "required": ["query"],
        },
    },
}

SEARCH_CROSSREF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_crossref",
        "description": "通过 DOI 验证引用文献是否存在。返回文献的元数据信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "要验证的 DOI（如 10.1000/xyz123）"},
            },
            "required": ["doi"],
        },
    },
}

SEARCH_SEMANTIC_SCHOLAR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_semantic_scholar",
        "description": "检索 Semantic Scholar 学术数据库，获取论文标题、作者、年份、摘要、DOI 等。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "max_results": {"type": "integer", "description": "最大返回结果数", "default": 5},
            },
            "required": ["query"],
        },
    },
}


# ── 技能管理工具 Schema ──

LIST_SKILLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": "列出当前所有已安装的技能及其能力描述。",
        "parameters": {"type": "object", "properties": {}},
    },
}

LOAD_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "加载指定技能的详细信息（能力、关联工具等）。不传 name 时列出全部技能。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称（目录名），留空则列出全部"},
            },
        },
    },
}

INSTALL_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "install_skill",
        "description": "从 SkillHub 安装新技能到本地 skills 目录。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要安装的技能名称"},
            },
            "required": ["name"],
        },
    },
}

SEARCH_REMOTE_SKILLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_remote_skills",
        "description": (
            "在远程技能库中搜索技能。"
            "返回候选技能列表，包含 slug、名称、描述、标签等信息。"
            "找到后用 install_skill 工具 + slug 安装。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词", "default": ""},
                "limit": {"type": "integer", "description": "返回结果数", "default": 10},
            },
        },
    },
}


# ── 联网工具 Schema ──

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "使用 DuckDuckGo 进行网络搜索（免费免配置）。"
            "适用于快速获取网络信息、查找学术资料、了解最新动态。"
            "如果搜索结果质量不高或关联性低，请改用 web_search_pro 获取更精准的结果。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "最大返回结果数", "default": 8},
            },
            "required": ["query"],
        },
    },
}

WEB_SEARCH_PRO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search_pro",
        "description": (
            "使用 Tavily AI 进行高质量网络搜索（专为 AI 设计，返回结构化结果和 AI 摘要）。"
            "当 web_search (DuckDuckGo) 结果质量不高、关联性低时使用。"
            "需要在 .env 中配置 TAVILY_API_KEY（每月 1000 次免费额度）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "最大返回结果数", "default": 8},
            },
            "required": ["query"],
        },
    },
}

WEB_FETCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "抓取指定 URL 的网页正文内容。"
            "适用于已知具体网址时获取完整页面信息，如学术论文、技术文档、API 文档等。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的网页 URL"},
                "max_length": {"type": "integer", "description": "返回内容最大字符数", "default": 8000},
            },
            "required": ["url"],
        },
    },
}
