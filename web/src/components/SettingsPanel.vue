<script setup>
import { ref, onMounted, computed, watch } from 'vue'

// ── 供应商定义 ──
const providers = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    defaultBaseUrl: 'https://api.deepseek.com/v1',
  },
  {
    id: 'openai',
    name: 'OpenAI',
    defaultBaseUrl: 'https://api.openai.com/v1',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    defaultBaseUrl: 'https://api.anthropic.com/v1',
  },
  {
    id: 'qwen',
    name: '通义千问 (Qwen)',
    defaultBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  },
  {
    id: 'zhipu',
    name: '智谱 (GLM)',
    defaultBaseUrl: 'https://open.bigmodel.cn/api/paas/v4',
  },
  {
    id: 'moonshot',
    name: '月之暗面 (Moonshot)',
    defaultBaseUrl: 'https://api.moonshot.cn/v1',
  },
  {
    id: 'custom',
    name: '自定义接口',
    defaultBaseUrl: '',
  },
]

const DEFAULT_SETTINGS = {
  provider: 'deepseek',
  apiKeys: {},       // { providerId: 'sk-xxx' }
  model: 'deepseek-chat',
  baseUrl: 'https://api.deepseek.com/v1',
  temperature: 0.3,
  maxTokens: 4096,
}

const settings = ref(structuredClone(DEFAULT_SETTINGS))
const saved = ref(false)

const activeProvider = computed(() => providers.find((p) => p.id === settings.value.provider))

// 动态模型列表（从后端 API 获取）
const providerModels = ref([])
const modelsLoading = ref(false)
const modelsError = ref('')

async function fetchModels(providerId, apiKey, baseUrl) {
  providerModels.value = []
  modelsError.value = ''

  if (!providerId || !baseUrl) {
    modelsError.value = '请先配置 Base URL'
    return
  }

  if (!apiKey) {
    modelsError.value = '请先配置 API Key'
    return
  }

  modelsLoading.value = true
  try {
    const params = new URLSearchParams({ provider: providerId })
    if (apiKey) params.set('api_key', apiKey)
    if (baseUrl) params.set('base_url', baseUrl)

    const resp = await fetch(`/api/models?${params}`)
    const data = await resp.json()

    if (data.error) {
      modelsError.value = data.error
      return
    }

    providerModels.value = (data.models || []).map(m => ({
      value: m.id,
      label: m.name || m.id,
    }))

    // 缓存模型列表到 localStorage 供 ChatInput 使用
    try {
      const cache = JSON.parse(localStorage.getItem('sage-models-cache') || '{}')
      cache[providerId] = providerModels.value
      localStorage.setItem('sage-models-cache', JSON.stringify(cache))
    } catch { /* ignore */ }

    // 如果当前选中的模型不在新列表中，自动选第一个
    if (providerModels.value.length && !providerModels.value.find(m => m.value === settings.value.model)) {
      settings.value.model = providerModels.value[0].value
    }
  } catch (e) {
    modelsError.value = `连接失败: ${e.message}`
  } finally {
    modelsLoading.value = false
  }
}

// provider 切换时自动同步 baseUrl + 拉取模型
watch(() => settings.value.provider, (newProviderId) => {
  const p = providers.find((pr) => pr.id === newProviderId)
  if (p && p.id !== 'custom') {
    settings.value.baseUrl = p.defaultBaseUrl
  }
  fetchModels(newProviderId, settings.value.apiKeys[newProviderId], settings.value.baseUrl)
})

// 当 API Key 或 Base URL 变更时重新拉取模型（防抖 800ms）
let modelFetchTimer = null
watch([() => settings.value.apiKeys[settings.value.provider], () => settings.value.baseUrl], () => {
  clearTimeout(modelFetchTimer)
  modelFetchTimer = setTimeout(() => {
    fetchModels(settings.value.provider, settings.value.apiKeys[settings.value.provider], settings.value.baseUrl)
  }, 800)
})

// ── 模型管理状态 ──
const allProviderModels = ref({})
const showModelManager = ref(false)
const refreshingAll = ref(false)

function loadAllProviderModels() {
  try {
    const cached = localStorage.getItem('sage-models-cache')
    if (cached) {
      allProviderModels.value = JSON.parse(cached)
    }
  } catch { /* ignore */ }
}

async function refreshAllModels() {
  refreshingAll.value = true
  const results = []
  for (const p of providers) {
    const key = settings.value.apiKeys[p.id]
    const url = p.id === 'custom' ? settings.value.baseUrl : p.defaultBaseUrl
    if (!key || !url) continue
    try {
      const params = new URLSearchParams({ provider: p.id, api_key: key, base_url: url })
      const resp = await fetch(`/api/models?${params}`)
      const data = await resp.json()
      if (data.models?.length) {
        results.push({ provider: p.id, models: data.models })
      }
    } catch { /* skip */ }
  }
  // 写入缓存
  const cache = {}
  for (const r of results) {
    cache[r.provider] = r.models.map(m => ({ value: m.id, label: m.name || m.id }))
  }
  try {
    localStorage.setItem('sage-models-cache', JSON.stringify(cache))
  } catch { /* ignore */ }
  allProviderModels.value = cache
  refreshingAll.value = false
}

