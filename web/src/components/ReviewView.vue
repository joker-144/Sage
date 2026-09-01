<script setup>
import { ref, reactive, onMounted, computed } from 'vue'

const props = defineProps({
  conversationId: { type: String, default: null },
})
const emit = defineEmits(['back'])

const loading = ref(true)
const error = ref('')
const workspace = ref('')
const draftPath = ref('')
const totalWords = ref(0)
const sections = ref([])
const activeKey = ref('')
const busy = ref(false)
const notice = ref('')

// 每个章节的本地操作状态
const opState = reactive({})  // key -> { outputs: [], detectIssues: [], aiBusy, detectBusy, dataInputs: [], revising, editing }

function getOp(key) {
  if (!opState[key]) {
    opState[key] = {
      detectIssues: [],
      aiBusy: false,
      detectBusy: false,
      dataInputs: [],
      rewriting: false,
    }
  }
  return opState[key]
}

const activeSection = computed(() => sections.value.find(s => s.key === activeKey.value) || null)

function fmtWord(n) {
  return (n || 0).toLocaleString('en-US')
}

function wordPct(sec) {
  if (!sec || !sec.target_words) return 0
  return Math.min(Math.round((sec.word_count || 0) * 100 / sec.target_words), 100)
}

function wordLevel(sec) {
  const p = wordPct(sec)
  if (!sec || !sec.target_words) return ''
  if (p >= 100) return 'done'
  if (p >= 60) return 'warn'
  return 'pending'
}

function statusText(sec) {
  if (!sec) return ''
  if (sec.locked) return '已锁定'
  if (sec.word_count === 0) return '待撰写'
  return '已撰写'
}

async function loadDraft() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (props.conversationId) params.set('conversation_id', props.conversationId)
    const res = await fetch(`/api/review/draft?${params.toString()}`, { signal: AbortSignal.timeout(10000) })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    workspace.value = data.workspace || ''
    draftPath.value = data.draft_path || ''
    totalWords.value = data.total_words || 0
    sections.value = data.sections || []
    if (!activeKey.value && sections.value.length) {
      activeKey.value = sections.value[0].key
    }
    // 初始化每个章节的数据占位输入
    for (const s of sections.value) {
      const op = getOp(s.key)
      op.dataInputs = (s.data_placeholders || []).map(p => ({
        index: p.index,
        context: p.context,
        value: '',
      }))
    }
  } catch (e) {
    error.value = '加载草稿失败: ' + e.message
  } finally {
    loading.value = false
  }
}

// 给审阅操作请求附加当前对话 ID（多对话草稿隔离）
function withConv(payload) {
  if (props.conversationId) {
    return { ...payload, conversation_id: props.conversationId }
  }
  return payload
}

