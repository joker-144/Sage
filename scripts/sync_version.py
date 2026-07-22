"""
sync_version.py — 统一版本号同步脚本

将项目根的 VERSION 文件同步到以下位置：
  - web/package.json       (electron-builder 打包时必读)
  - web/electron/main.cjs  (若发现硬编码兜底版本则修正)
  - src/sage/__init__.py   (若存在硬编码兜底版本则修正)

调用方式：
  python scripts/sync_version.py            # 仅当发现不一致时打印提示
  python scripts/sync_version.py --write    # 强制修正不一致项
  python scripts/sync_version.py --check    # CI 用：发现不一致则退出码 1

被 web/package.json 的 prebuild / prepack 钩子自动调用。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# ── 路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
PACKAGE_JSON = PROJECT_ROOT / "web" / "package.json"
ELECTRON_MAIN = PROJECT_ROOT / "web" / "electron" / "main.cjs"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


# ── 工具 ──

def read_version() -> str:
    if not VERSION_FILE.is_file():
        print(f"[ERROR] VERSION 文件不存在: {VERSION_FILE}", file=sys.stderr)
        sys.exit(2)
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def is_semver(v: str) -> bool:
    """宽松的 semver 校验：0.5.0 / 0.5.0-beta / 0.5.0-rc.1 / 1.0.0+build.1"""
    return bool(re.match(r"^\d+\.\d+\.\d+([\-+][\w\.\-]+)?$", v))


# ── 同步项 ──

def sync_package_json(version: str, write: bool) -> bool:
    """同步 web/package.json 的 version 字段"""
    if not PACKAGE_JSON.is_file():
        print(f"  [SKIP] {PACKAGE_JSON.relative_to(PROJECT_ROOT)} 不存在")
        return True

    data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    current = data.get("version", "")

    if current == version:
        print(f"  [OK]   web/package.json version = {version}")
        return True

    print(f"  [DIFF] web/package.json version: {current!r} -> {version!r}")
    if write:
        data["version"] = version
        # 保持缩进与键序（使用原始 indent，若文件无 indent 则用 2 空格）
        indent = 4 if "\n    " in PACKAGE_JSON.read_text(encoding="utf-8") else 2
        PACKAGE_JSON.write_text(
            json.dumps(data, ensure_ascii=False, indent=indent) + "\n",
            encoding="utf-8",
        )
        print(f"  [DONE] web/package.json 已更新")
    return False


def sync_electron_main(version: str, write: bool) -> bool:
    """同步 web/electron/main.cjs 中的兜底版本"""
    if not ELECTRON_MAIN.is_file():
        print(f"  [SKIP] {ELECTRON_MAIN.relative_to(PROJECT_ROOT)} 不存在")
        return True

    text = ELECTRON_MAIN.read_text(encoding="utf-8")

    # 匹配兜底 return 'x.y.z';
    pattern = re.compile(r"return\s+'(\d+\.\d+\.\d+[\w\.\-\+]*)'\s*;")
    matches = list(pattern.finditer(text))

    if not matches:
        print(f"  [OK]   web/electron/main.cjs 无硬编码兜底版本")
        return True

    all_match = all(m.group(1) == version for m in matches)
    if all_match:
        print(f"  [OK]   web/electron/main.cjs 兜底版本 = {version}")
        return True

    print(f"  [DIFF] web/electron/main.cjs 兜底版本: {[m.group(1) for m in matches]} -> {version!r}")
    if write:
        new_text = pattern.sub(f"return '{version}';", text)
        ELECTRON_MAIN.write_text(new_text, encoding="utf-8")
        print(f"  [DONE] web/electron/main.cjs 已更新")
    return False


def sync_dashboard_view(version: str, write: bool) -> bool:
    """同步 web/src/components/DashboardView.vue 中硬编码的 API 版本"""
    dashboard = PROJECT_ROOT / "web" / "src" / "components" / "DashboardView.vue"
    if not dashboard.is_file():
        print(f"  [SKIP] DashboardView.vue 不存在")
        return True

    text = dashboard.read_text(encoding="utf-8")
    # 匹配 apiVersion: '0.5.0' 或 h.version || '0.5.0'
    patterns = [
        re.compile(r"apiVersion:\s*'(\d+\.\d+\.\d+[\w\.\-\+]*)'"),
        re.compile(r"h\.version\s*\|\|\s*'(\d+\.\d+\.\d+[\w\.\-\+]*)'"),
    ]

    found = False
    new_text = text
    for pat in patterns:
        for m in pat.finditer(text):
            found = True
            if m.group(1) != version:
                print(f"  [DIFF] DashboardView.vue 硬编码: {m.group(1)!r} -> {version!r}")
                if write:
                    new_text = pat.sub(lambda mo, v=version: mo.group(0).replace(mo.group(1), v), new_text)

    if not found:
        print(f"  [OK]   DashboardView.vue 无硬编码版本")
        return True

    if new_text != text and write:
        dashboard.write_text(new_text, encoding="utf-8")
        print(f"  [DONE] DashboardView.vue 已更新")
    return new_text == text


def sync_pyproject(version: str) -> bool:
    """pyproject.toml 通过 dynamic version 自动读取，无需同步"""
    if not PYPROJECT.is_file():
        return True
    text = PYPROJECT.read_text(encoding="utf-8")
    if 'attr = "sage.__version__"' in text:
        print(f"  [OK]   pyproject.toml 动态读取 sage.__version__ (VERSION 文件)")
        return True
    print(f"  [WARN] pyproject.toml 未使用 dynamic version，请检查")
    return True


# ── 主流程 ──

def main() -> int:
    parser = argparse.ArgumentParser(description="统一同步 Sage 系统版本号")
    parser.add_argument("--write", action="store_true", help="自动修正不一致项")
    parser.add_argument("--check", action="store_true", help="CI 模式：发现不一致退出码 1")
    args = parser.parse_args()

    # 默认模式：仅在 VERSION 与 package.json 不一致时打印并自动修复
    if not args.check and not args.write:
        args.write = True

    version = read_version()
    if not is_semver(version):
        print(f"[ERROR] VERSION 文件内容不是合法的 semver: {version!r}", file=sys.stderr)
        return 2

    print(f"Sage 系统版本同步 — VERSION = {version}\n")

    results = [
        sync_package_json(version, args.write),
        sync_electron_main(version, args.write),
        sync_dashboard_view(version, args.write),
        sync_pyproject(version),
    ]

    print()
    if all(results):
        print(f"[OK] 全部一致 ({version})")
        return 0
    else:
        msg = "[DIFF] 存在不一致项"
        if args.write:
            msg += "（已自动修正）"
        elif args.check:
            msg += "（CI 检查失败）"
        print(msg)
        return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
