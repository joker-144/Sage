<script setup>
import { computed } from 'vue'
import { useDownloadNotify } from '../composables/useDownloadNotify'

const {
  visible,
  phase,
  percent,
  message,
  title,
  failCount,
  suggestRestart,
  closeNotification,
  cancelDownload,
  retryDownload,
  restartDownload,
} = useDownloadNotify()

const statusText = computed(() => {
  switch (phase.value) {
    case 'downloading': return '下载中'
    case 'done': return '下载完成'
    case 'error': return '下载失败'
    default: return '准备中'
  }
})

// 自动重试中（error 状态但还在倒计时重试）→ 显示"重试中"状态徽章
const isAutoRetrying = computed(() =>
  phase.value === 'error' && failCount.value > 0 && failCount.value < 3 && !suggestRestart.value
)

const displayStatusText = computed(() => {
  if (isAutoRetrying.value) return '重试中'
  return statusText.value
})

const iconPath = computed(() => {
  if (phase.value === 'done') {
    return 'M20 6L9 17l-5-5'
  } else if (phase.value === 'error') {
    return 'M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z'
  }
  return '' // downloading 用旋转动画
})
</script>

<template>
  <Teleport to="body">
    <Transition name="notify-slide">
      <div v-if="visible" class="download-notify" :class="`phase-${phase}`">
        <div class="dn-card">
          <!-- 图标区 -->
          <div class="dn-icon" :class="`icon-${phase}`">
            <svg v-if="phase === 'done' || phase === 'error'" width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path v-if="phase === 'done'" :d="iconPath" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
              <path v-else :d="iconPath" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <svg v-else class="dn-spinner" width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M21 12a9 9 0 11-6.219-8.56" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>
            </svg>
          </div>

          <!-- 内容区 -->
          <div class="dn-body">
            <div class="dn-header">
              <span class="dn-title">{{ title || '模型下载' }}</span>
              <span class="dn-status" :class="`status-${phase}`">{{ displayStatusText }}</span>
            </div>

            <!-- 进度条（下载中显示） -->
            <div v-if="phase === 'downloading'" class="dn-progress">
              <div class="dn-progress-track">
                <div class="dn-progress-fill" :style="{ width: percent + '%' }"></div>
              </div>
              <span class="dn-percent">{{ percent }}%</span>
            </div>

            <!-- 消息文本 -->
            <div class="dn-message" :class="{ 'is-error': phase === 'error' && !isAutoRetrying }">{{ message }}</div>

            <!-- 错误且达到重试上限：显示操作按钮 -->
            <div v-if="phase === 'error' && suggestRestart" class="dn-actions">
              <button class="dn-btn dn-btn-secondary" @click="retryDownload" title="断点续传，从上次中断处继续">
                重试
              </button>
              <button class="dn-btn dn-btn-primary" @click="restartDownload" title="清理缓存后从头下载">
                重新下载
              </button>
            </div>
          </div>

          <!-- 关闭/取消按钮 -->
          <button
            v-if="phase === 'downloading'"
            class="dn-action-btn"
            title="取消下载"
            @click="cancelDownload"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
          <button
            v-else-if="!isAutoRetrying"
            class="dn-action-btn"
            title="关闭"
            @click="closeNotification"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.download-notify {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 10001;
  max-width: 360px;
  min-width: 300px;
}

.dn-card {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 14px 14px 13px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg), 0 0 0 1px rgba(15, 23, 42, 0.04);
  transition: border-color 0.2s var(--ease-out-expo);
}

.phase-error .dn-card { border-color: rgba(239, 68, 68, 0.28); }
.phase-done .dn-card { border-color: rgba(16, 185, 129, 0.28); }

.dn-icon {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--accent-soft);
  color: var(--accent);
  transition: all 0.2s var(--ease-out-expo);
}
.dn-icon.icon-downloading { background: var(--accent-soft); color: var(--accent); }
.dn-icon.icon-done { background: var(--success-soft); color: var(--success); }
.dn-icon.icon-error { background: var(--error-soft); color: var(--error); }

.dn-spinner {
  animation: dn-spin 0.9s linear infinite;
}
@keyframes dn-spin {
  to { transform: rotate(360deg); }
}

.dn-body {
  flex: 1;
  min-width: 0;
}

.dn-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.dn-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.dn-status {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 9px;
  flex-shrink: 0;
}
.dn-status.status-downloading { background: var(--accent-soft); color: var(--accent); }
.dn-status.status-done { background: var(--success-soft); color: var(--success); }
.dn-status.status-error { background: var(--error-soft); color: var(--error); }

.dn-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0 4px;
}

.dn-progress-track {
  flex: 1;
  height: 5px;
  background: var(--bg-soft);
  border-radius: 3px;
  overflow: hidden;
}

.dn-progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.3s var(--ease-out-expo);
  box-shadow: 0 0 6px rgba(13, 148, 136, 0.32);
}

.dn-percent {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
  min-width: 32px;
  text-align: right;
}

.dn-message {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
  font-family: var(--font-mono);
  word-break: break-all;
}
.dn-message.is-error { color: var(--error); }

.dn-actions {
  display: flex;
  gap: 7px;
  margin-top: 9px;
}

.dn-btn {
  padding: 5px 12px;
  border-radius: var(--radius-sm);
  font-family: var(--font-sans);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s var(--ease-out-expo);
  border: 1px solid transparent;
}

.dn-btn-secondary {
  background: transparent;
  color: var(--text-secondary);
  border-color: var(--border);
}
.dn-btn-secondary:hover {
  border-color: var(--accent-border);
  color: var(--accent);
}

.dn-btn-primary {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: var(--accent-border);
}
.dn-btn-primary:hover {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}

.dn-action-btn {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  border: none;
  background: transparent;
  color: var(--text-faint);
  cursor: pointer;
  transition: all 0.15s var(--ease-out-expo);
}
.dn-action-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* 进出动画 */
.notify-slide-enter-active {
  transition: all 0.32s var(--ease-spring);
}
.notify-slide-leave-active {
  transition: all 0.22s var(--ease-out-expo);
}
.notify-slide-enter-from {
  opacity: 0;
  transform: translateX(40px) scale(0.96);
}
.notify-slide-leave-to {
  opacity: 0;
  transform: translateX(40px) scale(0.96);
}
</style>