// ── AI 检测 + 改写 ──
async function detectAI(sec) {
  const op = getOp(sec.key)
  op.detectBusy = true
  op.detectIssues = []
  try {
    const res = await fetch('/api/review/detect-ai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: sec.content }),
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    op.detectIssues = data.issues || []
    if (!op.detectIssues.length) {
      notice.value = '未检测到明显 AI 痕迹'
      setTimeout(() => (notice.value = ''), 2500)
    }
  } catch (e) {
    notice.value = '检测失败: ' + e.message
  } finally {
    op.detectBusy = false
  }
}

async function rewriteAI(sec) {
  const op = getOp(sec.key)
  if (!op.detectIssues.length) {
    notice.value = '请先执行 AI 痕迹检测'
    setTimeout(() => (notice.value = ''), 2500)
    return
  }
  op.rewriting = true
  busy.value = true
  try {
    const res = await fetch('/api/review/rewrite', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withConv({ section_key: sec.key, issues: op.detectIssues })),
      signal: AbortSignal.timeout(120000),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    sec.content = data.content
    sec.word_count = data.word_count || sec.content.length
    op.detectIssues = []
    notice.value = '改写完成，已更新草稿'
    setTimeout(() => (notice.value = ''), 2500)
    reloadAfterChange()
  } catch (e) {
    notice.value = '改写失败: ' + e.message
  } finally {
    op.rewriting = false
    busy.value = false
  }
}

// ── 数据回填 ──
async function fillData(sec, ph) {
  const op = getOp(sec.key)
  if (!ph.value.trim()) {
    notice.value = '请先输入数据'
    setTimeout(() => (notice.value = ''), 2500)
    return
  }
  busy.value = true
  try {
    const res = await fetch('/api/review/data-fill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withConv({ section_key: sec.key, placeholder_index: ph.index, value: ph.value })),
      signal: AbortSignal.timeout(120000),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    sec.content = data.content
    sec.word_count = data.word_count || sec.content.length
    notice.value = '数据已回填，段落已更新'
    setTimeout(() => (notice.value = ''), 2500)
    reloadAfterChange()
  } catch (e) {
    notice.value = '数据回填失败: ' + e.message
  } finally {
    busy.value = false
  }
}

function reloadAfterChange() {
  // 重新拉取以同步占位符变化与锁定状态
  loadDraft()
}

// ── 修订 ──
function startRevise(sec) {
  getOp(sec.key).revising = true
  getOp(sec.key).editContent = sec.content
}

function cancelRevise(sec) {
  delete getOp(sec.key).revising
}

async function saveRevise(sec) {
  const op = getOp(sec.key)
  busy.value = true
  try {
    const res = await fetch('/api/review/revise', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withConv({ section_key: sec.key, content: op.editContent || '' })),
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    sec.content = op.editContent || ''
    sec.word_count = (op.editContent || '').length
    delete op.revising
    notice.value = '修订已保存'
    setTimeout(() => (notice.value = ''), 2500)
  } catch (e) {
    notice.value = '修订保存失败: ' + e.message
  } finally {
    busy.value = false
  }
}

// ── 锁定/解锁 ──
async function toggleLock(sec) {
  busy.value = true
  try {
    const res = await fetch('/api/review/lock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withConv({ section_key: sec.key, locked: !sec.locked })),
      signal: AbortSignal.timeout(15000),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    sec.locked = !sec.locked
    notice.value = sec.locked ? '章节已锁定' : '章节已解锁'
    setTimeout(() => (notice.value = ''), 2000)
  } catch (e) {
    notice.value = '操作失败: ' + e.message
  } finally {
    busy.value = false
  }
}

function renderMarkdown(text) {
  if (!text) return ''
  let html = String(text)
  html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  // 代码块
  html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
  // 粗体
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
  // 行内代码
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  // 引用标注 [1][2] 高亮
  html = html.replace(/\[(\d+)\]|\[CITE:[^\]]+\]/g, '<span class="cite-mark">$&</span>')
  // 数据占位高亮
  html = html.replace(/【数据】|\[数据\]/g, '<span class="data-mark">$&</span>')
  return html
}

onMounted(loadDraft)
</script>

<template>
  <div class="review-view">
    <div class="view-header">
      <div class="header-row">
        <div>
          <div class="header-title-row">
            <button class="back-btn" title="返回草稿列表" @click="emit('back')">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <h1>成稿审阅</h1>
          </div>
          <p class="subtitle">
            工作区：{{ workspace || '未设置' }} · 全文 {{ fmtWord(totalWords) }} 字 · 成稿：{{ draftPath.split('/').pop() || 'paper.md' }}
            <span v-if="props.conversationId" class="conv-tag">对话草稿</span>
          </p>
        </div>
        <div class="header-actions">
          <button class="action-btn btn-secondary" @click="loadDraft">重新加载</button>
        </div>
      </div>
      <div v-if="notice" class="notice">{{ notice }}</div>
      <div v-if="error" class="alert alert-error">{{ error }}</div>
    </div>

    <div v-if="loading" class="empty-state">加载草稿中…</div>
    <div v-else-if="!sections.length" class="empty-state">
      <p>当前工作区还没有可审阅的论文草稿。</p>
      <p class="muted">请先在对话中使用「写作模式」生成论文，再回到此处审阅；或在工作区上传/创建 paper.md。</p>
    </div>
    <div v-else class="review-layout">
      <!-- 左：章节树 + 字数进度 -->
      <aside class="section-tree">
        <div class="tree-title">章节与字数进度</div>
        <div class="tree-total">
          <span>全文</span>
          <span class="total-num">{{ fmtWord(totalWords) }} 字</span>
        </div>
        <div
          v-for="sec in sections"
          :key="sec.key"
          class="tree-node"
          :class="{ active: sec.key === activeKey, locked: sec.locked, done: wordLevel(sec) === 'done' }"
          @click="activeKey = sec.key"
        >
          <div class="node-top">
            <span class="node-title">{{ sec.title }}</span>
            <span class="node-status" :class="sec.locked ? 's-locked' : ''">{{ statusText(sec) }}</span>
          </div>
          <div class="node-bar" v-if="sec.target_words">
            <div class="node-bar-fill" :class="wordLevel(sec)" :style="{ width: wordPct(sec) + '%' }"></div>
          </div>
          <div class="node-meta" v-if="sec.target_words">
            {{ fmtWord(sec.word_count) }} / {{ fmtWord(sec.target_words) }} 字
            · {{ Math.min(Math.round((sec.word_count || 0) * 100 / sec.target_words), 100) }}%
          </div>
          <div class="node-meta" v-else-if="sec.word_count">{{ fmtWord(sec.word_count) }} 字</div>
        </div>
      </aside>

      <!-- 右：章节详情审阅 -->
      <section v-if="activeSection" class="section-detail">
        <div class="detail-header">
          <h2>{{ activeSection.title }}</h2>
          <div class="detail-actions">
            <button
              class="action-btn btn-secondary"
              :disabled="busy || activeSection.locked"
              @click="detectAI(activeSection)"
            >{{ getOp(activeSection.key).detectBusy ? '检测中…' : 'AI 痕迹检测' }}</button>
            <button
              class="action-btn btn-primary"
              :disabled="busy || activeSection.locked || !getOp(activeSection.key).detectIssues.length"
              @click="rewriteAI(activeSection)"
            >{{ getOp(activeSection.key).rewriting ? '改写中…' : '深度改写' }}</button>
            <button
              class="action-btn btn-secondary"
              :disabled="busy || activeSection.locked"
              @click="startRevise(activeSection)"
            >修订</button>
            <button
              class="action-btn btn-secondary"
              :disabled="busy"
              @click="toggleLock(activeSection)"
            >{{ activeSection.locked ? '解锁' : '锁定' }}</button>
          </div>
        </div>
        <div class="detail-stats">
          实际字数 {{ fmtWord(activeSection.word_count) }}
          <template v-if="activeSection.target_words"> / 目标 {{ fmtWord(activeSection.target_words) }} 字（{{ wordPct(activeSection) }}%）</template>
          <span v-if="activeSection.locked" class="locked-badge">已锁定</span>
        </div>

        <!-- AI 检测结果 -->
        <div v-if="getOp(activeSection.key).detectIssues.length" class="ai-issues">
          <div class="ai-issues-title">检测到的 AI 痕迹（点击「深度改写」将据此重写本节）</div>
          <ul>
            <li v-for="(issue, i) in getOp(activeSection.key).detectIssues" :key="i">{{ issue }}</li>
          </ul>
        </div>

        <!-- 数据占位回填 -->
        <div v-if="getOp(activeSection.key).dataInputs.length" class="data-fill">
          <div class="data-fill-title">【数据】占位回填<span v-if="activeSection.locked" class="locked-hint">（章节已锁定）</span></div>
          <div v-for="(ph, i) in getOp(activeSection.key).dataInputs" :key="i" class="data-fill-row">
            <div class="data-fill-context">{{ ph.context }}</div>
            <div class="data-fill-input">
              <input
                v-model="ph.value"
                type="text"
                placeholder="输入真实数据/数值，如: 87.3% ± 2.1% (n=42)"
                :disabled="busy || activeSection.locked"
              />
              <button
                class="action-btn btn-primary btn-small"
                :disabled="busy || activeSection.locked || !ph.value.trim()"
                @click="fillData(activeSection, ph)"
              >回填</button>
            </div>
          </div>
        </div>

        <!-- 正文展示 / 修订编辑 -->
        <div v-if="getOp(activeSection.key).revising" class="revise-box">
          <textarea
            v-model="getOp(activeSection.key).editContent"
            class="revise-textarea"
            rows="16"
          ></textarea>
          <div class="revise-actions">
            <button class="action-btn btn-secondary" @click="cancelRevise(activeSection)">取消</button>
            <button class="action-btn btn-primary" :disabled="busy" @click="saveRevise(activeSection)">保存修订</button>
          </div>
        </div>
        <div v-else class="draft-body" v-html="renderMarkdown(activeSection.content)"></div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.review-view { flex: 1; overflow-y: auto; padding: 30px; max-width: 1200px; margin: 0 auto; width: 100%; }
.view-header { margin-bottom: 24px; }
.header-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.header-title-row { display: flex; align-items: center; gap: 8px; }
.back-btn {
  width: 26px; height: 26px; border: none; border-radius: var(--radius-sm);
  background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-muted);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  transition: all 0.18s var(--ease-out-expo);
}
.back-btn:hover { border-color: var(--accent-border); color: var(--accent); }
.conv-tag {
  margin-left: 8px; padding: 2px 7px; background: var(--accent-soft);
  color: var(--accent); border-radius: var(--radius-xs);
  font-size: 10.5px; font-weight: 600;
}
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
.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; transform: none; box-shadow: none; }
.btn-secondary { color: var(--text-secondary); background: var(--bg-surface); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent-border); color: var(--text-primary); }
.btn-secondary:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-small { padding: 5px 10px; font-size: 11.5px; }

