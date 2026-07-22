"""
API 入口 — 基于 FastAPI
提供 SSE 流式对话 + 对话管理 + 项目索引 + 记忆统计接口

同时托管 web/dist/ 静态界面（Vue 构建），访问根路径 / 即可使用。
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sage import __version__
from sage.config import get_config, reset_config

app = FastAPI(
    title="Sage API",
    description="Sage — 多智能体协作的学术论文写作辅助系统",
    version=__version__,
)

# CORS 允许前端直连后端（绕过 Vite 代理的 SSE 缓冲问题）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态 Web 界面托管（Vue 构建产物）
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后：多个可能的路径尝试
    _base = Path(sys._MEIPASS)
    for _p in [_base / "web" / "dist", _base / "_internal" / "web" / "dist"]:
        if _p.exists() and (_p / "index.html").exists():
            WEB_DIR = _p
            break
    else:
        WEB_DIR = _base / "web" / "dist"  # 默认路径
else:
    WEB_DIR = Path(__file__).parent.parent.parent / "web" / "dist"

# 全局 Agent 缓存 — 按 conversation_id 复用，实现多轮对话记忆
# key: conversation_id, value: AgentLoop 实例
_MAX_AGENTS = 50  # 缓存上限，防止内存无限增长
_agents: dict[str, "AgentLoop"] = {}


def _get_or_create_agent(conversation_id: str | None = None):
    """获取或创建 Agent（按 conversation_id 复用，保持多轮对话上下文）

    所有 LLM 配置从 .env 读取，前端设置通过 _save_user_settings 写入 .env。
    如果传入已有 conversation_id，会从 SQLite 恢复历史消息到 Agent 上下文。
    """
    from sage.agent.loop import create_agent

    if conversation_id and conversation_id in _agents:
        return _agents[conversation_id], conversation_id

    agent = create_agent(workspace=Path.cwd(), conversation_id=conversation_id)

    # 如果是已有对话（非新对话），从 DB 恢复历史消息到上下文
    if conversation_id:
        _restore_agent_context(agent, conversation_id)

    # 超过上限时淘汰最早的 Agent
    if len(_agents) >= _MAX_AGENTS:
        oldest = next(iter(_agents))
        del _agents[oldest]

    _agents[agent.conversation_id] = agent
    return agent, agent.conversation_id


def _restore_agent_context(agent, conversation_id: str):
    """从 SQLite 恢复历史消息到 Agent 的 ContextManager 中

    这样 Agent 在回答前就知道之前对话的全部内容，避免 AI 失忆。
    """
    import json as _json
    try:
        from sage.memory.store import get_store
        store = get_store()
        msgs = store.get_messages(conversation_id, limit=500)
        if not msgs:
            return

        for msg in msgs:
            role = msg.get("role")
            content = msg.get("content") or ""
            tool_name = msg.get("tool_name")
            tool_args_raw = msg.get("tool_args")
            tool_call_id = msg.get("tool_call_id")

            if role == "user":
                agent.context.add_user_message(content)
            elif role == "assistant":
                tool_calls = []
                if tool_args_raw:
                    try:
                        tool_calls = _json.loads(tool_args_raw)
                    except Exception:
                        pass
                agent.context.add_assistant_message(content, tool_calls if tool_calls else None)
            elif role == "tool":
                agent.context.add_tool_result(
                    tool_call_id=tool_call_id or "",
                    tool_name=tool_name or "",
                    result=content,
                )
    except Exception:
        # 恢复失败不影响核心功能
        pass


# ── 请求/响应模型 ──

class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息", min_length=1)
    conversation_id: str | None = Field(None, description="对话 ID（首次对话不传，后续传入以保持上下文）")
    settings: dict | None = Field(None, description="前端设置覆盖（已弃用，配置从 .env 读取）")
    mode: str = Field("single", description="运行模式: single=单Agent, collaborate=多Agent协作")


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = __version__


class ConversationCreate(BaseModel):
    title: str = ""


class IndexRequest(BaseModel):
    force: bool = False


# ── 基础接口 ──

@app.get("/")
async def root():
    """Web 界面"""
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    raise HTTPException(404, "Web 界面未找到，请先运行 cd web && npm install && npm run build")


# 挂载静态资源（JS/CSS/图片等）
if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/health", response_model=HealthResponse)
async def health():
    """健康检查"""
    return HealthResponse()


@app.get("/debug/webfiles")
async def debug_web_files():
    """调试接口：列出 WEB_DIR 路径和文件"""
    web_path = str(WEB_DIR)
    exists = WEB_DIR.exists()
    index_exists = (WEB_DIR / "index.html").exists()
    assets_exists = (WEB_DIR / "assets").exists()
    files = []
    if exists:
        for f in sorted(WEB_DIR.rglob("*")):
            if f.is_file():
                files.append(str(f.relative_to(WEB_DIR)))
    return {
        "web_dir": web_path,
        "exists": exists,
        "index_html": index_exists,
        "assets_dir": assets_exists,
        "pyinstaller_frozen": getattr(sys, 'frozen', False),
        "meipass": getattr(sys, '_MEIPASS', None),
        "file_count": len(files),
        "files": files[:50],
    }


# ── 对话接口 ──

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE 流式输出 — 实时返回 Agent 的思考和操作

    事件类型:
      - event: tool_start   工具调用开始
      - event: tool_result  工具执行结果
      - event: text         Agent 文本回复
      - event: collaborate  多智能体协作事件（mode=collaborate 时）
      - event: error        错误
      - event: done         完成（data 中含 conversation_id）

    通过传入 conversation_id 实现多轮对话上下文保持。
    mode=collaborate 时启动多智能体协作流程。
    """
    # 协作模式走多 Agent 流程
    if req.mode == "collaborate":
        return StreamingResponse(
            _collaborate_stream(req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    agent, conv_id = _get_or_create_agent(req.conversation_id)

    async def event_stream():
        event_queue: asyncio.Queue = asyncio.Queue()

        async def agent_producer():
            """后台任务：运行 Agent，将事件放入队列"""
            try:
                async for event in agent.run(req.message):
                    await event_queue.put(("event", event))
            except Exception as e:
                await event_queue.put(("error", str(e)))

        producer_task = asyncio.create_task(agent_producer())
        heartbeat_interval = 10  # 秒（低于前端 30s 超时）

        try:
            while True:
                try:
                    item_type, item_data = await asyncio.wait_for(
                        event_queue.get(),
                        timeout=heartbeat_interval,
                    )
                except asyncio.TimeoutError:
                    # 10 秒无事件 — 发送心跳，保持连接活跃
                    if producer_task.done():
                        break
                    yield ": heartbeat\n\n"
                    continue

                if item_type == "event":
                    event = item_data
                    if event.type == "tool_start":
                        # 智能体调用识别：只有 load_skill(name="xxx") 且指定了技能名时才标记
                        is_agent = bool(event.skill_name) and event.tool_name == "load_skill"
                        yield f"event: tool_start\ndata: {json.dumps({'tool': event.tool_name, 'args': event.tool_args, 'content': event.content, 'tokens': event.tokens or {}, 'is_agent': is_agent, 'agent_name': event.skill_name or ''}, ensure_ascii=False)}\n\n"
                    elif event.type == "tool_result":
                        yield f"event: tool_result\ndata: {json.dumps({'tool': event.tool_name, 'content': event.content}, ensure_ascii=False)}\n\n"
                    elif event.type == "text":
                        yield f"event: text\ndata: {json.dumps({'content': event.content}, ensure_ascii=False)}\n\n"
                    elif event.type == "error":
                        yield f"event: error\ndata: {json.dumps({'content': event.content, 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
                    elif event.type == "done":
                        yield f"event: done\ndata: {json.dumps({'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
                elif item_type == "done":
                    yield f"event: done\ndata: {json.dumps({'conversation_id': item_data}, ensure_ascii=False)}\n\n"
                    break
                elif item_type == "error":
                    yield f"event: error\ndata: {json.dumps({'content': item_data, 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
                    break
        finally:
            if not producer_task.done():
                producer_task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _collaborate_stream(req: ChatRequest):
    """多智能体协作 SSE 流"""
    from sage.agents.orchestrator import create_orchestrator

    orchestrator = create_orchestrator()
    conv_id = req.conversation_id or str(uuid.uuid4())

    try:
        async for event in orchestrator.collaborate(req.message):
            if event.type == "task_created":
                yield f"event: collaborate\ndata: {json.dumps({'phase': 'plan', 'role': event.role, 'content': event.content}, ensure_ascii=False)}\n\n"
            elif event.type == "worker_start":
                yield f"event: collaborate\ndata: {json.dumps({'phase': 'start', 'role': event.role, 'content': event.content}, ensure_ascii=False)}\n\n"
            elif event.type == "worker_done":
                yield f"event: collaborate\ndata: {json.dumps({'phase': 'done', 'role': event.role, 'content': event.content}, ensure_ascii=False)}\n\n"
            elif event.type == "reflection":
                yield f"event: collaborate\ndata: {json.dumps({'phase': 'reflection', 'role': event.role, 'content': event.content}, ensure_ascii=False)}\n\n"
            elif event.type == "text":
                yield f"event: text\ndata: {json.dumps({'content': event.content, 'role': event.role}, ensure_ascii=False)}\n\n"
            elif event.type == "done":
                yield f"event: done\ndata: {json.dumps({'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
    except Exception as e:
        yield f"event: error\ndata: {json.dumps({'content': str(e), 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
        yield f"event: done\ndata: {json.dumps({'conversation_id': conv_id}, ensure_ascii=False)}\n\n"


# ── 对话管理接口 ──

@app.get("/conversations")
async def list_conversations(limit: int = 50):
    """获取对话列表（按更新时间倒序）"""
    from sage.memory.store import get_store

    store = get_store()
    conversations = store.list_conversations(limit=limit)
    return {"conversations": conversations}


@app.post("/conversations")
async def create_conversation(req: ConversationCreate):
    """创建新对话"""
    from sage.memory.store import get_store

    store = get_store()
    conv_id = str(uuid.uuid4())
    store.create_conversation(conv_id, req.title)
    return {"id": conv_id, "title": req.title}


@app.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, limit: int = 100):
    """获取对话消息列表"""
    from sage.memory.store import get_store

    store = get_store()
    messages = store.get_messages(conversation_id, limit=limit)
    return {"conversation_id": conversation_id, "messages": messages}


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """删除对话及其所有消息"""
    from sage.memory.store import get_store

    store = get_store()
    store.delete_conversation(conversation_id)
    # 清理 Agent 缓存
    if conversation_id in _agents:
        del _agents[conversation_id]
    return {"success": True, "id": conversation_id}


# ── 项目索引接口 ──

@app.post("/index")
async def index_project(req: IndexRequest):
    """索引项目代码库（用于 search_code 语义搜索）

    通过 asyncio.to_thread 在后台线程执行，避免阻塞事件循环。
    """
    from sage.context.index import ProjectIndex

    try:
        project_index = ProjectIndex(_current_workspace())
        stats = await asyncio.to_thread(project_index.index_project, force=req.force)
        return {"success": True, "stats": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── 记忆系统接口 ──

@app.get("/memory/stats")
async def memory_stats():
    """获取三层记忆系统统计"""
    from sage.memory.memory_orch import create_memory_orchestrator

    orch = create_memory_orchestrator()
    data = orch.stats()
    data["active_agents"] = len(_agents)
    return data


@app.get("/api/token-stats")
async def token_stats():
    """获取 Token 用量统计（供仪表盘展示）"""
    try:
        from sage.memory.store import get_store
        store = get_store()
        return store.get_token_stats()
    except Exception as e:
        return {
            "total_prompt": 0, "total_completion": 0,
            "total_tokens": 0, "total_calls": 0,
            "today_prompt": 0, "today_completion": 0,
            "today_tokens": 0, "today_calls": 0,
            "error": str(e),
        }


@app.get("/memory/summaries")
async def list_memory_summaries(limit: int = 10):
    """获取跨会话记忆摘要列表"""
    from sage.memory.memory_orch import create_memory_orchestrator

    orch = create_memory_orchestrator()
    summaries = orch.long_term.get_recent_summaries(limit=limit)
    return {"summaries": summaries, "count": len(summaries)}


@app.get("/memory/memories")
async def list_memories(limit: int = 20):
    """获取所有语义记忆（按重要性排序）"""
    from sage.memory.memory_orch import create_memory_orchestrator

    orch = create_memory_orchestrator()
    memories = orch.long_term.recall_important_memories(limit=limit)
    return {"memories": memories, "count": len(memories)}


@app.post("/memory/search")
async def search_memories(query: str = "", top_k: int = 5):
    """语义搜索记忆"""
    if not query:
        return {"results": [], "count": 0}
    from sage.memory.memory_orch import create_memory_orchestrator

    orch = create_memory_orchestrator()
    if orch.semantic:
        results = orch.semantic.search(query, top_k=top_k)
        return {"results": results, "count": len(results)}
    return {"results": [], "count": 0, "note": "语义记忆未启用"}


# ── 版本检查与更新接口 ──

@app.get("/api/agent/info")
async def get_agent_info():
    """获取当前 Agent 的基本信息"""
    return {
        "name": "Sage 论文写作智能体",
        "version": __version__,
        "description": "Sage 多智能体协作的学术论文写作辅助系统 — 主控+平等协作+整理汇报+多重验证。具备文献索引、文档解析、写作辅助、引用管理、外部检索、降AI味等完整能力。支持 SCI/SSCI/CSSCI/EI 多学科论文写作。",
        "capabilities": [
            "文献语义检索与索引",
            "PDF/Word/LaTeX 文档解析",
            "论文大纲生成与段落撰写",
            "学术化润色与逻辑检查",
            "参考文献提取与引用插入",
            "多格式引用（APA/MLA/GB-T7714/Vancouver/Chicago/IEEE）",
            "查重检测",
            "外部学术检索（Scholar/arXiv/CrossRef/Semantic Scholar）",
            "降AI味改写",
            "多智能体协作写作",
            "多轮对话上下文保持",
            "工具执行反思与自动修正",
            "技能（Skill）扩展系统",
        ],
        "tools_endpoint": "/api/tools",
        "skills_endpoint": "/api/skills",
    }


@app.get("/api/agents")
async def list_agents():
    """获取所有已定义的 Agent 角色列表（含专属技能信息）"""
    from sage.agents.loader import get_agent_loader

    loader = get_agent_loader()
    agents = loader.get_all_role_info()
    return {"agents": agents, "count": len(agents)}


# ── 工作区管理 API ──


def _current_workspace() -> Path:
    """获取当前工作区路径"""
    from sage.config import get_config
    cfg = get_config()
    ws = cfg.workspace
    if ws is None or str(ws) == ".":
        ws = Path.cwd()
    return ws.resolve()


@app.get("/api/workspace")
async def get_workspace():
    """获取当前工作区路径和顶层文件列表"""
    ws = _current_workspace()
    return {
        "path": str(ws),
        "name": ws.name,
    }


@app.get("/api/workspace/tree")
async def get_workspace_tree(path: str = ""):
    """浏览目录树 — 返回指定目录下的一级内容

    Args:
        path: 要浏览的目录路径（绝对路径或相对当前工作区）。
              不传则返回当前工作区的内容。
              传 "roots" 返回磁盘根目录列表（Windows）。
    """
    import os

    if path == "roots" or not path:
        if not path:
            ws = _current_workspace()
            target = ws
        else:
            # Windows 磁盘根目录
            roots = []
            for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    roots.append({"name": f"{letter}:", "path": drive, "type": "dir"})
            return {"path": "roots", "entries": roots}
    else:
        p = Path(path)
        if not p.is_absolute():
            p = _current_workspace() / p
        target = p.resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {target}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {target}")

    entries = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            # 跳过隐藏文件和常见忽略目录
            if item.name.startswith(".") and item.name not in (".env", ".gitignore"):
                continue
            if item.name in {"node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".git"}:
                continue
            try:
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "path": str(item),
                    "type": "dir" if item.is_dir() else "file",
                    "size": stat.st_size if item.is_file() else 0,
                    "ext": item.suffix.lower() if item.is_file() else "",
                })
            except (PermissionError, OSError):
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"无权限访问: {target}")

    return {"path": str(target), "entries": entries}


