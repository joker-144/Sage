#!/usr/bin/env python3
"""
生成 version.json — 自建版本信息源

从项目 VERSION 文件读取当前版本号，生成 version.json，
提交到 gitcode 仓库根目录后，应用会通过
https://raw.gitcode.com/wu_yout/Sage/raw/main/version.json
检查是否有新版本。

使用方法:
    python scripts/gen_version.py

可选参数（通过交互式输入或命令行）:
    --notes "更新说明"
    --download-url "下载链接"  (默认指向 gitcode releases)

生成文件位置: 项目根目录 version.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def read_version() -> str:
    """从 VERSION 文件读取当前版本号"""
    # scripts/gen_version.py → 上两级为项目根目录
    root = Path(__file__).parent.parent
    version_file = root / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"未找到 VERSION 文件: {version_file}")


def main():
    parser = argparse.ArgumentParser(description="生成 version.json 版本信息源")
    parser.add_argument(
        "--notes", default="",
        help="更新说明（发行版日志），可选"
    )
    parser.add_argument(
        "--download-url",
        default="https://gitcode.com/wu_yout/Sage/releases",
        help="下载链接，默认指向 gitcode releases 页面"
    )
    parser.add_argument(
        "--release-name", default="",
        help="发行版名称，可选（留空则使用版本号）"
    )
    args = parser.parse_args()

    version = read_version()
    release_name = args.release_name.strip() or f"v{version}"

    data = {
        "latest": version,
        "release_name": release_name,
        "release_notes": args.notes,
        "download_url": args.download_url,
    }

    root = Path(__file__).parent.parent
    output = root / "version.json"
    output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"已生成 {output}")
    print(f"  版本号: {version}")
    print(f"  发行名: {release_name}")
    print(f"  下载链接: {args.download_url}")
    if args.notes:
        print(f"  更新说明: {args.notes}")
    print()
    print("请将 version.json 提交到 gitcode 仓库:")
    print("  git add version.json")
    print("  git commit -m \"chore: 更新版本信息源到 {version}\"")
    print(f"  git push gitcode main")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
