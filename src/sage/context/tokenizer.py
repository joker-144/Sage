"""
Token 计数器 — 基于 tiktoken 精确计数

不同模型使用不同的 tokenizer:
  - DeepSeek / Qwen: 近似使用 cl100k_base（OpenAI 系）
  - OpenAI gpt-4o: o200k_base
  - 无 tiktoken 时降级为字符数估算（4 字符 ≈ 1 token）
"""
from __future__ import annotations

import json
from functools import lru_cache


@lru_cache(maxsize=4)
def _get_encoder(model: str):
    """获取 tiktoken 编码器（带缓存）

    DeepSeek / Qwen 兼容 OpenAI 协议，近似使用 cl100k_base。
    """
    try:
        import tiktoken
        if "gpt-4o" in model or "o1" in model:
            return tiktoken.get_encoding("o200k_base")
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def count_tokens(text: str, model: str = "deepseek-chat") -> int:
    """精确计算文本的 token 数

    优先使用 tiktoken，不可用时降级为字符数估算（4 字符 ≈ 1 token）
    """
    if not text:
        return 0
    encoder = _get_encoder(model)
    if encoder is not None:
        return len(encoder.encode(text))
    # 降级估算
    return max(1, len(text) // 4)


def count_messages_tokens(messages: list[dict], model: str = "deepseek-chat") -> int:
    """计算消息列表的总 token 数（含每条消息的固定开销）

    OpenAI 协议中每条消息有约 4 token 的固定开销
    """
    total = 0
    for msg in messages:
        # 每条消息固定开销: role + 结构约 4 token
        total += 4
        for key, value in msg.items():
            if isinstance(value, str):
                total += count_tokens(value, model)
            elif isinstance(value, list):
                # tool_calls 等结构
                total += count_tokens(json.dumps(value, ensure_ascii=False), model)
    total += 2  # 结尾辅助 token
    return total
