<script setup>
import { ref, onMounted, watch, nextTick } from 'vue'

const props = defineProps({
  statusText: { type: String, default: '就绪' },
  isProcessing: { type: Boolean, default: false },
  activeView: { type: String, default: 'chat' },
  sidebarExpanded: { type: Boolean, default: true },
  currentConversationId: { type: String, default: null },
  refreshKey: { type: Number, default: 0 },
  activeAgentRoles: { type: Set, default: () => new Set() },
})
const emit = defineEmits(['new-chat', 'stats', 'navigate', 'load-conversation', 'delete-conversation'])

// 滑动指示器：跟随激活按钮位移
const activityTopRef = ref(null)
const indicatorOffsetY = ref(0)

// Activity Bar 顶部 6 个视图按钮的顺序（与模板一致）
const VIEW_ORDER = ['chat', 'dashboard', 'agents', 'skills', 'workspace', 'settings']

function updateIndicator() {
  const container = activityTopRef.value
  if (!container) return
  const idx = VIEW_ORDER.indexOf(props.activeView)
  if (idx < 0) return
  const btn = container.children[idx]
  if (!btn) return
  // 指示器竖条居中对齐到按钮：按钮相对容器顶部的偏移 + 按钮高度/2 - 竖条高度/2
  // 竖条高度 18px（见 CSS），按钮高度 32px，居中即 offsetTop + (32-18)/2 = offsetTop + 7
  indicatorOffsetY.value = btn.offsetTop + (btn.offsetHeight - 18) / 2
}

onMounted(() => {
  nextTick(updateIndicator)
})
watch(() => props.activeView, () => nextTick(updateIndicator))

const conversations = ref([])
const APP_VERSION = __APP_VERSION__
const loadingConversations = ref(false)

// 智能体列表（从后端加载已注册的 agent）
const agents = ref([])

async function fetchAgents() {
  try {
    const res = await fetch('/api/agents')
    if (res.ok) {
      const data = await res.json()
      agents.value = data.agents || []
    }
  } catch {
    // 静默失败，保留空列表
  }
}

