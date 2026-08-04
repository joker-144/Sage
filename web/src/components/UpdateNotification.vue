<script setup>
import { ref, onMounted } from 'vue'

const visible = ref(false)
const loading = ref(false)
const updateInfo = ref(null)
// 用户本次会话已关闭过提醒，避免重复弹出
const dismissed = ref(false)

async function checkUpdate() {
  if (dismissed.value) return
  loading.value = true
  try {
    const res = await fetch('/api/update-check')
    if (res.ok) {
      const data = await res.json()
      if (data.has_update) {
        updateInfo.value = data
        visible.value = true
      }
    }
  } catch {
    // 静默失败，不打扰用户
  } finally {
    loading.value = false
  }
}

function closeNotify() {
  visible.value = false
  dismissed.value = true
}

function openRelease() {
  if (updateInfo.value?.release_url) {
    window.open(updateInfo.value.release_url, '_blank', 'noopener')
  }
}

onMounted(() => {
  // 延迟 2s 检查，避免与启动关键请求抢资源
  setTimeout(checkUpdate, 2000)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="update-slide">
      <div v-if="visible" class="update-notify">
        <div class="un-card">
          <!-- 图标区 -->
          <div class="un-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
              <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"/>
            </svg>
          </div>

          <!-- 内容区 -->
          <div class="un-body">
            <div class="un-header">
              <span class="un-title">发现新版本</span>
              <span class="un-version-badge">v{{ updateInfo.latest }}</span>
            </div>
            <div class="un-message">
              当前版本 v{{ updateInfo.current }}，新版本 v{{ updateInfo.latest }} 已发布，建议更新以获取最新功能。
            </div>
            <div class="un-actions">
              <button class="un-btn un-btn-primary" @click="openRelease">
                前往更新
              </button>
              <button class="un-btn un-btn-secondary" @click="closeNotify">
                稍后再说
              </button>
            </div>
          </div>

          <!-- 关闭按钮 -->
          <button class="un-action-btn" title="关闭" @click="closeNotify">
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
.update-notify {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 10001;
  max-width: 360px;
  min-width: 300px;
}

.un-card {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 14px 14px 13px;
  background: var(--bg-surface);
  border: 1px solid var(--accent-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg), 0 0 0 1px rgba(13, 148, 136, 0.08);
}

.un-icon {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--accent-soft);
  color: var(--accent);
}

.un-body {
  flex: 1;
  min-width: 0;
}

.un-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.un-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.un-version-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 9px;
  background: var(--accent-soft);
  color: var(--accent);
  flex-shrink: 0;
  font-family: var(--font-mono);
}

.un-message {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
  margin-bottom: 8px;
}

.un-actions {
  display: flex;
  gap: 7px;
}

.un-btn {
  padding: 5px 12px;
  border-radius: var(--radius-sm);
  font-family: var(--font-sans);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s var(--ease-out-expo);
  border: 1px solid transparent;
}

.un-btn-primary {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: var(--accent-border);
}
.un-btn-primary:hover {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}

.un-btn-secondary {
  background: transparent;
  color: var(--text-secondary);
  border-color: var(--border);
}
.un-btn-secondary:hover {
  border-color: var(--accent-border);
  color: var(--accent);
}

.un-action-btn {
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
.un-action-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* 进出动画 */
.update-slide-enter-active {
  transition: all 0.32s var(--ease-spring);
}
.update-slide-leave-active {
  transition: all 0.22s var(--ease-out-expo);
}
.update-slide-enter-from {
  opacity: 0;
  transform: translateX(40px) scale(0.96);
}
.update-slide-leave-to {
  opacity: 0;
  transform: translateX(40px) scale(0.96);
}
</style>
