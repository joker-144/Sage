<script setup>
import { ref, onMounted } from 'vue'

const currentPath = ref('')
const currentName = ref('')
const entries = ref([])
const loading = ref(false)
const loadingTree = ref(false)

// 路径选择器状态
const showPicker = ref(false)
const pickerPath = ref('')
const pickerEntries = ref([])
const pickerLoading = ref(false)
const breadcrumb = ref([])

// 切换确认
const switchTarget = ref(null)
const switchSuccess = ref(null)

async function loadWorkspace() {
  loading.value = true
  try {
    const res = await fetch('/api/workspace')
    if (res.ok) {
      const data = await res.json()
      currentPath.value = data.path
      currentName.value = data.name
      await loadTree(data.path)
    }
  } catch { /* ignore */ }
  finally { loading.value = false }
}

async function loadTree(path) {
  loadingTree.value = true
  try {
    const res = await fetch(`/api/workspace/tree?path=${encodeURIComponent(path)}`)
    if (res.ok) {
      const data = await res.json()
      entries.value = data.entries || []
    }
  } catch { /* ignore */ }
  finally { loadingTree.value = false }
}

// 路径选择器
async function openPicker() {
  showPicker.value = true
  switchSuccess.value = null
  await browseTo(currentPath.value)
}

async function browseTo(path) {
  pickerLoading.value = true
  try {
    let url
    if (path === 'roots') {
      url = '/api/workspace/tree?path=roots'
    } else {
      url = `/api/workspace/tree?path=${encodeURIComponent(path)}`
    }
    const res = await fetch(url)
    if (res.ok) {
      const data = await res.json()
      pickerPath.value = data.path
      pickerEntries.value = data.entries || []
      updateBreadcrumb(data.path)
    }
  } catch { /* ignore */ }
  finally { pickerLoading.value = false }
}

function updateBreadcrumb(path) {
  if (path === 'roots') {
    breadcrumb.value = [{ name: '此电脑', path: 'roots' }]
    return
  }
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
  const crumbs = [{ name: '此电脑', path: 'roots' }]
  let accumulated = ''
  for (const part of parts) {
    if (part.includes(':')) {
      accumulated = part
    } else {
      accumulated += '/' + part
    }
    crumbs.push({ name: part, path: accumulated })
  }
  breadcrumb.value = crumbs
}

function selectFolder(entry) {
  if (entry.type === 'dir') {
    switchTarget.value = entry.path
  }
}

async function confirmSwitch() {
  if (!switchTarget.value) return
  try {
    const res = await fetch('/api/workspace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: switchTarget.value }),
    })
    if (res.ok) {
      const data = await res.json()
      switchSuccess.value = `已切换到: ${data.path}`
      showPicker.value = false
      switchTarget.value = null
      await loadWorkspace()
    } else {
      const err = await res.json()
      switchSuccess.value = `切换失败: ${err.detail || '未知错误'}`
    }
  } catch (e) {
    switchSuccess.value = `切换失败: ${e.message}`
  }
}

