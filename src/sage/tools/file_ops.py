"""
文件操作工具 — read_file, write_file, edit_file (diff), list_dir, delete_file

支持两种路径模式：
  - 相对路径：限制在 workspace 内（防止目录穿越）
  - 绝对路径：访问文件系统（C 盘限用户目录，其他盘自由）

delete_file 需用户确认后才执行（通过 token 机制）。
"""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from sage.tools.types import ToolResult


# ── 外部路径访问的安全边界 ──

# C 盘系统目录黑名单（绝对禁止访问）
_SYSTEM_BLACKLIST = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "recovery",
    "boot", "perflogs", "config.msi",
}

# C:\Users 下的系统保留用户名（禁止访问）
_SYSTEM_USERS = {"all users", "default", "default user", "public"}

# 待删除路径的 token 缓存（token -> (path, expire_at)）
_pending_deletes: dict[str, tuple[Path, float]] = {}


class FileOps:
    """文件操作工具 — 支持 workspace 相对路径和文件系统绝对路径"""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def _resolve(self, path: str) -> Path:
        """安全解析路径

        - 绝对路径：走外部文件系统校验（C 盘限用户目录，其他盘自由）
        - 相对路径：限制在 workspace 内（防止目录穿越）
        """
        p = Path(path)
        if p.is_absolute():
            return self._validate_external_path(p)
        # 相对路径：限制在 workspace 内
        clean = p.as_posix().lstrip("/")
        resolved = (self.workspace / clean).resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError:
            raise ValueError(f"路径越界: {path}")
        return resolved

    def _validate_external_path(self, path: Path) -> Path:
        r"""校验绝对路径是否在允许范围内

        允许：
          - C 盘：仅当前用户目录（Desktop/Documents/Downloads 等）
          - 其他盘符（D:/E: 等）：任意位置

        禁止：
          - C:\Windows, C:\Program Files* 等系统目录
          - C:\Users 下其他用户的目录
        """
        resolved = path.resolve()
        drive = resolved.drive.upper()  # "C:" / "D:" 等

        # 非 C 盘：自由访问
        if drive != "C:":
            return resolved

        # C 盘：解析第一级目录名判断归属
        parts = resolved.parts  # ('C:\\', 'Users', 'EDY', 'Desktop', ...)
        if len(parts) < 2:
            raise ValueError("禁止访问 C 盘根目录")

        first_part = parts[1].lower()

        # 黑名单系统目录
        if first_part in _SYSTEM_BLACKLIST:
            raise ValueError(f"禁止访问系统目录: {parts[1]}")

        # C:\Users 下的用户目录
        if first_part == "users":
            if len(parts) < 3:
                raise ValueError("禁止访问 C:\\Users 根目录")
            username = parts[2].lower()
            # 系统保留用户名
            if username in _SYSTEM_USERS:
                raise ValueError(f"禁止访问系统用户目录: {parts[2]}")
            # 仅允许当前用户
            current_user = os.environ.get("USERNAME", "") or os.environ.get("USER", "")
            if current_user and username != current_user.lower():
                raise ValueError(f"禁止访问其他用户目录: {parts[2]}")
            return resolved

        # C 盘其他一级目录（如 C:\Temp 等）：允许访问
        return resolved

    async def read_file(self, path: str, start_line: int = 0, end_line: int = 0) -> ToolResult:
        """读取文件完整内容。默认读取整个文件，无需传 start_line/end_line。仅在文件超过 2000 行时允许分段读取。"""
        try:
            target = self._resolve(path)
            if not target.exists():
                return ToolResult(success=False, error=f"文件不存在: {path}")
            content = target.read_text(encoding="utf-8")
            if start_line > 0 or end_line > 0:
                lines = content.splitlines()
                start = (start_line - 1) if start_line > 0 else 0
                end = end_line if end_line > 0 else len(lines)
                # 带行号输出
                numbered = []
                for i in range(start, min(end, len(lines))):
                    numbered.append(f"{i + 1}→{lines[i]}")
                content = "\n".join(numbered)
            return ToolResult(success=True, data=content)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def write_file(self, path: str, content: str) -> ToolResult:
        """创建或覆写文件（自动创建父目录）"""
        try:
            target = self._resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolResult(success=True, data=f"已写入: {path} ({len(content)} 字符)")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def edit_file(self, path: str, old_str: str, new_str: str) -> ToolResult:
        """diff 编辑 — 搜索替换

        old_str 必须是文件中唯一匹配的文本片段。
        若匹配多处，返回错误要求提供更多上下文。
        """
        try:
            target = self._resolve(path)
            if not target.exists():
                return ToolResult(success=False, error=f"文件不存在: {path}")
            content = target.read_text(encoding="utf-8")

            if old_str not in content:
                return ToolResult(success=False, error="old_str 未在文件中找到匹配")
            if content.count(old_str) > 1:
                return ToolResult(
                    success=False,
                    error="old_str 匹配多处，请提供更多上下文以唯一匹配",
                )

            new_content = content.replace(old_str, new_str, 1)
            target.write_text(new_content, encoding="utf-8")
            return ToolResult(success=True, data=f"已编辑: {path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def list_dir(self, path: str = ".") -> ToolResult:
        """列出目录内容"""
        try:
            target = self._resolve(path)
            if not target.is_dir():
                return ToolResult(success=False, error=f"不是目录: {path}")

            items = []
            for item in sorted(target.iterdir()):
                prefix = "[DIR] " if item.is_dir() else "[FILE]"
                items.append(f"{prefix} {item.name}")

            return ToolResult(success=True, data="\n".join(items))
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def delete_file(self, path: str, recursive: bool = False) -> ToolResult:
        """请求删除文件或目录（需用户确认后才实际执行）

        此方法不直接删除，而是生成一个 token 并缓存待删除路径，
        通过 SSE 推送确认请求到前端，用户确认后调用 confirm_delete 完成删除。
        """
        try:
            target = self._resolve(path)
            if not target.exists():
                return ToolResult(success=False, error=f"路径不存在: {path}")

            # 生成一次性 token
            token = secrets.token_urlsafe(16)
            import time
            _pending_deletes[token] = (target, time.time() + 300)  # 5 分钟过期

            # 返回确认请求（不实际删除）
            target_type = "目录" if target.is_dir() else "文件"
            return ToolResult(
                success=True,
                data=(
                    f"__DELETE_CONFIRM_REQUIRED__\n"
                    f"token: {token}\n"
                    f"path: {target}\n"
                    f"type: {target_type}\n"
                    f"recursive: {recursive}\n"
                    f"等待用户确认删除操作。"
                ),
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def confirm_delete(token: str) -> tuple[bool, str]:
    """用户确认后执行实际删除

    Returns:
        (success, message)
    """
    import time
    entry = _pending_deletes.pop(token, None)
    if entry is None:
        return False, "无效或已过期的删除确认 token"

    target, expire_at = entry
    if time.time() > expire_at:
        return False, "删除确认已过期，请重新发起删除请求"

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True, f"已删除: {target}"
    except Exception as e:
        return False, f"删除失败: {e}"
