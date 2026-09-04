<script setup>
import { ref, onMounted } from 'vue'

const emit = defineEmits(['open-draft'])

const loading = ref(true)
const error = ref('')
const drafts = ref([])

function fmtWord(n) {
  return (n || 0).toLocaleString('en-US')
}

function wordPct(item) {
  if (!item || !item.target_words) return 0
  return Math.min(Math.round((item.total_words || 0) * 100 / item.target_words), 100)
}

function wordLevel(item) {
  const p = wordPct(item)
  if (!item.target_words) return ''
  if (p >= 100) return 'done'
  if (p >= 60) return 'warn'
  return 'pending'
}

function formatTime(ts) {
  if (!ts) return ''
  let normalized = String(ts).replace(' ', 'T')
  if (normalized.length === 10) normalized += 'T00:00:00'
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(normalized)) normalized += 'Z'
  const d = new Date(normalized)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const diff = now - d
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' })
}

async function loadDrafts() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/review/drafts', { signal: AbortSignal.timeout(10000) })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    drafts.value = data.drafts || []
  } catch (e) {
    error.value = '加载草稿列表失败: ' + e.message
  } finally {
    loading.value = false
  }
}

onMounted(loadDrafts)
</script>

<template>
  <div class="review-list">
    <div class="view-header">
      <div class="header-row">
        <div>
          <h1>成稿审阅</h1>
          <p class="subtitle">工作区内已有 {{ drafts.length }} 篇论文草稿，点击进入审阅</p>
        </div>
        <div class="header-actions">
          <button class="action-btn btn-secondary" @click="loadDrafts">刷新列表</button>
        </div>
      </div>
      <div v-if="error" class="alert alert-error">{{ error }}</div>
    </div>

    <div v-if="loading" class="empty-state">加载草稿列表…</div>
    <div v-else-if="!drafts.length" class="empty-state">
      <p>当前还没有可审阅的论文草稿。</p>
      <p class="muted">请先在对话中生成论文，再回到此处审阅。每个对话的论文会独立保存，互不影响。</p>
    </div>
    <div v-else class="draft-grid">
      <div
        v-for="item in drafts"
        :key="item.conversation_id"
        class="draft-card"
        @click="emit('open-draft', item.conversation_id)"
      >
        <div class="card-top">
          <span class="card-conv">{{ item.conversation_title || '（未命名对话）' }}</span>
          <span class="card-time">{{ formatTime(item.updated_at) }}</span>
        </div>
        <div class="card-topic">{{ item.topic || '（无主题）' }}</div>
        <div class="card-words">
          <div class="card-bar"><div class="card-bar-fill" :class="wordLevel(item)" :style="{ width: wordPct(item) + '%' }"></div></div>
          <div class="card-words-meta">
            {{ fmtWord(item.total_words) }} / {{ fmtWord(item.target_words) }} 字
            <span class="card-pct" v-if="item.target_words">{{ wordPct(item) }}%</span>
          </div>
        </div>
        <div class="card-footer">
          <span class="card-path">{{ item.draft_path.split('/').pop() || 'paper.md' }}</span>
          <button class="action-btn btn-primary btn-small">进入审阅 →</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.review-list { flex: 1; overflow-y: auto; padding: 30px; max-width: 1200px; margin: 0 auto; width: 100%; }
.view-header { margin-bottom: 24px; }
.header-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.view-header h1 { font-size: 22px; font-weight: 650; color: var(--text-primary); letter-spacing: -0.02em; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
.header-actions { display: flex; gap: 8px; flex-shrink: 0; }
.action-btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 14px; border: none; border-radius: var(--radius-sm);
  font-size: 12.5px; cursor: pointer; transition: all 0.18s var(--ease-out-expo);
}
.btn-primary { color: white; background: var(--accent); }
.btn-primary:hover { background: var(--accent-hover); transform: translateY(-1px); box-shadow: 0 4px 12px rgba(13, 148, 136, 0.22); }
.btn-secondary { color: var(--text-secondary); background: var(--bg-surface); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent-border); color: var(--text-primary); }
.btn-small { padding: 5px 10px; font-size: 11.5px; }
.alert-error { background: var(--error-soft); border-color: #e74c3c; color: #e74c3c; margin-top: 10px; padding: 8px 12px; border-radius: var(--radius-sm); font-size: 12px; }
.empty-state { padding: 60px 20px; text-align: center; color: var(--text-muted); font-size: 14px; }
.empty-state .muted { color: var(--text-faint); font-size: 12.5px; margin-top: 6px; }

.draft-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.draft-card {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 16px 18px;
  cursor: pointer; transition: all 0.18s var(--ease-out-expo);
  display: flex; flex-direction: column; gap: 10px;
}
.draft-card:hover { border-color: var(--accent-border); transform: translateY(-2px); box-shadow: 0 6px 18px rgba(0, 0, 0, 0.06); }
.card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.card-conv { font-size: 13px; font-weight: 650; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-time { font-size: 11px; color: var(--text-faint); flex-shrink: 0; }
.card-topic { font-size: 12px; color: var(--text-muted); line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 36px; }
.card-words { display: flex; flex-direction: column; gap: 6px; }
.card-bar { height: 4px; background: var(--bg-input); border-radius: 2px; overflow: hidden; }
.card-bar-fill { height: 100%; border-radius: 2px; transition: width 0.4s var(--ease-out-expo); }
.card-bar-fill.pending { background: #e74c3c; }
.card-bar-fill.warn { background: #f59e0b; }
.card-bar-fill.done { background: var(--accent); }
.card-words-meta { font-size: 11px; color: var(--text-faint); font-family: var(--font-mono); display: flex; justify-content: space-between; }
.card-pct { color: var(--accent); font-weight: 600; }
.card-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; border-top: 1px solid var(--border-light); padding-top: 10px; }
.card-path { font-size: 11px; color: var(--text-faint); font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>