"""
Sage Desktop launcher
Supports both Electron native mode and browser fallback mode.
"""
import os
import sys
import webbrowser
import subprocess
from pathlib import Path

DEFAULT_PORT = 5173  # Vite dev server default
DEFAULT_URL = f"http://localhost:{DEFAULT_PORT}"


def _is_electron_available() -> bool:
    """
    Check if Electron build artifacts exist alongside this package.
    """
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent

    electron_indicators = [
        project_root / "dist-electron" / "win-unpacked" / "Sage.exe",
        project_root / "dist-electron" / "mac" / "Sage.app",
        project_root / "dist-electron" / "mac-arm64" / "Sage.app",
        project_root / "dist-electron" / "linux-unpacked" / "sage",
        project_root / "web" / "electron" / "main.js",
    ]

    for indicator in electron_indicators:
        if indicator.exists():
            return True
    return False


def _launch_electron():
    """
    Launch the Electron app.
    """
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent
    web_dir = project_root / "web"

    if not web_dir.is_dir():
        print("[Sage] Web directory not found.", file=sys.stderr)
        return False

    # Check for pre-built Electron (packaged)
    dist_electron = project_root / "dist-electron"
    if dist_electron.is_dir():
        if sys.platform == "win32":
            exe = dist_electron / "win-unpacked" / "Sage.exe"
            if exe.exists():
                subprocess.Popen([str(exe)], cwd=str(web_dir))
                return True
        elif sys.platform == "darwin":
            for arch in ["mac", "mac-arm64"]:
                app = dist_electron / arch / "Sage.app"
                if app.is_dir():
                    subprocess.Popen(["open", str(app)], cwd=str(web_dir))
                    return True
        else:
            exe = dist_electron / "linux-unpacked" / "sage"
            if exe.exists():
                subprocess.Popen([str(exe)], cwd=str(web_dir))
                return True

    # Fallback: use `electron` CLI from node_modules
    electron_cmd = "electron.cmd" if sys.platform == "win32" else "electron"
    node_modules_electron = web_dir / "node_modules" / ".bin" / electron_cmd
    if node_modules_electron.exists():
        subprocess.Popen([str(node_modules_electron), str(web_dir)], cwd=str(web_dir))
        return True

    # Check system PATH
    import shutil
    if shutil.which(electron_cmd):
        subprocess.Popen([electron_cmd, str(web_dir)], cwd=str(web_dir))
        return True

    return False


def open_browser(url: str = DEFAULT_URL):
    """
    Open the app in the default browser (fallback mode).
    """
    print(f"[Sage] Opening browser at {url}")
    webbrowser.open(url)


def launch_desktop():
    """
    Main desktop launch entry point.
    Tries Electron first, falls back to browser.
    """
    if _is_electron_available():
        print("[Sage] Electron detected, launching native window...")
        if _launch_electron():
            return

    # Fallback to browser
    print("[Sage] No Electron found, launching in browser...")
    open_browser()
