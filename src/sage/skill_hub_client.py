"""
内置 SkillHub 客户端 — 远程技能搜索/下载/安装

完全内置到系统中，不依赖外部 skillhub CLI。

数据源:
  - 技能索引: https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com/skills.json
  - 主下载:   https://lightmake.site/api/v1/download?slug={slug}
  - 备下载:   https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com/skills/{slug}.zip
  - 搜索:     https://lightmake.site/api/v1/search

流程:
  search  → 远程搜索 API → 返回候选技能列表
  install → 1) 从主 URL 下载 zip → 2) 解压到 skills 目录 → 3) 校验 skill.json
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

# ── SkillHub 远程端点（从 skillhub CLI 的 defaults 提取）──

SKILLS_INDEX_URL = (
    "https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com/skills.json"
)
PRIMARY_DOWNLOAD_URL = "https://lightmake.site/api/v1/download?slug={slug}"
FALLBACK_DOWNLOAD_URL = (
    "https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com/skills/{slug}.zip"
)
SEARCH_URL = "https://lightmake.site/api/v1/search"

DEFAULT_TIMEOUT = 30.0


@dataclass
class RemoteSkill:
    """远程技能信息"""
    slug: str
    name: str = ""
    description: str = ""
    version: str = ""
    author: str = ""
    tags: list[str] = None
    download_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "tags": self.tags or [],
            "download_url": self.download_url,
        }


class SkillHubClient:
    """内置 SkillHub 客户端 — 直接 HTTP 调用远程服务"""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._index_cache: list[dict] | None = None

    async def search(self, query: str, limit: int = 20) -> list[RemoteSkill]:
        """远程搜索技能

        Args:
            query: 搜索关键词
            limit: 返回数量限制
        """
        try:
            import httpx
        except ImportError:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # 先尝试主搜索 API
                try:
                    resp = await client.get(
                        SEARCH_URL,
                        params={"q": query, "limit": limit},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data if isinstance(data, list) else data.get("results", data.get("skills", []))
                        return [self._parse_remote_skill(item) for item in items[:limit]]
                except Exception:
                    pass

                # 回退: 从 skills.json 索引中本地匹配
                return await self._search_from_index(query, limit)

        except Exception as e:
            # 网络异常时尝试本地索引匹配
            return await self._search_from_index(query, limit)

    async def _search_from_index(self, query: str, limit: int) -> list[RemoteSkill]:
        """从技能索引文件中本地匹配"""
        try:
            import httpx
        except ImportError:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(SKILLS_INDEX_URL)
                resp.raise_for_status()
                self._index_cache = resp.json()
        except Exception:
            return []

        if not isinstance(self._index_cache, list):
            return []

        # 简单的关键词匹配
        query_lower = query.lower()
        results = []
        for item in self._index_cache:
            text = " ".join([
                str(item.get("slug", "")),
                str(item.get("name", "")),
                str(item.get("description", "")),
                " ".join(item.get("tags", []) or []),
            ]).lower()
            if not query_lower or query_lower in text:
                results.append(self._parse_remote_skill(item))
                if len(results) >= limit:
                    break
        return results

    def _parse_remote_skill(self, item: dict) -> RemoteSkill:
        """解析远程技能数据为 RemoteSkill 对象"""
        return RemoteSkill(
            slug=item.get("slug") or item.get("id") or item.get("name", ""),
            name=item.get("name", ""),
            description=item.get("description", ""),
            version=item.get("version", ""),
            author=item.get("author", ""),
            tags=item.get("tags", []) or [],
            download_url=item.get("download_url") or item.get("zip_url", ""),
        )

    async def download_and_install(
        self,
        slug: str,
        target_dir: Path,
        force: bool = False,
    ) -> dict[str, Any]:
        """下载并安装技能到目标目录

        Args:
            slug: 技能标识符
            target_dir: 目标目录（.agent/skills/）
            force: 是否覆盖已存在的目录

        Returns:
            {
                "success": bool,
                "path": str,       # 安装到的完整路径
                "skill_json": dict, # 解析的 skill.json
                "error": str,       # 错误信息
            }
        """
        if not slug:
            return {"success": False, "error": "缺少技能 slug"}

        target = Path(target_dir) / slug
        if target.exists():
            if not force:
                return {
                    "success": False,
                    "error": f"技能已存在: {target}。使用 force=True 覆盖",
                }
            shutil.rmtree(target, ignore_errors=True)

        # 1. 下载 zip 包
        zip_bytes = await self._download_skill(slug)
        if zip_bytes is None:
            return {"success": False, "error": f"无法下载技能 '{slug}'"}

        # 2. 解压到目标目录
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
                zf.extractall(target)
        except zipfile.BadZipFile as e:
            return {"success": False, "error": f"下载的文件不是有效的 zip: {e}"}
        except Exception as e:
            return {"success": False, "error": f"解压失败: {e}"}

        # 3. 校验 skill.json（支持多种清单格式）
        skill_json = self._find_skill_metadata(target)
        if skill_json is None:
            shutil.rmtree(target, ignore_errors=True)
            return {
                "success": False,
                "error": "技能包中未找到 skill.json / _meta.json / SKILL.md 元数据",
            }

        # 4. 规范化：确保 target 中存在系统所需的 skill.json
        #    （下载的技能可能只含 SKILL.md，需生成兼容的 skill.json）
        target_skill_json = target / "skill.json"
        if not target_skill_json.exists():
            normalized = self._normalize_skill_json(skill_json, slug)
            try:
                target_skill_json.write_text(
                    json.dumps(normalized, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                shutil.rmtree(target, ignore_errors=True)
                return {
                    "success": False,
                    "error": f"生成 skill.json 失败: {e}",
                }
            # 用规范化后的元数据
            skill_json = normalized

        return {
            "success": True,
            "path": str(target),
            "skill_json": skill_json,
        }

    def _normalize_skill_json(self, meta: dict, slug: str) -> dict:
        """将各种格式的元数据规范化为系统所需的 skill.json

        字段:
          - name        (string, required)
          - version     (string)
          - description (string)
          - capabilities (list of string)
          - tools        (list of string)
          - trigger_conditions (list of string)
        """
        return {
            "name": meta.get("name") or meta.get("slug") or slug,
            "version": str(meta.get("version", "1.0.0")),
            "description": meta.get("description", ""),
            "capabilities": self._extract_capabilities(meta),
            "tools": meta.get("tools", []) if isinstance(meta.get("tools"), list) else [],
            "trigger_conditions": self._extract_trigger_conditions(meta),
        }

    def _extract_capabilities(self, meta: dict) -> list[str]:
        """从元数据中提取能力列表"""
        # 优先取显式的 capabilities 字段
        caps = meta.get("capabilities")
        if isinstance(caps, list) and caps:
            return [str(c) for c in caps]
        # 从 SKILL.md 正文第一段提取要点
        # 这里简单返回空，由 description 兜底
        return []

    def _extract_trigger_conditions(self, meta: dict) -> list[str]:
        """从元数据中提取调用时机"""
        triggers = meta.get("trigger_conditions")
        if isinstance(triggers, list) and triggers:
            return [str(t) for t in triggers]
        return []

    def _find_skill_metadata(self, target: Path) -> dict | None:
        """在解压目录中查找技能元数据，支持多种格式

        优先级:
          1. skill.json (标准格式)
          2. SKILL.md (Claude Skills 格式) + YAML frontmatter — 含完整信息
          3. _meta.json (skillhub CLI 格式) — 只有 slug/version
        """
        import re

        # 1. skill.json（标准格式，最优先）
        skill_json_path = target / "skill.json"
        if skill_json_path.exists():
            try:
                return json.loads(skill_json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 2. SKILL.md — frontmatter 含完整描述
        for path in target.rglob("SKILL.md"):
            try:
                content = path.read_text(encoding="utf-8")
                m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
                if m:
                    yaml_text = m.group(1)
                    parsed = self._parse_simple_yaml(yaml_text)
                    if parsed.get("name") or parsed.get("description"):
                        return parsed
            except Exception:
                continue

        # 3. _meta.json（只有 slug/version）— 与 SKILL.md 合并
        meta = None
        for path in target.rglob("_meta.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
                break
            except Exception:
                continue

        if meta is not None:
            # 如果 _meta.json 没有 name，尝试从 SKILL.md 补充
            if not meta.get("name"):
                for path in target.rglob("SKILL.md"):
                    try:
                        content = path.read_text(encoding="utf-8")
                        m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
                        if m:
                            parsed = self._parse_simple_yaml(m.group(1))
                            if parsed.get("name"):
                                meta["name"] = parsed["name"]
                            if parsed.get("description") and not meta.get("description"):
                                meta["description"] = parsed["description"]
                    except Exception:
                        pass
                    break
            return meta

        return None

    def _parse_simple_yaml(self, text: str) -> dict:
        """极简 YAML 解析（仅支持 key: value 对和列表）

        不依赖 PyYAML，避免额外依赖。
        """
        result: dict[str, Any] = {}
        current_key = None
        current_list = None

        for line in text.split("\n"):
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith("#"):
                continue

            # 列表项
            if line_stripped.startswith("- "):
                if current_list is not None:
                    item = line_stripped[2:].strip().strip('"').strip("'")
                    current_list.append(item)
                continue

            # key: value 对
            if ":" in line_stripped:
                key, _, value = line_stripped.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if not value:
                    # 多行列表开始
                    current_key = key
                    current_list = []
                    result[key] = current_list
                else:
                    result[key] = value
                    current_key = None
                    current_list = None

        return result

    async def _download_skill(self, slug: str) -> bytes | None:
        """下载技能 zip 包

        尝试顺序: 主下载 URL → 备用下载 URL
        """
        try:
            import httpx
        except ImportError:
            return None

        urls = [
            PRIMARY_DOWNLOAD_URL.format(slug=slug),
            FALLBACK_DOWNLOAD_URL.format(slug=slug),
        ]

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "Sage/1.0"},
        ) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200 and resp.content:
                        # 验证是 zip 文件
                        if resp.content[:2] == b"PK":
                            return resp.content
                except Exception:
                    continue

        return None