function setActiveModel(providerId, modelId) {
  settings.value.provider = providerId
  settings.value.model = modelId
  const p = providers.find(pr => pr.id === providerId)
  if (p && p.id !== 'custom') {
    settings.value.baseUrl = p.defaultBaseUrl
  }
  saveSettings()
  // 同步 ChatInput 模型选择器
  try {
    const stored = localStorage.getItem('sage-settings')
    if (stored) {
      const parsed = JSON.parse(stored)
      parsed.provider = providerId
      parsed.model = modelId
      if (p && p.id !== 'custom') parsed.baseUrl = p.defaultBaseUrl
      localStorage.setItem('sage-settings', JSON.stringify(parsed))
    }
  } catch { /* ignore */ }
}

// ── 版本更新状态 ──
const currentVersion = ref('')
const latestVersion = ref('')
const hasUpdate = ref(false)
const changelog = ref('')
const releaseUrl = ref('')
const downloadUrl = ref('')
const updateSource = ref('none')
const checkingUpdate = ref(false)
const updatingVersion = ref(false)
const updateLog = ref([])
const updateDone = ref(false)

// ── 下载进度模态框状态 ──
const showDownloadModal = ref(false)
const downloadProgress = ref(0)
const downloadStatusText = ref('准备中…')
const downloadMessage = ref('')
const downloadedFilePath = ref('')
const downloadError = ref('')
const downloadPhase = ref('idle')  // idle | downloading | done | error

// 是否运行在 Electron 中
const isElectron = computed(() => !!window.electronAPI)

// 更新来源徽章文本
const sourceBadgeText = computed(() => {
  switch (updateSource.value) {
    case 'github': return 'GitHub 直连'
    case 'github-mirror': return 'GitHub 镜像'
    case 'pypi': return 'PyPI'
    default: return '—'
  }
})

// 模态框标题
const modalTitleText = computed(() => {
  switch (downloadPhase.value) {
    case 'done': return '下载完成'
    case 'error': return '下载失败'
    case 'downloading': return '正在下载更新'
    default: return '准备下载'
  }
})

// 所有供应商模型总数
const totalModelCount = computed(() => {
  let count = 0
  for (const pid of Object.keys(allProviderModels.value)) {
    count += (allProviderModels.value[pid] || []).length
  }
  return count
})

onMounted(() => {
  // 1. 优先从 localStorage 加载（端口固定后与上次一致）
  let loaded = false
  try {
    const stored = localStorage.getItem('sage-settings')
    if (stored) {
      const parsed = JSON.parse(stored)
      const merged = { ...structuredClone(DEFAULT_SETTINGS), ...parsed }
      settings.value = merged
      loaded = true
    }
  } catch { /* ignore */ }

  // 2. localStorage 无数据时，回退到服务端磁盘持久化
  if (!loaded) {
    fetch('/api/user-settings')
      .then(r => r.json())
      .then(data => {
        if (data && data.provider) {
          const merged = { ...structuredClone(DEFAULT_SETTINGS), ...data }
          settings.value = merged
          // 同步回 localStorage，保证后续读写一致
          localStorage.setItem('sage-settings', JSON.stringify(merged))
        }
      })
      .catch(() => {})
      .finally(() => {
        fetchModels(settings.value.provider, settings.value.apiKeys[settings.value.provider], settings.value.baseUrl)
        loadAllProviderModels()
      })
    } else {
      fetchModels(settings.value.provider, settings.value.apiKeys[settings.value.provider], settings.value.baseUrl)
      loadAllProviderModels()
    }
})

function saveSettings() {
  try {
    localStorage.setItem('sage-settings', JSON.stringify(settings.value))
  } catch { /* ignore */ }
  // 同步写入服务端磁盘持久化
  fetch('/api/user-settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings.value),
  }).catch(() => {})
  saved.value = true
  setTimeout(() => saved.value = false, 2000)
}

function resetSettings() {
  settings.value = structuredClone(DEFAULT_SETTINGS)
  saveSettings()
}

// ── 版本更新逻辑 ──

async function checkVersion() {
  checkingUpdate.value = true
  currentVersion.value = ''
  latestVersion.value = ''
  hasUpdate.value = false
  changelog.value = ''
  updateDone.value = false

  try {
    let data
    if (window.electronAPI) {
      data = await window.electronAPI.checkVersion()
    } else {
      const resp = await fetch('/api/version/check')
      data = await resp.json()
    }

    currentVersion.value = data.current || ''
    latestVersion.value = data.latest || ''
    hasUpdate.value = data.has_update || false
    changelog.value = data.changelog || ''
    releaseUrl.value = data.release_url || ''
    downloadUrl.value = data.download_url || ''
    updateSource.value = data.source || 'none'
  } catch (e) {
    updateLog.value = [{ text: `检查更新失败: ${e.message}`, url: '' }]
  } finally {
    checkingUpdate.value = false
  }
}