class WorkspaceSwitchRequest(BaseModel):
    path: str = Field(..., description="新的工作区路径")


@app.post("/api/workspace")
async def switch_workspace(req: WorkspaceSwitchRequest):
    """切换工作区到指定路径

    切换后会清除所有缓存的 Agent 实例，下次对话将使用新工作区。
    """
    from sage.config import get_config, reset_config
    import os

    target = Path(req.path).resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {target}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {target}")

    # 更新配置中的 workspace
    cfg = get_config()
    cfg.workspace = target

    # 清除所有缓存的 Agent 实例（它们绑定的是旧 workspace）
    cleared = len(_agents)
    _agents.clear()

    return {
        "success": True,
        "path": str(target),
        "name": target.name,
        "cleared_agents": cleared,
    }


@app.get("/api/tools")
async def list_tools():
    """获取所有已注册工具的名称和描述"""
    from sage.tools.engine import ToolEngine
    from sage.config import get_config
    cfg = get_config()
    engine = ToolEngine(workspace=cfg.workspace)
    schemas = engine.get_schemas()
    tools = []
    for s in schemas:
        tools.append({
            "name": s.get("function", {}).get("name", ""),
            "description": s.get("function", {}).get("description", ""),
            "parameters": s.get("function", {}).get("parameters", {}),
        })
    return {"tools": tools, "count": len(tools)}


