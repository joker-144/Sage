<script setup>
import { ref, onMounted } from 'vue'

const isMaximized = ref(false)
const isElectron = ref(false)

// 将 electronAPI 方法绑定到 setup scope，避免模板中 window 不可用
const api = ref(null)

function minimize() { api.value?.minimize() }
function maximize() { api.value?.maximize() }
function close() { api.value?.close() }

onMounted(() => {
  if (window.electronAPI) {
    api.value = window.electronAPI
    isElectron.value = true
    window.electronAPI.isMaximized().then((v) => isMaximized.value = v)
    window.electronAPI.onMaximizeChange((v) => isMaximized.value = v)
  }
})
</script>

<template>
  <header v-if="isElectron" class="titlebar" @dblclick="maximize">
    <div class="titlebar-drag">
      <div class="titlebar-logo">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
          <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
          <path d="M2 12l10 5 10-5M2 17l10 5 10-5" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
        </svg>
      </div>
      <span class="titlebar-text">Sage</span>
    </div>

    <div class="titlebar-controls">
      <button class="ctrl-btn ctrl-min" @click="minimize" title="最小化">
        <svg width="12" height="12" viewBox="0 0 12 12"><rect x="2" y="5" width="8" height="1" fill="currentColor"/></svg>
      </button>
      <button class="ctrl-btn ctrl-max" @click="maximize" :title="isMaximized ? '还原' : '最大化'">
        <svg v-if="!isMaximized" width="12" height="12" viewBox="0 0 12 12"><rect x="1.5" y="1.5" width="9" height="9" rx="1" stroke="currentColor" stroke-width="1.2" fill="none"/></svg>
        <svg v-else width="12" height="12" viewBox="0 0 12 12"><rect x="3" y="0.5" width="8" height="8" rx="1" stroke="currentColor" stroke-width="1.2" fill="none"/><rect x="0.5" y="3" width="8" height="8" rx="1" fill="var(--border-strong)" stroke="currentColor" stroke-width="1.2"/></svg>
      </button>
      <button class="ctrl-btn ctrl-close" @click="close" title="关闭">
        <svg width="12" height="12" viewBox="0 0 12 12"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      </button>
    </div>
  </header>
</template>

<style scoped>
.titlebar {
  height: 32px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-light);
  -webkit-app-region: drag;
  user-select: none;
}

.titlebar-drag {
  display: flex; align-items: center; gap: 7px;
  padding-left: 11px;
}

.titlebar-logo {
  width: 16px; height: 16px;
  background: var(--bg-elevated);
  border-radius: 3.5px; display: flex; align-items: center; justify-content: center;
}

.titlebar-text {
  font-size: 11px; font-weight: 550; color: var(--text-muted);
  letter-spacing: 0.02em;
}

.titlebar-controls {
  display: flex; -webkit-app-region: no-drag;
}

.ctrl-btn {
  width: 38px; height: 32px; border: none; background: transparent;
  color: var(--text-faint); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.15s ease, color 0.15s ease;
}
.ctrl-btn:hover { background: var(--bg-hover); color: var(--text-secondary); }
.ctrl-close:hover { background: #e81123; color: white; }
</style>