// 解析 SSE/IPC 进度消息，更新模态框状态
function handleProgressMessage(msg) {
  if (!msg) return

  // 保留到日志（兼容旧 UI 与调试）
  updateLog.value.push({ text: msg.message || JSON.stringify(msg), url: msg.release_url || '' })

  const status = msg.status
  const message = msg.message || ''
  const percent = msg.percent

  if (status === 'info') {
    // 信息性消息：镜像切换、续传提示等
    downloadStatusText.value = message
    downloadMessage.value = message
  } else if (status === 'progress') {
    downloadPhase.value = 'downloading'
    downloadStatusText.value = '下载中'
    downloadMessage.value = message
    if (typeof percent === 'number') {
      downloadProgress.value = Math.min(100, Math.max(0, percent))
    }
  } else if (status === 'done') {
    downloadPhase.value = 'done'
    downloadProgress.value = 100
    downloadStatusText.value = '下载完成'
    downloadMessage.value = message || '下载完成'
    downloadedFilePath.value = msg.file_path || ''
    updateDone.value = true
  } else if (status === 'error') {
    downloadPhase.value = 'error'
    downloadStatusText.value = '下载失败'
    downloadError.value = message
    downloadMessage.value = message
    if (msg.release_url) releaseUrl.value = msg.release_url
  }
}

function resetDownloadModal() {
  downloadProgress.value = 0
  downloadStatusText.value = '准备中…'
  downloadMessage.value = ''
  downloadedFilePath.value = ''
  downloadError.value = ''
  downloadPhase.value = 'idle'
  updateLog.value = []
}

async function startUpdate() {
  updatingVersion.value = true
  resetDownloadModal()
  showDownloadModal.value = true

  try {
    // Electron 模式：通过主进程下载（IPC 流式进度）
    if (window.electronAPI) {
      // 监听下载进度
      window.electronAPI.onUpdateProgress((msg) => {
        handleProgressMessage(msg)
      })

      const dlResult = await window.electronAPI.updateDownload()
      if (!dlResult.success) {
        downloadPhase.value = 'error'
        downloadStatusText.value = '下载失败'
        downloadError.value = dlResult.error || '未知错误'
        downloadMessage.value = dlResult.error || '下载失败'
      }
      // 下载成功后由 handleProgressMessage 的 done 事件处理
    } else {
      // 浏览器模式：SSE 流式下载
      const resp = await fetch('/api/version/download', { method: 'POST' })
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          const trimmed = line.trim()
          if (trimmed.startsWith('data: ')) {
            try {
              const msg = JSON.parse(trimmed.slice(6))
              handleProgressMessage(msg)
            } catch {
              // 忽略无法解析的行
            }
          }
        }
      }
      // 处理剩余缓冲
      if (buffer.trim().startsWith('data: ')) {
        try {
          const msg = JSON.parse(buffer.trim().slice(6))
          handleProgressMessage(msg)
        } catch { /* ignore */ }
      }
    }
  } catch (e) {
    downloadPhase.value = 'error'
    downloadStatusText.value = '更新异常'
    downloadError.value = e.message
    downloadMessage.value = `更新异常: ${e.message}`
  } finally {
    updatingVersion.value = false
  }
}

// 触发安装（Electron 模式）
async function installUpdate() {
  if (!downloadedFilePath.value) return

  try {
    downloadStatusText.value = '正在启动安装程序…'
    if (window.electronAPI) {
      const result = await window.electronAPI.updateInstall(downloadedFilePath.value)
      if (!result.success) {
        downloadPhase.value = 'error'
        downloadError.value = result.error || '安装启动失败'
        downloadStatusText.value = '安装失败'
        downloadMessage.value = `安装启动失败: ${result.error}`
      }
      // 安装程序启动后，应用会自动退出（main.cjs 中 app.quit()）
    } else {
      // 浏览器模式：提示用户手动运行安装包
      downloadStatusText.value = '请在文件管理器中双击运行安装包'
      downloadMessage.value = `安装包路径: ${downloadedFilePath.value}`
    }
  } catch (e) {
    downloadPhase.value = 'error'
    downloadError.value = e.message
    downloadStatusText.value = '安装失败'
    downloadMessage.value = `安装失败: ${e.message}`
  }
}

function closeDownloadModal() {
  // 下载进行中不允许关闭
  if (downloadPhase.value === 'downloading') return
  showDownloadModal.value = false
}
</script>

