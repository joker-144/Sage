"""
Sage 配置系统
基于 pydantic-settings，从 .env 和环境变量加载配置
单模型运行时，Provider 可切换（OpenAI 兼容协议）

打包后（PyInstaller frozen）自动将 .env 和数据文件路径定位到
%LOCALAPPDATA%/Sage/（与 Electron main.cjs 的 SAGE_DATA_DIR 对齐）。
"""
from __future__ import annotations

import os as _os
import sys as _sys
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_data_dir() -> Path:
    """获取用户数据目录。

    优先级：
    1. 环境变量 SAGE_DATA_DIR（Electron main.cjs 设置）
    2. PyInstaller frozen 模式 → %LOCALAPPDATA%/Sage
    3. 开发模式 → 当前工作目录
    """
    env_dir = _os.environ.get("SAGE_DATA_DIR", "")
    if env_dir:
        return Path(env_dir)
    if getattr(_sys, "frozen", False):
        local_appdata = _os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            return Path(local_appdata) / "Sage"
    return Path.cwd()


class AgentConfig(BaseSettings):
    """Sage 全局配置 — 统一从 .env 加载

    所有字段直接从 .env / 环境变量读取，避免嵌套模型的加载问题。
    LLM_CHAT_* 前缀对应对话模型配置，LLM_EMBEDDING_* 前缀对应 Embedding 配置。
    """

    model_config = SettingsConfigDict(
        env_file=str(_get_data_dir() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 对话 LLM 配置（LLM_CHAT_* 前缀）──
    llm_chat_api_key: str = Field(default="", validation_alias="LLM_CHAT_API_KEY")
    llm_chat_base_url: str = Field(default="https://api.deepseek.com", validation_alias="LLM_CHAT_BASE_URL")
    llm_chat_model: str = Field(default="deepseek-chat", validation_alias="LLM_CHAT_MODEL")
    llm_chat_temperature: float = Field(default=0.3, validation_alias="LLM_CHAT_TEMPERATURE")
    llm_chat_max_tokens: int = Field(default=8192, validation_alias="LLM_CHAT_MAX_TOKENS")
    llm_chat_timeout: float = Field(default=120.0, validation_alias="LLM_CHAT_TIMEOUT")
    llm_chat_streaming: bool = Field(default=True, validation_alias="LLM_CHAT_STREAMING")
    llm_chat_max_tool_rounds: int = Field(default=12, validation_alias="LLM_CHAT_MAX_TOOL_ROUNDS")

    # ── 记忆系统配置 ──
    memory_sqlite_path: str = Field(
        default=str(_get_data_dir() / "data" / "memory.db"),
        validation_alias="MEMORY_SQLITE_PATH",
    )

    # ── Embedding 配置（LLM_EMBEDDING_* 前缀）──
    # 本地 sentence-transformers 模型，默认 BAAI/bge-small-zh-v1.5（512 维，约 95MB）
    # 中英双语、中文效果显著优于纯英文的 all-MiniLM-L6-v2，与 Sage 面向
    # CSSCI / 中文文献的场景匹配；首次使用时自动从 HuggingFace 下载（hf-mirror 镜像加速）
    llm_embedding_model: str = Field(
        default="BAAI/bge-small-zh-v1.5",
        validation_alias="LLM_EMBEDDING_MODEL",
    )

    # ── Cross-Encoder 重排模型（LLM_RERANKER_MODEL）──
    # 默认 BAAI/bge-reranker-base（中英双语，约 1.1GB，仅 premium 索引级别启用重排时才下载）
    # 置空字符串可禁用重排（检索降级为仅 bi-encoder 召回）
    llm_reranker_model: str = Field(
        default="BAAI/bge-reranker-base",
        validation_alias="LLM_RERANKER_MODEL",
    )

    # ── 联网搜索配置 ──
    # Tavily AI: 专为 AI 设计的搜索 API，每月 1000 次免费额度
    # 获取地址: https://tavily.com
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")

    # ── Agent 配置 ──
    workspace: Path = Field(default=Path("."), validation_alias="sage_WORKSPACE")
    max_context_tokens: int = Field(default=60000, validation_alias="sage_MAX_CONTEXT_TOKENS")
    summary_trigger_tokens: int = Field(
        default=45000, validation_alias="sage_SUMMARY_TRIGGER_TOKENS"
    )

    # ── 调试接口访问令牌（SAGE_DEBUG_ACCESS_TOKEN）──
    # 为空（默认）：/debug/* 接口仅允许本机环回地址访问（127.0.0.1 / ::1），
    #              局域网其它机器一律拒绝——避免后端绑定 0.0.0.0 时暴露文件清单等敏感信息。
    # 非空：访问 /debug/* 需在请求头携带 X-Sage-Debug-Token 且值与之一致；
    #      环回地址访问同样需要令牌，用于本机多进程隔离调试。
    debug_access_token: str = Field(
        default="", validation_alias="SAGE_DEBUG_ACCESS_TOKEN"
    )

    def validate_api_keys(self) -> list[str]:
        """检查哪些 API Key 缺失"""
        missing = []
        if not self.llm_chat_api_key or "your-" in self.llm_chat_api_key:
            missing.append("对话 LLM (LLM_CHAT_API_KEY)")
        # 本地 Embedder 不需要 API Key
        return missing


_config: Optional[AgentConfig] = None

# 常用本地 Embedding 模型的向量维度表（用于检测旧索引数据是否与当前模型兼容）
# 未收录的模型返回 None，此时不做维度清理（检索阶段会自动跳过维度不匹配的行）
EMBEDDING_MODEL_DIMS: dict[str, int] = {
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-zh-v1.5": 768,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-zh-v1.5": 1024,
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-m3": 1024,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
}


def get_config() -> AgentConfig:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = AgentConfig()
        # 迁移：如果 .env 中残留旧的智谱 embedding-3 / embedding-2 模型名，
        # 自动替换为本地默认模型（中文优化的 bge-small-zh-v1.5）
        if _config.llm_embedding_model in ("embedding-3", "embedding-2", "Embedding-3"):
            _config.llm_embedding_model = "BAAI/bge-small-zh-v1.5"
    return _config


def reset_config():
    """重置配置单例（用于测试）"""
    global _config
    _config = None