.notice {
  margin-top: 10px; padding: 8px 12px;
  background: var(--accent-soft); border: 1px solid var(--accent-border);
  border-radius: var(--radius-sm); font-size: 12px; color: var(--accent);
}
.alert-error { background: var(--error-soft); border-color: #e74c3c; color: #e74c3c; margin-top: 10px; padding: 8px 12px; border-radius: var(--radius-sm); font-size: 12px; }
.empty-state { padding: 60px 20px; text-align: center; color: var(--text-muted); font-size: 14px; }
.empty-state .muted { color: var(--text-faint); font-size: 12.5px; margin-top: 6px; }

.review-layout { display: flex; gap: 20px; align-items: flex-start; }
.section-tree { flex: 0 0 300px; max-width: 300px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 14px; }
.tree-title { font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 10px; }
.tree-total { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-muted); padding-bottom: 10px; border-bottom: 1px solid var(--border-light); margin-bottom: 10px; }
.tree-total .total-num { font-family: var(--font-mono); font-weight: 600; color: var(--accent); }
.tree-node {
  padding: 10px 12px; border-radius: var(--radius-sm); cursor: pointer;
  transition: background 0.15s var(--ease-out-expo); border: 1px solid transparent;
  margin-bottom: 6px;
}
.tree-node:hover { background: var(--bg-input); }
.tree-node.active { background: var(--accent-soft); border-color: var(--accent-border); }
.tree-node.locked { opacity: 0.72; }
.tree-node.done .node-title { color: var(--accent); }
.node-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.node-title { font-size: 12.5px; font-weight: 550; color: var(--text-secondary); }
.node-status { font-size: 10px; color: var(--text-faint); flex-shrink: 0; }
.node-status.s-locked { color: #e74c3c; }
.node-bar { height: 3px; background: var(--bg-input); border-radius: 2px; margin-top: 7px; overflow: hidden; }
.node-bar-fill { height: 100%; border-radius: 2px; transition: width 0.4s var(--ease-out-expo); }
.node-bar-fill.pending { background: #e74c3c; }
.node-bar-fill.warn { background: #f59e0b; }
.node-bar-fill.done { background: var(--accent); }
.node-meta { font-size: 10px; color: var(--text-faint); font-family: var(--font-mono); margin-top: 4px; }

.section-detail { flex: 1; min-width: 0; background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 22px 24px; }
.detail-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.detail-header h2 { font-size: 17px; font-weight: 650; color: var(--text-primary); }
.detail-actions { display: flex; gap: 8px; flex-shrink: 0; flex-wrap: wrap; }
.detail-stats { font-size: 12px; color: var(--text-muted); margin-bottom: 14px; font-family: var(--font-mono); }
.locked-badge { margin-left: 8px; padding: 2px 7px; background: rgba(231, 76, 60, 0.12); color: #e74c3c; border-radius: var(--radius-xs); font-family: var(--font-sans); font-size: 10.5px; }

.ai-issues {
  background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: var(--radius-sm); padding: 12px 14px; margin-bottom: 14px;
}
.ai-issues-title { font-size: 12px; font-weight: 600; color: #f59e0b; margin-bottom: 6px; }
.ai-issues ul { margin: 0; padding-left: 18px; }
.ai-issues li { font-size: 12px; color: var(--text-secondary); line-height: 1.6; }

.data-fill {
  background: var(--bg-input); border: 1px dashed var(--accent-border);
  border-radius: var(--radius-sm); padding: 12px 14px; margin-bottom: 14px;
}
.data-fill-title { font-size: 12px; font-weight: 600; color: var(--accent); margin-bottom: 8px; }
.locked-hint { color: #e74c3c; font-weight: 400; }
.data-fill-row { margin-bottom: 10px; }
.data-fill-context { font-size: 11.5px; color: var(--text-muted); background: var(--bg-surface); border-radius: var(--radius-xs); padding: 6px 8px; margin-bottom: 6px; line-height: 1.5; }
.data-fill-input { display: flex; gap: 8px; }
.data-fill-input input {
  flex: 1; padding: 6px 10px; font-size: 12px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text-primary); outline: none;
}
.data-fill-input input:focus { border-color: var(--accent); }
.data-fill-input input:disabled { opacity: 0.5; }

.revise-box { margin-top: 6px; }
.revise-textarea {
  width: 100%; padding: 12px; font-size: 13px; line-height: 1.7;
  background: var(--bg-input); border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text-primary); resize: vertical; outline: none; font-family: inherit;
}
.revise-textarea:focus { border-color: var(--accent); }
.revise-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }

.draft-body { line-height: 1.8; font-size: 13.5px; color: var(--text-primary); }
.draft-body :deep(p) { margin: 0 0 12px; }
.draft-body :deep(pre) { background: var(--bg-input); padding: 10px 12px; border-radius: var(--radius-sm); overflow-x: auto; margin: 8px 0; }
.draft-body :deep(code) { font-family: var(--font-mono); font-size: 12px; }
.draft-body :deep(.cite-mark) { color: var(--accent); font-weight: 600; }
.draft-body :deep(.data-mark) { background: rgba(245, 158, 11, 0.18); color: #f59e0b; font-weight: 600; padding: 1px 5px; border-radius: var(--radius-xs); }
</style>