<template>
  <div class="settings">
    <header class="settings-header">
      <h1>设置</h1>
      <p class="subtitle">配置模型供应商、API Key 与参数</p>
    </header>

    <!-- ── 模型供应商 ── -->
    <div class="form-section">
      <h2>API 供应商</h2>
      <p class="section-desc">选择要使用的模型供应商，并提供对应的 API Key</p>

      <div class="provider-grid">
        <button
          v-for="p in providers"
          :key="p.id"
          class="provider-card"
          :class="{ active: settings.provider === p.id }"
          @click="settings.provider = p.id"
        >
          <span class="provider-name">{{ p.name }}</span>
          <span v-if="p.id !== 'custom'" class="provider-model-count">{{ providerModels.length || '...' }} 个模型</span>
        </button>
      </div>

      <div class="form-group">
        <label>API Key</label>
        <div class="input-with-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="3" y="11" width="18" height="11" rx="2" stroke="currentColor" stroke-width="2"/><path d="M7 11V7a5 5 0 0110 0v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          <input
            v-model="settings.apiKeys[settings.provider]"
            type="password"
            :placeholder="activeProvider?.id === 'custom' ? '输入完整 API Key' : `输入 ${activeProvider?.name} API Key`"
          />
        </div>
        <p class="hint">API Key 仅保存在浏览器本地存储中，不会上传到服务器。</p>
      </div>

      <div class="form-group">
        <label>API Base URL</label>
        <div class="input-with-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/><path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" stroke="currentColor" stroke-width="2"/></svg>
          <input
            v-model="settings.baseUrl"
            type="url"
            placeholder="https://api.example.com/v1"
          />
        </div>
        <p class="hint">API 端点地址。预设供应商会自动填充，切换供应商将覆盖自定义地址。</p>
      </div>
    </div>

    <!-- ── 模型与参数 ── -->
    <div class="form-section">
      <h2>模型与参数</h2>

      <div class="form-group">
        <label>模型选择
          <span v-if="modelsLoading" class="models-status loading">加载中...</span>
        </label>
        <div class="select-wrapper">
          <select v-model="settings.model" :disabled="modelsLoading">
            <template v-if="providerModels.length">
              <option v-for="m in providerModels" :key="m.value" :value="m.value">{{ m.label }}</option>
            </template>
            <option v-else value="">— 请先配置 API Key —</option>
          </select>
          <svg class="select-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </div>
        <p v-if="modelsError" class="hint error">{{ modelsError }}</p>
      </div>

      <div class="form-group">
        <label>自定义模型 ID</label>
        <div class="input-with-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="18" height="18" rx="3" stroke="currentColor" stroke-width="2"/><path d="M8 12h8M12 8v8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          <input
            v-model="settings.model"
            type="text"
            placeholder="如 gpt-4o, claude-3-sonnet，填写后覆盖上方下拉选择"
          />
        </div>
        <p class="hint">直接输入模型 ID 可覆盖下拉列表中的选项，用于使用最新或未列出的模型。</p>
      </div>

      <div class="form-group">
        <label>Temperature <span class="param-value">({{ settings.temperature }})</span></label>
        <div class="slider-row">
          <span class="slider-end">0</span>
          <input v-model.number="settings.temperature" type="range" min="0" max="2" step="0.1" class="slider" />
          <span class="slider-end">2.0</span>
        </div>
        <div class="range-labels"><span>精确</span><span>平衡</span><span>创造</span></div>
        <p class="hint">越高越有创造性，越低越精确。论文写作建议 0.3-0.5。</p>
      </div>

      <div class="form-group">
        <label>最大 Token 数</label>
        <div class="preset-row">
          <button
            v-for="n in [2048, 4096, 8192, 16384, 32768]"
            :key="n"
            class="preset-chip"
            :class="{ active: settings.maxTokens === n }"
            @click="settings.maxTokens = n"
          >{{ n >= 1024 ? n / 1024 + 'K' : n }}</button>
          <input
            v-model.number="settings.maxTokens"
            type="number"
            min="512"
            max="32768"
            step="512"
            class="number-input inline"
            placeholder="自定义"
          />
        </div>
        <p class="hint">单次回复的最大 Token 数量，较大值适合长文档生成。</p>
      </div>
    </div>

    <!-- ── 模型管理 ── -->
    <div class="form-section">
      <h2>
        模型管理
        <span class="section-badge">{{ totalModelCount }} 个模型</span>
      </h2>
      <p class="section-desc">
        查看所有已配置供应商的可用模型，点击模型可一键切换。配置了 API Key 的供应商会自动拉取模型列表。
      </p>

      <div class="model-manager-actions">
        <button class="btn-secondary btn-sm" @click="refreshAllModels" :disabled="refreshingAll">
          {{ refreshingAll ? '刷新中...' : '刷新全部模型' }}
        </button>
      </div>

      <div class="provider-model-list">
        <div
          v-for="p in providers"
          :key="p.id"
          class="provider-model-group"
        >
          <div class="pmg-header">
            <span class="pmg-provider-name">{{ p.name }}</span>
            <span
              class="pmg-api-status"
              :class="{ configured: settings.apiKeys[p.id] }"
            >
              {{ settings.apiKeys[p.id] ? '已配置' : '未配置' }}
            </span>
            <span v-if="settings.provider === p.id" class="pmg-active-badge">当前</span>
          </div>

          <div
            v-if="allProviderModels[p.id]?.length"
            class="pmg-models"
          >
            <button
              v-for="m in allProviderModels[p.id]"
              :key="m.value"
              class="pmg-model-chip"
              :class="{ active: settings.provider === p.id && settings.model === m.value }"
              @click="setActiveModel(p.id, m.value)"
              :title="m.value"
            >
              {{ m.label }}
            </button>
          </div>
          <div v-else class="pmg-empty">
            {{ settings.apiKeys[p.id] ? '暂无模型缓存，请点击上方刷新' : '配置 API Key 后可拉取模型' }}
          </div>
        </div>
      </div>
    </div>

    <!-- ── 版本更新 ── -->
    <div class="form-section">
      <h2>版本更新</h2>

      <div class="version-info">
        <div class="version-row">
          <span class="version-label">当前版本</span>
          <span class="version-value" :class="{ pulse: checkingUpdate }">
            {{ currentVersion || '—' }}
          </span>
        </div>
        <div v-if="latestVersion" class="version-row">
          <span class="version-label">最新版本</span>
          <span class="version-value latest">{{ latestVersion }}</span>
        </div>
        <!-- 来源/状态：检测成功显示来源徽章（即使版本相同，让用户确认连上了 GitHub）；
             检测失败显示检查失败提示，区分 fallback 误报 -->
        <div v-if="currentVersion && !checkingUpdate" class="version-row">
          <span class="version-label">{{ updateSource === 'none' ? '检测状态' : '更新来源' }}</span>
          <span class="version-value source-badge" :style="updateSource === 'none' ? 'color: var(--error)' : ''">
            {{ updateSource === 'none' ? '检查失败，未获取到版本信息' : sourceBadgeText }}
          </span>
        </div>
      </div>

      <div v-if="hasUpdate && changelog" class="changelog-box">
        <div class="changelog-title">更新日志</div>
        <pre class="changelog-content">{{ changelog }}</pre>
      </div>

      <div class="version-actions">
        <button class="btn-secondary" @click="checkVersion" :disabled="checkingUpdate">
          {{ checkingUpdate ? '检查中...' : '检查更新' }}
        </button>
        <button
          v-if="hasUpdate"
          class="btn-primary"
          @click="startUpdate()"
          :disabled="updatingVersion"
        >
          {{ updatingVersion ? '更新中...' : '立即更新' }}
        </button>
      </div>

      <p v-if="!isElectron" class="hint warning">
        当前运行在浏览器模式，请在桌面端使用一键更新功能以获得最佳体验。
      </p>
    </div>

    <!-- ── 下载进度模态框 ── -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="showDownloadModal" class="modal-overlay" @click.self="closeDownloadModal">
          <div class="download-modal-card" :class="`phase-${downloadPhase}`">
            <!-- 头部 -->
            <div class="modal-header">
              <div class="modal-title-group">
                <div class="modal-icon" :class="`icon-${downloadPhase}`">
                  <svg v-if="downloadPhase === 'done'" width="20" height="20" viewBox="0 0 24 24" fill="none">
                    <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                  <svg v-else-if="downloadPhase === 'error'" width="20" height="20" viewBox="0 0 24 24" fill="none">
                    <path d="M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                  <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </div>
                <div>
                  <div class="modal-title">{{ modalTitleText }}</div>
                  <div class="modal-subtitle">
                    <span v-if="currentVersion && latestVersion">v{{ currentVersion }} → v{{ latestVersion }}</span>
                    <span v-else>更新下载</span>
                  </div>
                </div>
              </div>
              <button
                v-if="downloadPhase !== 'downloading'"
                class="modal-close-btn"
                @click="closeDownloadModal"
                title="关闭"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                </svg>
              </button>
            </div>

            <!-- 进度条区域 -->
            <div class="modal-body">
              <div class="progress-section">
                <div class="progress-header">
                  <span class="progress-status">{{ downloadStatusText }}</span>
                  <span class="progress-percent">{{ downloadProgress }}%</span>
                </div>
                <div class="progress-bar-wrapper">
                  <div class="progress-bar-track">
                    <div
                      class="progress-bar-fill"
                      :style="{ width: downloadProgress + '%' }"
                      :class="{ 'is-error': downloadPhase === 'error', 'is-done': downloadPhase === 'done', 'is-indeterminate': downloadPhase === 'idle' }"
                    ></div>
                  </div>
                </div>
                <div v-if="downloadMessage" class="progress-message" :class="{ 'is-error': downloadPhase === 'error' }">
                  {{ downloadMessage }}
                </div>
              </div>

              <!-- 错误时显示手动下载链接 -->
              <div v-if="downloadPhase === 'error' && releaseUrl" class="error-actions">
                <a :href="releaseUrl" target="_blank" class="manual-download-link">
                  手动下载最新版本
                </a>
              </div>
            </div>

            <!-- 底部操作 -->
            <div class="modal-footer">
              <button
                v-if="downloadPhase === 'done'"
                class="btn-primary modal-install-btn"
                @click="installUpdate()"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M12 3v12m0 0l-4-4m4 4l4-4M3 17v2a2 2 0 002 2h14a2 2 0 002-2v-2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                立即安装
              </button>
              <button
                v-if="downloadPhase === 'done' && !isElectron"
                class="btn-secondary"
                @click="closeDownloadModal"
              >
                稍后安装
              </button>
              <button
                v-if="downloadPhase === 'error'"
                class="btn-secondary"
                @click="closeDownloadModal"
              >
                关闭
              </button>
              <button
                v-if="downloadPhase === 'downloading' || downloadPhase === 'idle'"
                class="btn-secondary modal-cancel-btn"
                disabled
              >
                下载中…
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <div class="actions">
      <button class="btn-save" @click="saveSettings" :class="{ saved }">
        {{ saved ? '已保存' : '保存设置' }}
      </button>
      <button class="btn-reset" @click="resetSettings">
        恢复默认
      </button>
    </div>
  </div>
