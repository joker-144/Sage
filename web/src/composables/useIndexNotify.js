import { ref } from 'vue'

// 全局索引任务通知状态（单例，跨组件共享）
// status: 'idle' | 'running' | 'done' | 'error'
const visible = ref(false)
const status = ref('idle')
const progress = ref(0)
const total = ref(0)
const currentFile = ref('')
const message = ref('')
const workspaceId = ref('')
const workspaceName = ref('')
// 当前订阅的 ws_id，避免重复启动
const activeWsId = ref('')
let eventSource = null
// 索引完成回调（外部传入，用于刷新工作空间列表）
let doneCallback = null

/**
 * 触发异步索引并通过 SSE 订阅进度
 *
 * @param {Object} options
 * @param {string} options.wsId - 工作空间 ID
 * @param {string} options.wsName - 工作空间名称（显示用）
 * @param {boolean} [options.force] - 是否强制重建索引
 * @param {Function} [options.onDone] - 索引完成回调
 */
async function startIndex({ wsId, wsName, force = false, onDone }) {
  // 已有索引任务在运行 → 忽略
  if (status.value === 'running') return

  workspaceId.value = wsId
  workspaceName.value = wsName || wsId
  activeWsId.value = wsId
  if (onDone) doneCallback = onDone

  visible.value = true
  status.value = 'running'
  progress.value = 0
  total.value = 0
  currentFile.value = ''
  message.value = '准备索引...'

  // 1. 触发异步索引
  try {
    const resp = await fetch(`/api/sage/workspaces/${wsId}/async-index?force=${force}`, {
      method: 'POST',
    })
    if (!resp.ok) {
      const err = await resp.json()
      handleError(err.detail || '启动索引失败')
      return
    }
    const data = await resp.json()
    // 如果返回 already_running，直接订阅已有任务
    if (data.status === 'already_running') {
      message.value = data.message || '索引进行中'
    }
  } catch (e) {
    handleError(e.message)
    return
  }

  // 2. 订阅 SSE 进度
  subscribeEvents(wsId)
}

/**
 * 订阅索引进度 SSE
 */
function subscribeEvents(wsId) {
  // 关闭旧的 EventSource
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }

  eventSource = new EventSource(`/api/sage/workspaces/${wsId}/index-events`)

  eventSource.addEventListener('start', (e) => {
    try {
      const data = JSON.parse(e.data)
      total.value = data.total || 0
      message.value = data.message || '开始索引'
    } catch { /* ignore */ }
  })

  eventSource.addEventListener('progress', (e) => {
    try {
      const data = JSON.parse(e.data)
      progress.value = data.progress || 0
      total.value = data.total || total.value
      currentFile.value = data.current_file || ''
      message.value = data.message || ''
    } catch { /* ignore */ }
  })

  eventSource.addEventListener('done', (e) => {
    try {
      const data = JSON.parse(e.data)
      status.value = 'done'
      message.value = data.message || '索引完成'
      if (doneCallback) doneCallback(data.stats)
    } catch { /* ignore */ }
    closeEventSource()
  })

  eventSource.addEventListener('error', (e) => {
    // SSE 错误事件（连接层面）— 不一定是业务错误，只在 status 仍为 running 时处理
    if (status.value === 'running') {
      try {
        const data = e.data ? JSON.parse(e.data) : {}
        handleError(data.error || '索引连接中断')
      } catch {
        handleError('索引连接中断')
      }
    }
    closeEventSource()
  })
}

function closeEventSource() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

function handleError(errMsg) {
  status.value = 'error'
  message.value = errMsg || '索引失败'
  closeEventSource()
}

/** 关闭通知卡片 */
function closeNotification() {
  // 索引进行中不允许关闭
  if (status.value === 'running') return
  visible.value = false
  setTimeout(() => {
    status.value = 'idle'
    progress.value = 0
    total.value = 0
    currentFile.value = ''
    message.value = ''
    workspaceId.value = ''
    workspaceName.value = ''
    activeWsId.value = ''
    doneCallback = null
  }, 300)
}

export function useIndexNotify() {
  return {
    visible,
    status,
    progress,
    total,
    currentFile,
    message,
    workspaceId,
    workspaceName,
    activeWsId,
    startIndex,
    closeNotification,
  }
}
