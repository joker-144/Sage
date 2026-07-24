<script setup>
import { ref, computed, onMounted } from 'vue'

const workspaces = ref([])
const loading = ref(true)
const selectedWs = ref(null)
const papers = ref([])
const papersLoading = ref(false)
const indexStatus = ref(null)

// 创建工作空间
const showCreate = ref(false)
const newWs = ref({ domain_tag: '', description: '', index_level: 'SCI' })

// 文件夹选择器
const showPicker = ref(false)
const pickerPath = ref('')
const pickerEntries = ref([])
const pickerLoading = ref(false)
const breadcrumb = ref([])
const selectedFolder = ref(null)

// 上传文件
const uploadInput = ref(null)
const uploading = ref(false)

// 操作状态
const actionMsg = ref('')
const actionType = ref('')

function showAction(msg, type = 'success') {
  actionMsg.value = msg
  actionType.value = type
  setTimeout(() => { actionMsg.value = '' }, 3000)
}

async function loadWorkspaces() {
  loading.value = true
  try {
    const res = await fetch('/api/sage/workspaces')
    if (res.ok) {
      const data = await res.json()
      workspaces.value = data.workspaces || []
    }
  } catch { /* ignore */ }
  finally { loading.value = false }
}

async function createWorkspace() {
  if (!newWs.value.domain_tag.trim()) {
    showAction('请输入领域标签', 'error')
    return
  }
  try {
    const res = await fetch('/api/sage/workspaces', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newWs.value),
    })
    if (res.ok) {
      showCreate.value = false
      newWs.value = { domain_tag: '', description: '', index_level: 'SCI' }
      showAction('工作空间创建成功')
      await loadWorkspaces()
    } else {
      const err = await res.json()
      showAction(`创建失败: ${err.detail || '未知错误'}`, 'error')
    }
  } catch (e) {
    showAction(`创建失败: ${e.message}`, 'error')
  }
}

async function deleteWorkspace(wsId) {
  if (!confirm('确定删除此工作空间？所有论文和索引将被清除，此操作不可恢复。')) return
  try {
    const res = await fetch(`/api/sage/workspaces/${wsId}`, { method: 'DELETE' })
    if (res.ok) {
      if (selectedWs.value === wsId) selectedWs.value = null
      showAction('工作空间已删除')
      await loadWorkspaces()
    } else {
      showAction('删除失败', 'error')
    }
  } catch (e) {
    showAction(`删除失败: ${e.message}`, 'error')
  }
}

async function switchWorkspace(wsId) {
  try {
    const res = await fetch(`/api/sage/workspaces/${wsId}/switch`, { method: 'POST' })
    if (res.ok) {
      showAction('已切换到该工作空间')
    } else {
      showAction('切换失败', 'error')
    }
  } catch (e) {
    showAction(`切换失败: ${e.message}`, 'error')
  }
}

async function viewPapers(wsId) {
  selectedWs.value = wsId
  papersLoading.value = true
  try {
    const [papersRes, statusRes] = await Promise.all([
      fetch(`/api/sage/workspaces/${wsId}/papers`),
      fetch(`/api/sage/workspaces/${wsId}/index-status`),
    ])
    if (papersRes.ok) {
      const data = await papersRes.json()
      papers.value = data.papers || []
    }
    if (statusRes.ok) {
      const data = await statusRes.json()
      indexStatus.value = data
    }
  } catch { /* ignore */ }
  finally { papersLoading.value = false }
}

// 文件夹选择器
async function openPicker() {
  showPicker.value = true
  await browseTo('roots')
}

async function browseTo(path) {
  pickerLoading.value = true
  try {
    let url = path === 'roots'
      ? '/api/workspace/tree?path=roots'
      : `/api/workspace/tree?path=${encodeURIComponent(path)}`
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
    accumulated = part.includes(':') ? part : accumulated + '/' + part
    crumbs.push({ name: part, path: accumulated })
  }
  breadcrumb.value = crumbs
}