@app.get("/api/skills")
async def list_skills():
    """获取所有已安装技能的信息（含调用时机等丰富信息）"""
    try:
        from sage.skill_system import SkillLoader
        loader = SkillLoader()
        manifest = loader.generate_manifest()
        return manifest
    except Exception as e:
        return {"skills": [], "count": 0, "error": str(e)}


@app.get("/api/skills/remote-search")
async def search_remote_skills(q: str = "", limit: int = 10):
    """搜索远程技能库（内置 HTTP 客户端，不依赖 skillhub CLI）"""
    try:
        from sage.skill_hub_client import SkillHubClient
        client = SkillHubClient()
        results = await client.search(query=q, limit=limit)
        return {
            "query": q,
            "count": len(results),
            "skills": [r.to_dict() for r in results],
        }
    except Exception as e:
        return {"query": q, "count": 0, "skills": [], "error": str(e)}


@app.post("/api/skills/install")
async def install_skill_api(req: dict):
    """通过 API 安装技能到 .agent/skills/ 目录

    Body: {"name": "skill-slug", "force": false}
    """
    from sage.skill_hub_client import SkillHubClient
    from sage.skill_system import SkillLoader

    name = req.get("name", "").strip()
    force = bool(req.get("force", False))
    if not name:
        return {"success": False, "error": "缺少技能名称"}

    try:
        from sage.skill_system import get_skills_dir
        skills_dir = get_skills_dir()
        skills_dir.mkdir(parents=True, exist_ok=True)

        client = SkillHubClient()
        result = await client.download_and_install(
            slug=name,
            target_dir=skills_dir,
            force=force,
        )

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "未知错误")}

        # 重新加载技能清单
        SkillLoader.reload()

        return {
            "success": True,
            "name": name,
            "path": result.get("path"),
            "skill_json": result.get("skill_json"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/skills/manifest")
async def get_skills_manifest():
    """直接返回 manifest.json 文件内容（快速加载，无需重新扫描）"""
    from sage.skill_system import get_skills_dir
    manifest_path = get_skills_dir() / "manifest.json"
    if manifest_path.exists():
        try:
            import json
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # fallback: 重新扫描
    from sage.skill_system import SkillLoader
    return SkillLoader().generate_manifest()


@app.get("/api/models")
async def list_models(
    provider: str = "",
    api_key: str = "",
    base_url: str = "",
):
    """获取供应商的真实模型列表

    直接调用供应商的 GET {base_url}/models 接口拉取真实模型清单。
    不提供内置回退——需要用户提供有效的 API Key 和 Base URL。
    """
    import httpx

    result = {
        "models": [],
        "source": "none",
        "error": None,
    }

    if not base_url:
        result["error"] = "请先配置 Base URL"
        return result

    if not api_key:
        result["error"] = "请先配置 API Key"
        return result

    try:
        models_url = base_url.rstrip("/") + "/models"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                models_url,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("data", data.get("models", []))
                if isinstance(raw_models, list) and raw_models:
                    result["models"] = [
                        {"id": m.get("id", m) if isinstance(m, dict) else str(m),
                         "name": m.get("id", m) if isinstance(m, dict) else str(m)}
                        for m in raw_models
                    ]
                    result["source"] = "api"
                else:
                    result["error"] = "API 返回的模型列表为空"
            else:
                result["error"] = f"API 请求失败 (HTTP {resp.status_code})"
    except Exception as e:
        result["error"] = f"连接失败: {str(e)}"

    return result


# ── 用户配置持久化接口 ──

_USER_SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "settings.json"


def _load_user_settings() -> dict:
    """从磁盘加载用户配置"""
    try:
        if _USER_SETTINGS_FILE.exists():
            return json.loads(_USER_SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


_DOTENV_KEY_MAP = {
    "LLM_CHAT_API_KEY": ("apiKeys", "provider"),
    "LLM_CHAT_BASE_URL": ("baseUrl",),
    "LLM_CHAT_MODEL": ("model",),
    "LLM_CHAT_TEMPERATURE": ("temperature",),
    "LLM_CHAT_MAX_TOKENS": ("maxTokens",),
}


def _save_to_dotenv(data: dict) -> None:
    """将前端用户配置写入 .env 文件，确保后端始终使用前端配置"""
    dotenv_path = Path(".env").resolve()
    if not dotenv_path.exists():
        print(f"[WARN] .env 文件不存在: {dotenv_path}")
        return

    # 从 data 中提取值，映射为 .env 变量
    env_values: dict[str, str] = {}
    for env_key, keys in _DOTENV_KEY_MAP.items():
        if env_key == "LLM_CHAT_API_KEY":
            # apiKeys 是 { provider: key } 字典，需要知道当前 provider
            api_keys = data.get("apiKeys") or {}
            provider = data.get("provider", "deepseek")
            value = api_keys.get(provider, "")
        else:
            # 其他字段直接取
            value = data.get(keys[0])
        if value is not None and value != "":
            env_values[env_key] = str(value)

    if not env_values:
        return

    # 读取当前 .env，逐行替换
    lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    updated_keys = set()
    for line in lines:
        stripped = line.strip()
        # 跳过注释，但保留
        if stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in env_values:
            new_lines.append(f"{key}={env_values[key]}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # 追加尚未在 .env 中的新变量
    for key, value in env_values.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    dotenv_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"[INFO] .env 已更新: {', '.join(f'{k}={v}' for k, v in env_values.items())}")


def _save_user_settings(data: dict) -> None:
    """将用户配置写入磁盘，同时写入 .env 并重载配置"""
    # 1) 写入 data/settings.json（保持向前兼容）
    try:
        _USER_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USER_SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] 保存 settings.json 失败: {e}")

    # 2) 写入 .env 并重载配置
    _save_to_dotenv(data)
    reset_config()
    # 触发一次 get_config() 让新配置生效并打印
    cfg = get_config()
    masked_key = cfg.llm_chat_api_key[:8] + "..." + cfg.llm_chat_api_key[-4:] if len(cfg.llm_chat_api_key) > 12 else "***"
    print(f"[INFO] 配置已重载: model={cfg.llm_chat_model}, base_url={cfg.llm_chat_base_url}, api_key={masked_key}")


@app.get("/api/user-settings")
async def get_user_settings():
    """获取持久化的用户配置（provider / apiKeys / model / baseUrl / temperature / maxTokens）"""
    return _load_user_settings()


class UserSettingsPayload(BaseModel):
    provider: str | None = None
    apiKeys: dict | None = None
    model: str | None = None
    baseUrl: str | None = None
    temperature: float | None = None
    maxTokens: int | None = None


@app.post("/api/user-settings")
async def save_user_settings(payload: UserSettingsPayload):
    """保存用户配置到磁盘，与 localStorage 双写保证多端一致"""
    current = _load_user_settings()
    # 仅更新传入的非 None 字段
    update = payload.model_dump(exclude_none=True)
    current.update(update)
    _save_user_settings(current)
    return {"success": True}


@app.get("/api/version/check")
async def version_check():
    """检查最新版本（优先 GitHub Releases，回退 PyPI）"""
    import httpx

    current = __version__
    result = {
        "current": current,
        "latest": current,
        "changelog": "",
        "has_update": False,
        "release_url": "",
        "download_url": "",
        "source": "none",
    }

    # 优先尝试 GitHub Releases
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            gh_resp = client.get(
                "https://api.github.com/repos/joker-144/Sage/releases/latest",
                headers={
                    "User-Agent": "Sage-Updater",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            gh_resp.raise_for_status()
            gh_data = gh_resp.json()
            tag = gh_data.get("tag_name", "").lstrip("v")
            changelog_body = gh_data.get("body", "") or ""
            html_url = gh_data.get("html_url", "")

            # 查找 Windows 安装包资源
            download_url = ""
            for asset in gh_data.get("assets", []):
                name = asset.get("name", "")
                if name.endswith(".exe") and ("Setup" in name or "setup" in name or "install" in name or "Installer" in name):
                    download_url = asset.get("browser_download_url", "")
                    break
            if not download_url:
                for asset in gh_data.get("assets", []):
                    if asset.get("name", "").endswith(".exe"):
                        download_url = asset.get("browser_download_url", "")
                        break

            if tag:
                result["latest"] = tag
                result["changelog"] = changelog_body[:4096]
                result["release_url"] = html_url
                result["download_url"] = download_url
                result["has_update"] = _compare_versions(tag, current) > 0
                result["source"] = "github"
    except Exception:
        # GitHub 失败，回退 PyPI
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                py_resp = client.get(
                    "https://pypi.org/pypi/sage-paper/json",
                    headers={"User-Agent": "Sage-Updater"},
                )
                py_resp.raise_for_status()
                data = py_resp.json()
                latest = data.get("info", {}).get("version", current)
                release_url = data.get("info", {}).get("release_url", "")

                result["latest"] = latest
                result["release_url"] = release_url
                result["has_update"] = _compare_versions(latest, current) > 0
                result["source"] = "pypi"

                if result["has_update"]:
                    result["changelog"] = f"PyPI 新版本 {latest} 已发布，请使用 pip install --upgrade sage-paper 更新。"
        except Exception as e:
            result["error"] = f"检查更新失败: {str(e)}"

    return result


@app.post("/api/version/download")
async def version_download():
    """下载最新版本的安装包到下载目录，返回本地文件路径（SSE 流式进度）"""
    import asyncio
    import httpx

    async def download_stream():
        import time
        release_url = ""

        try:
            # 获取最新 Release 信息
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                gh_resp = await client.get(
                    "https://api.github.com/repos/joker-144/Sage/releases/latest",
                    headers={
                        "User-Agent": "Sage-Updater",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                gh_resp.raise_for_status()
                gh_data = gh_resp.json()
                tag = gh_data.get("tag_name", "").lstrip("v")
                release_url = gh_data.get("html_url", "")

            download_url = ""
            file_name = ""
            for asset in gh_data.get("assets", []):
                name = asset.get("name", "")
                if name.endswith(".exe"):
                    download_url = asset.get("browser_download_url", "")
                    file_name = name
                    break

            if not download_url:
                msg = "未找到可用安装包"
                if release_url:
                    msg += f"，请手动下载: {release_url}"
                yield f"data: {json.dumps({'status': 'error', 'message': msg, 'release_url': release_url}, ensure_ascii=False)}\n\n"
                return

            download_dir = Path.home() / "Downloads"
            download_dir.mkdir(exist_ok=True)
            dest = download_dir / file_name

            total_size = 0
            # 先获取文件大小并检查已下载多少
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                head_resp = await client.head(download_url, headers={"User-Agent": "Sage-Updater"})
                if head_resp.status_code == 200:
                    total_size = int(head_resp.headers.get("Content-Length", 0))

            existing_size = dest.stat().st_size if dest.exists() else 0
            if existing_size > 0 and total_size > 0 and existing_size >= total_size:
                # 文件已完整下载
                yield f"data: {json.dumps({'status': 'done', 'message': '文件已存在，跳过下载', 'file_path': str(dest)}, ensure_ascii=False)}\n\n"
                return
            elif existing_size > 0 and total_size > 0:
                yield f"data: {json.dumps({'status': 'info', 'message': f'发现未完成的下载，从 {existing_size/1024/1024:.1f}MB 处续传…'}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'info', 'message': f'找到版本 {tag}，开始下载 {file_name}…'}, ensure_ascii=False)}\n\n"

            # 进度报告间隔控制
            last_report = 0.0

            download_client = httpx.AsyncClient(
                timeout=httpx.Timeout(600.0, connect=15.0),
                follow_redirects=True,
                headers={"User-Agent": "Sage-Updater"},
            )

            max_retries = 3
            total_downloaded = existing_size

            for attempt in range(max_retries + 1):
                try:
                    headers = {"User-Agent": "Sage-Updater"}
                    resume_from = dest.stat().st_size if dest.exists() else 0
                    if resume_from > 0:
                        headers["Range"] = f"bytes={resume_from}-"

                    async with download_client.stream("GET", download_url, headers=headers) as resp:
                        if resp.status_code not in (200, 206):
                            resp.raise_for_status()

                        # 206 = 断点续传
                        if resp.status_code == 206:
                            cr = resp.headers.get("Content-Range", "")
                            if cr:
                                total_size = int(cr.split("/")[-1])

                        open_mode = "ab" if (resp.status_code == 206 and resume_from > 0) else "wb"
                        with open(dest, open_mode) as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
                                total_downloaded += len(chunk)
                                now = time.time()
                                if total_size and (now - last_report > 0.5 or total_downloaded >= total_size):
                                    last_report = now
                                    pct = int(total_downloaded * 100 / total_size)
                                    yield f"data: {json.dumps({'status': 'progress', 'message': f'下载中 {total_downloaded//1024//1024}MB / {total_size//1024//1024}MB ({pct}%)', 'percent': pct}, ensure_ascii=False)}\n\n"

                    # 下载成功，跳出重试循环
                    break

                except Exception:
                    if attempt < max_retries:
                        delay = 2 ** attempt
                        yield f"data: {json.dumps({'status': 'info', 'message': f'连接中断，{delay}秒后重试 (第{attempt+1}次)…'}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(delay)
                    else:
                        raise

            await download_client.aclose()
            yield f"data: {json.dumps({'status': 'done', 'message': '下载完成', 'file_path': str(dest)}, ensure_ascii=False)}\n\n"

        except Exception as e:
            err_msg = f"下载失败: {str(e)}"
            if release_url:
                err_msg += f" | 请手动下载: {release_url}"
            yield f"data: {json.dumps({'status': 'error', 'message': err_msg, 'release_url': release_url}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        download_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/version/install")
async def version_install(request: Request):
    """先卸载旧版本（如已安装），再安装新版本。返回 JSON 状态。"""
    import os
    import subprocess
    import winreg

    body = await request.json()
    file_path = body.get("file_path", "")

    if not file_path or not Path(file_path).exists():
        return {"success": False, "error": f"安装包不存在: {file_path}"}

    try:
        uninstalled = False
        uninstall_result = ""

        # 查找已安装的 Sage（Inno Setup 注册表项）
        base_keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]

        uninstaller_path = ""
        for hkey_root, subkey_path in base_keys:
            try:
                with winreg.OpenKey(hkey_root, subkey_path) as uninstall_key:
                    i = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(uninstall_key, i)
                            with winreg.OpenKey(uninstall_key, subkey_name) as app_key:
                                try:
                                    display_name = winreg.QueryValueEx(app_key, "DisplayName")[0]
                                    if "Sage" in display_name or "sage-paper" in display_name.lower():
                                        try:
                                            uninstaller_path = winreg.QueryValueEx(app_key, "UninstallString")[0]
                                            # UninstallString 通常带引号: "C:\...\unins000.exe"
                                            uninstaller_path = uninstaller_path.strip('"')
                                        except FileNotFoundError:
                                            pass
                                        break
                                except FileNotFoundError:
                                    pass
                            i += 1
                        except OSError:
                            break
                if uninstaller_path:
                    break
            except OSError:
                continue

        # 如果注册表没找到，尝试常见路径
        if not uninstaller_path:
            common_paths = [
                Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Sage" / "unins000.exe",
                Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Sage" / "unins000.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Sage" / "unins000.exe",
            ]
            for p in common_paths:
                if p.exists():
                    uninstaller_path = str(p)
                    break

        # 执行卸载
        if uninstaller_path and Path(uninstaller_path).exists():
            proc = subprocess.run(
                [uninstaller_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                capture_output=True, text=True, timeout=120,
            )
            uninstalled = True
            uninstall_result = f"旧版本已卸载 (返回码 {proc.returncode})"

        # 安装新版本
        proc = subprocess.Popen(
            [file_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        msg_parts = []
        if uninstalled:
            msg_parts.append(uninstall_result)
        msg_parts.append("安装程序已启动，应用即将关闭以完成更新。")

        return {
            "success": True,
            "message": " ".join(msg_parts),
            "pid": proc.pid,
            "uninstalled": uninstalled,
        }
    except Exception as e:
        return {"success": False, "error": f"安装失败: {str(e)}"}


def _compare_versions(v1: str, v2: str) -> int:
    """比较两个 semver 版本号，返回 1(v1>v2) / 0(相等) / -1(v1<v2)"""
    try:
        from packaging.version import parse as parse_version
    except ImportError:
        def parse_version(v: str):
            parts = []
            for x in v.replace("-", ".").split("."):
                try:
                    parts.append(int(x))
                except ValueError:
                    parts.append(0)
            return tuple(parts)

    p1 = parse_version(v1)
    p2 = parse_version(v2)
    if p1 > p2:
        return 1
    elif p1 < p2:
        return -1
    return 0


# ── Sage 工作空间管理 API（新增，不修改原有 /api/workspace 接口）──
# 路由前缀: /api/sage/workspaces
# 功能: 多工作空间创建/列表/删除 + 文件夹导入 + 文件上传 + 自动向量化 + 切换


class SageWorkspaceCreateRequest(BaseModel):
    """创建 Sage 工作空间请求"""
    domain_tag: str = Field(..., description="领域标签（如 CS-AI, MED-Cardio, SSCI-PSY）")
    description: str = Field(default="", description="工作空间描述")
    index_level: str = Field(default="SCI", description="索引级别 SCI/SSCI/CSSCI/EI")


class SageFolderImportRequest(BaseModel):
    """从文件夹导入论文请求"""
    source_path: str = Field(..., description="源文件夹路径")


@app.post("/api/sage/workspaces")
async def sage_create_workspace(req: SageWorkspaceCreateRequest):
    """创建新的 Sage 工作空间

    命名规则: 时间戳_领域标签（如 20260721_143022_CS-AI）
    """
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        ws = manager.create_workspace(
            domain_tag=req.domain_tag,
            description=req.description,
            index_level=req.index_level,
        )
        return {"success": True, "workspace": ws}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/sage/workspaces")
async def sage_list_workspaces():
    """列出所有 Sage 工作空间"""
    from sage.workspace_manager import get_workspace_manager

    manager = get_workspace_manager()
    workspaces = manager.list_workspaces()
    return {"workspaces": workspaces, "count": len(workspaces)}


@app.get("/api/sage/workspaces/{ws_id}")
async def sage_get_workspace(ws_id: str):
    """获取 Sage 工作空间详情"""
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        ws = manager.get_workspace(ws_id)
        return {"workspace": ws}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/sage/workspaces/{ws_id}")
async def sage_delete_workspace(ws_id: str):
    """删除 Sage 工作空间（含所有文件与索引）"""
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        result = manager.delete_workspace(ws_id)
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/sage/workspaces/{ws_id}/import-folder")
async def sage_import_folder(ws_id: str, req: SageFolderImportRequest):
    """从本地文件夹导入论文到工作空间

    导入完成后自动触发向量化索引。
    """
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        result = manager.import_folder(ws_id, req.source_path)
        return {"success": True, **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sage/workspaces/{ws_id}/upload")
async def sage_upload_file(ws_id: str, request: Request):
    """上传单个论文文件到工作空间

    支持表单上传：
    - file: 文件内容
    - filename: 文件名（可选，默认从表单读取）
    - subdir: 子目录（可选，默认 papers）

    上传后自动触发增量向量化索引。
    """
    from sage.workspace_manager import get_workspace_manager

    try:
        form = await request.form()
        upload_file = form.get("file")
        filename = form.get("filename") or (upload_file.filename if upload_file else "")
        subdir = form.get("subdir") or "papers"

        if not upload_file or not filename:
            raise HTTPException(status_code=400, detail="缺少 file 或 filename")

        content = await upload_file.read()
        manager = get_workspace_manager()
        result = manager.upload_file(
            ws_id=ws_id,
            filename=filename,
            content=content,
            subdir=subdir,
        )
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sage/workspaces/{ws_id}/index")
async def sage_trigger_indexing(ws_id: str, force: bool = False):
    """触发工作空间向量化索引

    Args:
        force: 是否强制重建索引（查询参数）
    """
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        result = manager.trigger_indexing(ws_id, force=force)
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/sage/workspaces/{ws_id}/index-status")
async def sage_get_index_status(ws_id: str):
    """获取工作空间索引状态"""
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        status = manager.get_index_status(ws_id)
        return {"success": True, **status}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/sage/workspaces/{ws_id}/switch")
async def sage_switch_workspace(ws_id: str):
    """切换到指定 Sage 工作空间

    切换后更新全局配置的 workspace 字段，所有后续 Agent 操作基于该工作空间。
    原有 /api/workspace 接口返回的路径将指向当前激活的 Sage 工作空间。
    """
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        result = manager.switch_to(ws_id)

        # 清除所有缓存的 Agent 实例（它们绑定的是旧 workspace）
        cleared = len(_agents)
        _agents.clear()

        return {"success": True, "cleared_agents": cleared, **result}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/sage/workspaces/{ws_id}/papers")
async def sage_list_papers(ws_id: str):
    """列出工作空间中的所有论文文件"""
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        ws_path = manager.get_workspace_path(ws_id)
        papers_dir = ws_path / "papers"

        if not papers_dir.exists():
            return {"papers": [], "count": 0}

        papers = []
        for f in sorted(papers_dir.rglob("*"), key=lambda x: x.name.lower()):
            if not f.is_file():
                continue
            try:
                stat = f.stat()
                papers.append({
                    "name": f.name,
                    "path": str(f.relative_to(ws_path)),
                    "size": stat.st_size,
                    "ext": f.suffix.lower(),
                    "modified": stat.st_mtime,
                })
            except (PermissionError, OSError):
                continue

        return {"papers": papers, "count": len(papers)}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Sage 论文工具直接 API（封装 PaperOps，不通过 Agent 对话）──
# 路由前缀: /api/sage
# 功能: 文献检索/引用提取/格式化/查重/外部检索
# 这些 API 使用当前激活的工作空间（_current_workspace()）


class SageSearchRequest(BaseModel):
    """文献语义检索请求"""
    query: str = Field(..., description="自然语言查询")
    top_k: int = Field(default=5, description="返回结果数量")


class SageExtractRefsRequest(BaseModel):
    """提取参考文献请求"""
    file_path: str = Field(..., description="论文文件路径（相对工作空间）")


class SageFormatRefsRequest(BaseModel):
    """格式化参考文献请求"""
    references: str = Field(..., description="参考文献列表（每行一条）")
    style: str = Field(default="APA", description="引用格式: APA/MLA/GB-T7714/Vancouver/Chicago/IEEE")


class SagePlagiarismRequest(BaseModel):
    """查重检测请求"""
    content: str = Field(..., description="要检测的论文内容")
    threshold: float = Field(default=0.8, description="相似度阈值（0-1）")


class SageExternalSearchRequest(BaseModel):
    """外部学术检索请求"""
    query: str = Field(..., description="检索查询")
    source: str = Field(default="scholar", description="检索源: scholar/arxiv/crossref/semantic_scholar")
    max_results: int = Field(default=5, description="最大返回结果数")


def _get_paper_ops():
    """获取当前工作空间的 PaperOps 实例"""
    from sage.tools.paper_ops import PaperOps
    return PaperOps(_current_workspace())


@app.post("/api/sage/search")
async def sage_search_literature(req: SageSearchRequest):
    """语义检索当前工作空间的文献库

    首次使用前需通过 /api/sage/workspaces/{ws_id}/index 建立索引。
    """
    ops = _get_paper_ops()
    result = await ops.search_literature(query=req.query, top_k=req.top_k)
    return {
        "success": result.success,
        "data": result.data if result.success else None,
        "error": result.error if not result.success else None,
    }


@app.post("/api/sage/extract-references")
async def sage_extract_references(req: SageExtractRefsRequest):
    """从论文文件中提取参考文献列表"""
    ops = _get_paper_ops()
    result = await ops.extract_references(file_path=req.file_path)
    return {
        "success": result.success,
        "data": result.data if result.success else None,
        "error": result.error if not result.success else None,
    }


@app.post("/api/sage/format-references")
async def sage_format_references(req: SageFormatRefsRequest):
    """按目标期刊格式化参考文献列表"""
    ops = _get_paper_ops()
    result = await ops.format_references(references=req.references, style=req.style)
    return {
        "success": result.success,
        "data": result.data if result.success else None,
        "error": result.error if not result.success else None,
    }


@app.post("/api/sage/check-plagiarism")
async def sage_check_plagiarism(req: SagePlagiarismRequest):
    """查重检测，识别与已索引文献库的重复内容"""
    ops = _get_paper_ops()
    result = await ops.check_plagiarism(content=req.content, threshold=req.threshold)
    return {
        "success": result.success,
        "data": result.data if result.success else None,
        "error": result.error if not result.success else None,
    }


@app.post("/api/sage/search-external")
async def sage_search_external(req: SageExternalSearchRequest):
    """外部学术检索（Google Scholar/arXiv/CrossRef/Semantic Scholar）

    用于本地文献库不足时补充检索，或验证引用真实性。
    """
    ops = _get_paper_ops()

    if req.source == "scholar":
        result = await ops.search_scholar(query=req.query, max_results=req.max_results)
    elif req.source == "arxiv":
        result = await ops.search_arxiv(query=req.query, max_results=req.max_results)
    elif req.source == "crossref":
        result = await ops.search_crossref(query=req.query, max_results=req.max_results)
    elif req.source == "semantic_scholar":
        result = await ops.search_semantic_scholar(query=req.query, max_results=req.max_results)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的检索源: {req.source}")

    return {
        "success": result.success,
        "data": result.data if result.success else None,
        "error": result.error if not result.success else None,
    }


@app.get("/api/sage/citation-styles")
async def sage_get_citation_styles():
    """获取支持的引用格式列表"""
    return {
        "styles": [
            {"code": "APA", "name": "APA", "description": "心理学/教育学/社会科学"},
            {"code": "MLA", "name": "MLA", "description": "人文学科/文学"},
            {"code": "GB-T7714", "name": "GB/T 7714", "description": "中文期刊国家标准"},
            {"code": "Vancouver", "name": "Vancouver", "description": "医学/生物"},
            {"code": "Chicago", "name": "Chicago", "description": "历史/艺术"},
            {"code": "IEEE", "name": "IEEE", "description": "工程/计算机"},
        ]
    }
