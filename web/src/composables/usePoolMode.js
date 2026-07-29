import { ref } from 'vue'

// 全局池模式状态（单例，跨组件共享）
// poolMode 为 true 时，检索覆盖所有工作空间
const poolMode = ref(false)

/**
 * 切换全选池模式
 * @param {boolean} value - 目标状态，不传则切换当前值
 * @returns {Promise<boolean>} 切换后的状态
 */
async function togglePoolMode(value) {
  const next = typeof value === 'boolean' ? value : !poolMode.value
  // 同步到后端（失败不阻塞前端状态切换，后端会兜底默认单空间）
  try {
    await fetch('/api/sage/pool-mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pool_mode: next }),
    })
  } catch { /* ignore — 后端不可达时前端仍可切换 UI 状态 */ }
  poolMode.value = next
  return next
}

/**
 * 从后端同步当前池模式状态（页面初始化时调用）
 */
async function syncPoolMode() {
  try {
    const res = await fetch('/api/sage/pool-mode')
    if (res.ok) {
      const data = await res.json()
      poolMode.value = !!data.pool_mode
    }
  } catch { /* ignore */ }
}

export function usePoolMode() {
  return {
    poolMode,
    togglePoolMode,
    syncPoolMode,
  }
}
