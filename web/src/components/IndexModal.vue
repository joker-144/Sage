<script setup>
import { ref } from 'vue'

const emit = defineEmits(['close'])

const state = ref('idle') // idle | indexing | success | error
const stats = ref(null)
const errorMsg = ref('')

async function startIndex(force = false) {
  state.value = 'indexing'
  try {
    const res = await fetch('/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force }),
    })
    const data = await res.json()
    if (data.success) {
      stats.value = data.stats
      state.value = 'success'
    } else {
      errorMsg.value = data.error || '未知错误'
      state.value = 'error'
    }
  } catch (err) {
    errorMsg.value = err.message
    state.value = 'error'
  }
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-header">
        <h2>索引论文文献库</h2>
        <button class="close-btn" @click="emit('close')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </button>
      </div>

      <div class="modal-body">
        <!-- 待机状态 -->
        <div v-if="state === 'idle'" class="state-idle">
          <div class="info-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none">
              <path d="M3 7l9-4 9 4M3 7v10l9 4 9-4V7M3 7l9 4 9-4M12 11v10" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
            </svg>
          </div>
          <p class="info-text">
            索引会将论文文档分块并通过本地 Embedding 模型生成向量，<br>
            存入 SQLite 供 <code>search_literature</code> 工具语义检索。
          </p>
          <div class="actions">
            <button class="btn-primary" @click="startIndex(false)">
              增量索引
            </button>
            <button class="btn-secondary" @click="startIndex(true)">
              强制全量索引
            </button>
          </div>
        </div>

        <!-- 索引中 -->
        <div v-else-if="state === 'indexing'" class="state-indexing">
          <div class="spinner"></div>
          <p>正在通过本地 Embedding 模型生成向量…</p>
          <p class="hint">首次使用需下载模型（约 80MB），大项目可能需要几分钟</p>
        </div>

        <!-- 成功 -->
        <div v-else-if="state === 'success'" class="state-success">
          <div class="success-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/>
              <path d="M8 12l3 3 5-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>
          <div class="stats-grid">
            <div class="stat-item">
              <div class="stat-value">{{ stats.files }}</div>
              <div class="stat-label">索引文件</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.chunks }}</div>
              <div class="stat-label">文档块</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.skipped }}</div>
              <div class="stat-label">跳过未改</div>
            </div>
          </div>
          <button class="btn-primary" @click="emit('close')">完成</button>
        </div>

        <!-- 失败 -->
        <div v-else-if="state === 'error'" class="state-error">
          <div class="error-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/>
              <path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </div>
          <p class="error-text">{{ errorMsg }}</p>
          <button class="btn-primary" @click="state = 'idle'">重试</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  width: 440px;
  max-width: 90vw;
  box-shadow: var(--shadow-lg);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid var(--border-light);
}

.modal-header h2 {
  font-size: 16px;
  font-weight: 600;
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 4px;
  border-radius: var(--radius-sm);
  transition: all var(--transition);
}

.close-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.modal-body {
  padding: 32px 24px;
  text-align: center;
  min-height: 200px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.info-icon, .success-icon, .error-icon {
  color: var(--accent);
  margin-bottom: 16px;
}

.success-icon { color: var(--success); }
.error-icon { color: var(--error); }

.info-text {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.7;
  margin-bottom: 24px;
}

.info-text code {
  font-family: var(--font-mono);
  background: var(--bg-card);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--accent);
}

.actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}

.btn-primary {
  background: var(--accent);
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: var(--radius-md);
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition);
}

.btn-primary:hover {
  background: var(--accent-hover);
}

.btn-secondary {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
  padding: 10px 20px;
  border-radius: var(--radius-md);
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition);
}

.btn-secondary:hover {
  border-color: var(--accent-border);
  color: var(--text-primary);
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.hint {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
}

.stats-grid {
  display: flex;
  gap: 16px;
  justify-content: center;
  margin: 20px 0 24px;
}

.stat-item {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 16px 20px;
  min-width: 90px;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--accent);
  font-family: var(--font-mono);
}

.stat-label {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
}

.error-text {
  font-size: 13px;
  color: var(--error);
  margin-bottom: 24px;
  word-break: break-word;
}
</style>