function selectFolder(entry) {
  if (entry.type === 'dir') {
    selectedFolder.value = entry.path
  }
}

async function confirmImport() {
  if (!selectedFolder.value || !selectedWs.value) return
  try {
    const res = await fetch(`/api/sage/workspaces/${selectedWs.value}/import-folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_path: selectedFolder.value }),
    })
    if (res.ok) {
      const data = await res.json()
      showAction(`导入完成: ${data.imported || 0} 个文件，已自动索引`)
      showPicker.value = false
      selectedFolder.value = null
      await viewPapers(selectedWs.value)
      await loadWorkspaces()
    } else {
      const err = await res.json()
      showAction(`导入失败: ${err.detail || '未知错误'}`, 'error')
    }
  } catch (e) {
    showAction(`导入失败: ${e.message}`, 'error')
  }
}

function closePicker() {
  showPicker.value = false
  selectedFolder.value = null
}

// 文件上传
function triggerUpload() {
  uploadInput.value?.click()
}

async function handleUpload(event) {
  const files = event.target.files
  if (!files || files.length === 0 || !selectedWs.value) return
  uploading.value = true
  try {
    for (const file of files) {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('filename', file.name)
      const res = await fetch(`/api/sage/workspaces/${selectedWs.value}/upload`, {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) {
        const err = await res.json()
        showAction(`上传 ${file.name} 失败: ${err.detail || '未知错误'}`, 'error')
        uploading.value = false
        event.target.value = ''
        return
      }
    }
    showAction(`已上传 ${files.length} 个文件，已自动索引`)
    await viewPapers(selectedWs.value)
    await loadWorkspaces()
  } catch (e) {
    showAction(`上传失败: ${e.message}`, 'error')
  } finally {
    uploading.value = false
    event.target.value = ''
  }
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`
  return `${(bytes / 1073741824).toFixed(1)} GB`
}

function formatDate(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}

function getFileIcon(ext) {
  const iconMap = { '.pdf': 'PDF', '.docx': 'DOC', '.doc': 'DOC', '.txt': 'TXT', '.md': 'MD', '.tex': 'TEX', '.bib': 'BIB', '.rtf': 'RTF', '.csv': 'CSV' }
  return iconMap[ext] || 'FILE'
}

function getLevelColor(level) {
  const colors = { SCI: '#8b5cf6', SSCI: '#0ea5e9', CSSCI: '#f59e0b', EI: '#10b981' }
  return colors[level] || 'var(--text-faint)'
}

const selectedWsInfo = computed(() => {
  if (!selectedWs.value) return null
  return workspaces.value.find(w => w.id === selectedWs.value)
})

onMounted(loadWorkspaces)
</script>

<template>
  <div class="workspace-view">
    <header class="view-header">
      <div class="header-row">
        <div>
          <h1>{{ selectedWs ? '论文管理' : '知识库' }}</h1>
          <p class="subtitle">
            {{ selectedWs
              ? `${selectedWsInfo?.domain_tag || ''} · ${papers.length} 篇论文`
              : `管理论文工作空间，导入后自动向量化索引`
            }}
          </p>
        </div>
        <div class="header-actions">
          <button v-if="selectedWs" class="action-btn btn-secondary" @click="selectedWs = null">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M19 12H5M12 19l-7-7 7-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            返回列表
          </button>
          <button v-if="!selectedWs" class="action-btn btn-primary" @click="showCreate = true">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
            创建工作空间
          </button>
        </div>
      </div>
    </header>

    <div v-if="actionMsg" class="alert" :class="{ 'alert-error': actionType === 'error' }">
      {{ actionMsg }}
    </div>

    <!-- 工作空间列表 -->
    <template v-if="!selectedWs">
      <div v-if="loading" class="loading-state">加载中...</div>

      <div v-else-if="workspaces.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none">
            <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
          </svg>
        </div>
        <p>暂无知识库工作空间</p>
        <p class="empty-hint">创建工作空间后可导入论文，系统将自动进行向量化索引</p>
      </div>

      <div v-else class="ws-grid">
        <div
          v-for="ws in workspaces"
          :key="ws.id"
          class="ws-card"
          tabindex="0"
          @click="viewPapers(ws.id)"
          @keydown.enter="viewPapers(ws.id)"
        >
          <div class="ws-card-header">
            <div class="ws-domain-tag" :style="{ borderColor: getLevelColor(ws.index_level) }">
              {{ ws.domain_tag }}
            </div>
            <div class="ws-level-badge" :style="{ color: getLevelColor(ws.index_level) }">
              {{ ws.index_level }}
            </div>
          </div>

          <p class="ws-desc">{{ ws.description || '暂无描述' }}</p>

          <div class="ws-stats">
            <div class="ws-stat">
              <span class="stat-num">{{ ws.papers_count || 0 }}</span>
              <span class="stat-label">论文</span>
            </div>
            <div class="ws-stat">
              <span class="stat-num">{{ ws.indexed ? '✓' : '—' }}</span>
              <span class="stat-label">索引</span>
            </div>
          </div>

          <div class="ws-card-footer">
            <span class="ws-date">{{ formatDate(ws.created_at) }}</span>
            <div class="ws-card-actions">
              <button class="mini-btn" title="切换到此工作空间" @click.stop="switchWorkspace(ws.id)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M12 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
              <button class="mini-btn mini-btn-danger" title="删除工作空间" @click.stop="deleteWorkspace(ws.id)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 论文管理详情 -->
    <template v-else>
      <!-- 操作按钮 -->
      <div class="paper-actions">
        <button class="action-btn btn-primary" @click="openPicker">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>
          导入文件夹
        </button>
        <button class="action-btn btn-secondary" :disabled="uploading" @click="triggerUpload">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
          {{ uploading ? '上传中...' : '上传论文' }}
        </button>
        <input ref="uploadInput" type="file" multiple accept=".pdf,.docx,.doc,.txt,.md,.tex,.bib,.rtf,.csv" style="display:none" @change="handleUpload" />

        <!-- 索引状态 -->
        <div v-if="indexStatus" class="index-status">
          <span class="status-dot" :class="{ indexed: indexStatus.indexed }"></span>
          <span>{{ indexStatus.indexed ? `已索引 (${indexStatus.chunks || 0} 个文档块)` : '未索引' }}</span>
        </div>
      </div>

      <!-- 论文列表 -->
      <div v-if="papersLoading" class="loading-state">加载中...</div>

      <div v-else-if="papers.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
            <path d="M14 2v6h6" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
          </svg>
        </div>
        <p>此工作空间暂无论文</p>
        <p class="empty-hint">通过导入文件夹或上传论文来添加文献</p>
      </div>

      <div v-else class="paper-list">
        <div v-for="paper in papers" :key="paper.path" class="paper-item">
          <div class="paper-icon" :class="{ [`ext-${getFileIcon(paper.ext).toLowerCase()}`]: true }">
            {{ getFileIcon(paper.ext) }}
          </div>
          <div class="paper-info">
            <div class="paper-name">{{ paper.name }}</div>
            <div class="paper-meta">
              <span>{{ formatSize(paper.size) }}</span>
              <span class="dot-sep">·</span>
              <span>{{ formatDate(paper.modified) }}</span>
            </div>
          </div>
          <span class="paper-ext">{{ paper.ext }}</span>
        </div>
      </div>
    </template>

    <!-- 创建工作空间弹窗 -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
          <div class="modal" role="dialog" aria-modal="true">
            <div class="modal-header">
              <h2>创建知识库工作空间</h2>
              <button class="modal-close" aria-label="关闭" @click="showCreate = false">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </div>
            <div class="modal-body">
              <div class="form-group">
                <label>领域标签 *</label>
                <input v-model="newWs.domain_tag" class="form-input" placeholder="如 CS-AI, MED-Cardio, SSCI-PSY" />
                <span class="form-hint">仅字母/数字/连字符/下划线，2-32 字符</span>
              </div>
              <div class="form-group">
                <label>描述</label>
                <textarea v-model="newWs.description" class="form-textarea" rows="2" placeholder="工作空间描述（可选）"></textarea>
              </div>
              <div class="form-group">
                <label>索引级别</label>
                <div class="level-selector">
                  <button
                    v-for="level in ['SCI', 'SSCI', 'CSSCI', 'EI']"
                    :key="level"
                    class="level-btn"
                    :class="{ active: newWs.index_level === level }"
                    :style="newWs.index_level === level ? { borderColor: getLevelColor(level), color: getLevelColor(level) } : {}"
                    @click="newWs.index_level = level"
                  >{{ level }}</button>
                </div>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn-cancel" @click="showCreate = false">取消</button>
              <button class="btn-confirm" @click="createWorkspace">创建</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- 文件夹选择器弹窗 -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="showPicker" class="modal-overlay" @click.self="closePicker">
          <div class="modal picker-modal" role="dialog" aria-modal="true">
            <div class="modal-header">
              <h2>选择论文文件夹</h2>
              <button class="modal-close" aria-label="关闭" @click="closePicker">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </div>
            <div class="breadcrumb">
              <button
                v-for="(crumb, i) in breadcrumb"
                :key="i"
                class="crumb"
                :class="{ active: i === breadcrumb.length - 1 }"
                @click="browseTo(crumb.path)"
              >{{ crumb.name }}</button>
            </div>
            <div class="picker-body">
              <div v-if="pickerLoading" class="picker-loading">加载中...</div>
              <div v-else-if="pickerEntries.filter(e => e.type === 'dir').length === 0" class="picker-empty">没有子目录</div>
              <div v-else class="picker-list">
                <div
                  v-for="entry in pickerEntries.filter(e => e.type === 'dir')"
                  :key="entry.path"
                  class="picker-item"
                  :class="{ selected: selectedFolder === entry.path }"
                  @click="selectFolder(entry)"
                  @dblclick="browseTo(entry.path)"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" fill="#f59e0b" stroke="#d97706" stroke-width="1"/></svg>
                  <span class="picker-item-name">{{ entry.name }}</span>
                  <svg v-if="selectedFolder === entry.path" class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
                </div>
              </div>
            </div>
            <div v-if="selectedFolder" class="picker-preview">
              <span class="preview-label">选中：</span>
              <span class="preview-path">{{ selectedFolder }}</span>
            </div>
            <div class="modal-footer">
              <button class="btn-cancel" @click="closePicker">取消</button>
              <button class="btn-confirm" :disabled="!selectedFolder" @click="confirmImport">导入并索引</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.workspace-view { flex: 1; overflow-y: auto; padding: 30px; max-width: 1100px; margin: 0 auto; }

.view-header { margin-bottom: 24px; }
.header-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.view-header h1 { font-size: 22px; font-weight: 650; color: var(--text-primary); letter-spacing: -0.02em; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }

.header-actions { display: flex; gap: 8px; flex-shrink: 0; }

.action-btn {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 600;
  border: none; border-radius: var(--radius-sm);
  padding: 7px 14px; cursor: pointer;
  transition: all 0.18s var(--ease-out-expo);
}
.btn-primary { color: white; background: var(--accent); }
.btn-primary:hover { background: var(--accent-hover); }
.btn-secondary { color: var(--text-secondary); background: var(--bg-surface); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent-border); color: var(--text-primary); }
.btn-secondary:disabled { opacity: 0.4; cursor: not-allowed; }

.alert {
  background: var(--accent-soft); border: 1px solid var(--accent-border);
  border-radius: var(--radius-sm); padding: 10px 14px;
  font-size: 12.5px; color: var(--accent); margin-bottom: 16px;
}
.alert-error { background: #fef2f2; border-color: #fecaca; color: #dc2626; }

.loading-state { text-align: center; padding: 40px; color: var(--text-faint); font-size: 13px; }

.empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); }
.empty-icon { color: var(--text-faint); margin-bottom: 12px; }
.empty-state p { margin: 0 0 6px; font-size: 13px; }
.empty-hint { font-size: 11px !important; color: var(--text-faint); }

