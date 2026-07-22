"""
bump_version.py — Sage 系统版本号提升工具

修改项目根的 VERSION 文件，并自动级联到 web/package.json、electron/main.cjs 等位置。

支持语义化版本号（semver）：
  major  — 不兼容的 API 变更
  minor  — 向后兼容的新功能
  patch  — 向后兼容的 bug 修复
  pre    — 预发布版本（alpha/beta/rc）

调用方式：
  python scripts/bump_version.py patch               # 0.5.0 -> 0.5.1
  python scripts/bump_version.py minor               # 0.5.0 -> 0.6.0
  python scripts/bump_version.py major               # 0.5.0 -> 1.0.0
  python scripts/bump_version.py pre --tag beta      # 0.5.0 -> 0.5.0-beta
  python scripts/bump_version.py set 1.2.3           # 直接设置版本
  python scripts/bump_version.py set 1.2.3-rc.1      # 支持预发布后缀
  python scripts/bump_version.py --show              # 仅查看当前版本

提升完成后会自动调用 sync_version.py 同步到所有目标位置。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
CHANGELOG_FILE = PROJECT_ROOT / "CHANGELOG.md"


SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([\-+])([\w\.\-]+))?$")


def parse_version(v: str) -> tuple[int, int, int, str | None, str | None]:
    """解析 semver 为 (major, minor, patch, prefix, suffix)"""
    m = SEMVER_PATTERN.match(v)
    if not m:
        raise ValueError(f"非法的 semver: {v!r}")
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    prefix, suffix = m.group(4), m.group(5)
    return major, minor, patch, prefix, suffix


def format_version(major: int, minor: int, patch: int, prefix: str | None, suffix: str | None) -> str:
    out = f"{major}.{minor}.{patch}"
    if prefix and suffix:
        out += f"{prefix}{suffix}"
    return out


def bump(current: str, kind: str, tag: str | None = None) -> str:
    """根据 kind 提升版本号"""
    major, minor, patch, prefix, suffix = parse_version(current)

    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if kind == "pre":
        # 预发布：保持当前版本号，添加或递增预发布标签
        if not tag:
            tag = "rc.1" if prefix == "-" else "rc"
        if prefix == "-" and suffix:
            # 已存在预发布版本：递增数字后缀
            m = re.match(r"^(\w+)\.?(\d+)$", suffix)
            if m:
                base, num = m.group(1), int(m.group(2))
                return f"{major}.{minor}.{patch}-{base}.{num + 1}"
        # 新预发布
        suffix_to_use = tag if not re.match(r"^\w+\.\d+$", tag) else tag
        return f"{major}.{minor}.{patch}-{suffix_to_use}"

    raise ValueError(f"未知的 bump kind: {kind}")


def write_version(v: str):
    VERSION_FILE.write_text(v + "\n", encoding="utf-8")


def append_changelog(v: str, kind: str):
    """向 CHANGELOG.md 追加新版本条目（若文件存在）"""
    if not CHANGELOG_FILE.is_file():
        return
    text = CHANGELOG_FILE.read_text(encoding="utf-8")
    today = datetime.now().strftime("%Y-%m-%d")
    entry = f"\n## [{v}] - {today}\n\n- 版本提升（{kind}）\n"
    # 插入到文件开头（在第一个 ## 条目之前），或追加到末尾
    lines = text.splitlines(keepends=True)
    insert_idx = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_idx = i
            break
    if insert_idx is None:
        new_text = text.rstrip() + "\n" + entry
    else:
        new_text = "".join(lines[:insert_idx]) + entry + "".join(lines[insert_idx:])
    CHANGELOG_FILE.write_text(new_text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sage 系统版本号提升工具")
    parser.add_argument("action", nargs="?", choices=["major", "minor", "patch", "pre", "set", "show"],
                        help="操作类型")
    parser.add_argument("version", nargs="?", help="set 操作时指定新版本号")
    parser.add_argument("--tag", help="预发布标签（如 beta/rc/alpha）")
    parser.add_argument("--no-sync", action="store_true", help="不同步到 package.json 等位置")
    parser.add_argument("--no-changelog", action="store_true", help="不追加 CHANGELOG 条目")
    args = parser.parse_args()

    if not VERSION_FILE.is_file():
        print(f"[ERROR] VERSION 文件不存在: {VERSION_FILE}", file=sys.stderr)
        return 2

    current = VERSION_FILE.read_text(encoding="utf-8").strip()
    print(f"当前版本: {current}")

    # 仅查看
    if args.action == "show" or args.action is None:
        try:
            parse_version(current)
            print(f"semver 格式有效")
        except ValueError as e:
            print(f"[WARN] {e}", file=sys.stderr)
            return 1
        return 0

    # 直接设置
    if args.action == "set":
        if not args.version:
            print("[ERROR] set 操作需要指定版本号", file=sys.stderr)
            return 2
        try:
            parse_version(args.version)
        except ValueError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            return 1
        new_version = args.version
    else:
        try:
            new_version = bump(current, args.action, tag=args.tag)
        except ValueError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            return 1

    print(f"新版本:   {new_version}")

    # 确认（非 --yes 模式）
    if not args.no_sync and not args.no_changelog:
        resp = input("确认写入？[y/N] ").strip().lower()
        if resp != "y":
            print("已取消")
            return 0

    write_version(new_version)
    print(f"[DONE] VERSION 文件已更新为 {new_version}")

    if not args.no_changelog:
        append_changelog(new_version, args.action)
        if CHANGELOG_FILE.is_file():
            print(f"[DONE] CHANGELOG.md 已追加条目")

    if not args.no_sync:
        # 调用 sync_version.py 同步到所有位置
        import subprocess
        sync_script = PROJECT_ROOT / "scripts" / "sync_version.py"
        result = subprocess.run(
            [sys.executable, str(sync_script)],
            cwd=PROJECT_ROOT,
        )
        if result.returncode != 0:
            print(f"[WARN] sync_version.py 返回非零退出码: {result.returncode}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
