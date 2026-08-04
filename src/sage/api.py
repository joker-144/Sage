"""
API 入口 — 基于 FastAPI
提供 SSE 流式对话 + 对话管理 + 项目索引 + 记忆统计接口

同时托管 web/dist/ 静态界面（Vue 构建），访问根路径 / 即可使用。
"""
from __future__ import annotations

import asyncio
import json
import os
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
# 开发模式：仅允许 Vite dev server
# 生产模式（PyInstaller 打包或 Electron）：允许任意 localhost 端口 + file:// + app://
if getattr(sys, 'frozen', False) or os.environ.get("SAGE_PRODUCTION", ""):
    _cors_origins = [
        "http://localhost:*",
        "https://localhost:*",
        "http://127.0.0.1:*",
        "file://",
        "app://",
        "tauri://localhost",
    ]
else:
    _cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态 Web 界面托管（Vue 构建产物）
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后：多候选路径尝试，覆盖常见的 datas 放置布局变更
    _base = Path(sys._MEIPASS)
    _index_found = False
    for _p in [
        _base / "web" / "dist",
        _base / "_internal" / "web" / "dist",
        _base / "web",                          # 未构建 dist 时直接托管 web/
        _base / "_internal" / "web",
    ]:
        if (_p / "index.html").exists():
            WEB_DIR = _p
            _index_found = True
            break

    if not _index_found:
        # 最后手段：递归搜索 _MEIPASS 下任意包含 index.html 的目录
        for _p in sorted(_base.glob("**/index.html"), key=lambda x: len(str(x))):
            _candidate = _p.parent
            if (_candidate / "assets").is_dir():
                WEB_DIR = _candidate
                _index_found = True
                break

    if not _index_found:
        # 所有路径都失效时返回一个占位目录，后续挂载时跳过
        WEB_DIR = _base / "web" / "dist"
else:
    WEB_DIR = Path(__file__).parent.parent.parent / "web" / "dist"

# 全局 Agent 缓存 — 按 conversation_id 复用，实现多轮对话记忆
# key: conversation_id, value: AgentLoop 实例
_MAX_AGENTS = 50  # 缓存上限，防止内存无限增长
_agents: dict[str, "AgentLoop"] = {}


async def _pool_search_literature(query: str, top_k: int | None = None):
    """跨工作空间文献检索（池模式包装函数）

    遍历所有工作空间的索引库，合并检索结果并按相关度排序。
    每条结果标注来源工作空间，返回格式与 PaperOps.search_literature 一致。
    """
    from sage.workspace_manager import get_workspace_manager
    from sage.tools.paper_ops import PaperOps
    from sage.tools.types import ToolResult

    manager = get_workspace_manager()
    all_workspaces = manager.list_workspaces()
    all_results = []
    for ws in all_workspaces:
        ws_id = ws.get("id")
        if not ws_id:
            continue
        ws_path = manager.get_workspace_path(ws_id)
        db_path = ws_path / ".sage" / "index.db"
        if not db_path.exists():
            continue
        try:
            ops = PaperOps(ws_path)
            result = await ops.search_literature(query=query, top_k=top_k)
            if result.success and result.data:
                ws_tag = ws.get("domain_tag", ws_id)
                for r in result.data:
                    r["workspace_id"] = ws_id
                    r["workspace_tag"] = ws_tag
                    all_results.append(r)
        except Exception:
            continue

    if not all_results:
        return ToolResult(
            success=True,
            output="未找到相关文献。池模式已遍历所有工作空间，可能尚未建立索引或相关度不足。",
            data=[],
        )

    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    limit = top_k or 10
    all_results = all_results[:limit]

    formatted = []
    for i, r in enumerate(all_results, 1):
        source_parts = []
        if r.get("title"):
            source_parts.append(f"标题: {r['title']}")
        if r.get("authors"):
            source_parts.append(f"作者: {r['authors']}")
        if r.get("year"):
            source_parts.append(f"年份: {r['year']}")
        if r.get("doi"):
            source_parts.append(f"DOI: {r['doi']}")
        source_parts.append(f"工作空间: {r.get('workspace_tag', '')}")
        source_line = " | ".join(source_parts)
        formatted.append(
            f"### 结果 {i}（相关度: {r.get('score', 0):.3f}）\n"
            f"**来源**: {source_line}\n"
            f"**文件**: {r.get('file', '')}\n"
        )

    return ToolResult(
        success=True,
        output="\n".join(formatted),
        data=all_results,
    )


def _apply_pool_mode(agent, pool_mode: bool):
    """根据池模式状态切换 agent 的 search_literature 工具注册

    pool_mode=True 时替换为跨工作空间检索；False 时恢复为单工作空间检索。
    """
    from sage.tools.engine import SEARCH_LITERATURE_SCHEMA
    from sage.tools.paper_ops import PaperOps

    if pool_mode:
        agent.tools.register("search_literature", _pool_search_literature, SEARCH_LITERATURE_SCHEMA)
    else:
        paper_ops = PaperOps(agent.workspace)
        agent.tools.register("search_literature", paper_ops.search_literature, SEARCH_LITERATURE_SCHEMA)


