"""
CLI 入口 — 基于 Typer + Rich

Sage 论文写作系统命令:
  sage chat           交互式对话
  sage init           首次配置向导
  sage index          索引工作空间论文
  sage serve          启动 API 服务
  sage workspace      工作空间管理
  sage version        显示版本信息
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

import httpx

from sage import __version__
from sage.config import get_config, reset_config

app = typer.Typer(
    name="sage",
    help=f"Sage — 多智能体协作的学术论文写作辅助系统 (v{__version__})",
    no_args_is_help=False,
)
console = Console()

# ── 共享的 exit code 常量 ──
EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_AGENT_ERROR = 2
EXIT_API_ERROR = 3
EXIT_FILE_NOT_FOUND = 4
EXIT_GIT_ERROR = 5

# ── 共享的 JSON 输出标志 ──
_json_output: bool = False


def _get_json_flag() -> bool:
    """获取全局 JSON 输出标志"""
    return _json_output


def _set_json_flag(val: bool):
    global _json_output
    _json_output = val


def _output_result(data: dict, text: str = ""):
    """根据 --json 标志输出结果"""
    if _get_json_flag():
        console.print_json(data=data)
    elif text:
        console.print(Markdown(text))


def _check_config_or_exit():
    """检查配置，缺失时打印引导信息并退出"""
    config = get_config()
    missing = config.validate_api_keys()
    if not missing:
        return config

    console.print("[red]配置不完整:[/red]")
    for m in missing:
        console.print(f"  - {m}")
    console.print()

    has_env = Path(".env").exists()
    if not has_env:
        console.print("[yellow]未找到 .env 文件。运行 [bold]sage init[/bold] 开始首次配置。[/yellow]")
    else:
        console.print("[yellow].env 文件已存在但缺少上述 Key。运行 [bold]sage init[/bold] 重新配置。[/yellow]")

    raise typer.Exit(code=EXIT_CONFIG_ERROR)


async def _run_agent_prompt(user_prompt: str, workspace: Optional[Path] = None) -> str:
    """通用 Agent 执行：发送 prompt 并收集所有文本输出"""
    from sage.agent.loop import create_agent

    ws = workspace or Path.cwd()
    agent = create_agent(workspace=ws)
    chunks = []

    try:
        async for event in agent.run(user_prompt):
            if event.type == "text":
                chunks.append(event.content)
            elif event.type == "error" and not _get_json_flag():
                console.print(f"[red]Agent 错误: {event.content}[/red]")
    except Exception as e:
        if not _get_json_flag():
            console.print(f"[red]执行失败: {e}[/red]")
        raise typer.Exit(code=EXIT_AGENT_ERROR)

    return "\n\n".join(chunks)


def _read_pipe_or_args(file_arg: Optional[str]) -> tuple[str, str]:
    """读取管道输入或文件参数，返回 (content, source_name)

    优先级: 管道 > 文件参数
    """
    # 检测管道输入
    if not sys.stdin.isatty():
        content = sys.stdin.read().strip()
        if content:
            return content, "<stdin>"

    if file_arg:
        fpath = Path(file_arg)
        if not fpath.exists():
            console.print(f"[red]文件不存在: {file_arg}[/red]")
            raise typer.Exit(code=EXIT_FILE_NOT_FOUND)
        try:
            content = fpath.read_text(encoding="utf-8")
            return content, str(fpath)
        except UnicodeDecodeError:
            console.print(f"[red]无法读取文件（可能为二进制）: {file_arg}[/red]")
            raise typer.Exit(code=EXIT_FILE_NOT_FOUND)
        except Exception as e:
            console.print(f"[red]读取文件失败: {e}[/red]")
            raise typer.Exit(code=EXIT_FILE_NOT_FOUND)

    console.print("[red]请通过管道传入内容或指定文件路径[/red]")
    console.print("[dim]用法: sage review <文件路径>  或  cat file.py | sage review[/dim]")
    raise typer.Exit(code=EXIT_FILE_NOT_FOUND)


# ══════════════════════════════════════════════════════════════════
# 命令: init — 首次配置向导
# ══════════════════════════════════════════════════════════════════

PROVIDER_PRESETS = {
    "1": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "2": {
        "name": "Qwen (通义千问)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_url": "https://dashscope.console.aliyun.com/apiKey",
    },
    "3": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "4": {
        "name": "自定义",
        "base_url": "",
        "model": "",
        "key_url": "",
    },
}


@app.command()
def init():
    """首次配置向导 — 选择模型提供商、填入 API Key

    引导你完成 Sage 的所有必要配置，生成 .env 文件。

    示例:
      sage init
    """
    console.print(Panel(
        "[bold]欢迎使用 Sage v1.0.0[/bold]\n\n"
        "首次使用需要配置 LLM API Key，请按提示操作。\n"
        "所有配置保存在 .env 文件中（不会上传到 git）。",
        title="首次配置向导",
        border_style="green",
    ))

    # ── 步骤 1: 选择对话模型提供商 ──
    table = Table(title="可用的模型提供商")
    table.add_column("序号", style="cyan", width=6)
    table.add_column("提供商", style="green", width=20)
    table.add_column("API Key 获取地址", style="dim")

    for key, preset in PROVIDER_PRESETS.items():
        table.add_row(key, preset["name"], preset["key_url"] if preset["key_url"] else "（手动输入）")

    console.print()
    console.print(table)

    choice = Prompt.ask(
        "\n请选择对话模型提供商",
        choices=["1", "2", "3", "4"],
        default="1",
    )
    preset = PROVIDER_PRESETS[choice]

    if choice == "4":
        base_url = Prompt.ask("  API Base URL", default="https://api.deepseek.com")
        model = Prompt.ask("  模型名称", default="deepseek-chat")
        console.print(f"  [dim]API Key 获取: 请参考你的提供商文档[/dim]")
    else:
        base_url = preset["base_url"]
        model = preset["model"]
        console.print(f"  [dim]获取 API Key: {preset['key_url']}[/dim]")

    api_key = Prompt.ask("  API Key", password=True)

    # ── 步骤 2: Embedding 配置 ──
    console.print()
    console.print("[bold]Embedding 配置[/bold]（用于代码语义搜索 `sage index`）")
    console.print("[dim]使用本地 sentence-transformers（all-MiniLM-L6-v2，约 80MB）[/dim]")
    console.print("[dim]首次使用时自动从 HuggingFace 下载（通过 hf-mirror 镜像加速）[/dim]")

    embed_model = "sentence-transformers/all-MiniLM-L6-v2"
    embed_choice = Confirm.ask("是否使用默认 Embedding 模型？", default=True)
    if not embed_choice:
        embed_model = Prompt.ask("  Embedding 模型", default=embed_model)

    # ── 步骤 3: 写入 .env ──
    console.print()
    overwrite = True
    if Path(".env").exists():
        overwrite = Confirm.ask(".env 已存在，是否覆盖？", default=False)

    if overwrite:
        lines = [
            f"# Sage v1.0.0 — 由 `sage init` 生成",
            f"",
            f"LLM_CHAT_API_KEY={api_key}",
            f"LLM_CHAT_BASE_URL={base_url}",
            f"LLM_CHAT_MODEL={model}",
            f"LLM_CHAT_TEMPERATURE=0.3",
            f"LLM_CHAT_MAX_TOKENS=8192",
            f"LLM_CHAT_TIMEOUT=120.0",
            f"LLM_CHAT_STREAMING=true",
            f"LLM_CHAT_MAX_TOOL_ROUNDS=20",
            f"",
        ]
        if embed_model:
            lines += [
                f"LLM_EMBEDDING_MODEL={embed_model}",
                f"",
            ]
        lines += [
            f"MEMORY_SQLITE_PATH=data/memory.db",
            f"sage_WORKSPACE=.",
            f"sage_MAX_CONTEXT_TOKENS=60000",
            f"sage_SUMMARY_TRIGGER_TOKENS=45000",
            f"",
        ]

        Path(".env").write_text("\n".join(lines), encoding="utf-8")
        reset_config()  # 刷新配置缓存

        console.print()
        console.print(Panel(
            f"[bold green]配置完成![/bold green]\n\n"
            f"对话模型: [cyan]{model}[/cyan]\n"
            f"Embedding:  [cyan]{embed_model if embed_api_key else '未配置'}[/cyan]\n\n"
            f"[bold]下一步:[/bold]\n"
            f"  sage chat         开始交互式对话\n"
            f"  sage review file  审查代码\n"
            f"  sage explain file 解释代码\n"
            f"  sage test file    生成测试\n"
            f"  sage commit       自动生成 commit message",
            title="配置保存成功",
            border_style="green",
        ))
    else:
        console.print("[yellow]已取消，配置未修改[/yellow]")


# ══════════════════════════════════════════════════════════════════
# 命令: chat — 交互式对话
# ══════════════════════════════════════════════════════════════════

@app.command()
def chat(
    prompt: Optional[str] = typer.Argument(None, help="直接传入 prompt（非交互模式）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出结果"),
):
    """交互式对话 — 流式输出 Agent 的思考和操作

    不传参数进入交互 REPL 模式，传入参数则直接执行后退出。

    示例:
      sage chat
      sage chat "帮我把 utils.py 中的函数加上类型注解"
      sage chat --json "显示项目的主要模块"
    """
    _set_json_flag(json_output)
    config = _check_config_or_exit()
    _check_for_update_async()

    # 非交互模式
    if prompt:
        result = asyncio.run(_run_agent_prompt(prompt))
        if _get_json_flag():
            console.print_json(data={"status": "ok", "response": result})
        else:
            console.print(Markdown(result))
        return

    # 交互模式
    from sage.agent.loop import create_agent

    agent = create_agent(workspace=Path.cwd())

    console.print(Panel(
        "[bold]Sage[/bold] — 学术论文写作辅助系统\n"
        "输入写作需求，多智能体协作完成（文献调研/方法设计/撰写/引用/审校）\n"
        "[yellow]/help[/yellow] 查看命令 | [yellow]/clear[/yellow] 清空上下文 | [yellow]/exit[/yellow] 退出",
        title="Sage",
        border_style="green",
    ))

    while True:
        try:
            console.print()
            user_input = console.input("[bold green]❯[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]再见![/yellow]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd in ("/exit", "/quit"):
                console.print("[yellow]再见![/yellow]")
                break
            elif cmd == "/help":
                _show_help()
                continue
            elif cmd == "/clear":
                _clear_context(agent)
                continue
            elif cmd == "/tokens":
                _show_tokens(agent)
                continue
            elif cmd == "/index":
                _run_index()
                continue
            elif cmd == "/stats":
                _show_stats()
                continue
            else:
                console.print(f"[red]未知命令: {user_input}[/red]  (输入 /help 查看可用命令)")
                continue

        asyncio.run(_run_agent_loop(agent, user_input))


# ══════════════════════════════════════════════════════════════════
# 现有命令: version / index / collaborate / serve / stats
# ══════════════════════════════════════════════════════════════════

@app.command()
def version():
    """显示版本信息，并与 PyPI 最新版本对比"""
    from sage import __version__

    # 检查远程版本
    remote_info = ""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get("https://pypi.org/pypi/sage/json")
            if resp.status_code == 200:
                latest = resp.json()["info"]["version"]
                if latest != __version__:
                    remote_info = f"\n[yellow]新版本可用: [bold]{latest}[/bold] （当前 {__version__}）[/yellow]\n[dim]运行 [bold]sage update[/bold] 升级[/dim]"
                else:
                    remote_info = f"\n[dim]已是最新版本 ({__version__})[/dim]"
    except Exception:
        remote_info = "\n[dim]无法检查远程版本[/dim]"

    console.print(f"""
