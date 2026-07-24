<script setup>
import { ref, onMounted } from 'vue'

const stats = ref({
  todayConversations: 0,
  todayMessages: 0,
  todayToolCalls: 0,
  totalTokens: 0,
  activeAgents: 0,
  apiVersion: '0.5.3',
})

const tokenStats = ref({
  total_prompt: 0,
  total_completion: 0,
  total_tokens: 0,
  total_calls: 0,
  today_prompt: 0,
  today_completion: 0,
  today_tokens: 0,
  today_calls: 0,
})

const loading = ref(true)

// 格式化数字（加千分位逗号）
function fmt(n) {
  return (n || 0).toLocaleString('en-US')
}

async function loadStats() {
  loading.value = true
  try {
    const [healthRes, memRes, tokenRes] = await Promise.allSettled([
      fetch('/health'),
      fetch('/memory/stats'),
      fetch('/api/token-stats'),
    ])
    if (healthRes.status === 'fulfilled') {
      const h = await healthRes.value.json()
      stats.value.apiVersion = h.version || '0.5.3'
    }
    if (memRes.status === 'fulfilled') {
      const m = await memRes.value.json()
      stats.value.todayConversations = m.conversations || 0
      stats.value.todayMessages = m.messages || 0
      stats.value.todayToolCalls = m.tool_calls || 0
      stats.value.activeAgents = m.active_agents || 0
    }
    if (tokenRes.status === 'fulfilled') {
      tokenStats.value = await tokenRes.value.json()
    }
  } catch { /* 使用默认值 */ }
  finally { loading.value = false }
}

onMounted(loadStats)

const quickActions = [
  { icon: 'LW', label: '文献综述', prompt: '请基于工作空间的论文文献库，撰写关于以下研究主题的文献综述：' },
  { icon: 'OT', label: '生成大纲', prompt: '请为以下研究课题生成符合学术规范的论文大纲：' },
  { icon: 'WR', label: '撰写段落', prompt: '请撰写论文以下章节的段落，结合文献库引用支撑文献：' },
  { icon: 'PL', label: '学术润色', prompt: '请对以下论文段落进行学术化润色，消除口语化表达：' },
]
</script>

<template>
  <div class="dashboard">
    <header class="dashboard-header">
      <h1>仪表盘</h1>
      <p class="subtitle">Sage 运行状态一览</p>
    </header>

    <!-- 统计卡片 -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon-wrap" style="background: rgba(13, 148, 136, 0.10); color: var(--accent);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.todayConversations }}</div>
          <div class="stat-label">今日对话数</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon-wrap" style="background: rgba(16, 185, 129, 0.10); color: var(--success);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><polyline points="14 2 14 8 20 8" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><line x1="16" y1="13" x2="8" y2="13" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="16" y1="17" x2="8" y2="17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.todayMessages }}</div>
          <div class="stat-label">消息数量</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon-wrap" style="background: rgba(245, 158, 11, 0.10); color: var(--warning);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><polyline points="16 18 22 12 16 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><polyline points="8 6 2 12 8 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.todayToolCalls }}</div>
          <div class="stat-label">工具调用</div>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon-wrap" style="background: rgba(139, 92, 246, 0.10); color: #8b5cf6;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/><path d="M12 6v6l4 2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.activeAgents }}</div>
          <div class="stat-label">活跃 Agent</div>
        </div>
      </div>
    </div>

    <!-- Token 用量统计 -->
    <div class="section">
      <h2>Token 用量统计</h2>
      <div class="token-grid">
        <div class="token-card highlight">
          <div class="token-label">今日 Token</div>
          <div class="token-value">{{ fmt(tokenStats.today_tokens) }}</div>
          <div class="token-sub">{{ tokenStats.today_calls }} 次调用</div>
        </div>
        <div class="token-card">
          <div class="token-label">累计 Token</div>
          <div class="token-value">{{ fmt(tokenStats.total_tokens) }}</div>
          <div class="token-sub">{{ tokenStats.total_calls }} 次调用</div>
        </div>
        <div class="token-card">
          <div class="token-label">今日输入 / 输出</div>
          <div class="token-value-split">
            <span class="in">{{ fmt(tokenStats.today_prompt) }}</span>
            <span class="sep">/</span>
            <span class="out">{{ fmt(tokenStats.today_completion) }}</span>
          </div>
          <div class="token-sub">prompt / completion</div>
        </div>
        <div class="token-card">
          <div class="token-label">累计输入 / 输出</div>
          <div class="token-value-split">
            <span class="in">{{ fmt(tokenStats.total_prompt) }}</span>
            <span class="sep">/</span>
            <span class="out">{{ fmt(tokenStats.total_completion) }}</span>
          </div>
          <div class="token-sub">prompt / completion</div>
        </div>
      </div>
    </div>

    <!-- 快捷操作 -->
    <div class="section">
      <h2>快捷操作</h2>
      <div class="quick-grid">
        <div v-for="act in quickActions" :key="act.label" class="quick-card">
          <div class="quick-icon">{{ act.icon }}</div>
          <div class="quick-label">{{ act.label }}</div>
          <div class="quick-prompt">{{ act.prompt }}</div>
        </div>
      </div>
    </div>

    <!-- 系统信息 -->
    <div class="section">
      <h2>系统信息</h2>
      <div class="sys-info">
        <div class="sys-row"><span>API 版本</span><code>{{ stats.apiVersion }}</code></div>
        <div class="sys-row"><span>运行环境</span><code>Python 3.12 · FastAPI + SSE</code></div>
        <div class="sys-row"><span>架构模式</span><code>Agentic Loop · 多 Agent 协同</code></div>
        <div class="sys-row"><span>前端框架</span><code>Vue 3 + Vite · Marked + highlight.js</code></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard { flex: 1; overflow-y: auto; padding: 30px; max-width: 920px; margin: 0 auto; }

