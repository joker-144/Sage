<script setup>
import { computed } from 'vue'
import { useIndexNotify } from '../composables/useIndexNotify'

const {
  visible,
  status,
  progress,
  total,
  currentFile,
  message,
  workspaceName,
  closeNotification,
} = useIndexNotify()

const statusText = computed(() => {
  switch (status.value) {
    case 'running': return '索引中'
    case 'done': return '索引完成'
    case 'error': return '索引失败'
    default: return '准备中'
  }
})

// 进度百分比（基于文件数）
const percent = computed(() => {
  if (!total.value) return 0
  return Math.min(100, Math.round((progress.value / total.value) * 100))
})

const iconPath = computed(() => {
  if (status.value === 'done') {
    return 'M20 6L9 17l-5-5'
  } else if (status.value === 'error') {
    return 'M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z'
  }
  return ''
})
</script>

<template>
  <Teleport to="body">
    <Transition name="notify-slide">
      <div v-if="visible" class="index-notify" :class="`status-${status}`">
        <div class="in-card">
          <!-- 图标区 -->
          <div class="in-icon" :class="`icon-${status}`">
            <svg v-if="status === 'done' || status === 'error'" width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path v-if="status === 'done'" :d="iconPath" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
              <path v-else :d="iconPath" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <svg v-else class="in-spinner" width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M21 12a9 9 0 11-6.219-8.56" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>
            </svg>
          </div>

          <!-- 内容区 -->
          <div class="in-body">
            <div class="in-header">
              <span class="in-title">{{ workspaceName ? `${workspaceName} · 索引` : '工作空间索引' }}</span>
              <span class="in-status" :class="`status-${status}`">{{ statusText }}</span>
            </div>

            <!-- 进度条（索引中显示） -->
            <div v-if="status === 'running'" class="in-progress">
              <div class="in-progress-track">
                <div class="in-progress-fill" :style="{ width: percent + '%' }"></div>
              </div>
              <span class="in-percent">{{ progress }}/{{ total || '?' }}</span>
            </div>

            <!-- 消息文本 -->
            <div class="in-message" :class="{ 'is-error': status === 'error' }">
              <span v-if="currentFile && status === 'running'" class="in-file">{{ currentFile }}</span>
              <span class="in-msg-text">{{ message }}</span>
            </div>
          </div>

          <!-- 关闭按钮（索引中不显示） -->
          <button
            v-if="status !== 'running'"
            class="in-action-btn"
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
.index-notify {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 10001;
  max-width: 380px;
  min-width: 320px;
}

.in-card {
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

.status-error .in-card { border-color: rgba(239, 68, 68, 0.28); }
.status-done .in-card { border-color: rgba(16, 185, 129, 0.28); }

.in-icon {
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
.in-icon.icon-running { background: var(--accent-soft); color: var(--accent); }
.in-icon.icon-done { background: var(--success-soft); color: var(--success); }
.in-icon.icon-error { background: var(--error-soft); color: var(--error); }

.in-spinner {
  animation: in-spin 0.9s linear infinite;
}
@keyframes in-spin {
  to { transform: rotate(360deg); }
}

.in-body {
  flex: 1;
  min-width: 0;
}

.in-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.in-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.in-status {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 9px;
  flex-shrink: 0;
}
.in-status.status-running { background: var(--accent-soft); color: var(--accent); }
.in-status.status-done { background: var(--success-soft); color: var(--success); }
.in-status.status-error { background: var(--error-soft); color: var(--error); }

.in-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0 4px;
}

.in-progress-track {
  flex: 1;
  height: 5px;
  background: var(--bg-soft);
  border-radius: 3px;
  overflow: hidden;
}

.in-progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.3s var(--ease-out-expo);
  box-shadow: 0 0 6px rgba(13, 148, 136, 0.32);
}

.in-percent {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
  min-width: 42px;
  text-align: right;
}

.in-message {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
  font-family: var(--font-mono);
  word-break: break-all;
}
.in-message.is-error { color: var(--error); }

.in-file {
  display: inline-block;
  color: var(--accent);
  margin-right: 6px;
  font-weight: 600;
}

.in-action-btn {
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
.in-action-btn:hover {
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