</template>

<style scoped>
.settings { flex: 1; overflow-y: auto; padding: 30px; max-width: 660px; margin: 0 auto; }

.settings-header { margin-bottom: 30px; }
.settings-header h1 { font-size: 21px; font-weight: 650; color: var(--text-primary); letter-spacing: -0.01em; }
.subtitle { font-size: 12.5px; color: var(--text-muted); margin-top: 3px; }

.form-section {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 22px; margin-bottom: 22px;
}
.form-section h2 { font-size: 13.5px; font-weight: 600; color: var(--text-secondary); margin-bottom: 5px; letter-spacing: -0.01em; }
.section-desc { font-size: 12px; color: var(--text-muted); margin-bottom: 16px; line-height: 1.5; }

/* ── 供应商卡片 ── */
.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 9px;
  margin-bottom: 20px;
}
.provider-card {
  display: flex; flex-direction: column; align-items: flex-start; gap: 3px;
  background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 12px 14px;
  cursor: pointer; transition: all 0.18s var(--ease-out-expo);
  font-family: var(--font-sans); text-align: left;
}
.provider-card:hover { border-color: var(--accent-border); background: var(--bg-hover); }
.provider-card.active {
  border-color: var(--accent-border); background: var(--accent-soft);
  box-shadow: 0 0 0 1px var(--accent-border);
}
.provider-name { font-size: 12.5px; font-weight: 600; color: var(--text-primary); }
.provider-model-count { font-size: 10.5px; color: var(--text-faint); }