/* 工作空间卡片 */
.ws-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }

.ws-card {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 16px;
  transition: transform 0.18s var(--ease-out-expo), box-shadow 0.18s var(--ease-out-expo), border-color 0.18s var(--ease-out-expo);
  display: flex; flex-direction: column; gap: 12px; cursor: pointer;
}
.ws-card:hover, .ws-card:focus {
  transform: translateY(-2px); border-color: var(--accent-border);
  box-shadow: var(--shadow-md); outline: none;
}

.ws-card-header { display: flex; align-items: center; justify-content: space-between; }
.ws-domain-tag {
  font-size: 13px; font-weight: 700; color: var(--text-primary);
  padding: 4px 10px; border-radius: var(--radius-sm);
  border: 2px solid; letter-spacing: 0.02em;
}
.ws-level-badge { font-size: 10px; font-weight: 600; font-family: var(--font-mono); }

.ws-desc { font-size: 12px; color: var(--text-secondary); line-height: 1.6; margin: 0; }

.ws-stats { display: flex; gap: 20px; }
.ws-stat { display: flex; flex-direction: column; align-items: center; }
.stat-num { font-size: 18px; font-weight: 700; color: var(--accent); font-family: var(--font-mono); }
.stat-label { font-size: 10px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.04em; }

.ws-card-footer { display: flex; align-items: center; justify-content: space-between; margin-top: 4px; }
.ws-date { font-size: 10px; color: var(--text-faint); }
.ws-card-actions { display: flex; gap: 4px; }
.mini-btn {
  width: 26px; height: 26px; border: 1px solid var(--border-light); border-radius: 5px;
  background: var(--bg-input); color: var(--text-muted); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.18s var(--ease-out-expo);
}
.mini-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
.mini-btn-danger:hover { background: #fef2f2; color: #dc2626; border-color: #fecaca; }

/* 论文操作栏 */
.paper-actions { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.index-status { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-muted); }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text-faint); }
.status-dot.indexed { background: var(--success); }