def _get_or_create_agent(conversation_id: str | None = None):
    """获取或创建 Agent（按 conversation_id 复用，保持多轮对话上下文）

    所有 LLM 配置从 .env 读取，前端设置通过 _save_user_settings 写入 .env。
    如果传入已有 conversation_id，会从 SQLite 恢复历史消息到 Agent 上下文。
    """
    from sage.agent.loop import create_agent

    if conversation_id and conversation_id in _agents:
        return _agents[conversation_id], conversation_id

    agent = create_agent(workspace=_current_workspace(), conversation_id=conversation_id)

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
    mode: str = Field("single", description="运行模式: single=单Agent, writing=写作模式(智能选择流程)")
    pool_mode: bool = Field(False, description="全选池模式：True 时检索覆盖所有工作空间")


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
      - event: reasoning    模型思考内容（reasoning_content，推理模型才有）
      - event: collaborate  多智能体协作事件（mode=writing 时）
      - event: error        错误
      - event: done         完成（data 中含 conversation_id）

    通过传入 conversation_id 实现多轮对话上下文保持。
    mode=writing 时启动写作模式（智能选择流程：简单任务单Agent，复杂任务多智能体）。
    """
    # 写作模式走多 Agent 流程（内部根据任务复杂度智能选择单/多智能体）
    if req.mode == "writing":
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
    # 根据请求的池模式标记切换 search_literature 工具注册
    # （每次请求都同步，确保缓存 agent 的工具注册与当前池模式状态一致）
    _apply_pool_mode(agent, req.pool_mode)

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
                        # 拦截删除确认请求：delete_file 返回 __DELETE_CONFIRM_REQUIRED__ 标记
                        content = event.content or ""
                        if "__DELETE_CONFIRM_REQUIRED__" in content:
                            # 解析 token 和 path
                            token = ""
                            del_path = ""
                            del_type = ""
                            for line in content.split("\n"):
                                if line.startswith("token:"):
                                    token = line.split(":", 1)[1].strip()
                                elif line.startswith("path:"):
                                    del_path = line.split(":", 1)[1].strip()
                                elif line.startswith("type:"):
                                    del_type = line.split(":", 1)[1].strip()
                            yield f"event: delete_confirm_required\ndata: {json.dumps({'tool': event.tool_name, 'token': token, 'path': del_path, 'type': del_type}, ensure_ascii=False)}\n\n"
                        else:
                            yield f"event: tool_result\ndata: {json.dumps({'tool': event.tool_name, 'content': event.content}, ensure_ascii=False)}\n\n"
                    elif event.type == "reasoning":
                        yield f"event: reasoning\ndata: {json.dumps({'content': event.content}, ensure_ascii=False)}\n\n"
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
    """写作模式 SSE 流（智能选择流程：简单任务单Agent，复杂任务多智能体）"""
    from sage.agents.orchestrator import create_orchestrator

    orchestrator = create_orchestrator(workspace=_current_workspace())
    conv_id = req.conversation_id or str(uuid.uuid4())

    try:
        async for event in orchestrator.collaborate(req.message):
            if event.type == "task_created":
                yield f"event: collaborate\ndata: {json.dumps({'phase': 'plan', 'role': event.role, 'content': event.content}, ensure_ascii=False)}\n\n"
            elif event.type == "worker_start":
                yield f"event: collaborate\ndata: {json.dumps({'phase': 'start', 'role': event.role, 'content': event.content, 'tokens': event.tokens or {}}, ensure_ascii=False)}\n\n"
            elif event.type == "worker_done":
                yield f"event: collaborate\ndata: {json.dumps({'phase': 'done', 'role': event.role, 'content': event.content}, ensure_ascii=False)}\n\n"
            elif event.type == "reflection":
                yield f"event: collaborate\ndata: {json.dumps({'phase': 'reflection', 'role': event.role, 'content': event.content}, ensure_ascii=False)}\n\n"
            elif event.type == "reasoning":
                yield f"event: reasoning\ndata: {json.dumps({'content': event.content, 'role': event.role}, ensure_ascii=False)}\n\n"
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
    """获取所有已定义的 Agent 角色列表（含专属技能信息 + 自定义标记）"""
    from sage.agents.loader import get_agent_loader

    loader = get_agent_loader()
    agents = loader.get_all_role_info()
    # 标记自定义智能体
    for agent in agents:
        agent["is_custom"] = loader.is_custom_agent(agent.get("role", ""))
    return {"agents": agents, "count": len(agents)}


@app.delete("/api/agents/{role}")
async def delete_agent(role: str):
    """删除自定义智能体（仅允许删除自定义的，不可删除内置）"""
    from sage.agents.loader import get_agent_loader

    loader = get_agent_loader()
    # 内置智能体不可删除
    if not loader.is_custom_agent(role):
        raise HTTPException(status_code=400, detail=f"内置智能体 '{role}' 不可删除")
    # 执行删除
    if loader.delete_custom_agent(role):
        return {"success": True, "message": f"自定义智能体 '{role}' 已删除"}
    raise HTTPException(status_code=404, detail=f"自定义智能体 '{role}' 不存在")


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


@app.get("/api/system/models-status")
async def system_models_status():
    """检测本地模型状态（Embedding + OCR）

    返回各模型的安装/下载状态：
    - embedding: sentence-transformers/all-MiniLM-L6-v2 是否已下载到 HuggingFace 缓存
    - ocr: rapidocr-onnxruntime 是否安装 + ONNX 模型是否可用
    - reranker: cross-encoder 是否已下载（可选，不影响核心功能）
    """
    status = {
        "embedding": {"name": "all-MiniLM-L6-v2", "status": "unknown", "detail": ""},
        "ocr": {"name": "RapidOCR (ONNX)", "status": "unknown", "detail": ""},
        "reranker": {"name": "cross-encoder/ms-marco-MiniLM-L-6-v2", "status": "unknown", "detail": ""},
    }

    # 1. Embedding 模型
    try:
        from sage.config import get_config
        cfg = get_config()
        model_name = cfg.llm_embedding_model
        status["embedding"]["name"] = model_name
        from sage.context.index import LocalEmbedder
        if LocalEmbedder._is_model_cached(model_name):
            status["embedding"]["status"] = "ready"
            status["embedding"]["detail"] = "已下载，可正常使用"
        else:
            status["embedding"]["status"] = "not_downloaded"
            status["embedding"]["detail"] = "未下载，首次索引时自动下载（约 80MB）"
    except ImportError:
        status["embedding"]["status"] = "missing"
        status["embedding"]["detail"] = "sentence-transformers 未安装"
    except Exception as e:
        status["embedding"]["status"] = "error"
        status["embedding"]["detail"] = f"检测失败: {e}"

    # 2. OCR 模型 (RapidOCR)
    try:
        from rapidocr_onnxruntime import RapidOCR
        # 尝试初始化（会加载 ONNX 模型）
        RapidOCR()
        status["ocr"]["status"] = "ready"
        status["ocr"]["detail"] = "已安装，扫描版 PDF 可自动 OCR"
    except ImportError:
        status["ocr"]["status"] = "missing"
        status["ocr"]["detail"] = "rapidocr-onnxruntime 未安装，扫描版 PDF 无法 OCR"
    except Exception as e:
        # 可能是 ONNX Runtime 缺失或模型文件损坏
        err_msg = str(e)[:120]
        status["ocr"]["status"] = "error"
        status["ocr"]["detail"] = f"已安装但加载失败: {err_msg}"

    # 3. Reranker 模型（cross-encoder，可选）
    try:
        from sage.config import get_config
        model_name = "cross-encoder/ms-marco-MiniLM-L-6-v2"
        status["reranker"]["name"] = model_name
        from huggingface_hub import try_to_load_from_cache
        if try_to_load_from_cache(model_name, "config.json") is not None:
            status["reranker"]["status"] = "ready"
            status["reranker"]["detail"] = "已下载，检索结果自动重排"
        else:
            status["reranker"]["status"] = "not_downloaded"
            status["reranker"]["detail"] = "未下载，首次检索时自动下载（约 80MB）"
    except ImportError:
        status["reranker"]["status"] = "missing"
        status["reranker"]["detail"] = "huggingface_hub 未安装"
    except Exception as e:
        status["reranker"]["status"] = "error"
        status["reranker"]["detail"] = f"检测失败: {e}"

    return {"models": status}


class DownloadModelRequest(BaseModel):
    model_type: str = Field("reranker", description="要下载的模型类型: embedding/ocr/reranker")
    retry_mode: str = Field("resume", description="重试模式: resume=断点续传(默认), restart=清理缓存后重新下载")


def _clear_hf_model_cache(model_name: str) -> tuple[bool, str]:
    """清理指定模型的 HuggingFace 缓存目录。

    用于"重新下载"场景：当缓存可能损坏时，先清掉再从头下载。
    Returns:
        (success, message)
    """
    import shutil
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        cache_dir = Path(HF_HUB_CACHE) if HF_HUB_CACHE else Path.home() / ".cache" / "huggingface" / "hub"
    except Exception:
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub"

    repo_cache = cache_dir / f"models--{model_name.replace('/', '--')}"
    if not repo_cache.exists():
        return True, "缓存目录不存在，无需清理"
    try:
        shutil.rmtree(repo_cache)
        return True, f"已清理缓存: {repo_cache.name}"
    except Exception as e:
        return False, f"清理缓存失败: {e}"


# HF 模型配置：model_type → (model_name, display_name)
_HF_MODELS = {
    "reranker": ("cross-encoder/ms-marco-MiniLM-L-6-v2", "Reranker 重排模型"),
    "embedding": ("sentence-transformers/all-MiniLM-L6-v2", "Embedding 向量模型"),
}


def _stream_download_hf_model(model_type: str, retry_mode: str = "resume"):
    """流式下载 HuggingFace 模型（embedding/reranker），生成 SSE 进度事件。

    使用 huggingface_hub + 国内镜像（hf-mirror.com），独立于模型加载逻辑，
    不修改原有加载流程。下载完成后模型进入 HF 缓存，后续加载直接命中缓存。

    Args:
        model_type: "embedding" 或 "reranker"
        retry_mode: "resume"=断点续传(默认); "restart"=先清缓存再重新下载
    """
    import os as _os
    # 优先使用已设置的 HF_ENDPOINT，否则走国内镜像（与 CrossEncoderReranker 保持一致）
    if not _os.environ.get("HF_ENDPOINT"):
        _os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    _os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
    _os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

    model_name, display_name = _HF_MODELS.get(model_type, (None, None))
    if not model_name:
        yield f"data: {json.dumps({'status': 'error', 'percent': 0, 'message': f'不支持的模型类型: {model_type}'}, ensure_ascii=False)}\n\n"
        return

    def emit(status: str, percent: int = 0, message: str = ""):
        return f"data: {json.dumps({'status': status, 'percent': percent, 'message': message}, ensure_ascii=False)}\n\n"

    # 重新下载模式：先清理缓存
    if retry_mode == "restart":
        yield emit("info", 0, "正在清理旧缓存...")
        ok, msg = _clear_hf_model_cache(model_name)
        if not ok:
            yield emit("error", 0, f"无法清理缓存: {msg}")
            return
        yield emit("info", 0, f"缓存已清理，开始重新下载")

    yield emit("info", 0, "正在获取文件列表...")

    try:
        from huggingface_hub import list_repo_files, hf_hub_download, hf_hub_url
        import httpx
        import tempfile
        import shutil
        from huggingface_hub.constants import HF_HUB_CACHE

        all_files = list_repo_files(model_name)
    except Exception as e:
        yield emit("error", 0, f"获取文件列表失败: {e}")
        return

    # 排除不必要的大文件（与 LocalEmbedder._download_model_streaming 策略一致）
    skip_extensions = {".bin", ".h5", ".msgpack", ".pt", ".pth", ".ckpt", ".onnx"}
    download_files = [f for f in all_files if Path(f).suffix.lower() not in skip_extensions]
    if not download_files:
        yield emit("error", 0, "未找到可下载的模型文件")
        return

    # 估算总大小
    total_bytes = 0
    file_sizes: dict[str, int] = {}
    base_url = _os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            for fname in download_files:
                try:
                    url = f"{base_url}/{model_name}/resolve/main/{fname}"
                    head_resp = client.head(url)
                    if head_resp.status_code == 200:
                        size = int(head_resp.headers.get("content-length", 0))
                        file_sizes[fname] = size
                        total_bytes += size
                except Exception:
                    pass
    except Exception:
        pass

    if total_bytes == 0:
        total_bytes = 90 * 1024 * 1024  # 约 90MB
    total_mb = total_bytes / (1024 * 1024)
    yield emit("info", 0, f"开始下载（共 {total_mb:.0f}MB）...")

    # 缓存目录
    try:
        cache_dir = Path(HF_HUB_CACHE) if HF_HUB_CACHE else Path.home() / ".cache" / "huggingface" / "hub"
    except Exception:
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    repo_cache = cache_dir / f"models--{model_name.replace('/', '--')}" / "snapshots"
    repo_cache.mkdir(parents=True, exist_ok=True)

    downloaded_bytes = 0
    for fname in download_files:
        fsize = file_sizes.get(fname, 0)
        is_large = fsize > 1 * 1024 * 1024
        try:
            if is_large:
                url = f"{base_url}/{model_name}/resolve/main/{fname}"
                with tempfile.NamedTemporaryFile(delete=False, suffix=Path(fname).suffix) as tmp:
                    tmp_path = tmp.name
                try:
                    with httpx.Client(timeout=120, follow_redirects=True) as client:
                        with client.stream("GET", url) as resp:
                            resp.raise_for_status()
                            with open(tmp_path, "wb") as f:
                                for chunk in resp.iter_bytes(1024 * 1024):
                                    f.write(chunk)
                                    downloaded_bytes += len(chunk)
                                    if total_bytes > 0:
                                        pct = min(int(downloaded_bytes * 100 / total_bytes), 95)
                                        dl_mb = downloaded_bytes / (1024 * 1024)
                                        yield emit("progress", pct, f"下载中 {dl_mb:.1f}/{total_mb:.1f}MB ({pct}%)")
                    dest = repo_cache / fname
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(tmp_path, str(dest))
                except Exception:
                    try:
                        Path(tmp_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    # 回退到 hf_hub_download
                    yield emit("info", min(int(downloaded_bytes * 100 / total_bytes), 95), f"切换下载方式: {Path(fname).name}")
                    hf_hub_download(model_name, fname, resume_download=True)
                    if fsize > 0:
                        downloaded_bytes += fsize
            else:
                hf_hub_download(model_name, fname, resume_download=True)
                if fsize > 0:
                    downloaded_bytes += fsize
        except Exception as e:
            # 单个文件失败不中断，继续下一个
            yield emit("info", min(int(downloaded_bytes * 100 / max(total_bytes, 1)), 95), f"文件 {fname} 跳过: {e}")

    yield emit("progress", 100, "下载完成，验证中...")
    # 最终校验：config.json 是否可从缓存读取
    try:
        from huggingface_hub import try_to_load_from_cache
        if try_to_load_from_cache(model_name, "config.json") is not None:
            yield emit("done", 100, f"{display_name}下载完成")
        else:
            yield emit("done", 100, "下载已完成，但缓存校验未通过，可能需要重启应用")
    except Exception:
        yield emit("done", 100, f"{display_name}下载完成")


def _stream_install_pip_package(package_name: str, display_name: str, retry_mode: str = "resume"):
    """通过 pip 安装 Python 包（如 rapidocr-onnxruntime），生成 SSE 进度事件。

    使用当前 Python 解释器的 pip，以 --progress-stream 方式输出进度。
    安装完成后包立即可用，无需重启应用。

    Args:
        package_name: pip 包名（如 "rapidocr-onnxruntime"）
        display_name: 显示名称（如 "OCR 文字识别"）
        retry_mode: "resume"=直接安装(默认); "restart"=先卸载再安装
    """
    import subprocess
    import sys as _sys

    def emit(status: str, percent: int = 0, message: str = ""):
        return f"data: {json.dumps({'status': status, 'percent': percent, 'message': message}, ensure_ascii=False)}\n\n"

    # 重新安装模式：先卸载
    if retry_mode == "restart":
        yield emit("info", 0, f"正在卸载旧版本 {package_name}...")
        try:
            subprocess.run(
                [_sys.executable, "-m", "pip", "uninstall", "-y", package_name],
                capture_output=True, timeout=60,
            )
            yield emit("info", 0, "卸载完成，开始重新安装")
        except Exception as e:
            yield emit("info", 0, f"卸载跳过: {e}")

    yield emit("info", 0, f"正在安装 {display_name} ({package_name})...")

    try:
        proc = subprocess.Popen(
            [_sys.executable, "-m", "pip", "install", package_name, "--progress-bar=on"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # pip 没有精确的进度百分比，用行读取模拟进度
        lines_processed = 0
        for line in proc.stdout:
            lines_processed += 1
            line = line.strip()
            if line:
                # 模拟进度：前 90% 逐步增长，留 10% 给完成
                pct = min(90, lines_processed * 3)
                yield emit("progress", pct, line[:120])

        proc.wait()
        if proc.returncode == 0:
            yield emit("progress", 95, "安装完成，验证中...")
            # 验证安装：pip 包名 → import 名映射
            _IMPORT_NAMES = {
                "rapidocr-onnxruntime": "rapidocr_onnxruntime",
            }
            import_name = _IMPORT_NAMES.get(package_name, package_name.replace('-', '_'))
            try:
                result = subprocess.run(
                    [_sys.executable, "-c", f"import {import_name}; print('OK')"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    yield emit("done", 100, f"{display_name}安装完成")
                else:
                    yield emit("done", 100, f"{display_name}安装完成，但验证未通过，可能需要重启应用")
            except Exception:
                yield emit("done", 100, f"{display_name}安装完成")
        else:
            yield emit("error", 0, f"安装失败 (exit code {proc.returncode})，请检查网络或手动执行 pip install {package_name}")
    except Exception as e:
        yield emit("error", 0, f"安装异常: {e}")


@app.post("/api/system/download-model")
async def download_model(req: DownloadModelRequest):
    """触发本地模型下载（SSE 流式返回进度）

    支持 embedding/ocr/reranker 三种本地模型：
    - embedding: HuggingFace 模型（sentence-transformers/all-MiniLM-L6-v2）
    - reranker: HuggingFace 模型（cross-encoder/ms-marco-MiniLM-L-6-v2）
    - ocr: pip 包（rapidocr-onnxruntime）

    retry_mode:
      - "resume" (默认): 断点续传/直接安装
      - "restart": 先清理缓存/卸载再重新下载安装
    """
    if req.model_type in ("embedding", "reranker"):
        return StreamingResponse(
            _stream_download_hf_model(model_type=req.model_type, retry_mode=req.retry_mode),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    elif req.model_type == "ocr":
        return StreamingResponse(
            _stream_install_pip_package("rapidocr-onnxruntime", "OCR 文字识别", retry_mode=req.retry_mode),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 用户配置持久化接口 ──

def _get_data_dir() -> Path:
    """获取用户数据目录

    打包后 main.cjs 会将 cwd 设为 %LOCALAPPDATA%/Sage 并通过 SAGE_DATA_DIR 环境变量传递，
    开发时默认为当前工作目录。
    """
    import os as _os
    env_dir = _os.environ.get("SAGE_DATA_DIR", "")
    if env_dir:
        return Path(env_dir)
    return Path.cwd()


_USER_SETTINGS_FILE = _get_data_dir() / "data" / "settings.json"


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
    "TAVILY_API_KEY": ("tavilyApiKey",),
}


def _save_to_dotenv(data: dict) -> None:
    """将前端用户配置写入 .env 文件，确保后端始终使用前端配置"""
    dotenv_path = _get_data_dir() / ".env"
    if not dotenv_path.exists():
        # 打包后首次运行通常没有 .env，自动创建空文件
        try:
            dotenv_path.parent.mkdir(parents=True, exist_ok=True)
            dotenv_path.touch()
        except Exception as e:
            print(f"[WARN] 无法创建 .env 文件: {dotenv_path} ({e})")
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

    # 3) 清空已缓存的 AgentLoop 实例
    # 已缓存的 Agent 在创建时就持有了旧 LLMClient（用旧 config 初始化），
    # 仅 reset_config 不会更新它们的 api_key。清空后下次对话会用新配置重建 AgentLoop。
    cleared = len(_agents)
    _agents.clear()
    if cleared:
        print(f"[INFO] 已清空 {cleared} 个缓存的 Agent 实例，下次对话将使用新配置")


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
    tavilyApiKey: str | None = None


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
    """检查最新版本（优先 GitHub Releases，回退 PyPI）

    国内访问 api.github.com 经常失败，因此采用多镜像回退机制：
    1. 优先尝试 api.github.com（VPN 或可直连环境）
    2. 失败时通过 gh-proxy.com 等镜像代理 api.github.com
    3. 镜像也失败时通过 kkgithub.com 解析 releases.atom + expanded_assets
    4. 最后回退到 PyPI

    带 1 小时缓存，避免短时间内重复请求触发 GitHub API 速率限制（60次/小时）。

    下载 URL 始终包装为 mirror.ghproxy.com 代理，确保国内无需 VPN 即可下载。
    """
    import httpx
    import time as _time

    global _version_cache
    current = __version__

    # ── 缓存检查（成功结果 1 小时；速率限制 5 分钟；普通失败不缓存）──
    now = _time.time()
    if _version_cache is not None:
        age = now - _version_cache["ts"]
        ttl = _version_cache.get("ttl", 3600)
        if age < ttl and _version_cache.get("current") == current:
            return _version_cache["data"]

    result = {
        "current": current,
        "latest": current,
        "changelog": "",
        "has_update": False,
        "release_url": "",
        "download_url": "",
        "source": "none",
    }

    rate_limited = False       # 是否命中 GitHub API 速率限制
    rate_reset_at: float = 0   # 速率限制重置时间（Unix timestamp）

    # ── 第 1 步：直连 GitHub API ──
    gh_api_ok = False
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            gh_resp = client.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
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
                result["download_url"] = _wrap_ghproxy(download_url)
                result["has_update"] = _compare_versions(tag, current) > 0
                result["source"] = "github"
                gh_api_ok = True
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            # 检查是否是速率限制
            remaining = e.response.headers.get("x-ratelimit-remaining", "")
            reset_at = e.response.headers.get("x-ratelimit-reset", "")
            if remaining == "0" or "rate limit" in (e.response.text or "").lower():
                rate_limited = True
                if reset_at:
                    try:
                        rate_reset_at = float(reset_at)
                    except ValueError:
                        pass
    except Exception:
        pass

    # ── 第 2 步：国内镜像代理 api.github.com ──
    if not gh_api_ok and not rate_limited:
        proxied_data = _fetch_latest_release_via_ghproxy_api()
        if proxied_data:
            tag = proxied_data.get("tag_name", "").lstrip("v")
            changelog_body = proxied_data.get("body", "") or ""
            html_url = proxied_data.get("html_url", "")
            download_url = ""
            for asset in proxied_data.get("assets", []):
                name = asset.get("name", "")
                if name.endswith(".exe") and ("Setup" in name or "setup" in name or "install" in name or "Installer" in name or "Windows" in name):
                    download_url = asset.get("browser_download_url", "")
                    break
            if not download_url:
                for asset in proxied_data.get("assets", []):
                    if asset.get("name", "").endswith(".exe"):
                        download_url = asset.get("browser_download_url", "")
                        break

            if tag:
                result["latest"] = tag
                result["changelog"] = changelog_body[:4096]
                result["release_url"] = html_url
                result["download_url"] = _wrap_ghproxy(download_url)
                result["has_update"] = _compare_versions(tag, current) > 0
                result["source"] = "github-mirror"
                gh_api_ok = True

    # ── 第 3 步：Atom Feed 解析（绕过 API 速率限制）──
    if not gh_api_ok:
        atom_info = _fetch_latest_release_via_atom()
        if atom_info and atom_info.get("tag"):
            tag = atom_info["tag"]
            release_url = atom_info.get("release_url", "") or f"https://github.com/{GITHUB_REPO}/releases/tag/v{tag}"
            asset_url = _fetch_asset_url_via_mirror(tag)

            result["latest"] = tag
            result["release_url"] = release_url
            result["download_url"] = _wrap_ghproxy(asset_url) if asset_url else ""
            result["has_update"] = _compare_versions(tag, current) > 0
            result["source"] = "github-mirror"
            gh_api_ok = True

    # ── 第 4 步：回退 PyPI ──
    if result["source"] == "none":
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

    # ── 速率限制提示 ──
    if rate_limited and result["source"] == "none":
        wait_minutes = "几"
        if rate_reset_at > 0:
            wait_seconds = max(0, int(rate_reset_at - now))
            wait_minutes = str(max(1, wait_seconds // 60))
        result["error"] = (
            f"GitHub API 速率限制（每小时 60 次），请等待约 {wait_minutes} 分钟后重试。\n"
            f"您也可以直接访问 {f'https://github.com/{GITHUB_REPO}/releases'} 手动下载。"
        )
        # 速率限制场景：缓存 5 分钟，避免用户疯狂重试触发更多限制
        _version_cache = {"ts": now, "data": result, "current": current, "ttl": 300}
        return result

    # ── 有新版本时：用版本间的 commit 列表覆盖 changelog ──
    # 调用 GitHub Compare API 获取 v{current}...v{latest} 之间的所有 commit message，
    # 比手动填写的 Release body 更准确、更及时。获取失败时保留原 Release body。
    if result["has_update"] and result["latest"] != current:
        commits_changelog = _fetch_commits_between_versions(current, result["latest"])
        if commits_changelog:
            result["changelog"] = commits_changelog

    # 成功时写缓存（1 小时）；普通失败（source='none'）不缓存，允许用户立即重试
    if result["source"] != "none":
        _version_cache = {"ts": now, "data": result, "current": current, "ttl": 3600}
    else:
        _version_cache = None
    return result


@app.post("/api/version/download")
async def version_download():
    """下载最新版本的安装包到下载目录，返回本地文件路径（SSE 流式进度）

    流程：
    1. 获取最新 release 信息（优先 api.github.com，失败时使用 kkgithub.com 镜像）
    2. 下载 URL 包装为 mirror.ghproxy.com 代理，确保国内可下载
    3. 支持断点续传与失败重试，镜像失败时自动切换备用镜像
    """
    import asyncio
    import httpx

    async def download_stream():
        import time
        release_url = ""
        tag = ""
        download_url = ""
        file_name = ""

        try:
            # 获取最新 Release 信息 — 优先 api.github.com
            gh_api_ok = False
            try:
                async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                    gh_resp = await client.get(
                        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                        headers={
                            "User-Agent": "Sage-Updater",
                            "Accept": "application/vnd.github.v3+json",
                        },
                    )
                    if gh_resp.status_code == 200:
                        gh_data = gh_resp.json()
                        tag = gh_data.get("tag_name", "").lstrip("v")
                        release_url = gh_data.get("html_url", "")
                        for asset in gh_data.get("assets", []):
                            name = asset.get("name", "")
                            if name.endswith(".exe"):
                                download_url = asset.get("browser_download_url", "")
                                file_name = name
                                break
                        if download_url:
                            gh_api_ok = True
            except Exception:
                pass

            # 直连失败 → 优先用国内镜像代理 api.github.com（与 version_check 一致）
            if not gh_api_ok:
                proxied_data = await asyncio.to_thread(_fetch_latest_release_via_ghproxy_api)
                if proxied_data:
                    tag = proxied_data.get("tag_name", "").lstrip("v")
                    release_url = proxied_data.get("html_url", "") or release_url
                    for asset in proxied_data.get("assets", []):
                        name = asset.get("name", "")
                        if name.endswith(".exe"):
                            download_url = asset.get("browser_download_url", "")
                            file_name = name
                            break
                    if download_url:
                        gh_api_ok = True

            # 镜像代理也失败 → 使用 atom feed 解析版本号 + 资源 URL
            if not gh_api_ok:
                # 同步函数包装为异步执行
                atom_info = await asyncio.to_thread(_fetch_latest_release_via_atom)
                if atom_info and atom_info.get("tag"):
                    tag = atom_info["tag"]
                    release_url = atom_info.get("release_url", "") or f"https://github.com/{GITHUB_REPO}/releases/tag/v{tag}"
                    asset_url = await asyncio.to_thread(_fetch_asset_url_via_mirror, tag)
                    if asset_url:
                        download_url = asset_url
                        file_name = asset_url.rsplit("/", 1)[-1] if asset_url else ""

            if not download_url:
                msg = "未找到可用安装包"
                if release_url:
                    msg += f"，请手动下载: {release_url}"
                yield f"data: {json.dumps({'status': 'error', 'message': msg, 'release_url': release_url}, ensure_ascii=False)}\n\n"
                return

            # 始终使用国内加速镜像下载（ghproxy 代理）
            mirrored_download_url = _wrap_ghproxy(download_url)
            if not file_name:
                file_name = download_url.rsplit("/", 1)[-1]

            download_dir = Path.home() / "Downloads"
            download_dir.mkdir(exist_ok=True)
            dest = download_dir / file_name

            total_size = 0
            # 先获取文件大小并检查已下载多少
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    head_resp = await client.head(mirrored_download_url, headers={"User-Agent": "Sage-Updater"})
                    if head_resp.status_code == 200:
                        total_size = int(head_resp.headers.get("Content-Length", 0))
            except Exception:
                pass

            existing_size = dest.stat().st_size if dest.exists() else 0
            if existing_size > 0 and total_size > 0 and existing_size >= total_size:
                # 文件已完整下载
                yield f"data: {json.dumps({'status': 'done', 'message': '文件已存在，跳过下载', 'file_path': str(dest)}, ensure_ascii=False)}\n\n"
                return
            elif existing_size > 0 and total_size > 0:
                yield f"data: {json.dumps({'status': 'info', 'message': f'发现未完成的下载，从 {existing_size/1024/1024:.1f}MB 处续传…'}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'info', 'message': f'找到版本 {tag}，开始下载 {file_name}（使用国内镜像）…'}, ensure_ascii=False)}\n\n"

            # 进度报告间隔控制
            last_report = 0.0

            download_client = httpx.AsyncClient(
                timeout=httpx.Timeout(600.0, connect=15.0),
                follow_redirects=True,
                headers={"User-Agent": "Sage-Updater"},
            )

            max_retries = 3
            total_downloaded = existing_size

            # 镜像列表：失败时切换备用镜像
            # 第一个为首选，后续为备用
            mirror_urls_to_try = [mirrored_download_url]
            if "github.com" in download_url:
                for mirror_prefix in _GH_PROXY_MIRRORS[1:]:
                    mirror_urls_to_try.append(mirror_prefix + download_url)
                # 最后回退到原始 URL（可能需要 VPN）
                mirror_urls_to_try.append(download_url)

            download_success = False
            current_url_idx = 0

            for attempt in range(max_retries + 1):
                try:
                    current_url = mirror_urls_to_try[current_url_idx]
                    headers = {"User-Agent": "Sage-Updater"}
                    resume_from = dest.stat().st_size if dest.exists() else 0
                    if resume_from > 0:
                        headers["Range"] = f"bytes={resume_from}-"

                    async with download_client.stream("GET", current_url, headers=headers) as resp:
                        if resp.status_code not in (200, 206):
                            # 切换备用镜像
                            if current_url_idx < len(mirror_urls_to_try) - 1:
                                current_url_idx += 1
                                yield f"data: {json.dumps({'status': 'info', 'message': '当前镜像不可用，切换备用镜像…'}, ensure_ascii=False)}\n\n"
                                continue
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
                    download_success = True
                    break

                except Exception as e:
                    if current_url_idx < len(mirror_urls_to_try) - 1:
                        current_url_idx += 1
                        yield f"data: {json.dumps({'status': 'info', 'message': f'镜像下载失败，切换备用镜像… ({str(e)[:80]})'}, ensure_ascii=False)}\n\n"
                        continue
                    if attempt < max_retries:
                        delay = 2 ** attempt
                        yield f"data: {json.dumps({'status': 'info', 'message': f'连接中断，{delay}秒后重试 (第{attempt+1}次)…'}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(delay)
                    else:
                        raise

            await download_client.aclose()
            if download_success:
                yield f"data: {json.dumps({'status': 'done', 'message': '下载完成', 'file_path': str(dest)}, ensure_ascii=False)}\n\n"

        except Exception as e:
            err_msg = f"下载失败: {str(e)}"
            if release_url:
                err_msg += f" | 请手动下载: {release_url}"
            # 清理半下载文件，避免残留文件影响下次断点续传判断
            try:
                dest.unlink(missing_ok=True)
            except (NameError, AttributeError, OSError):
                pass
            yield f"data: {json.dumps({'status': 'error', 'message': err_msg, 'release_url': release_url}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        download_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/version/install")
async def version_install(request: Request):
    """先卸载旧版本（如已安装），再安装新版本。返回 JSON 状态。

    改进点：
    - 安装后等待 3 秒检查进程状态，若已退出则返回退出码
    - 卸载操作增加错误信息收集
    """
    import subprocess
    import winreg
    import time

    body = await request.json()
    file_path = body.get("file_path", "")

    if not file_path or not Path(file_path).exists():
        return {"success": False, "error": f"安装包不存在: {file_path}"}

    try:
        uninstalled = False
        uninstall_result = ""

        # 查找已安装的 Sage（Inno Setup 注册表项 / NSIS）
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

        # 如果注册表没找到，尝试常见安装路径
        if not uninstaller_path:
            program_files = os.environ.get("ProgramFiles", "") or "C:\\Program Files"
            program_files_x86 = os.environ.get("ProgramFiles(x86)", "") or "C:\\Program Files (x86)"
            local_appdata = os.environ.get("LOCALAPPDATA", "") or ""
            common_paths = [
                Path(program_files) / "Sage" / "unins000.exe",
                Path(program_files_x86) / "Sage" / "unins000.exe",
            ]
            if local_appdata:
                common_paths.append(Path(local_appdata) / "Sage" / "unins000.exe")
            for p in common_paths:
                if p.exists():
                    uninstaller_path = str(p)
                    break

        # 执行卸载（等待完成 + 超时保护）
        if uninstaller_path and Path(uninstaller_path).exists():
            try:
                proc = subprocess.run(
                    [uninstaller_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                    capture_output=True, text=True, timeout=120,
                )
                uninstalled = True
                uninstall_result = f"旧版本已卸载 (返回码 {proc.returncode})"
                if proc.returncode != 0:
                    stderr_tail = proc.stderr.strip()[-200:] if proc.stderr else "(无错误输出)"
                    uninstall_result += f"，stderr: {stderr_tail}"
            except subprocess.TimeoutExpired:
                uninstall_result = "旧版本卸载超时（120秒），可能仍需管理员权限"

        # 安装新版本 — 启动后短暂等待确认进程未立即崩溃
        proc = subprocess.Popen(
            [file_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # 等待 3 秒检测是否立即失败
        time.sleep(3)
        exit_code = proc.poll()
        if exit_code is not None and exit_code != 0:
            stdout_tail = ""
            stderr_tail = ""
            try:
                out, err = proc.communicate(timeout=5)
                stdout_tail = (out or b"").decode("utf-8", errors="replace")[-200:]
                stderr_tail = (err or b"").decode("utf-8", errors="replace")[-200:]
            except Exception:
                pass
            return {
                "success": False,
                "error": f"安装程序启动失败 (退出码 {exit_code})。可能需要以管理员身份运行。\nstdout: {stdout_tail}\nstderr: {stderr_tail}",
            }

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


# ── GitHub 镜像辅助函数 ──
# 国内访问 api.github.com 经常失败，使用多镜像回退机制确保可用性
GITHUB_REPO = "joker-144/Sage"
# GitHub 下载加速镜像列表（按优先级排序，已验证可用性）
# gh-proxy.com: 支持 Range 断点续传，国内可直连
# gh.api.99988866.xyz: Cloudflare 加速
# mirror.ghproxy.com: 备用
_GH_PROXY_MIRRORS = [
    "https://gh-proxy.com/",
    "https://gh.api.99988866.xyz/",
    "https://mirror.ghproxy.com/",
]


def _wrap_ghproxy(download_url: str) -> str:
    """将 GitHub 下载 URL 包装为国内加速镜像 URL

    仅对 github.com 域名生效，其他域名原样返回。
    若镜像包装后 URL 异常则回退到原始 URL。
    """
    if not download_url or "github.com" not in download_url:
        return download_url
    # 优先使用第一个镜像，下载失败时由上层重试逻辑切换
    return _GH_PROXY_MIRRORS[0] + download_url


def _fetch_latest_release_via_ghproxy_api() -> dict | None:
    """通过国内镜像代理 api.github.com 的 releases/latest 接口

    国内直连 api.github.com 经常超时，但 gh-proxy.com 等镜像可代理该 API 请求
    并返回完整 JSON（含 tag_name、assets、html_url）。

    Returns:
        解析后的 release JSON dict，或 None（所有镜像均失败时）
    """
    import httpx

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    for mirror_prefix in _GH_PROXY_MIRRORS:
        proxied_url = mirror_prefix + api_url
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(
                    proxied_url,
                    headers={
                        "User-Agent": "Sage-Updater",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("tag_name"):
                        return data
        except Exception:
            continue
    return None


def _fetch_commits_between_versions(current: str, latest: str) -> str:
    """获取两个版本 tag 之间的所有 commit message，拼接为 changelog 文本

    调用 GitHub Compare API: /repos/{owner}/{repo}/compare/{base}...{head}
    返回两个 tag 之间的完整 commit 列表，每条取第一行（标题）拼接为更新日志。

    镜像策略与 version_check 一致：
      1. 优先直连 api.github.com
      2. 失败时通过 gh-proxy.com 等镜像代理

    Args:
        current: 当前版本号（如 "0.5.7"）
        latest:  最新版本号（如 "0.5.8"）

    Returns:
        拼接好的 changelog 文本；获取失败时返回空字符串（降级为 Release body）
    """
    import httpx

    # 统一加 v 前缀（GitHub tag 通常为 v0.5.7 格式）
    base_tag = f"v{current.lstrip('v')}"
    head_tag = f"v{latest.lstrip('v')}"
    if base_tag == head_tag:
        return ""

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/compare/{base_tag}...{head_tag}"
    headers = {
        "User-Agent": "Sage-Updater",
        "Accept": "application/vnd.github.v3+json",
    }

    # 第 1 步：直连 GitHub API
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(api_url, headers=headers)
            if resp.status_code == 200:
                return _parse_compare_commits(resp.json())
    except Exception:
        pass

    # 第 2 步：国内镜像代理
    for mirror_prefix in _GH_PROXY_MIRRORS:
        proxied_url = mirror_prefix + api_url
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(proxied_url, headers=headers)
                if resp.status_code == 200:
                    return _parse_compare_commits(resp.json())
        except Exception:
            continue

    return ""


def _parse_compare_commits(compare_data: dict) -> str:
    """解析 GitHub Compare API 返回的 JSON，提取完整 commit message 拼接为 changelog

    格式：
        • commit 完整 message1（可能多行）
        • commit 完整 message2（可能多行）
        ...

    保留 commit message 的完整内容（含多行 body），对重复内容自动去重。
    """
    commits = compare_data.get("commits", [])
    if not commits:
        return ""

    seen = set()  # 记录已出现的 commit message，用于去重
    lines = []
    for c in commits:
        msg = (c.get("commit", {}).get("message") or "").strip()
        if not msg or msg in seen:
            continue
        seen.add(msg)
        lines.append(f"• {msg}")

    return "\n".join(lines) if lines else ""


def _fetch_latest_release_via_atom() -> dict | None:
    """通过 GitHub releases.atom 获取最新版本信息

    Atom feed 比 api.github.com 更易访问（走 github.com 而非 api 子域），
    格式稳定且易解析。kkgithub.com 作为备用镜像。

    Returns:
        {"tag": "0.5.5", "release_url": "..."} 或 None
    """
    import xml.etree.ElementTree as ET

    import httpx

    # 优先直连 github.com（许多国内环境可直连 github.com 但无法访问 api.github.com）
    # kkgithub.com 作为备用镜像
    atom_urls = [
        f"https://github.com/{GITHUB_REPO}/releases.atom",
        f"https://kkgithub.com/{GITHUB_REPO}/releases.atom",
    ]

    for atom_url in atom_urls:
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.get(
                    atom_url,
                    headers={"User-Agent": "Sage-Updater"},
                )
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
                # Atom namespace
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                entries = root.findall("atom:entry", ns)
                if not entries:
                    # 退而求其次：使用无命名空间解析
                    entries = root.findall("entry")
                if not entries:
                    return None

                # 第一条 entry 即为最新 release
                entry = entries[0]
                # 提取版本号：从 <title> 或 <id> 中解析
                title_el = entry.find("atom:title", ns) or entry.find("title")
                id_el = entry.find("atom:id", ns) or entry.find("id")
                link_el = entry.find("atom:link", ns) or entry.find("link")

                title = (title_el.text or "").strip() if title_el is not None else ""
                id_text = (id_el.text or "").strip() if id_el is not None else ""
                release_url = ""
                if link_el is not None:
                    release_url = link_el.get("href", "") or id_text

                # 从 title 中提取版本号（常见格式: "v0.5.5" 或 "Release 0.5.5" 或 "0.5.5"）
                tag = ""
                for candidate in (title, id_text):
                    if not candidate:
                        continue
                    # 提取 v?数字.数字.数字 格式
                    import re
                    m = re.search(r"v?(\d+\.\d+(?:\.\d+)*)", candidate)
                    if m:
                        tag = m.group(1)
                        break

                if tag:
                    if not release_url:
                        release_url = id_text
                    return {"tag": tag, "release_url": release_url}
        except Exception:
            continue
    return None


def _fetch_asset_url_via_mirror(tag: str) -> str:
    """获取指定版本 release 的 Windows 安装包下载 URL

    使用 GitHub 的 expanded_assets 端点，返回 HTML 页面列出该 release 的所有资源。
    解析 HTML 找到 .exe 安装包的下载链接。
    kkgithub.com 作为备用镜像。

    Args:
        tag: 版本号（如 "0.5.5"）

    Returns:
        原始 github.com 下载 URL（未包装 ghproxy），失败返回空字符串
    """
    import re

    import httpx

    # 尝试带 v 前缀和不带 v 前缀两种 tag 格式
    tag_variants = [f"v{tag}", tag] if not tag.startswith("v") else [tag, tag.lstrip("v")]

    for tag_variant in tag_variants:
        # 优先直连 github.com，kkgithub.com 作为备用
        for base in ["https://github.com", "https://kkgithub.com"]:
            url = f"{base}/{GITHUB_REPO}/releases/expanded_assets/{tag_variant}"
            try:
                with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                    resp = client.get(
                        url,
                        headers={"User-Agent": "Sage-Updater"},
                    )
                    if resp.status_code != 200:
                        continue
                    html = resp.text

                    # 解析所有 href 中的下载链接
                    # 格式: href="/joker-144/Sage/releases/download/v0.5.5/Sage-0.5.5-Windows.exe"
                    pattern = re.compile(
                        r'href="(/[^"]*?/releases/download/[^"]*\.exe)"',
                        re.IGNORECASE,
                    )
                    matches = pattern.findall(html)

                    if not matches:
                        continue

                    # 优先选择包含 Setup/installer/Windows 关键字的资源
                    preferred = ""
                    for path in matches:
                        lower = path.lower()
                        if "setup" in lower or "installer" in lower or "install" in lower or "windows" in lower:
                            preferred = path
                            break
                    if not preferred:
                        preferred = matches[0]

                    # 将相对路径转为绝对 URL（始终使用 github.com 域名，由上层 _wrap_ghproxy 包装代理）
                    if preferred.startswith("/"):
                        return f"https://github.com{preferred}"
                    return preferred
            except Exception:
                continue
    return ""


# ── Sage 工作空间管理 API（新增，不修改原有 /api/workspace 接口）──
# 路由前缀: /api/sage/workspaces
# 功能: 多工作空间创建/列表/删除 + 文件夹导入 + 文件上传 + 自动向量化 + 切换


class SageWorkspaceCreateRequest(BaseModel):
    """创建 Sage 工作空间请求"""
    domain_tag: str = Field(..., description="领域标签（如 CS-AI, MED-Cardio, SSCI-PSY）")
    description: str = Field(default="", description="工作空间描述")
    index_level: str = Field(default="standard", description="索引级别 standard(标准索引)/premium(高精度索引)")


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


@app.post("/api/sage/workspaces/{ws_id}/async-index")
async def sage_async_index(ws_id: str, force: bool = False):
    """异步触发工作空间向量化索引

    立即返回任务状态，索引在后台执行。
    前端可通过 GET /api/sage/workspaces/{ws_id}/index-task-status 轮询状态，
    或通过 GET /api/sage/workspaces/{ws_id}/index-events 订阅 SSE 进度事件。

    Args:
        force: 是否强制重建索引（查询参数）
    """
    from sage.workspace_manager import get_index_task_manager

    try:
        mgr = get_index_task_manager()
        result = await mgr.start_index(ws_id, force=force)
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/sage/workspaces/{ws_id}/index-task-status")
async def sage_get_index_task_status(ws_id: str):
    """获取异步索引任务的实时状态（用于前端轮询）"""
    from sage.workspace_manager import get_index_task_manager

    mgr = get_index_task_manager()
    status = mgr.get_status(ws_id)
    return {"success": True, **status}


@app.get("/api/sage/workspaces/{ws_id}/index-events")
async def sage_index_events(ws_id: str):
    """SSE 订阅异步索引进度事件

    事件类型:
      - event: start    索引开始
      - event: progress 单个文件索引完成
      - event: done     全部索引完成
      - event: error    索引失败
    """
    from sage.workspace_manager import get_index_task_manager

    mgr = get_index_task_manager()
    queue = await mgr.subscribe(ws_id)

    async def event_stream():
        try:
            # 先推送当前状态（便于客户端连接后立即获取进度）
            current = mgr.get_status(ws_id)
            if current["status"] in ("running", "pending"):
                yield f"event: progress\ndata: {json.dumps({'progress': current['progress'], 'total': current['total'], 'current_file': current['current_file'], 'message': current['message']}, ensure_ascii=False)}\n\n"
            elif current["status"] == "done":
                yield f"event: done\ndata: {json.dumps({'stats': current['stats'], 'message': current['message']}, ensure_ascii=False)}\n\n"
            elif current["status"] == "error":
                yield f"event: error\ndata: {json.dumps({'error': current['error'], 'message': current['message']}, ensure_ascii=False)}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    event_type = event.get("type", "progress")
                    yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event_type in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            await mgr.unsubscribe(ws_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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


@app.get("/api/sage/workspaces/{ws_id}/papers/download")
async def sage_download_paper(ws_id: str, path: str):
    """下载工作空间中的论文文件

    Args:
        ws_id: 工作空间 ID
        path: 论文相对于工作空间根目录的路径（如 "papers/xxx.pdf"）
              —— 与 /papers 端点返回的 path 字段一致
    """
    from pathlib import Path as _Path
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        ws_path = manager.get_workspace_path(ws_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"工作空间不存在: {e}")

    # 安全校验：解析后的绝对路径必须仍位于工作空间内，防止路径穿越
    try:
        rel = _Path(path)
        # 拒绝绝对路径和 .. 穿越
        if rel.is_absolute() or ".." in rel.parts:
            raise HTTPException(status_code=400, detail="非法路径")
        target = (ws_path / rel).resolve()
        ws_root = ws_path.resolve()
        # 使用 is_relative_to 而非字符串 startswith，避免 'ws1' 误匹配 'ws10' 前缀
        if not target.is_relative_to(ws_root):
            raise HTTPException(status_code=400, detail="路径越界")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"路径解析失败: {e}")

    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


@app.delete("/api/sage/workspaces/{ws_id}/papers")
async def sage_delete_paper(ws_id: str, path: str):
    """删除工作空间中的论文文件，并同步清理索引块

    Args:
        ws_id: 工作空间 ID
        path: 论文相对于工作空间根目录的路径（如 "papers/xxx.pdf"）
              —— 与 /papers 端点返回的 path 字段一致
    """
    from sage.workspace_manager import get_workspace_manager

    try:
        manager = get_workspace_manager()
        result = manager.delete_paper(ws_id, path)
        return {"success": True, **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")


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


@app.post("/api/sage/search-pool")
async def sage_search_pool(req: SageSearchRequest):
    """跨工作空间检索（池模式）

    遍历所有工作空间的索引库，合并检索结果，每条结果标注来源工作空间。
    用于"全选池模式"下不分工作空间的全量检索。

    返回结果按相关度降序排列，每条结果含 workspace_id 和 workspace_tag 元数据。
    """
    from sage.workspace_manager import get_workspace_manager
    from sage.tools.paper_ops import PaperOps

    manager = get_workspace_manager()
    all_workspaces = manager.list_workspaces()

    all_results = []
    for ws in all_workspaces:
        ws_id = ws.get("id")
        if not ws_id:
            continue
        ws_path = manager.get_workspace_path(ws_id)
        db_path = ws_path / ".sage" / "index.db"
        if not db_path.exists():
            continue
        try:
            ops = PaperOps(ws_path)
            result = await ops.search_literature(query=req.query, top_k=req.top_k)
            if result.success and result.data:
                for r in result.data:
                    r["workspace_id"] = ws_id
                    r["workspace_tag"] = ws.get("domain_tag", "")
                    all_results.append(r)
        except Exception:
            continue

    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {
        "success": True,
        "data": all_results[:req.top_k],
        "total": len(all_results),
        "pool_mode": True,
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


class PoolModeRequest(BaseModel):
    pool_mode: bool = Field(..., description="是否启用全选池模式")


@app.get("/api/sage/pool-mode")
async def sage_get_pool_mode():
    """获取当前全选池模式状态"""
    from sage.workspace_manager import get_pool_mode
    return {"pool_mode": get_pool_mode()}


@app.post("/api/sage/pool-mode")
async def sage_set_pool_mode(req: PoolModeRequest):
    """设置全选池模式状态

    启用后，智能体对话中的 search_literature 工具将跨所有工作空间检索。
    """
    from sage.workspace_manager import set_pool_mode
    new_value = set_pool_mode(req.pool_mode)
    return {"pool_mode": new_value, "success": True}


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


@app.post("/api/sage/confirm-delete")
async def sage_confirm_delete(req: dict):
    """用户确认删除操作

    前端收到 delete_confirm_required SSE 事件后，弹出确认对话框，
    用户点击确认后调用此端点，传入 token 执行实际删除。
    """
    from sage.tools.file_ops import confirm_delete

    token = req.get("token", "")
    confirmed = req.get("confirmed", False)

    if not token:
        raise HTTPException(status_code=400, detail="缺少 token")

    if not confirmed:
        return {"success": False, "message": "用户取消删除"}

    success, message = confirm_delete(token)
    return {"success": success, "message": message}


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


# ──────────────────────────── 模型下载进度 SSE ────────────────────────────

# 版本检查缓存（避免检测更新时反复触发 GitHub API 速率限制）
# 结构: {"ts": timestamp, "data": result_dict, "current": version_str, "ttl": 缓存有效期秒数}
# - 成功(source!=none): ttl=3600 (1小时)
# - 速率限制: ttl=300 (5分钟)
# - 普通失败(source==none): 不缓存(=None)，允许用户立即重试
_version_cache: dict | None = None

# 全局进度队列: 每个 model_download 任务通过此队列推送进度事件
# key: session_id (str) → value: asyncio.Queue
_model_progress_queues: dict[str, asyncio.Queue] = {}
_model_progress_lock = asyncio.Lock()


class ModelLoadRequest(BaseModel):
    """模型加载请求"""
    session_id: str = Field(default="default", description="客户端生成的会话ID，用于 SSE 匹配")


@app.post("/api/model/preload")
async def model_preload(req: ModelLoadRequest):
    """触发 Embedding 模型预加载。

    返回 {"cached": true} 表示模型已在缓存，直接完成；
    返回 {"cached": false} 表示需要下载，请连接 SSE 端点查看进度。
    后端会异步启动下载任务并通过 SSE 队列推送进度。
    """
    from sage.context.index import LocalEmbedder
    from sage.config import get_config

    embedder = LocalEmbedder()

    # 检查是否已加载或已缓存
    if LocalEmbedder._model is not None:
        return {"cached": True, "message": "模型已在内存中"}

    if embedder._is_model_cached(get_config().llm_embedding_model):
        # 已缓存，同步加载
        try:
            embedder._ensure_model()
            return {"cached": True, "message": "模型从缓存加载完成"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # 需要下载 → 启动异步下载任务
    sid = req.session_id
    async with _model_progress_lock:
        # 同一个 session 复用已有队列（避免重复启动）
        if sid in _model_progress_queues:
            return {"cached": False, "message": "下载已在进行中，请连接 SSE 端点"}

        q: asyncio.Queue = asyncio.Queue()
        _model_progress_queues[sid] = q

    # 在后台线程中执行下载（避免阻塞 asyncio 事件循环）
    async def _run_download():
        loop = asyncio.get_running_loop()

        def _progress_callback(stage: str, percent: int, message: str):
            """将进度事件推送到 asyncio 队列"""
            event = json.dumps({
                "stage": stage,
                "percent": percent,
                "message": message,
            })
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:
                pass

        def _do_download():
            try:
                embedder._ensure_model(progress_callback=_progress_callback)
            except Exception as e:
                loop.call_soon_threadsafe(
                    q.put_nowait,
                    json.dumps({"stage": "error", "percent": 0, "message": str(e)}),
                )

        await asyncio.to_thread(_do_download)

        # 发送结束信号
        await q.put(None)

    asyncio.create_task(_run_download())
    return {"cached": False, "message": "下载已启动"}


@app.get("/api/model/download-progress")
async def model_download_progress(request: Request, session_id: str = "default"):
    """SSE 端点：流式推送模型下载进度。

    连接后持续接收 JSON 格式的事件:
        {"stage": "checking"|"downloading"|"loading"|"ready"|"error",
         "percent": 0-100, "message": "..."}

    下载完成后流自动关闭。
    """
    async with _model_progress_lock:
        q = _model_progress_queues.get(session_id)
        if q is None:
            q = asyncio.Queue()
            _model_progress_queues[session_id] = q

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                data = await asyncio.wait_for(q.get(), timeout=600)  # 10 分钟超时
                if data is None:
                    break
                yield f"data: {data}\n\n"
        except asyncio.TimeoutError:
            yield "data: {\"stage\":\"error\",\"percent\":0,\"message\":\"下载超时\"}\n\n"
        finally:
            async with _model_progress_lock:
                if session_id in _model_progress_queues:
                    del _model_progress_queues[session_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