.form-group { margin-bottom: 18px; }
.form-group:last-child { margin-bottom: 0; }
.form-group label { display: block; font-size: 12.5px; font-weight: 500; color: var(--text-primary); margin-bottom: 5px; }
.param-value { font-weight: 400; color: var(--text-muted); }

.input-with-icon {
  display: flex; align-items: center; gap: 9px;
  background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 0 13px;
  transition: border-color 0.15s var(--ease-out-expo);
}
.input-with-icon:focus-within { border-color: var(--accent-border); box-shadow: 0 0 0 2px var(--accent-soft); }
.input-with-icon svg { color: var(--text-muted); flex-shrink: 0; }
.input-with-icon input {
  flex: 1; background: none; border: none; outline: none;
  color: var(--text-primary); font-family: var(--font-mono);
  font-size: 12.5px; padding: 11px 0;
}
.input-with-icon input::placeholder { color: var(--text-faint); }

.select-wrapper { position: relative; }
select {
  width: 100%; appearance: none; background: var(--bg-input);
  border: 1px solid var(--border); border-radius: var(--radius-md);
  color: var(--text-primary); font-family: var(--font-sans);
  font-size: 12.5px; padding: 10px 13px; cursor: pointer;
  transition: border-color 0.15s var(--ease-out-expo);
}
select:focus { outline: none; border-color: var(--accent-border); box-shadow: 0 0 0 2px var(--accent-soft); }
.select-chevron { position: absolute; right: 13px; top: 50%; transform: translateY(-50%); color: var(--text-muted); pointer-events: none; }

/* ── Temperature 滑块 ── */
.slider-row {
  display: flex; align-items: center; gap: 9px; margin-bottom: 3px;
}
.slider-end { font-size: 10.5px; color: var(--text-faint); font-family: var(--font-mono); min-width: 22px; }
.slider-row .slider { flex: 1; }
.slider {
  appearance: none; height: 5px; background: var(--bg-card); border-radius: 3px; outline: none;
}
.slider::-webkit-slider-thumb { appearance: none; width: 17px; height: 17px; background: var(--accent); border-radius: 50%; cursor: pointer; border: 2px solid var(--bg-surface); }
.range-labels { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-faint); padding: 0 2px; }

/* ── Token 预设芯片 ── */
.preset-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.preset-chip {
  padding: 5px 11px; border-radius: var(--radius-sm);
  border: 1px solid var(--border); background: var(--bg-input);
  color: var(--text-muted); font-family: var(--font-mono);
  font-size: 11.5px; cursor: pointer; transition: all 0.15s var(--ease-out-expo);
}
.preset-chip:hover { border-color: var(--accent-border); color: var(--text-primary); }
.preset-chip.active {
  border-color: var(--accent-border); background: var(--accent-soft);
  color: var(--accent); font-weight: 600;
}
.number-input.inline {
  width: 90px; text-align: center; margin-left: auto;
}
.number-input {
  background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-md); color: var(--text-primary);
  font-family: var(--font-mono); font-size: 12.5px; padding: 8px 13px;
  transition: border-color 0.15s var(--ease-out-expo);
}
.number-input:focus { outline: none; border-color: var(--accent-border); box-shadow: 0 0 0 2px var(--accent-soft); }