[bold cyan]Sage[/bold cyan] v{__version__}
AI 编码智能体 — 多 Agent 协同 + 反思机制 + 弹性工程 (2026 标准)
{remote_info}
[dim]模型: 单模型 + Provider 可切换 (OpenAI 兼容)[/dim]
[dim]工具: read_file / write_file / edit_file / search_code / run_command / git[/dim]
[dim]核心: Agentic Loop + 多 Agent 协同编排 (Supervisor-Worker)[/dim]
[dim]记忆: SQLite + 向量 Embedding + 知识图谱 + 长期经验检索[/dim]
[dim]反思: 工具执行后自动反思，失败自动修正 (ReflectionEngine)[/dim]
[dim]弹性: 指数退避重试 + 断路器保护 + 可观测性监控[/dim]
[dim]协议: MCP 标准化工具协议支持[/dim]
[dim]接口: CLI 交互式 REPL + Web SSE 流式 API[/dim]
    """)


@app.command()
def update():
    """自动升级到最新版本 — 检查 PyPI 并使用 pip 升级

    示例:
      sage update
    """
    from sage import __version__

    console.print(f"[bold cyan]Sage v{__version__}[/bold cyan]")

    # 检查远程版本
    with console.status("[cyan]正在检查 PyPI 最新版本...[/cyan]"):
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get("https://pypi.org/pypi/sage/json")
                if resp.status_code != 200:
                    console.print("[red]无法访问 PyPI[/red]")
                    raise typer.Exit(code=EXIT_API_ERROR)
                data = resp.json()
                latest = data["info"]["version"]
        except httpx.HTTPError:
            console.print("[red]网络错误，无法检查更新[/red]")
            raise typer.Exit(code=EXIT_API_ERROR)

    if latest == __version__:
        console.print(f"[green]已是最新版本 ({__version__})[/green]")
        return

    console.print(f"\n[yellow]发现新版本: [bold]{latest}[/bold]（当前 {__version__}）[/yellow]")

    # 显示简要 changelog
    try:
        release = data.get("urls", [])
        if release:
            console.print(f"[dim]发布文件: {release[0].get('filename', 'N/A')} ({release[0].get('size', 0) / 1024:.0f} KB)[/dim]")
    except Exception:
        pass

    confirmed = Confirm.ask(f"\n是否升级到 v{latest}？", default=True)
    if not confirmed:
        console.print("[yellow]已取消[/yellow]")
        return

    console.print()
    with console.status(f"[bold cyan]正在升级到 v{latest}...[/bold cyan]"):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "sage"],
            capture_output=True, text=True,
        )

    if result.returncode == 0:
        console.print(f"[bold green]升级成功![/bold green] Sage v{latest}")
        console.print("[dim]重新运行 sage version 验证新版本[/dim]")
    else:
        console.print(f"[red]升级失败:[/red]")
        console.print(result.stderr.strip()[-500:])
        raise typer.Exit(code=EXIT_AGENT_ERROR)


@app.command()
def index(
    force: bool = typer.Option(False, "--force", help="强制重新索引（忽略 hash 跳过）"),
):
    """索引项目代码库（用于 search_code 语义搜索）"""
    _check_config_or_exit()

    from sage.context.index import ProjectIndex

    console.print("[bold cyan]开始索引项目代码库...[/bold cyan] [dim](本地 sentence-transformers)[/dim]")
    console.print(f"[dim]工作区: {Path.cwd()}[/dim]")

    try:
        project_index = ProjectIndex(Path.cwd())
        with console.status("[cyan]索引中（本地 Embedder 推理）...[/cyan]"):
            stats = project_index.index_project(force=force)

        console.print(Panel(
            f"[bold green]索引完成[/bold green]\n\n"
            f"  索引文件: [cyan]{stats['files']}[/cyan]\n"
            f"  代码块:   [cyan]{stats['chunks']}[/cyan]\n"
            f"  跳过未改: [dim]{stats['skipped']}[/dim]",
            title="索引统计",
            border_style="green",
        ))
    except Exception as e:
        console.print(f"[red]索引失败: {e}[/red]")
        raise typer.Exit(code=EXIT_API_ERROR)


@app.command()
def collaborate():
    """多 Agent 协同模式 — 主管-员工协作"""
    _check_config_or_exit()
    _check_for_update_async()

    from sage.agents.orchestrator import create_orchestrator

    orchestrator = create_orchestrator(workspace=Path.cwd())

    console.print(Panel(
        "[bold]Sage — 多 Agent 协同模式[/bold]\n"
        "输入需求，主管 Agent 自动判断是否需要多 Agent 协作\n"
        "[yellow]/help[/yellow] 查看命令 | [yellow]/exit[/yellow] 退出",
        title="Multi-Agent",
        border_style="magenta",
    ))

    while True:
        try:
            console.print()
            user_input = console.input("[bold magenta]❯[/bold magenta] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]再见![/yellow]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd in ("/exit", "/quit"):
                console.print("[yellow]再见![/yellow]")
                break
            elif cmd == "/help":
                _show_help()
                continue
            elif cmd == "/clear":
                console.print("[green]多 Agent 模式暂不支持清空上下文[/green]")
                continue
            else:
                console.print(f"[red]未知命令: {user_input}[/red]")
                continue

        asyncio.run(_run_collaboration(orchestrator, user_input))


@app.command()
def stats():
    """查看可观测性统计 — Token 用量、工具调用、错误率"""
    try:
        from sage.core.observability import get_observability

        obs = get_observability()
        global_stats = obs.get_global_stats()
        recent_tools = obs.get_recent_tool_calls(limit=10)

        console.print(Panel(
            f"[bold]可观测性统计[/bold]\n\n"
            f"追踪记录:  [cyan]{global_stats['total_traces']}[/cyan]\n"
            f"工具调用:  [cyan]{global_stats['total_tool_calls']}[/cyan]\n"
            f"工具成功率: [cyan]{global_stats['tool_success_rate']}%[/cyan]\n"
            f"活跃会话:  [cyan]{global_stats['active_sessions']}[/cyan]",
            title="Observability",
            border_style="cyan",
        ))

        if recent_tools:
            console.print("\n[bold]最近工具调用:[/bold]")
            for t in recent_tools[:5]:
                status = "[green]✓[/green]" if t["success"] else "[red]✗[/red]"
                console.print(f"  {status} {t['tool']} ({t['duration_ms']}ms)")

    except Exception as e:
        console.print(f"[red]获取统计失败: {e}[/red]")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h"),
    port: int = typer.Option(8000, "--port", "-p"),
):
    """启动 API 服务（不打开浏览器，适合后端部署）

    port=0 时自动分配可用端口，并在 stdout 输出 PORT:<port> 供前端解析。
    """
    import socket
    import uvicorn

    actual_port = port
    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            actual_port = s.getsockname()[1]

    # 首行输出机器可读的端口号（Electron 前端依赖此格式）
    print(f"PORT:{actual_port}", flush=True)
    uvicorn.run("sage.api:app", host=host, port=actual_port, reload=False)


@app.command()
def desktop():
    """启动桌面端 — 优先 Electron 原生窗口，回退浏览器

    检测 Electron 打包产物，找到则启动原生窗口（无浏览器外框），
    否则回退到浏览器 + 系统托盘模式。

    示例:
      sage desktop
    """
    from sage.desktop import launch_desktop

    console.print("[bold cyan]启动 Sage 桌面端...[/bold cyan]")
    launch_desktop()


# ══════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════

def _show_help():
    """显示帮助"""
    console.print(Panel(
        "[bold]可用命令:[/bold]\n\n"
        "[cyan]/help[/cyan]     显示帮助\n"
        "[cyan]/clear[/cyan]    清空当前对话上下文\n"
        "[cyan]/tokens[/cyan]   查看当前 token 使用情况\n"
        "[cyan]/index[/cyan]    索引项目代码库（用于 search_code）\n"
        "[cyan]/stats[/cyan]    查看记忆系统统计\n"
        "[cyan]/exit[/cyan]     退出\n\n"
        "[bold]使用方式:[/bold]\n"
        "直接输入需求，Agent 自主完成开发任务",
        title="帮助",
        border_style="cyan",
    ))


def _check_for_update_async():
    """非阻塞检查新版本，有更新时打印提示

    仅在交互模式启动时调用（chat / collaborate），静默运行不阻塞用户。
    """
    import threading

    from sage import __version__

    def _check():
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get("https://pypi.org/pypi/sage/json")
                if resp.status_code != 200:
                    return
                latest = resp.json()["info"]["version"]
                if latest != __version__:
                    console.print(
                        f"\n[yellow]Sage {latest} 可用（当前 {__version__}）— "
                        f"运行 [bold]sage update[/bold] 升级[/yellow]"
                    )
        except Exception:
            pass  # 静默失败，不打扰用户

    t = threading.Thread(target=_check, daemon=True)
    t.start()


def _clear_context(agent):
    """清空对话上下文"""
    from sage.context.manager import ContextManager
    from sage.agent.system_prompt import get_system_prompt

    agent.context = ContextManager(
        workspace=agent.workspace,
        system_prompt=get_system_prompt(),
    )
    console.print("[green]已清空对话上下文[/green]")


def _show_tokens(agent):
    """显示 token 使用"""
    tokens = agent.context.token_count()
    config = get_config()
    max_tokens = config.max_context_tokens
    percentage = (tokens / max_tokens) * 100 if max_tokens > 0 else 0

    color = "green" if percentage < 70 else "yellow" if percentage < 90 else "red"
    console.print(Panel(
        f"当前上下文 token: [cyan]{tokens:,}[/cyan] / {max_tokens:,}\n"
        f"使用率: [{color}]{percentage:.1f}%[/]",
        title="Token 使用",
        border_style="cyan",
    ))


def _run_index():
    """触发项目索引"""
    from sage.context.index import ProjectIndex

    console.print("[bold cyan]开始索引项目代码库...[/bold cyan] [dim](本地 sentence-transformers)[/dim]")
    try:
        project_index = ProjectIndex(Path.cwd())
        with console.status("[cyan]索引中（本地 Embedder 推理）...[/cyan]"):
            stats = project_index.index_project()

        console.print(Panel(
            f"[bold green]索引完成[/bold green]\n\n"
            f"  索引文件: [cyan]{stats['files']}[/cyan]\n"
            f"  代码块:   [cyan]{stats['chunks']}[/cyan]\n"
            f"  跳过未改: [dim]{stats['skipped']}[/dim]",
            title="索引统计",
            border_style="green",
        ))
    except Exception as e:
        console.print(f"[red]索引失败: {e}[/red]")


def _show_stats():
    """显示记忆系统统计"""
    from sage.memory.store import get_store

    try:
        store = get_store()
        stats = store.stats()

        console.print(Panel(
            f"对话会话:  [cyan]{stats['conversations']}[/cyan]\n"
            f"消息数量:  [cyan]{stats['messages']}[/cyan]\n"
            f"代码块:    [cyan]{stats['file_chunks']}[/cyan]\n"
            f"经验教训:  [cyan]{stats['lessons']}[/cyan]\n"
            f"数据库:    [dim]{stats['db_path']}[/dim]",
            title="记忆系统统计",
            border_style="cyan",
        ))
    except Exception as e:
        console.print(f"[red]获取统计失败: {e}[/red]")


async def _run_agent_loop(agent, user_input: str):
    """运行 Agent 并流式输出"""
    try:
        async for event in agent.run(user_input):
            if event.type == "tool_start":
                console.print(f"[dim cyan]⚙ {event.content}[/dim cyan]")
            elif event.type == "tool_result":
                result_text = event.content
                if len(result_text) > 500:
                    result_text = result_text[:500] + " ..."
                console.print(f"[dim]  ↳ {result_text}[/dim]")
            elif event.type == "text":
                console.print()
                console.print(Markdown(event.content))
            elif event.type == "error":
                console.print(f"[red]错误: {event.content}[/red]")
            elif event.type == "done":
                pass
    except Exception as e:
        console.print(f"[red]执行失败: {e}[/red]")


async def _run_collaboration(orchestrator, user_input: str):
    """运行多 Agent 协同并流式输出"""
    try:
        async for event in orchestrator.collaborate(user_input):
            if event.type == "task_created":
                console.print(f"[dim yellow]📋 {event.content}[/dim yellow]")
            elif event.type == "worker_start":
                console.print(f"[cyan]🤖 [{event.role}] {event.content}[/cyan]")
            elif event.type == "worker_done":
                if event.content:
                    text = event.content[:300] + (" ..." if len(event.content) > 300 else "")
                    console.print(f"[dim green]  ↳ [{event.role}] {text}[/dim green]")
            elif event.type == "reflection":
                console.print(f"[magenta]🔄 {event.content}[/magenta]")
            elif event.type == "text":
                console.print()
                console.print(Markdown(event.content))
            elif event.type == "done":
                console.print("[green]协同完成[/green]")
    except Exception as e:
        console.print(f"[red]协同执行失败: {e}[/red]")


def main():
    app()


if __name__ == "__main__":
    main()
