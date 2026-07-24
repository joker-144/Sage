const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
} = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');

const BACKEND_STARTUP_TIMEOUT = 30000;
const DEFAULT_PORT = 19476;

// 数据目录：打包后使用 %LOCALAPPDATA%/Sage，开发时使用项目根目录
const DATA_DIR = (() => {
  if (app.isPackaged) {
    const dir = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'Sage');
    fs.mkdirSync(dir, { recursive: true });
    fs.mkdirSync(path.join(dir, 'data'), { recursive: true });
    return dir;
  }
  return path.join(__dirname, '..', '..');
})();

let mainWindow = null;
let backendProcess = null;
let backendPort = null;

// ── 统一版本号读取 ──
function readAppVersion() {
  const candidates = [
    path.join(__dirname, '../../VERSION'),
    path.join(__dirname, '../VERSION'),
    path.join(process.resourcesPath || '', 'VERSION'),
  ];
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) {
        return fs.readFileSync(p, 'utf-8').trim();
      }
    } catch { /* ignore */ }
  }
  return '0.5.6';
}

const APP_VERSION = readAppVersion();
console.log(`[Sage] Version: ${APP_VERSION}`);

function getBackendPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', 'sage.exe');
  }
  return path.join(__dirname, '../../dist/sage.exe');
}

function _parseBackendPort(firstLine) {
  // 尝试多种格式解析端口号
  const portMatch = firstLine.match(/port[:\s]*(\d+)/i);
  if (portMatch) return parseInt(portMatch[1], 10);
  const numMatch = firstLine.match(/(\d{4,5})/);
  if (numMatch) return parseInt(numMatch[1], 10);
  const parsed = parseInt(firstLine.trim(), 10);
  return isNaN(parsed) ? null : parsed;
}

function _launchBackend(port) {
  const backendPath = getBackendPath();
  const portArg = String(port);
  console.log(`[Sage] Starting backend: ${backendPath} --port ${portArg}`);
  backendProcess = spawn(backendPath, ['serve', '--port', portArg], {
    cwd: DATA_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: { ...process.env, SAGE_VERSION: APP_VERSION, SAGE_DATA_DIR: DATA_DIR },
  });
}

function startBackend() {
  return new Promise((resolve, reject) => {
    let isFirstLaunch = true;
    let attemptPort = DEFAULT_PORT;

    function onPortResolved(port) {
      backendPort = port;
      console.log(`[Sage] Backend started on port ${backendPort}`);
      resolve(backendPort);
    }

    function tryLaunch(port) {
      _launchBackend(port);

      let firstLine = '';
      const timeout = setTimeout(() => {
        if (isFirstLaunch) {
          reject(new Error('Backend startup timed out after 30 seconds'));
        } else {
          // 固定端口被占用，自动切为随机端口后仍然超时
          reject(new Error('Backend startup timed out — both fixed port and random port failed'));
        }
      }, BACKEND_STARTUP_TIMEOUT);

      backendProcess.stdout.on('data', (data) => {
        const text = data.toString();
        if (!firstLine) {
          const newlineIdx = text.indexOf('\n');
          if (newlineIdx !== -1) {
            firstLine += text.substring(0, newlineIdx);
            clearTimeout(timeout);

            const parsedPort = _parseBackendPort(firstLine);
            if (parsedPort) {
              return onPortResolved(parsedPort);
            }
            // 端口解析失败：先打印输出，如果是首次尝试，则自动切随机端口重试
            console.log(`[Sage] Backend stdout: ${firstLine}`);
            if (isFirstLaunch) {
              console.log('[Sage] Port parsing failed, retrying with random port...');
              isFirstLaunch = false;
              killBackend();
              return tryLaunch(0);
            }
            reject(new Error(`Could not parse port from backend output: ${firstLine}`));
          } else {
            firstLine += text;
            if (firstLine.length > 500) {
              clearTimeout(timeout);
              if (isFirstLaunch) {
                console.log('[Sage] Backend stdout too long, retrying with random port...');
                isFirstLaunch = false;
                killBackend();
                return tryLaunch(0);
              }
              reject(new Error('Backend output too long without valid port'));
            }
          }
        }
      });

      backendProcess.stderr.on('data', (data) => {
        const errText = data.toString();
        console.error(`[Sage Backend] ${errText}`);
        // 检测端口占用错误
        if (isFirstLaunch && (errText.includes('Address already in use') || errText.includes('address in use') || errText.includes('EADDRINUSE'))) {
          clearTimeout(timeout);
          console.log('[Sage] Fixed port in use, trying random port...');
          isFirstLaunch = false;
          killBackend();
          tryLaunch(0);
        }
      });

      backendProcess.on('error', (err) => {
        clearTimeout(timeout);
        if (isFirstLaunch) {
          isFirstLaunch = false;
          killBackend();
          return tryLaunch(0);
        }
        reject(new Error(`Failed to start backend: ${err.message}`));
      });

      backendProcess.on('exit', (code, signal) => {
        clearTimeout(timeout);
        if (!backendPort) {
          if (isFirstLaunch) {
            isFirstLaunch = false;
            killBackend();
            return tryLaunch(0);
          }
          reject(new Error(`Backend exited with code ${code} before reporting port`));
        }
      });
    }

    tryLaunch(DEFAULT_PORT);
  });
}