.hint { font-size: 10.5px; color: var(--text-faint); margin-top: 5px; line-height: 1.5; }
.hint.error { color: var(--error); }
.hint.warning { color: var(--text-muted); background: var(--bg-card); padding: 9px 13px; border-radius: var(--radius-md); border: 1px solid var(--border); margin-top: 11px; }

.models-status {
  font-size: 9.5px; font-weight: 500; padding: 1px 6px; border-radius: 3px;
  margin-left: 6px; vertical-align: middle;
}
.models-status.loading { background: var(--bg-card); color: var(--text-muted); }

/* ── 模型管理 ── */
.section-badge {
  font-size: 10px; font-weight: 500; color: var(--accent);
  background: var(--accent-soft); padding: 2px 8px; border-radius: 10px;
  margin-left: 8px; vertical-align: middle;
}
.model-manager-actions { margin-bottom: 14px; }
.btn-sm { padding: 6px 14px; font-size: 11px; }

.provider-model-list { display: flex; flex-direction: column; gap: 12px; }
.provider-model-group {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 13px 14px;
  transition: border-color 0.15s var(--ease-out-expo);
}
.provider-model-group:hover { border-color: var(--accent-border); }

.pmg-header {
  display: flex; align-items: center; gap: 8px; margin-bottom: 9px;
}
.pmg-provider-name { font-size: 12.5px; font-weight: 600; color: var(--text-primary); }
.pmg-api-status {
  font-size: 9.5px; padding: 1px 7px; border-radius: 8px;
  background: var(--bg-input); color: var(--text-faint);
}
.pmg-api-status.configured {
  background: var(--success-soft); color: var(--success);
}
.pmg-active-badge {
  font-size: 9px; padding: 1px 7px; border-radius: 8px;
  background: var(--accent-soft); color: var(--accent); font-weight: 600;
}

.pmg-models { display: flex; flex-wrap: wrap; gap: 5px; }
.pmg-model-chip {
  display: inline-block; padding: 3px 10px;
  border-radius: var(--radius-sm); border: 1px solid var(--border);
  background: var(--bg-input); color: var(--text-muted);
  font-family: var(--font-mono); font-size: 10.5px;
  cursor: pointer; transition: all 0.15s var(--ease-out-expo);
  white-space: nowrap; max-width: 220px; overflow: hidden;
  text-overflow: ellipsis;
}
.pmg-model-chip:hover {
  border-color: var(--accent-border); color: var(--text-primary); background: var(--bg-hover);
}
.pmg-model-chip.active {
  border-color: var(--accent-border); background: var(--accent-soft);
  color: var(--accent); font-weight: 600;
}
.pmg-empty { font-size: 11px; color: var(--text-faint); padding: 6px 0; }

/* ── 版本更新 ── */
.version-info { margin-bottom: 14px; }
.version-row { display: flex; justify-content: space-between; align-items: center; padding: 9px 0; border-bottom: 1px solid var(--border); }
.version-label { font-size: 12.5px; color: var(--text-muted); }
.version-value { font-family: var(--font-mono); font-size: 12.5px; color: var(--text-primary); font-weight: 500; }
.version-value.latest { color: var(--accent); }
.version-value.pulse { animation: pulse 1s ease-in-out infinite; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.changelog-box { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-md); margin-bottom: 14px; overflow: hidden; }
.changelog-title { font-size: 11.5px; font-weight: 600; color: var(--text-secondary); padding: 9px 13px; background: var(--bg-input); border-bottom: 1px solid var(--border); }
.changelog-content { font-size: 10.5px; font-family: var(--font-mono); color: var(--text-muted); padding: 11px 13px; margin: 0; white-space: pre-wrap; word-break: break-all; max-height: 200px; overflow-y: auto; line-height: 1.6; }

.source-badge {
  font-size: 10.5px; font-family: var(--font-sans); font-weight: 500;
  color: var(--accent); background: var(--accent-soft);
  padding: 2px 9px; border-radius: 10px;
}

.version-actions { display: flex; gap: 9px; margin-top: 7px; }
.btn-primary {
  background: var(--accent); color: white; border: none;
  padding: 9px 22px; border-radius: var(--radius-md);
  font-family: var(--font-sans); font-size: 12.5px; font-weight: 600;
  cursor: pointer; transition: all 0.18s var(--ease-out-expo);
}
.btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-secondary {
  background: transparent; color: var(--text-primary); border: 1px solid var(--border);
  padding: 9px 22px; border-radius: var(--radius-md);
  font-family: var(--font-sans); font-size: 12.5px; font-weight: 500;
  cursor: pointer; transition: all 0.18s var(--ease-out-expo);
}
.btn-secondary:hover:not(:disabled) { border-color: var(--accent-border); color: var(--accent); }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }

.actions { display: flex; gap: 9px; }
.btn-save {
  background: var(--accent); color: white; border: none;
  padding: 9px 22px; border-radius: var(--radius-md);
  font-family: var(--font-sans); font-size: 12.5px; font-weight: 600;
  cursor: pointer; transition: all 0.18s var(--ease-out-expo);
}
.btn-save:hover { background: var(--accent-hover); }
.btn-save.saved { background: var(--success); }
.btn-reset {
  background: transparent; color: var(--text-muted);
  border: 1px solid var(--border); padding: 9px 22px;
  border-radius: var(--radius-md); font-family: var(--font-sans);
  font-size: 12.5px; cursor: pointer; transition: all 0.18s var(--ease-out-expo);
}
.btn-reset:hover { border-color: var(--error); color: var(--error); }

/* ── 下载进度模态框 ── */
.modal-overlay {
  position: fixed; inset: 0; z-index: 10000;
  background: rgba(15, 23, 42, 0.42);
  backdrop-filter: blur(6px) saturate(140%);
  -webkit-backdrop-filter: blur(6px) saturate(140%);
  display: flex; align-items: center; justify-content: center;
  padding: 20px;
}

.download-modal-card {
  width: 100%; max-width: 460px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg), 0 0 0 1px rgba(15, 23, 42, 0.04);
  overflow: hidden;
  animation: scaleIn 0.24s var(--ease-spring);
}
.download-modal-card.phase-error { border-color: rgba(239, 68, 68, 0.28); }
.download-modal-card.phase-done { border-color: rgba(16, 185, 129, 0.28); }

.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 20px 14px;
  border-bottom: 1px solid var(--border-light);
}
.modal-title-group { display: flex; align-items: center; gap: 12px; }

.modal-icon {
  width: 38px; height: 38px;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--radius-md);
  background: var(--accent-soft); color: var(--accent);
  transition: all 0.2s var(--ease-out-expo);
}
.modal-icon.icon-downloading { background: var(--accent-soft); color: var(--accent); }
.modal-icon.icon-done { background: var(--success-soft); color: var(--success); }
.modal-icon.icon-error { background: var(--error-soft); color: var(--error); }
.modal-icon.icon-idle { background: var(--bg-soft); color: var(--text-muted); }

.modal-title {
  font-size: 14.5px; font-weight: 650; color: var(--text-primary);
  letter-spacing: -0.01em;
}
.modal-subtitle {
  font-size: 11px; color: var(--text-muted);
  font-family: var(--font-mono); margin-top: 1px;
}

.modal-close-btn {
  width: 28px; height: 28px;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--radius-sm);
  border: none; background: transparent;
  color: var(--text-faint);
  cursor: pointer; transition: all 0.15s var(--ease-out-expo);
}
.modal-close-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.modal-body { padding: 18px 20px; }

.progress-section { display: flex; flex-direction: column; gap: 9px; }
.progress-header {
  display: flex; align-items: center; justify-content: space-between;
}
.progress-status {
  font-size: 12.5px; font-weight: 600; color: var(--text-primary);
}
.progress-percent {
  font-family: var(--font-mono);
  font-size: 13px; font-weight: 600; color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.phase-error .progress-percent { color: var(--error); }
.phase-done .progress-percent { color: var(--success); }

.progress-bar-wrapper {
  padding: 2px 0;
}
.progress-bar-track {
  position: relative;
  height: 7px;
  background: var(--bg-soft);
  border-radius: 4px;
  overflow: hidden;
}
.progress-bar-fill {
  position: absolute; left: 0; top: 0; bottom: 0;
  background: var(--accent);
  border-radius: 4px;
  transition: width 0.32s var(--ease-out-expo);
  box-shadow: 0 0 8px rgba(13, 148, 136, 0.32);
}
.progress-bar-fill.is-done {
  background: var(--success);
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.32);
}
.progress-bar-fill.is-error {
  background: var(--error);
  box-shadow: 0 0 8px rgba(239, 68, 68, 0.32);
}
.progress-bar-fill.is-indeterminate {
  width: 32% !important;
  animation: indeterminate-slide 1.4s var(--ease-in-out) infinite;
}

@keyframes indeterminate-slide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(360%); }
}

.progress-message {
  font-size: 11.5px; color: var(--text-muted);
  font-family: var(--font-mono);
  line-height: 1.55;
  word-break: break-all;
  margin-top: 2px;
}
.progress-message.is-error { color: var(--error); }

.error-actions {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border-light);
}
.manual-download-link {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11.5px; color: var(--accent);
  text-decoration: none;
  padding: 5px 11px;
  border: 1px solid var(--accent-border);
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  transition: all 0.15s var(--ease-out-expo);
}
.manual-download-link:hover {
  background: var(--accent); color: white;
  border-color: var(--accent);
}

.modal-footer {
  display: flex; justify-content: flex-end; gap: 9px;
  padding: 14px 20px 18px;
  border-top: 1px solid var(--border-light);
}
.modal-install-btn {
  display: inline-flex; align-items: center; gap: 6px;
}
.modal-cancel-btn { cursor: not-allowed; opacity: 0.6; }

@media (max-width: 768px) {
  .settings { padding: 18px 14px; }
  .provider-grid { grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); }
  .download-modal-card { max-width: calc(100vw - 28px); }
  .modal-header, .modal-body, .modal-footer { padding-left: 16px; padding-right: 16px; }
}
</style>
