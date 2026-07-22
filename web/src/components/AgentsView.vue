<script setup>
import { ref, onMounted } from 'vue'

const agents = ref([])
const loading = ref(true)
const selectedAgent = ref(null)

async function loadData() {
  loading.value = true
  try {
    const res = await fetch('/api/agents')
    if (res.ok) {
      const data = await res.json()
      agents.value = data.agents || []
    }
  } catch { /* 使用默认值 */ }
  finally { loading.value = false }
}

function getInitials(name) {
  if (!name) return '?'
  const cleaned = name.replace(/[（(].*$/, '').trim()
  return cleaned.slice(0, 2).toUpperCase()
}

function getColor(index) {
  const colors = ['var(--accent)', '#8b5cf6', 'var(--warning)', 'var(--success)', '#0ea5e9', 'var(--agent)']
  return colors[index % colors.length]
}

function openDetail(agent) {
  selectedAgent.value = agent
}

function closeDetail() {
  selectedAgent.value = null
}

onMounted(loadData)
</script>

<template>
  <div class="agents-view">
    <header class="view-header">
      <h1>智能体</h1>
      <p class="subtitle">系统已注册的智能体角色，部分智能体拥有专属技能</p>
    </header>

    <div v-if="loading" class="loading-state">加载中...</div>

    <template v-else>
      <div v-if="agents.length === 0" class="empty-state">
        <p>暂无已注册的智能体</p>
      </div>

      <div v-else class="agents-grid">
        <div
          v-for="(agent, index) in agents"
          :key="agent.role"
          class="agent-card"
          tabindex="0"
          @click="openDetail(agent)"
          @keydown.enter="openDetail(agent)"
        >
          <div class="agent-header">
            <div class="agent-avatar" :style="{ background: getColor(index) }">
              {{ getInitials(agent.name) }}
            </div>
            <div class="agent-meta">
              <div class="agent-name">{{ agent.name }}</div>
              <div class="agent-role">{{ agent.role }}</div>
            </div>
            <div v-if="agent.has_skill" class="skill-badge" title="拥有专属技能">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
                <path d="M2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
              </svg>
              专属技能
            </div>
          </div>

          <p class="agent-desc">{{ agent.description || '暂无描述' }}</p>

          <div v-if="agent.capabilities && agent.capabilities.length" class="agent-caps">
            <span v-for="(cap, j) in agent.capabilities.slice(0, 3)" :key="j" class="cap-tag">{{ cap }}</span>
            <span v-if="agent.capabilities.length > 3" class="cap-more">+{{ agent.capabilities.length - 3 }}</span>
          </div>

          <div v-if="agent.skill" class="agent-skill-preview">
            <div class="skill-label">专属技能</div>
            <div class="skill-name">{{ agent.skill.name }} <span class="skill-ver">v{{ agent.skill.version }}</span></div>
            <p class="skill-desc">{{ agent.skill.description }}</p>
          </div>

          <div class="card-hint">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
              <path d="M5 12h14M12 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            点击查看详情
          </div>
        </div>
      </div>
    </template>

    <!-- 智能体详情弹窗 -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="selectedAgent" class="agent-modal-overlay" @click.self="closeDetail">
          <div class="agent-modal" role="dialog" aria-modal="true">
            <div class="modal-header">
              <div class="modal-title-row">
                <div class="agent-avatar modal-avatar">{{ getInitials(selectedAgent.name) }}</div>
                <div>
                  <div class="modal-name">{{ selectedAgent.name }}</div>
                  <div class="modal-role">{{ selectedAgent.role }}</div>
                </div>
              </div>
              <button class="modal-close" aria-label="关闭" @click="closeDetail">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </button>
            </div>

            <div class="modal-body">
              <section class="modal-section">
                <h3>角色介绍</h3>
                <p class="modal-desc">{{ selectedAgent.description || '暂无描述' }}</p>
              </section>

              <section v-if="selectedAgent.capabilities && selectedAgent.capabilities.length" class="modal-section">
                <h3>核心能力</h3>
                <div class="detail-tags">
                  <span v-for="(cap, i) in selectedAgent.capabilities" :key="i" class="tag tag-cap">{{ cap }}</span>
                </div>
              </section>

              <section v-if="selectedAgent.skill" class="modal-section">
                <h3>专属技能</h3>
                <div class="skill-detail-card">
                  <div class="skill-detail-header">
                    <span class="skill-detail-name">{{ selectedAgent.skill.name }}</span>
                    <span class="skill-detail-ver">v{{ selectedAgent.skill.version }}</span>
                  </div>
                  <p class="skill-detail-desc">{{ selectedAgent.skill.description }}</p>

                  <div v-if="selectedAgent.skill.capabilities && selectedAgent.skill.capabilities.length" class="skill-sub-section">
                    <div class="sub-label">技能能力</div>
                    <div class="detail-tags">
                      <span v-for="(cap, i) in selectedAgent.skill.capabilities" :key="i" class="tag tag-tool">{{ cap }}</span>
                    </div>
                  </div>

                  <div v-if="selectedAgent.skill.trigger_conditions && selectedAgent.skill.trigger_conditions.length" class="skill-sub-section">
                    <div class="sub-label">调用时机</div>
                    <div class="detail-tags">
                      <span v-for="(cond, i) in selectedAgent.skill.trigger_conditions" :key="i" class="tag tag-trigger">{{ cond }}</span>
                    </div>
                  </div>

                  <div v-if="selectedAgent.skill.tools && selectedAgent.skill.tools.length" class="skill-sub-section">
                    <div class="sub-label">可用工具</div>
                    <div class="detail-tags">
                      <span v-for="(tool, i) in selectedAgent.skill.tools" :key="i" class="tag tag-tool">{{ tool }}</span>
                    </div>
                  </div>
                </div>
              </section>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.agents-view { flex: 1; overflow-y: auto; padding: 30px; max-width: 1100px; margin: 0 auto; }

.view-header { margin-bottom: 24px; }
.view-header h1 { font-size: 22px; font-weight: 650; color: var(--text-primary); letter-spacing: -0.02em; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }

.loading-state { text-align: center; padding: 40px; color: var(--text-faint); font-size: 13px; }
.empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); font-size: 13px; }

