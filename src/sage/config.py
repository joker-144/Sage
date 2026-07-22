"""
Sage 配置系统
基于 pydantic-settings，从 .env 和环境变量加载配置
单模型运行时，Provider 可切换（OpenAI 兼容协议）
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    """Sage 全局配置 — 统一从 .env 加载

    所有字段直接从 .env / 环境变量读取，避免嵌套模型的加载问题。
    LLM_CHAT_* 前缀对应对话模型配置，LLM_EMBEDDING_* 前缀对应 Embedding 配置。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
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
    memory_sqlite_path: str = Field(default="data/memory.db", validation_alias="MEMORY_SQLITE_PATH")

    # ── Embedding 配置（LLM_EMBEDDING_* 前缀）──
    # 本地 sentence-transformers 模型（all-MiniLM-L6-v2，384 维，约 80MB）
    # 首次使用时自动从 HuggingFace 下载（通过 hf-mirror 镜像加速）
    llm_embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        validation_alias="LLM_EMBEDDING_MODEL",
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

    def validate_api_keys(self) -> list[str]:
        """检查哪些 API Key 缺失"""
        missing = []
        if not self.llm_chat_api_key or "your-" in self.llm_chat_api_key:
            missing.append("对话 LLM (LLM_CHAT_API_KEY)")
        # 本地 Embedder 不需要 API Key
        return missing


_config: Optional[AgentConfig] = None


def get_config() -> AgentConfig:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = AgentConfig()
        # 迁移：如果 .env 中残留旧的智谱 embedding-3 模型名，自动替换为本地模型
        if _config.llm_embedding_model in ("embedding-3", "embedding-2", "Embedding-3"):
            _config.llm_embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
    return _config


def reset_config():
    """重置配置单例（用于测试）"""
    global _config
    _config = None
