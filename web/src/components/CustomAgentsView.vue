<script setup>
import { ref, onMounted } from 'vue'

const customAgents = ref([])
const loading = ref(true)
const selectedAgent = ref(null)
const deleteConfirm = ref({ show: false, role: null, name: '' })
const deleting = ref(false)

async function loadData() {
  loading.value = true
  try {
    const res = await fetch('/api/agents')
    if (res.ok) {
      const data = await res.json()
      // 只显示自定义智能体
      customAgents.value = (data.agents || []).filter(a => a.is_custom)
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

function confirmDelete(agent) {
  deleteConfirm.value = { show: true, role: agent.role, name: agent.name }
}

function cancelDelete() {
  deleteConfirm.value = { show: false, role: null, name: '' }
}

async function doDelete() {
  if (!deleteConfirm.value.role) return
  deleting.value = true
  try {
    const res = await fetch(`/api/agents/${deleteConfirm.value.role}`, { method: 'DELETE' })
    if (res.ok) {
      // 删除成功，刷新列表
      await loadData()
      cancelDelete()
    } else {
      const err = await res.json().catch(() => ({}))
      alert(err.detail || err.error || '删除失败')
    }
  } catch (e) {
    alert('删除失败: ' + e.message)
  } finally {
    deleting.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="agents-view">
    <header class="view-header">
      <h1>自定义智能体</h1>
      <p class="subtitle">由智能体自主创建并经审核通过的自定义智能体，可在对话中直接使用</p>
    </header>

    <div v-if="loading" class="loading-state">加载中...</div>

    <template v-else>
      <div v-if="customAgents.length === 0" class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" style="opacity: 0.3; margin-bottom: 12px;">
          <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
          <path d="M2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
        </svg>
        <p>暂无自定义智能体</p>
        <p class="empty-hint">在对话中让智能体调用 create_agent 工具创建新智能体</p>
      </div>

      <div v-else class="agents-grid">
        <div
          v-for="(agent, index) in customAgents"
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
            <div class="custom-badge" title="自定义智能体">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none">
                <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
              </svg>
              自定义
            </div>
          </div>

          <p class="agent-desc">{{ agent.description || '暂无描述' }}</p>

          <div v-if="agent.capabilities && agent.capabilities.length" class="agent-caps">
            <span v-for="(cap, j) in agent.capabilities.slice(0, 3)" :key="j" class="cap-tag">{{ cap }}</span>
            <span v-if="agent.capabilities.length > 3" class="cap-more">+{{ agent.capabilities.length - 3 }}</span>
          </div>

          <div class="card-footer">
            <div class="card-hint">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                <path d="M5 12h14M12 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
              点击查看详情
            </div>
            <button
              class="delete-btn"
              title="删除此智能体"
              @click.stop="confirmDelete(agent)"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
                <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
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
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- 删除确认弹窗 -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="deleteConfirm.show" class="confirm-overlay" @click.self="cancelDelete">
          <div class="confirm-dialog" role="dialog" aria-modal="true">
            <div class="confirm-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </div>
            <h3 class="confirm-title">删除自定义智能体</h3>
            <p class="confirm-message">
              确定要删除 "{{ deleteConfirm.name }}" 吗？<br>
              <span class="confirm-warning">此操作不可恢复，该智能体将从系统中永久移除。</span>
            </p>
            <div class="confirm-actions">
              <button class="btn-cancel" @click="cancelDelete" :disabled="deleting">取消</button>
              <button class="btn-delete" @click="doDelete" :disabled="deleting">
                {{ deleting ? '删除中...' : '确认删除' }}
              </button>
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
.empty-hint { font-size: 11.5px; color: var(--text-faint); margin-top: 6px; }

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
.agent-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.18);
  border-color: var(--accent);
}

.agent-header { display: flex; align-items: center; gap: 12px; }
.agent-avatar {
  width: 38px; height: 38px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  color: white; font-size: 13px; font-weight: 700; flex-shrink: 0;
}
.agent-meta { flex: 1; min-width: 0; }
.agent-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.agent-role { font-size: 11px; color: var(--text-faint); font-family: var(--font-mono); margin-top: 2px; }

.custom-badge {
  display: flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 10px;
  background: rgba(139, 92, 246, 0.12); color: #a78bfa;
  font-size: 10px; font-weight: 600;
}

.agent-desc {
  font-size: 12.5px; color: var(--text-muted); line-height: 1.55;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.agent-caps { display: flex; flex-wrap: wrap; gap: 5px; }
.cap-tag {
  font-size: 10.5px; padding: 2px 8px; border-radius: 4px;
  background: var(--bg-hover); color: var(--text-secondary);
}
.cap-more { font-size: 10.5px; padding: 2px 6px; color: var(--text-faint); align-self: center; }

.card-footer {
  display: flex; align-items: center; justify-content: space-between;
  margin-top: 2px;
}
.card-hint {
  display: flex; align-items: center; gap: 4px;
  font-size: 11px; color: var(--text-faint);
}
.delete-btn {
  width: 28px; height: 28px; border: none; border-radius: 6px;
  background: transparent; color: var(--text-faint); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s var(--ease-out-expo);
}
.delete-btn:hover { color: var(--error); background: rgba(239, 68, 68, 0.1); }

/* ── 详情弹窗 ── */
.agent-modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.55);
  display: flex; align-items: center; justify-content: center; z-index: 200;
  backdrop-filter: blur(4px);
}
.agent-modal {
  width: 560px; max-height: 80vh; overflow-y: auto;
  background: var(--bg-elevated); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 24px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.4);
}
.modal-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.modal-title-row { display: flex; align-items: center; gap: 12px; }
.modal-avatar { width: 44px; height: 44px; font-size: 15px; }
.modal-name { font-size: 17px; font-weight: 650; color: var(--text-primary); }
.modal-role { font-size: 12px; color: var(--text-faint); font-family: var(--font-mono); }
.modal-close {
  width: 32px; height: 32px; border: none; border-radius: 8px;
  background: transparent; color: var(--text-muted); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s var(--ease-out-expo);
}
.modal-close:hover { background: var(--bg-hover); color: var(--text-primary); }

.modal-body { display: flex; flex-direction: column; gap: 20px; }
.modal-section h3 { font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px; }
.modal-desc { font-size: 13px; color: var(--text-muted); line-height: 1.6; }
.detail-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag { font-size: 11px; padding: 3px 9px; border-radius: 4px; }
.tag-cap { background: var(--accent-soft); color: var(--accent); }

/* ── 删除确认弹窗 ── */
.confirm-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.55);
  display: flex; align-items: center; justify-content: center; z-index: 300;
  backdrop-filter: blur(4px);
}
.confirm-dialog {
  width: 400px; background: var(--bg-elevated);
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: 24px; text-align: center;
  box-shadow: 0 20px 60px rgba(0,0,0,0.4);
}
.confirm-icon {
  width: 48px; height: 48px; margin: 0 auto 12px;
  border-radius: 50%; background: rgba(239, 68, 68, 0.12); color: var(--error);
  display: flex; align-items: center; justify-content: center;
}
.confirm-title { font-size: 16px; font-weight: 650; color: var(--text-primary); margin-bottom: 8px; }
.confirm-message { font-size: 13px; color: var(--text-muted); line-height: 1.55; margin-bottom: 20px; }
.confirm-warning { color: var(--error); font-size: 12px; }
.confirm-actions { display: flex; gap: 10px; justify-content: center; }
.btn-cancel, .btn-delete {
  padding: 8px 18px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 600;
  cursor: pointer; border: none; transition: all 0.15s var(--ease-out-expo);
}
.btn-cancel { background: var(--bg-hover); color: var(--text-secondary); }
.btn-cancel:hover { background: var(--border); }
.btn-delete { background: var(--error); color: white; }
.btn-delete:hover { background: #dc2626; }
.btn-cancel:disabled, .btn-delete:disabled { opacity: 0.6; cursor: not-allowed; }

/* ── 过渡动画 ── */
.modal-enter-active, .modal-leave-active { transition: opacity 0.2s var(--ease-out-expo); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s var(--ease-out-expo); }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