.dashboard-header { margin-bottom: 26px; }
.dashboard-header h1 { font-size: 21px; font-weight: 650; color: var(--text-primary); letter-spacing: -0.01em; }
.subtitle { font-size: 12.5px; color: var(--text-muted); margin-top: 3px; }

.stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 30px; }

.stat-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px 14px;
  display: flex; align-items: flex-start; gap: 13px;
  transition: all 0.2s var(--ease-out-expo);
  animation: fadeInUp 0.4s var(--ease-out-expo) both;
}
.stat-card:nth-child(2) { animation-delay: 0.04s; }
.stat-card:nth-child(3) { animation-delay: 0.08s; }
.stat-card:nth-child(4) { animation-delay: 0.12s; }
.stat-card:hover {
  border-color: var(--border-strong);
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

.stat-icon-wrap {
  width: 38px; height: 38px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.stat-value { font-size: 24px; font-weight: 700; font-family: var(--font-mono); color: var(--text-primary); line-height: 1.05; }
.stat-label { font-size: 11.5px; color: var(--text-muted); margin-top: 1px; }

.section { margin-bottom: 26px; }
.section h2 { font-size: 14px; font-weight: 600; color: var(--text-secondary); margin-bottom: 11px; padding-left: 2px; letter-spacing: -0.01em; }

/* Token 统计 */
.token-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.token-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 14px 16px;
  transition: all 0.2s var(--ease-out-expo);
}
.token-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}
.token-card.highlight {
  border-color: var(--accent-border);
  background: linear-gradient(135deg, var(--accent-soft), var(--bg-surface));
}
.token-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; letter-spacing: 0.02em; }
.token-value { font-size: 22px; font-weight: 700; font-family: var(--font-mono); color: var(--text-primary); line-height: 1.1; }
.token-value-split { font-size: 18px; font-weight: 700; font-family: var(--font-mono); line-height: 1.1; display: flex; align-items: baseline; gap: 5px; }
.token-value-split .in { color: var(--accent); }
.token-value-split .out { color: var(--success); }
.token-value-split .sep { color: var(--text-faint); font-weight: 400; }
.token-sub { font-size: 10.5px; color: var(--text-faint); margin-top: 4px; }

.quick-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px; }
.quick-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 15px;
  cursor: pointer;
  transition: all 0.18s var(--ease-out-expo);
}
.quick-card:hover {
  background: var(--bg-card);
  border-color: var(--accent-border);
  box-shadow: var(--shadow-sm);
}
.quick-icon { font-size: 20px; font-weight: 700; font-family: var(--font-mono); color: var(--accent); margin-bottom: 3px; }
.quick-label { font-size: 13.5px; font-weight: 600; color: var(--text-primary); }
.quick-prompt { font-size: 11px; color: var(--text-muted); margin-top: 3px; line-height: 1.45; }

.sys-info {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.sys-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 11px 15px;
  border-bottom: 1px solid var(--border-light);
  font-size: 12.5px; color: var(--text-secondary);
}
.sys-row:last-child { border-bottom: none; }
.sys-row code {
  font-family: var(--font-mono); font-size: 11.5px;
  color: var(--text-primary); background: var(--bg-card);
  padding: 3px 9px; border-radius: 4px; border: 1px solid var(--border-light);
}

@media (max-width: 768px) {
  .dashboard { padding: 18px 14px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  .token-grid { grid-template-columns: repeat(2, 1fr); }
  .quick-grid { grid-template-columns: 1fr; }
}
</style>
