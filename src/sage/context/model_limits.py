"""模型上下文窗口内置映射表

由于各家 OpenAI 兼容供应商的 /models 接口均不返回上下文长度字段，
这里维护一份内置的 model_id → 上下文窗口（token）映射表作为兜底，
用于动态计算压缩触发阈值（默认取窗口的 80%）。

数据来源：各供应商官方文档定价/模型页，社区维护的 llm-context-limits 汇总。

匹配规则：
  1. 先精确匹配 model_id；
  2. 未命中时按前缀（startswith）匹配 —— 列表靠前的更具体前缀优先。
未命中任何条目时返回 None，由调用方回退到配置默认值。
"""
from __future__ import annotations

# 压缩触发阈值默认取上下文窗口的比例（预留 20% 给输出与 system prompt）
COMPRESSION_TRIGGER_RATIO = 0.8

# (model_id 前缀, 上下文窗口 token 数)
# 注意：更具体的版本前缀需排在更宽泛的前缀之前，避免误命中。
MODEL_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    # ── DeepSeek ──
    ("deepseek-v4-flash", 1_000_000),
    ("deepseek-v4-pro", 1_000_000),
    ("deepseek-v4", 1_000_000),
    ("deepseek-chat", 128_000),
    ("deepseek-reasoner", 128_000),

    # ── OpenAI ──
    ("gpt-4.1-mini", 1_047_576),
    ("gpt-4.1-nano", 1_047_576),
    ("gpt-4.1", 1_047_576),
    ("gpt-5.1", 400_000),
    ("gpt-5.2", 400_000),
    ("gpt-5.4", 400_000),
    ("gpt-5.5", 400_000),
    ("gpt-5-mini", 400_000),
    ("gpt-5-nano", 400_000),
    ("gpt-5", 400_000),
    ("gpt-4o-mini", 128_000),
    ("gpt-4o", 128_000),
    ("o1", 200_000),
    ("o3", 200_000),
    ("o4-mini", 200_000),

    # ── Anthropic ──
    ("claude", 200_000),

    # ── 通义千问 Qwen ──
    ("qwen-max-longcontext", 1_000_000),
    ("qwen3.6-plus", 1_000_000),
    ("qwen3.7-max", 1_000_000),
    ("qwen3.7-flash", 1_000_000),
    ("qwen-max", 128_000),
    ("qwen-plus", 128_000),
    ("qwen-turbo", 1_000_000),
    ("qwen-long", 1_000_000),

    # ── 智谱 GLM ──
    ("glm-5.2", 1_000_000),
    ("glm-5.1", 200_000),
    ("glm-5", 200_000),
    ("glm-4.6", 200_000),
    ("glm-4.7", 200_000),
    ("glm-4", 128_000),

    # ── 月之暗面 Moonshot / Kimi ──
    ("moonshot-v1-8k", 8_192),
    ("moonshot-v1-32k", 32_768),
    ("moonshot-v1-128k", 131_072),
    ("kimi", 256_000),
]


def get_context_window(model_id: str | None) -> int | None:
    """根据 model_id 查询上下文窗口 token 数，未命中返回 None。

    model_id 可能带版本后缀（如 glm-4.6 / deepseek-v4-flash），
    因此先精确匹配，再按表内顺序做前缀匹配。
    """
    if not model_id:
        return None
    key = model_id.strip().lower()

    # 1. 精确匹配
    for prefix, window in MODEL_CONTEXT_WINDOWS:
        if key == prefix:
            return window

    # 2. 前缀匹配（表内顺序，更具体的前缀已排在前）
    for prefix, window in MODEL_CONTEXT_WINDOWS:
        if key.startswith(prefix):
            return window

    return None


def get_compression_trigger(
    model_id: str | None,
    fallback: int,
    ratio: float = COMPRESSION_TRIGGER_RATIO,
) -> int:
    """计算压缩触发阈值 = 上下文窗口 × ratio。

    - 命中映射表：返回 int(window * ratio)
    - 未命中：返回 fallback（即调用方传入的当前配置默认值）
    """
    window = get_context_window(model_id)
    if window is None:
        return fallback
    return int(window * ratio)