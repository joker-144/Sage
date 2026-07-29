import { ref } from 'vue'

// 全局下载通知状态（单例，跨组件共享）
// phase: 'idle' | 'downloading' | 'done' | 'error'
const visible = ref(false)
const phase = ref('idle')
const percent = ref(0)
const message = ref('')
const title = ref('')
// 当前正在下载的模型类型（供其他组件判断是否在下载同一模型，避免重复触发）
const currentModelType = ref('')
// 连续失败计数（用于 C 方案：自动重试 3 次后提示"重新下载"）
const failCount = ref(0)
// 是否已达到"建议重新下载"阈值（3 次失败）
const suggestRestart = ref(false)

const MAX_AUTO_RETRY = 3
let abortController = null
// 下载完成回调（外部传入，用于刷新模型状态）
let doneCallback = null
// 当前下载配置（用于自动重试时复用）
let currentConfig = null

/**
 * 触发模型下载（SSE 流式），右上角通知卡片实时展示进度
 *
 * C 方案：失败后自动重试（resume 断点续传），连续失败 3 次后停止自动重试，
 * 通知卡片显示"重试"/"重新下载"两个按钮供用户选择。
 *
 * @param {Object} options
 * @param {string} options.modelType - 下载的模型类型，如 'reranker'
 * @param {string} options.title - 通知卡片标题
 * @param {Function} [options.onDone] - 下载完成回调
 * @param {string} [options.retryMode] - 'resume'(默认) | 'restart'，传 'restart' 会先清缓存
 */
async function startDownload({ modelType, title: titleText, onDone, retryMode = 'resume' }) {
  // 若已有下载进行中，忽略
  if (phase.value === 'downloading') return

  // 首次发起下载（非重试）时重置计数
  if (retryMode === 'resume' && failCount.value === 0) {
    suggestRestart.value = false
  }
  if (retryMode === 'restart') {
    // 用户主动选择重新下载 → 重置计数
    failCount.value = 0
    suggestRestart.value = false
  }

  currentConfig = { modelType, title: titleText, onDone }
  if (onDone) doneCallback = onDone

  visible.value = true
  phase.value = 'downloading'
  percent.value = 0
  message.value = retryMode === 'restart' ? '正在清理旧缓存...' : '准备中…'
  title.value = titleText || '模型下载'
  currentModelType.value = modelType

  abortController = new AbortController()

  try {
    const resp = await fetch('/api/system/download-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_type: modelType, retry_mode: retryMode }),
      signal: abortController.signal,
    })

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let gotDone = false

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
            if (msg.status === 'done') {
              gotDone = true
              // 下载成功 → 重置失败计数
              failCount.value = 0
              suggestRestart.value = false
              if (doneCallback) doneCallback()
            }
          } catch { /* 忽略无法解析的行 */ }
        }
      }
    }
    // 处理剩余缓冲
    if (buffer.trim().startsWith('data: ')) {
      try {
        const msg = JSON.parse(buffer.trim().slice(6))
        handleProgressMessage(msg)
        if (msg.status === 'done') {
          gotDone = true
          failCount.value = 0
          suggestRestart.value = false
          if (doneCallback) doneCallback()
        }
      } catch { /* ignore */ }
    }

    // 流结束但未收到 done 事件 → 视为失败
    if (!gotDone && phase.value !== 'error') {
      handleFailure()
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      // 用户主动取消，不触发自动重试
      phase.value = 'error'
      message.value = '下载已取消'
      // 取消不算失败，保留 failCount 不变
    } else {
      handleFailure(e.message)
    }
  } finally {
    abortController = null
  }
}

function handleProgressMessage(msg) {
  const status = msg.status
  const messageText = msg.message || ''
  const pct = msg.percent

  if (status === 'info') {
    message.value = messageText
  } else if (status === 'progress') {
    phase.value = 'downloading'
    message.value = messageText
    if (typeof pct === 'number') {
      percent.value = Math.min(100, Math.max(0, pct))
    }
  } else if (status === 'done') {
    phase.value = 'done'
    percent.value = 100
    message.value = messageText || '下载完成'
  } else if (status === 'error') {
    handleFailure(messageText)
  }
}

/**
 * 处理下载失败：自动重试或提示用户
 */
function handleFailure(errMsg) {
  failCount.value += 1
  phase.value = 'error'
  if (errMsg) message.value = errMsg

  // 未达到自动重试上限 → 自动重试（resume 断点续传）
  if (failCount.value < MAX_AUTO_RETRY && currentConfig) {
    message.value = `下载失败，正在自动重试 (${failCount.value}/${MAX_AUTO_RETRY})...`
    // 延迟 1.5 秒后自动重试，避免瞬间重试风暴
    setTimeout(() => {
      if (currentConfig && phase.value === 'error') {
        startDownload({ ...currentConfig, retryMode: 'resume' })
      }
    }, 1500)
    return
  }

  // 达到上限 → 提示用户手动选择
  suggestRestart.value = true
  message.value = `连续 ${MAX_AUTO_RETRY} 次下载失败，缓存可能损坏，建议重新下载`
}

/** 关闭通知卡片 */
function closeNotification() {
  // 下载进行中不允许关闭
  if (phase.value === 'downloading') return
  visible.value = false
  // 延迟重置，避免关闭动画闪烁
  setTimeout(() => {
    phase.value = 'idle'
    percent.value = 0
    message.value = ''
    title.value = ''
    currentModelType.value = ''
    failCount.value = 0
    suggestRestart.value = false
    doneCallback = null
    currentConfig = null
  }, 300)
}

/** 取消下载（中止 SSE 请求） */
function cancelDownload() {
  if (abortController) {
    abortController.abort()
  }
}

/** 用户手动点击"重试"按钮 → resume 模式重试，重置计数 */
function retryDownload() {
  if (!currentConfig) return
  failCount.value = 0
  suggestRestart.value = false
  startDownload({ ...currentConfig, retryMode: 'resume' })
}

/** 用户手动点击"重新下载"按钮 → restart 模式，先清缓存 */
function restartDownload() {
  if (!currentConfig) return
  failCount.value = 0
  suggestRestart.value = false
  startDownload({ ...currentConfig, retryMode: 'restart' })
}

export function useDownloadNotify() {
  return {
    visible,
    phase,
    percent,
    message,
    title,
    currentModelType,
    failCount,
    suggestRestart,
    startDownload,
    closeNotification,
    cancelDownload,
    retryDownload,
    restartDownload,
  }
}