function closePicker() {
  showPicker.value = false
  switchTarget.value = null
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`
  return `${(bytes / 1073741824).toFixed(1)} GB`
}

function getFileIcon(ext, isDir) {
  if (isDir) return 'folder'
  const iconMap = {
    '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
    '.vue': 'vue', '.json': 'json', '.md': 'markdown',
    '.html': 'html', '.css': 'css', '.txt': 'text',
  }
  return iconMap[ext] || 'file'
}

onMounted(loadWorkspace)
</script>

<template>
  <div class="workspace-view">
    <header class="view-header">
      <div class="header-row">
        <div>
          <h1>工作区</h1>
          <p class="subtitle">当前工作目录及文件浏览</p>
        </div>
        <button class="switch-btn" @click="openPicker">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
            <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          </svg>
          切换工作区
        </button>
      </div>
    </header>

    <div v-if="switchSuccess" class="alert" :class="{ 'alert-error': switchSuccess.includes('失败') }">
      {{ switchSuccess }}
      <button class="alert-close" @click="switchSuccess = null">&times;</button>
    </div>

    <!-- 当前工作区路径 -->
    <div class="workspace-info">
      <div class="info-label">当前工作目录</div>
      <div class="info-path">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
          <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>
        </svg>
        <span class="path-text">{{ currentPath || '加载中...' }}</span>
      </div>
    </div>

    <!-- 文件树 -->
    <div class="file-tree-section">
      <div class="tree-header">
        <span>文件列表</span>
        <button class="refresh-btn" @click="loadTree(currentPath)" title="刷新">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
            <path d="M23 4v6h-6M1 20v-6h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
      </div>

      <div v-if="loadingTree" class="tree-loading">加载中...</div>

      <div v-else-if="entries.length === 0" class="tree-empty">目录为空</div>

      <div v-else class="tree-list">
        <div
          v-for="entry in entries"
          :key="entry.path"
          class="tree-item"
          :class="{ 'is-dir': entry.type === 'dir' }"
        >
          <div class="item-icon">
            <svg v-if="entry.type === 'dir'" width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" fill="#f59e0b" stroke="#d97706" stroke-width="1"/>
            </svg>
            <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
              <path d="M14 2v6h6" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
            </svg>
          </div>
          <span class="item-name">{{ entry.name }}</span>
          <span v-if="entry.type === 'file'" class="item-size">{{ formatSize(entry.size) }}</span>
          <span v-if="entry.type === 'dir'" class="item-badge">文件夹</span>
        </div>
      </div>
    </div>

    <!-- 路径选择器弹窗 -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="showPicker" class="picker-overlay" @click.self="closePicker">
          <div class="picker-modal" role="dialog" aria-modal="true">
            <div class="picker-header">
              <div class="picker-title">选择工作目录</div>
              <button class="modal-close" aria-label="关闭" @click="closePicker">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </button>
            </div>

            <!-- 面包屑导航 -->
            <div class="breadcrumb">
              <button
                v-for="(crumb, i) in breadcrumb"
                :key="i"
                class="crumb"
                :class="{ active: i === breadcrumb.length - 1 }"
                @click="browseTo(crumb.path)"
              >
                {{ crumb.name }}
              </button>
            </div>

            <!-- 目录列表 -->
            <div class="picker-body">
              <div v-if="pickerLoading" class="picker-loading">加载中...</div>
              <div v-else-if="pickerEntries.length === 0" class="picker-empty">没有可访问的子目录</div>
              <div v-else class="picker-list">
                <div
                  v-for="entry in pickerEntries.filter(e => e.type === 'dir')"
                  :key="entry.path"
                  class="picker-item"
                  :class="{ selected: switchTarget === entry.path }"
                  @click="selectFolder(entry)"
                  @dblclick="browseTo(entry.path)"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" fill="#f59e0b" stroke="#d97706" stroke-width="1"/>
                  </svg>
                  <span class="picker-item-name">{{ entry.name }}</span>
                  <svg v-if="switchTarget === entry.path" class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </div>
              </div>
            </div>

            <!-- 选中路径预览 -->
            <div v-if="switchTarget" class="picker-preview">
              <span class="preview-label">选中：</span>
              <span class="preview-path">{{ switchTarget }}</span>
            </div>

            <!-- 操作按钮 -->
            <div class="picker-footer">
              <button class="btn-cancel" @click="closePicker">取消</button>
              <button
                class="btn-confirm"
                :disabled="!switchTarget"
                @click="confirmSwitch"
              >
                切换到此目录
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.workspace-view { flex: 1; overflow-y: auto; padding: 30px; max-width: 900px; margin: 0 auto; }

.view-header { margin-bottom: 20px; }
.header-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.view-header h1 { font-size: 22px; font-weight: 650; color: var(--text-primary); letter-spacing: -0.02em; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }

.switch-btn {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 600;
  color: white; background: var(--accent);
  border: none; border-radius: var(--radius-sm);
  padding: 7px 14px; cursor: pointer;
  transition: background 0.18s var(--ease-out-expo);
  flex-shrink: 0;
}
.switch-btn:hover { background: var(--accent-hover); }

.alert {
  display: flex; align-items: center; justify-content: space-between;
  background: var(--accent-soft); border: 1px solid var(--accent-border);
  border-radius: var(--radius-sm); padding: 10px 14px;
  font-size: 12.5px; color: var(--accent); margin-bottom: 16px;
}
.alert-error { background: #fef2f2; border-color: #fecaca; color: #dc2626; }
.alert-close { background: none; border: none; color: inherit; font-size: 18px; cursor: pointer; padding: 0 4px; }

.workspace-info {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 14px 16px; margin-bottom: 16px;
}
.info-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-faint); margin-bottom: 6px; }
.info-path { display: flex; align-items: center; gap: 8px; color: var(--text-secondary); }
.info-path svg { color: var(--accent); flex-shrink: 0; }
.path-text { font-family: var(--font-mono); font-size: 13px; word-break: break-all; }

.file-tree-section {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-lg); overflow: hidden;
}
.tree-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-bottom: 1px solid var(--border-light);
  font-size: 12px; font-weight: 600; color: var(--text-muted);
}
.refresh-btn {
  width: 26px; height: 26px; border: none; border-radius: 5px;
  background: transparent; color: var(--text-faint); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.18s var(--ease-out-expo);
}
.refresh-btn:hover { background: var(--bg-hover); color: var(--text-secondary); }

.tree-loading, .tree-empty {
  padding: 24px; text-align: center; color: var(--text-faint); font-size: 12px;
}

.tree-list { max-height: 500px; overflow-y: auto; }
.tree-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 14px; font-size: 12.5px;
  border-bottom: 1px solid var(--border-light);
  transition: background 0.12s ease;
}
.tree-item:hover { background: var(--bg-hover); }
.tree-item.is-dir .item-name { font-weight: 500; color: var(--text-primary); }
.item-icon { flex-shrink: 0; display: flex; align-items: center; }
.item-name { flex: 1; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item-size { font-size: 10px; color: var(--text-faint); font-family: var(--font-mono); flex-shrink: 0; }
.item-badge {
  font-size: 9px; color: var(--text-faint); background: var(--bg-input);
  border: 1px solid var(--border-light); padding: 1px 6px; border-radius: 3px; flex-shrink: 0;
}

/* 路径选择器 */
.picker-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(6px);
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
}
.picker-modal {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-xl); width: 100%; max-width: 580px;
  max-height: 80vh; display: flex; flex-direction: column;
  box-shadow: var(--shadow-lg); overflow: hidden;
}
.picker-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 18px; border-bottom: 1px solid var(--border-light);
}
.picker-title { font-size: 15px; font-weight: 650; color: var(--text-primary); }
.modal-close {
  width: 28px; height: 28px; border-radius: 6px;
  border: 1px solid transparent; background: transparent;
  color: var(--text-muted); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.18s var(--ease-out-expo);
}
.modal-close:hover { background: var(--bg-hover); color: var(--text-primary); border-color: var(--border-light); }

.breadcrumb {
  display: flex; align-items: center; gap: 2px; flex-wrap: wrap;
  padding: 8px 18px; border-bottom: 1px solid var(--border-light);
  background: var(--bg-input);
}
.crumb {
  font-size: 11.5px; color: var(--text-muted);
  background: none; border: none; cursor: pointer;
  padding: 3px 6px; border-radius: 4px;
  transition: all 0.12s ease;
}
.crumb:hover { background: var(--bg-hover); color: var(--text-secondary); }
.crumb.active { color: var(--accent); font-weight: 600; }
.crumb:not(:last-child)::after { content: '/'; margin-left: 2px; color: var(--text-faint); }

.picker-body { flex: 1; overflow-y: auto; padding: 6px; }
.picker-loading, .picker-empty {
  padding: 32px; text-align: center; color: var(--text-faint); font-size: 12px;
}
.picker-list { display: flex; flex-direction: column; gap: 1px; }
.picker-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; border-radius: var(--radius-sm);
  cursor: pointer; transition: all 0.12s ease;
}
.picker-item:hover { background: var(--bg-hover); }
.picker-item.selected { background: var(--accent-soft); }
.picker-item-name {
  flex: 1; font-size: 12.5px; color: var(--text-secondary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.picker-item.selected .picker-item-name { color: var(--accent); font-weight: 500; }
.check-icon { color: var(--accent); flex-shrink: 0; }

.picker-preview {
  padding: 8px 18px; border-top: 1px solid var(--border-light);
  background: var(--bg-input);
}
.preview-label { font-size: 11px; color: var(--text-faint); }
.preview-path { font-size: 11.5px; font-family: var(--font-mono); color: var(--text-secondary); }

.picker-footer {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 18px; border-top: 1px solid var(--border-light);
}
.btn-cancel {
  font-size: 12px; font-weight: 500;
  color: var(--text-muted); background: var(--bg-input);
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 7px 16px; cursor: pointer;
  transition: all 0.18s var(--ease-out-expo);
}
.btn-cancel:hover { color: var(--text-primary); border-color: var(--border-strong); }
.btn-confirm {
  font-size: 12px; font-weight: 600;
  color: white; background: var(--accent);
  border: none; border-radius: var(--radius-sm);
  padding: 7px 16px; cursor: pointer;
  transition: background 0.18s var(--ease-out-expo);
}
.btn-confirm:hover:not(:disabled) { background: var(--accent-hover); }
.btn-confirm:disabled { opacity: 0.4; cursor: not-allowed; }

.modal-enter-active, .modal-leave-active { transition: opacity 0.2s var(--ease-out-expo); }
.modal-enter-from, .modal-leave-to { opacity: 0; }

@media (max-width: 768px) {
  .workspace-view { padding: 18px 14px; }
  .picker-modal { max-height: 90vh; }
}
</style>