.agents-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.agent-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px;
  transition: transform 0.18s var(--ease-out-expo), box-shadow 0.18s var(--ease-out-expo), border-color 0.18s var(--ease-out-expo);
  display: flex; flex-direction: column; gap: 10px;
  cursor: pointer;
}
.agent-card:hover, .agent-card:focus {
  transform: translateY(-2px);
  border-color: var(--accent-border);
  box-shadow: var(--shadow-md);
  outline: none;
}
.agent-card:focus-visible {
  box-shadow: var(--shadow-md), var(--shadow-glow);
}

.agent-header { display: flex; align-items: center; gap: 10px; }
.agent-avatar {
  width: 36px; height: 36px; border-radius: 9px;
  color: white; display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; flex-shrink: 0;
  box-shadow: var(--shadow-xs);
}
.agent-meta { min-width: 0; flex: 1; }
.agent-name { font-size: 14px; font-weight: 650; color: var(--text-primary); }
.agent-role { font-size: 11px; color: var(--text-faint); font-family: var(--font-mono); margin-top: 1px; }

.skill-badge {
  display: flex; align-items: center; gap: 4px;
  font-size: 10px; color: var(--accent);
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  padding: 2px 7px; border-radius: 4px;
  flex-shrink: 0;
}

.agent-desc {
  font-size: 12px; color: var(--text-secondary); line-height: 1.6; margin: 0;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.agent-caps { display: flex; flex-wrap: wrap; gap: 6px; }
.cap-tag {
  font-size: 10.5px; padding: 3px 8px; border-radius: 4px;
  color: var(--text-muted); background: var(--bg-input);
  border: 1px solid var(--border-light);
}
.cap-more {
  font-size: 10.5px; padding: 3px 8px; border-radius: 4px;
  color: var(--text-faint); background: var(--bg-input);
  border: 1px solid var(--border-light);
}

.agent-skill-preview {
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}
.skill-label { font-size: 10px; color: var(--accent); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
.skill-name { font-size: 12.5px; font-weight: 600; color: var(--text-primary); margin-top: 2px; }
.skill-ver { font-size: 10px; font-family: var(--font-mono); color: var(--text-faint); font-weight: 400; }
.skill-desc {
  font-size: 11px; color: var(--text-secondary); line-height: 1.5; margin: 4px 0 0;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.card-hint {
  display: flex; align-items: center; gap: 5px;
  font-size: 11px; color: var(--text-faint);
  transition: color 0.18s var(--ease-out-expo);
}
.agent-card:hover .card-hint { color: var(--accent); }

/* 详情弹窗 */
.agent-modal-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(6px);
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
}
.agent-modal {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  width: 100%; max-width: 560px;
  max-height: 80vh;
  display: flex; flex-direction: column;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}
.modal-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 20px 20px 14px;
  border-bottom: 1px solid var(--border-light);
}
.modal-title-row { display: flex; align-items: center; gap: 12px; }
.modal-avatar { width: 40px; height: 40px; font-size: 13px; }
.modal-name { font-size: 16px; font-weight: 650; color: var(--text-primary); }
.modal-role { font-size: 11px; color: var(--text-faint); margin-top: 2px; font-family: var(--font-mono); }
.modal-close {
  width: 30px; height: 30px; border-radius: 6px;
  border: 1px solid transparent; background: transparent;
  color: var(--text-muted); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.18s var(--ease-out-expo);
}
.modal-close:hover {
  background: var(--bg-hover); color: var(--text-primary);
  border-color: var(--border-light);
}

.modal-body {
  padding: 18px 20px 20px;
  overflow-y: auto;
  display: flex; flex-direction: column; gap: 18px;
}
.modal-section h3 {
  font-size: 12px; font-weight: 650; color: var(--text-muted);
  margin: 0 0 8px; text-transform: uppercase; letter-spacing: 0.04em;
}
.modal-desc { font-size: 13px; color: var(--text-secondary); line-height: 1.7; margin: 0; }

.detail-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag { font-size: 10.5px; line-height: 1.4; padding: 3px 8px; border-radius: 4px; }
.tag-cap { color: var(--accent); background: var(--accent-soft); border: 1px solid var(--accent-border); }
.tag-trigger { color: var(--text-muted); background: var(--bg-input); border: 1px solid var(--border-light); }
.tag-tool {
  color: var(--text-secondary); background: var(--bg-input);
  border: 1px solid var(--border-light); font-family: var(--font-mono);
}

.skill-detail-card {
  background: var(--bg-input);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 14px;
}
.skill-detail-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.skill-detail-name { font-size: 14px; font-weight: 650; color: var(--text-primary); }
.skill-detail-ver { font-size: 11px; font-family: var(--font-mono); color: var(--text-faint); }
.skill-detail-desc { font-size: 12.5px; color: var(--text-secondary); line-height: 1.6; margin: 0 0 12px; }
.skill-sub-section { margin-top: 10px; }
.sub-label { font-size: 10.5px; color: var(--text-muted); font-weight: 600; margin-bottom: 5px; }

.modal-enter-active, .modal-leave-active { transition: opacity 0.2s var(--ease-out-expo); }
.modal-enter-from, .modal-leave-to { opacity: 0; }

@media (max-width: 900px) {
  .agents-grid { grid-template-columns: 1fr; }
  .agents-view { padding: 24px 18px; }
}

@media (max-width: 768px) {
  .agents-view { padding: 18px 14px; }
  .agent-modal { max-height: 90vh; }
}
</style>