/* 论文列表 */
.paper-list { display: flex; flex-direction: column; gap: 4px; }
.paper-item {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px; background: var(--bg-surface);
  border: 1px solid var(--border-light); border-radius: var(--radius-sm);
  transition: all 0.12s ease;
}
.paper-item:hover { border-color: var(--border); background: var(--bg-elevated); }
.paper-icon {
  width: 32px; height: 32px; border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 700; color: white; flex-shrink: 0;
}
.ext-pdf { background: #dc2626; }
.ext-doc, .ext-docx { background: #2563eb; }
.ext-tex { background: #8b5cf6; }
.ext-md { background: #10b981; }
.ext-bib { background: #f59e0b; }
.ext-txt, .ext-csv, .ext-rtf { background: var(--text-faint); }
.paper-info { flex: 1; min-width: 0; }
.paper-name { font-size: 13px; font-weight: 500; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.paper-meta { font-size: 10px; color: var(--text-faint); margin-top: 2px; display: flex; gap: 4px; }
.dot-sep { color: var(--text-faint); }
.paper-ext { font-size: 10px; color: var(--text-faint); font-family: var(--font-mono); }

/* 弹窗通用 */
.modal-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(15, 23, 42, 0.45); backdrop-filter: blur(6px);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.modal {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-xl); width: 100%; max-width: 480px;
  max-height: 80vh; display: flex; flex-direction: column;
  box-shadow: var(--shadow-lg); overflow: hidden;
}
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid var(--border-light);
}
.modal-header h2 { font-size: 15px; font-weight: 650; color: var(--text-primary); }
.modal-close {
  width: 28px; height: 28px; border-radius: 6px;
  border: 1px solid transparent; background: transparent;
  color: var(--text-muted); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.18s var(--ease-out-expo);
}
.modal-close:hover { background: var(--bg-hover); color: var(--text-primary); border-color: var(--border-light); }

.modal-body { padding: 18px 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 20px; border-top: 1px solid var(--border-light); }

/* 表单 */
.form-group { display: flex; flex-direction: column; gap: 5px; }
.form-group label { font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.form-input, .form-textarea {
  font-family: var(--font-sans); font-size: 13px;
  background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 8px 12px;
  color: var(--text-primary); transition: border-color 0.18s var(--ease-out-expo);
  outline: none; width: 100%;
}
.form-input:focus, .form-textarea:focus { border-color: var(--accent); }
.form-textarea { resize: vertical; min-height: 36px; }
.form-hint { font-size: 10px; color: var(--text-faint); }

.level-selector { display: flex; gap: 6px; }
.level-btn {
  flex: 1; font-size: 12px; font-weight: 600;
  padding: 7px 0; border-radius: var(--radius-sm);
  border: 1px solid var(--border); background: var(--bg-input);
  color: var(--text-muted); cursor: pointer;
  transition: all 0.18s var(--ease-out-expo);
}
.level-btn:hover { border-color: var(--border-strong); }

/* 文件夹选择器 */
.picker-modal { max-width: 580px; max-height: 80vh; }
.breadcrumb {
  display: flex; align-items: center; gap: 2px; flex-wrap: wrap;
  padding: 8px 20px; border-bottom: 1px solid var(--border-light);
  background: var(--bg-input);
}
.crumb {
  font-size: 11.5px; color: var(--text-muted);
  background: none; border: none; cursor: pointer;
  padding: 3px 6px; border-radius: 4px; transition: all 0.12s ease;
}
.crumb:hover { background: var(--bg-hover); color: var(--text-secondary); }
.crumb.active { color: var(--accent); font-weight: 600; }
.crumb:not(:last-child)::after { content: '/'; margin-left: 2px; color: var(--text-faint); }

.picker-body { flex: 1; overflow-y: auto; padding: 6px; }
.picker-loading, .picker-empty { padding: 32px; text-align: center; color: var(--text-faint); font-size: 12px; }
.picker-list { display: flex; flex-direction: column; gap: 1px; }
.picker-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; border-radius: var(--radius-sm);
  cursor: pointer; transition: all 0.12s ease;
}
.picker-item:hover { background: var(--bg-hover); }
.picker-item.selected { background: var(--accent-soft); }
.picker-item-name { flex: 1; font-size: 12.5px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-item.selected .picker-item-name { color: var(--accent); font-weight: 500; }
.check-icon { color: var(--accent); flex-shrink: 0; }

.picker-preview { padding: 8px 20px; border-top: 1px solid var(--border-light); background: var(--bg-input); }
.preview-label { font-size: 11px; color: var(--text-faint); }
.preview-path { font-size: 11.5px; font-family: var(--font-mono); color: var(--text-secondary); word-break: break-all; }

/* 按钮 */
.btn-cancel {
  font-size: 12px; font-weight: 500; color: var(--text-muted);
  background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 7px 16px; cursor: pointer;
  transition: all 0.18s var(--ease-out-expo);
}
.btn-cancel:hover { color: var(--text-primary); border-color: var(--border-strong); }
.btn-confirm {
  font-size: 12px; font-weight: 600; color: white;
  background: var(--accent); border: none; border-radius: var(--radius-sm);
  padding: 7px 16px; cursor: pointer; transition: background 0.18s var(--ease-out-expo);
}
.btn-confirm:hover:not(:disabled) { background: var(--accent-hover); }
.btn-confirm:disabled { opacity: 0.4; cursor: not-allowed; }

.modal-enter-active, .modal-leave-active { transition: opacity 0.2s var(--ease-out-expo); }
.modal-enter-from, .modal-leave-to { opacity: 0; }

@media (max-width: 900px) {
  .ws-grid { grid-template-columns: 1fr; }
  .workspace-view { padding: 24px 18px; }
}
@media (max-width: 768px) {
  .workspace-view { padding: 18px 14px; }
}
</style>
