"""
反思与自我修正机制 — Reflection & Self-Correction

参考 ReAct / CoT / Self-Reflection 模式，
在 Agent 完成工具调用后增加反思环节。

核心流程:
  1. 工具执行完成 → 检查结果
  2. 结果异常/失败 → 触发反思
  3. 分析失败原因 → 生成修正策略
  4. 重新执行 → 验证修正结果
  5. 达到最大重试次数 → 汇报给用户

反思维度:
  - 工具执行是否成功？
  - 输出是否符合预期格式/质量？
  - 是否需要补充信息？
  - 是否有更优的方案？
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ReflectionTrigger(Enum):
    """反思触发条件"""
    TOOL_FAILED = "tool_failed"          # 工具执行失败
    EMPTY_RESULT = "empty_result"        # 工具返回空结果
    LOW_QUALITY = "low_quality"          # 输出质量不高（启发式判断）
    CONTRADICTION = "contradiction"      # 与之前结果矛盾
    INCOMPLETE = "incomplete"            # 任务未完全完成
    LLM_ERROR = "llm_error"              # LLM 调用失败
    TIMEOUT = "timeout"                  # 超时


@dataclass
class ReflectionResult:
    """反思结果"""
    needs_correction: bool
    trigger: Optional[ReflectionTrigger] = None
    analysis: str = ""           # 问题分析
    correction_strategy: str = ""  # 修正策略
    retry_prompt: str = ""       # 重试时的额外提示
    suggestion: str = ""         # 给用户的建议（当无法自动修正时）


@dataclass
class ReflectionContext:
    """反思上下文 — 记录每一步操作以供反思"""
    step: int
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    result: str = ""
    success: bool = True
    error: str = ""
    timestamp: float = 0.0


class ReflectionEngine:
    """反思引擎 — 分析工具执行结果，决定是否需要修正

    启发式规则 + 模式匹配，不依赖额外 LLM 调用（节省 token）。
    对于复杂情况，可以触发 LLM 辅助反思（可选）。
    """

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries
        self._contexts: list[ReflectionContext] = []
        self._retry_counts: dict[str, int] = {}  # tool_name -> retries

    def record(self, ctx: ReflectionContext):
        """记录执行上下文"""
        self._contexts.append(ctx)
        # 清理过旧的上下文（保留最近 20 条）
        if len(self._contexts) > 20:
            self._contexts = self._contexts[-20:]

    def reflect(
        self,
        tool_name: str,
        result: str,
        success: bool,
        error: str = "",
    ) -> ReflectionResult:
        """分析工具执行结果，决定是否需要修正

        Args:
            tool_name: 工具名
            result: 工具返回内容
            success: 是否成功
            error: 错误信息

        Returns:
            ReflectionResult: 反思结论和修正建议
        """
        # 1. 成功 → 无需修正
        if success and result and not self._is_low_quality(result):
            return ReflectionResult(needs_correction=False)

        # 2. 确定性错误不重试（相同参数重试必然重复失败）
        #    P1-3：措辞对齐 file_ops/engine/paper_ops 的真实错误返回 ——
        #    补充遗漏项（"不是目录"/"未在文件中找到匹配"/"匹配多处"/"路径不存在"），
        #    收窄无效宽泛单字（"参数"→由"参数错误"覆盖；"路径"→精确为"路径不存在"；
        #    "Permission"→"permission denied"）。保留"未找到"（实证匹配
        #    paper_ops"未找到标记"、mcp/engine"未找到工具"，均为确定性错误）。
        #    不采用 core.resilience.classify_error：它面向英文 API/网络错误域，
        #    对中文工具错误（"文件不存在"等）一律 fallback 为 RETRYABLE，不适用于此。
        _non_retryable_markers = (
            # 文件 / 目录 / 路径（file_ops 真实措辞）
            "文件不存在", "路径不存在", "不是目录", "no such file",
            # 编辑定位失败（edit_file：相同 old_str 重试必然仍不匹配）
            "未在文件中找到匹配", "匹配多处",
            # 引用标记 / 工具不存在（paper_ops"未找到标记"、engine/mcp"未找到工具"）
            "未找到", "未知工具",
            # 参数错误（engine 将 TypeError 包装为 "参数错误: ..."）
            "参数错误",
            # 权限
            "权限不足", "permission denied",
        )
        if error:
            error_l = error.lower()
            if any(m.lower() in error_l for m in _non_retryable_markers):
                return ReflectionResult(
                    needs_correction=False,
                    trigger=ReflectionTrigger.TOOL_FAILED,
                    analysis=f"工具 {tool_name} 失败（确定性错误，不重试）: {error}",
                    suggestion=f"路径/参数/权限/定位问题，Agent 需先校验或修正参数后再调用: {error[:200]}",
                )

        # 3. 检查重试次数
        current_retries = self._retry_counts.get(tool_name, 0)
        if current_retries >= self.max_retries:
            return ReflectionResult(
                needs_correction=False,
                trigger=ReflectionTrigger.TOOL_FAILED,
                analysis=f"工具 {tool_name} 已重试 {current_retries} 次，超过上限",
                suggestion=f"无法自动修复 {tool_name} 的问题，请手动检查",
            )

        self._retry_counts[tool_name] = current_retries + 1

        # 4. 分析具体的失败类型
        trigger, analysis, strategy, retry_prompt = self._analyze(
            tool_name, result, success, error
        )

        return ReflectionResult(
            needs_correction=True,
            trigger=trigger,
            analysis=analysis,
            correction_strategy=strategy,
            retry_prompt=retry_prompt,
        )

    def _analyze(
        self, tool_name: str, result: str, success: bool, error: str
    ) -> tuple[ReflectionTrigger, str, str, str]:
        """启发式分析失败原因"""

        # 工具执行失败
        if not success:
            trigger = ReflectionTrigger.TOOL_FAILED
            analysis = f"工具 {tool_name} 执行失败: {error}"

            # 文件不存在 → 建议先 list_dir
            if "文件不存在" in error or "No such file" in error:
                strategy = "路径可能有误，建议先用 list_dir 确认文件位置"
                retry_prompt = f"工具 {tool_name} 执行失败: {error}。请先确认文件路径是否正确。"
            # 参数错误
            elif "参数" in error:
                strategy = "参数格式有误，检查参数类型和必填项"
                retry_prompt = f"参数错误: {error}。请检查并修正参数后重试。"
            # 权限问题
            elif "权限" in error or "Permission" in error:
                strategy = "权限不足，跳过该操作"
                retry_prompt = ""
            else:
                strategy = f"重新尝试 {tool_name}，修正错误原因"
                retry_prompt = f"上一次 {tool_name} 执行失败: {error}。请修正后重试。"

            return trigger, analysis, strategy, retry_prompt

        # 空结果
        if not result or result.strip() == "":
            trigger = ReflectionTrigger.EMPTY_RESULT
            analysis = f"工具 {tool_name} 返回空结果"
            strategy = "尝试扩大搜索范围或调整参数"
            retry_prompt = f"工具 {tool_name} 返回空结果。请尝试调整参数或扩大范围。"

            return trigger, analysis, strategy, retry_prompt

        # 搜索未找到
        if "未找到" in result and tool_name in ("search_code",):
            trigger = ReflectionTrigger.EMPTY_RESULT
            analysis = f"搜索 {tool_name} 未找到匹配结果"
            strategy = "尝试更换关键词或扩展搜索范围"
            retry_prompt = "上一次搜索未找到结果。尝试用更通用的关键词或搜索相关概念。"

            return trigger, analysis, strategy, retry_prompt

        # 命令执行失败（exit code != 0）
        if "exit_code" in result.lower() and "exit_code: 1" in result.lower():
            trigger = ReflectionTrigger.TOOL_FAILED
            analysis = "命令执行返回非零退出码"
            strategy = "检查命令语法、依赖是否安装、路径是否正确"
            retry_prompt = f"命令执行失败。请检查: 1) 命令语法 2) 依赖是否安装 3) 路径是否正确\n错误输出: {result[:300]}"

            return trigger, analysis, strategy, retry_prompt

        # 默认：低质量结果
        trigger = ReflectionTrigger.LOW_QUALITY
        return trigger, "结果可能不完整", "重新执行并要求更详细的输出", "请提供更详细的输出"

    def _is_low_quality(self, result: str) -> bool:
        """启发式判断结果质量是否过低"""
        if not result:
            return True
        # 太短且含错误关键词
        if len(result) < 20:
            return True
        return False

    def get_context_summary(self) -> str:
        """生成反思上下文摘要（供 LLM 使用）"""
        if not self._contexts:
            return ""
        lines = ["## 操作历史\n"]
        for ctx in self._contexts[-10:]:
            status = "成功" if ctx.success else "失败"
            lines.append(f"- [{status}] {ctx.tool_name}({ctx.tool_args}): {ctx.result[:100]}")
        return "\n".join(lines)

    def reset(self):
        """重置反思状态（新对话开始时）"""
        self._contexts.clear()
        self._retry_counts.clear()


def create_reflection_engine(max_retries: int = 2) -> ReflectionEngine:
    """创建反思引擎（工厂函数）"""
    return ReflectionEngine(max_retries=max_retries)
