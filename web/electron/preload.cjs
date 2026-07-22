const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.invoke('window-minimize'),
  maximize: () => ipcRenderer.invoke('window-maximize'),
  close: () => ipcRenderer.invoke('window-close'),
  isMaximized: () => ipcRenderer.invoke('window-is-maximized'),
  onMaximizeChange: (callback) => {
    ipcRenderer.on('maximize-change', (_event, isMaximized) => callback(isMaximized));
  },
  checkVersion: () => ipcRenderer.invoke('check-version'),
  updateDownload: () => ipcRenderer.invoke('update-download'),
  updateInstall: (filePath) => ipcRenderer.invoke('update-install', filePath),
  onUpdateProgress: (callback) => {
    ipcRenderer.on('update-download-progress', (_event, msg) => callback(msg));
  },
});