// agent role → 显示名（取括号前的中文名）
function agentDisplayName(agent) {
  const name = agent.name || agent.role || '?'
  return name.replace(/[（(].*$/, '').trim()
}

// 判断 agent 是否正在调用（圆圈高亮）
function isAgentActive(role) {
  return props.activeAgentRoles && props.activeAgentRoles.has(role)
}

async function fetchConversations() {
  loadingConversations.value = true
  try {
    const res = await fetch('/conversations?limit=20')
    if (res.ok) {
      const data = await res.json()
      conversations.value = data.conversations || []
    }
  } catch {
    // 静默失败
  } finally {
    loadingConversations.value = false
  }
}

function formatTime(ts) {
  if (!ts) return ''
  // SQLite CURRENT_TIMESTAMP 返回 UTC 时间字符串（"YYYY-MM-DD HH:MM:SS" 或 "YYYY-MM-DD"）
  // 必须显式标记为 UTC（加 'Z' 后缀），否则 new Date() 按本地时区解析，
  // 在 UTC+8 时区下会少 8 小时（表现为"8 小时前"）
  let normalized = String(ts).replace(' ', 'T')
  if (normalized.length === 10) normalized += 'T00:00:00'
  // 末尾追加 Z 标记为 UTC 时间（若未带时区标记）
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(normalized)) normalized += 'Z'
  const d = new Date(normalized)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const diff = now - d
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

onMounted(() => {
  fetchConversations()
  fetchAgents()
})

// 切回对话视图时刷新列表（新对话/消息后自动更新）
watch(() => props.activeView, (v) => {
  if (v === 'chat') fetchConversations()
})

// 删除/加载对话后刷新列表
watch(() => props.refreshKey, () => {
  fetchConversations()
})
</script>

<template>
  <div class="sidebar-layout">
    <nav class="activity-bar">
      <span class="activity-indicator" :style="{ transform: 'translateY(' + indicatorOffsetY + 'px)' }"></span>
      <div class="activity-top" ref="activityTopRef">
        <button class="activity-btn" :class="{ active: activeView === 'chat' }" title="对话" @click="emit('navigate', 'chat')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
        </button>
        <button class="activity-btn" :class="{ active: activeView === 'dashboard' }" title="仪表盘" @click="emit('navigate', 'dashboard')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.6"/><rect x="14" y="3" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.6"/><rect x="3" y="14" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.6"/><rect x="14" y="14" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.6"/></svg>
        </button>
        <button class="activity-btn" :class="{ active: activeView === 'agents' }" title="智能体" @click="emit('navigate', 'agents')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
        </button>
        <button class="activity-btn" :class="{ active: activeView === 'skills' }" title="技能" @click="emit('navigate', 'skills')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18M5 12h4m-4 4h4m-4 4h10a2 2 0 002-2v-4M3 9h18" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </button>
        <button class="activity-btn" :class="{ active: activeView === 'workspace' }" title="工作区" @click="emit('navigate', 'workspace')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
        </button>
        <button class="activity-btn" :class="{ active: activeView === 'settings' }" title="设置" @click="emit('navigate', 'settings')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.6"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </button>
      </div>
      <div class="activity-bottom">
        <button class="activity-btn" title="记忆统计" @click="emit('stats')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M4 19V10M10 19V4M16 19v-7M22 19H2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </button>
      </div>
    </nav>

    <!-- Expanded Sidebar Panel: 仅在对话页面显示，可收缩 -->
    <aside v-show="sidebarExpanded && activeView === 'chat'" class="expanded-sidebar">
      <div class="sidebar-section">
        <div class="section-label">对话</div>
        <button class="new-chat-btn" @click="emit('new-chat')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          <span>新对话</span>
        </button>
        <div v-if="loadingConversations" class="chat-list-empty">加载中...</div>
        <div v-else-if="conversations.length === 0" class="chat-list-empty">暂无历史对话</div>
        <div v-else class="chat-list">
          <div
            v-for="conv in conversations"
            :key="conv.id"
            class="chat-item-wrapper"
            :class="{ active: conv.id === currentConversationId }"
          >
            <button
              class="chat-item"
              @click="emit('load-conversation', conv.id)"
              :title="conv.title || '无标题'"
            >
              <span class="chat-item-dot"></span>
              <span class="chat-item-text">{{ conv.title || '对话' }}</span>
              <span class="chat-item-time">{{ formatTime(conv.updated_at || conv.created_at) }}</span>
            </button>
            <button
              class="chat-item-delete"
              title="删除对话"
              @click.stop="emit('delete-conversation', conv.id)"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
        </div>
      </div>

      <div class="sidebar-section">
        <div class="section-label">Agent</div>
        <div
          v-for="agent in agents"
          :key="agent.role"
          class="agent-item"
          :class="{ active: isAgentActive(agent.role) }"
          :title="agent.description || agent.name"
        >
          <span class="agent-dot" :class="{ glowing: isAgentActive(agent.role) }"></span>
          <span>{{ agentDisplayName(agent) }}</span>
        </div>
      </div>

      <div class="sidebar-footer">
        <div class="status-indicator">
          <span class="status-dot" :class="{ active: isProcessing }"></span>
          <span>{{ statusText }}</span>
        </div>
        <span class="version-tag">v{{ APP_VERSION }}</span>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.sidebar-layout { display: flex; flex-shrink: 0; height: 100%; }

/* ── Activity Bar: 40px ── */
.activity-bar {
  width: 40px; flex-shrink: 0;
  background: var(--bg-surface);
  border-right: 1px solid var(--border-light);
  display: flex; flex-direction: column; justify-content: space-between;
  padding: 5px 0;
  position: relative;
}
.activity-top, .activity-bottom {
  display: flex; flex-direction: column; align-items: center; gap: 0;
}
/* 滑动指示器：独立竖条，跟随激活按钮位移 */
.activity-indicator {
  position: absolute; left: 0; top: 0;
  width: 2px; height: 18px;
  background: var(--accent); border-radius: 0 2px 2px 0;
  transition: transform 0.34s var(--ease-spring);
  z-index: 1; pointer-events: none;
  /* translateY 由 JS 计算，对齐到激活按钮垂直中心 */
}
.activity-btn {
  width: 32px; height: 32px; border: none; border-radius: 6px;
  background: transparent; color: var(--text-faint);
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all 0.18s var(--ease-out-expo);
  position: relative;
}
.activity-btn:hover { color: var(--text-secondary); background: var(--bg-hover); }
.activity-btn.active { color: var(--accent); background: var(--accent-soft); }

/* ── Expanded Sidebar: 188px ── */
.expanded-sidebar {
  width: 188px; flex-shrink: 0;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  padding: 10px 8px 8px;
}

.sidebar-section { margin-bottom: 10px; }
.section-label {
  font-size: 9px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.09em; color: var(--text-faint);
  padding: 2px 6px 5px;
}

.new-chat-btn {
  display: flex; align-items: center; gap: 6px; padding: 7px 10px;
  background: var(--accent); border: none; border-radius: var(--radius-sm);
  color: white; font-family: var(--font-sans); font-size: 11.5px; font-weight: 600;
  cursor: pointer; transition: all 0.18s var(--ease-out-expo); width: 100%;
}
.new-chat-btn:hover { background: var(--accent-hover); transform: translateY(-1px); box-shadow: 0 4px 12px rgba(13, 148, 136, 0.22); }

.chat-list-empty {
  font-size: 11px; color: var(--text-faint); padding: 10px 6px; text-align: center;
}

.chat-list {
  display: flex; flex-direction: column; gap: 1px;
  max-height: 320px; overflow-y: auto;
}

.chat-item-wrapper {
  display: flex; align-items: center; border-radius: var(--radius-sm);
  transition: background 0.15s var(--ease-out-expo);
}
.chat-item-wrapper.active { background: var(--accent-soft); }
.chat-item-wrapper:hover .chat-item-delete { opacity: 1; }

.chat-item {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px; border-radius: var(--radius-sm);
  font-size: 11px; color: var(--text-muted); cursor: pointer;
  background: none; border: none; text-align: left; flex: 1; min-width: 0;
  transition: background 0.15s var(--ease-out-expo);
}
.chat-item:hover { color: var(--text-secondary); }
.chat-item-wrapper.active .chat-item { color: var(--accent); }
.chat-item-wrapper.active .chat-item-dot { background: var(--accent); }
.chat-item-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--text-faint); flex-shrink: 0;
}
.chat-item-text {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.chat-item-time {
  font-size: 9px; color: var(--text-faint); flex-shrink: 0;
}
.chat-item-delete {
  flex-shrink: 0; width: 20px; height: 20px; border: none; border-radius: 4px;
  background: transparent; color: var(--text-faint); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  opacity: 0; transition: all 0.15s var(--ease-out-expo);
}
.chat-item-delete:hover { color: var(--error); background: var(--bg-hover); }

.agent-item {
  display: flex; align-items: center; gap: 7px;
  padding: 5px 8px; border-radius: var(--radius-sm);
  font-size: 11.5px; color: var(--text-secondary);
  cursor: default; transition: all 0.18s var(--ease-out-expo);
}
.agent-item:hover { background: var(--bg-hover); }
.agent-item.active { color: var(--accent); }
/* 圆圈：默认灰色，调用中高亮 accent + 发光脉冲 */
.agent-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--text-faint);
  flex-shrink: 0;
  transition: background 0.2s var(--ease-out-expo), box-shadow 0.2s var(--ease-out-expo);
}
.agent-dot.glowing {
  background: var(--accent);
  box-shadow: 0 0 6px var(--accent), 0 0 2px var(--accent);
  animation: agent-pulse 1.2s ease-in-out infinite;
}
@keyframes agent-pulse {
  0%, 100% { box-shadow: 0 0 4px var(--accent), 0 0 1px var(--accent); }
  50%      { box-shadow: 0 0 8px var(--accent), 0 0 3px var(--accent); }
}

.sidebar-footer {
  margin-top: auto; padding-top: 8px;
  border-top: 1px solid var(--border-light);
  display: flex; align-items: center; justify-content: space-between;
}
.status-indicator {
  display: flex; align-items: center; gap: 5px;
  font-size: 10px; color: var(--text-muted);
}
.status-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--success); flex-shrink: 0; }
.status-dot.active { background: var(--accent); animation: pulse 0.8s ease-in-out infinite; }

.version-tag {
  font-size: 10px; color: var(--text-faint);
  font-family: var(--font-mono);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
</style>