function killBackend() {
  if (!backendProcess) return;

  console.log('[Sage] Stopping backend...');
  const pid = backendProcess.pid;

  try {
    if (process.platform === 'win32') {
      // Windows: taskkill 强制终止整个进程树
      const { execSync } = require('child_process');
      execSync(`taskkill /pid ${pid} /f /t`, { stdio: 'ignore' });
      console.log(`[Sage] Backend (pid ${pid}) killed via taskkill`);
    } else {
      // macOS/Linux: SIGTERM -> SIGKILL
      if (!backendProcess.killed) {
        backendProcess.kill('SIGTERM');
      }
      setTimeout(() => {
        try {
          if (backendProcess && !backendProcess.killed) {
            backendProcess.kill('SIGKILL');
          }
        } catch { /* ignore */ }
      }, 3000);
    }
  } catch (e) {
    // 进程可能已退出
    console.log(`[Sage] Backend kill skipped: ${e.message}`);
  }

  backendProcess = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    icon: path.join(__dirname, '../../public/log.ico'),
  });

  mainWindow.loadURL(`http://localhost:${backendPort}`);

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL) => {
    console.error(`[Sage] Page load failed: ${errorDescription} (code: ${errorCode}) URL: ${validatedURL}`);
    mainWindow.webContents.loadURL(`data:text/html,<h2>Sage 加载失败</h2><p>${errorDescription}</p><p>后端地址: ${validatedURL}</p><p>请检查后端是否正常运行。</p>`);
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('maximize', () => {
    mainWindow?.webContents.send('maximize-change', true);
  });
  mainWindow.on('unmaximize', () => {
    mainWindow?.webContents.send('maximize-change', false);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// IPC handlers for frameless window controls
ipcMain.handle('window-minimize', () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.handle('window-maximize', () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.handle('window-close', () => {
  if (mainWindow) mainWindow.close();
});

ipcMain.handle('window-is-maximized', () => {
  return mainWindow ? mainWindow.isMaximized() : false;
});

ipcMain.handle('check-version', async () => {
  try {
    const http = require('http');
    return await new Promise((resolve, reject) => {
      http.get(`http://localhost:${backendPort}/api/version/check`, (res) => {
        let data = '';
        res.on('data', (chunk) => data += chunk);
        res.on('end', () => {
          try { resolve(JSON.parse(data)); }
          catch { reject(new Error('Invalid response')); }
        });
      }).on('error', reject);
    });
  } catch (e) {
    return { error: e.message };
  }
});

ipcMain.handle('update-download', async () => {
  return new Promise((resolve, reject) => {
    const http = require('http');
    const req = http.request(`http://localhost:${backendPort}/api/version/download`, {
      method: 'POST',
    }, (res) => {
      let buffer = '';
      res.on('data', (chunk) => {
        buffer += chunk.toString();
        // 解析 SSE 流并推送到渲染进程
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const msg = JSON.parse(line.slice(6));
              mainWindow?.webContents.send('update-download-progress', msg);
              if (msg.status === 'done') {
                resolve({ success: true, file_path: msg.file_path });
              } else if (msg.status === 'error') {
                resolve({ success: false, error: msg.message });
              }
            } catch { /* ignore parse errors */ }
          }
        }
      });
      res.on('end', () => {
        if (buffer) {
          // 处理剩余缓冲
          const lines = buffer.split('\n');
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const msg = JSON.parse(line.slice(6));
                if (msg.status === 'done') {
                  resolve({ success: true, file_path: msg.file_path });
                  return;
                } else if (msg.status === 'error') {
                  resolve({ success: false, error: msg.message });
                  return;
                }
              } catch { /* ignore */ }
            }
          }
        }
        resolve({ success: false, error: '下载未完成' });
      });
    });
    req.on('error', (e) => resolve({ success: false, error: e.message }));
    req.end();
  });
});

ipcMain.handle('update-install', async (_event, filePath) => {
  try {
    const { spawn } = require('child_process');
    const fs = require('fs');

    // 校验安装包路径
    if (!filePath || !fs.existsSync(filePath)) {
      return { success: false, error: `安装包不存在: ${filePath}` };
    }

    console.log(`[Sage] Launching installer (detached): ${filePath}`);

    // 使用 detached:true + stdio:'ignore' + unref()
    // 使安装程序脱离 Electron 进程组，应用退出后安装程序可继续运行
    // 否则 app.quit() 会连带杀死子进程，导致下载完成后无法安装
    const child = spawn(
      filePath,
      ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-'],
      {
        detached: true,
        stdio: 'ignore',
        windowsHide: false,
      }
    );

    // unref() 允许父进程独立退出，不等待子进程
    child.unref();

    // 监听启动错误（如文件不可执行）
    child.on('error', (err) => {
      console.error('[Sage] Installer launch error:', err);
    });

    // 给安装程序一点启动时间，然后退出应用
    // killBackend 会用 taskkill /t 终止后端进程树，但安装程序已 detached 不受影响
    setTimeout(() => {
      app.quit();
    }, 2000);
    return { success: true };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

app.whenReady().then(async () => {
  try {
    await startBackend();
    createWindow();
  } catch (err) {
    dialog.showErrorBox(
      'Sage Startup Error',
      `Failed to start the backend service:\n\n${err.message}\n\nPlease ensure sage.exe is available and try again.`
    );
    app.quit();
  }
});

app.on('window-all-closed', () => {
  killBackend();
  app.quit();
});

app.on('before-quit', () => {
  killBackend();
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

// Prevent multiple instances
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}
