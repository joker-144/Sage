"""
Provider 抽象层 — 通过 OpenAI 兼容协议统一不同 LLM 服务商

支持的 Provider（均兼容 OpenAI 协议）:
  - DeepSeek:  https://api.deepseek.com           model: deepseek-chat
  - Qwen:      https://dashscope.aliyuncs.com/compatible-mode/v1   model: qwen-plus
  - OpenAI:    https://api.openai.com/v1          model: gpt-4o
  - 本地:       http://localhost:11434/v1          model: qwen2.5

切换 Provider 只需修改 .env 中的 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    """Provider 预设"""
    name: str
    base_url: str
    default_model: str
    supports_function_calling: bool = True
    supports_streaming: bool = True


# 常用 Provider 预设（仅供参考，实际配置从 .env 读取）
PROVIDERS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
    ),
    "qwen": ProviderPreset(
        name="Qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
    ),
    "openai": ProviderPreset(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
    ),
    "local": ProviderPreset(
        name="Local",
        base_url="http://localhost:11434/v1",
        default_model="qwen2.5",
    ),
}


def get_preset(name: str) -> ProviderPreset:
    """获取 Provider 预设"""
    key = name.lower().strip()
    if key not in PROVIDERS:
        raise ValueError(f"未知 Provider: {name}，可选: {', '.join(PROVIDERS)}")
    return PROVIDERS[key]
