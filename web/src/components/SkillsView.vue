<script setup>
import { ref, computed, onMounted } from 'vue'

const skills = ref([])
const summary = ref('')
const loading = ref(true)
const selectedSkill = ref(null)

// 后端 SkillLoader 已排除智能体专属技能（planner/coder/reviewer），
// 前端直接展示所有返回的技能即可
const filteredSkills = computed(() => skills.value)

const displaySummary = computed(() => {
  if (filteredSkills.value.length === 0) return '当前系统未安装任何技能'
  const names = filteredSkills.value.map(s => s.name || s.agent_name).join(', ')
  return `当前系统已安装 ${filteredSkills.value.length} 个技能: ${names}`
})

async function loadData() {
  loading.value = true
  try {
    const res = await fetch('/api/skills')
    if (res.ok) {
      const data = await res.json()
      skills.value = data.skills || []
      summary.value = data.summary || ''
    }
  } catch { /* 使用默认值 */ }
  finally { loading.value = false }
}

function openDetail(skill) {
  selectedSkill.value = skill
}

function closeDetail() {
  selectedSkill.value = null
}

onMounted(loadData)
</script>

<template>
  <div class="skills-view">
    <header class="view-header">
      <h1>已安装技能</h1>
      <p class="subtitle">{{ displaySummary }}</p>
    </header>

    <div v-if="loading" class="loading-state">加载中...</div>

    <template v-else>
      <div v-if="filteredSkills.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
            <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
            <path d="M2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
          </svg>
        </div>
        <p>暂无已安装的技能</p>
        <p class="empty-hint">可以通过对话让 Agent 安装技能，或运行 <code>sage skill install &lt;name&gt;</code></p>
      </div>

      <div v-else class="skills-grid">
        <div
          v-for="skill in filteredSkills"
          :key="skill.agent_name"
          class="skill-card"
          tabindex="0"
          @click="openDetail(skill)"
          @keydown.enter="openDetail(skill)"
        >
          <div class="skill-header">
            <div class="skill-icon">{{ skill.agent_name.slice(0, 2).toUpperCase() }}</div>
            <div class="skill-meta">
              <div class="skill-name-row">
                <span class="skill-name">{{ skill.name || skill.agent_name }}</span>
                <span class="skill-version">v{{ skill.version }}</span>
              </div>
            </div>
          </div>

          <p class="skill-desc">
            {{ skill.description || '暂无介绍' }}
          </p>

          <div v-if="skill.capabilities && skill.capabilities.length" class="skill-tags">
            <span v-for="(cap, j) in skill.capabilities.slice(0, 4)" :key="j" class="tag tag-cap">
              {{ cap }}
            </span>
            <span v-if="skill.capabilities.length > 4" class="tag tag-more">+{{ skill.capabilities.length - 4 }}</span>
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

    <!-- 技能详情弹窗 -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="selectedSkill" class="skill-modal-overlay" @click.self="closeDetail">
          <div class="skill-modal" role="dialog" aria-modal="true">
            <div class="modal-header">
              <div class="modal-title-row">
                <div class="skill-icon modal-icon">{{ selectedSkill.agent_name.slice(0, 2).toUpperCase() }}</div>
                <div>
                  <div class="modal-name">{{ selectedSkill.name || selectedSkill.agent_name }}</div>
                  <div class="modal-version">v{{ selectedSkill.version }}</div>
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
                <h3>技能介绍</h3>
                <p class="modal-desc">{{ selectedSkill.description || '暂无介绍' }}</p>
              </section>

              <section v-if="selectedSkill.capabilities && selectedSkill.capabilities.length" class="modal-section">
                <h3>核心能力</h3>
                <div class="detail-tags">
                  <span v-for="(cap, i) in selectedSkill.capabilities" :key="i" class="tag tag-cap">{{ cap }}</span>
                </div>
              </section>

              <section v-if="selectedSkill.trigger_conditions && selectedSkill.trigger_conditions.length" class="modal-section">
                <h3>调用时机</h3>
                <div class="detail-tags">
                  <span v-for="(cond, i) in selectedSkill.trigger_conditions" :key="i" class="tag tag-trigger">{{ cond }}</span>
                </div>
              </section>

              <section v-if="selectedSkill.tools && selectedSkill.tools.length" class="modal-section">
                <h3>可用工具</h3>
                <div class="detail-tags">
                  <span v-for="(tool, i) in selectedSkill.tools" :key="i" class="tag tag-tool">{{ tool }}</span>
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
.skills-view { flex: 1; overflow-y: auto; padding: 30px; max-width: 1100px; margin: 0 auto; }

.view-header { margin-bottom: 24px; }
.view-header h1 { font-size: 22px; font-weight: 650; color: var(--text-primary); letter-spacing: -0.02em; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }

.loading-state { text-align: center; padding: 40px; color: var(--text-faint); font-size: 13px; }

.empty-state {
  text-align: center; padding: 60px 20px; color: var(--text-muted);
}
.empty-icon { color: var(--text-faint); margin-bottom: 12px; }
.empty-state p { margin: 0 0 6px; font-size: 13px; }
.empty-hint { font-size: 11px !important; color: var(--text-faint); }
.empty-state code {
  font-family: var(--font-mono); font-size: 11px;
  background: var(--bg-card); padding: 2px 6px; border-radius: 3px;
}

.skills-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.skill-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px;
  transition: transform 0.18s var(--ease-out-expo), box-shadow 0.18s var(--ease-out-expo), border-color 0.18s var(--ease-out-expo);
  display: flex; flex-direction: column; gap: 10px;
  cursor: pointer;
}
.skill-card:hover, .skill-card:focus {
  transform: translateY(-2px);
  border-color: var(--accent-border);
  box-shadow: var(--shadow-md);
  outline: none;
}
.skill-card:focus-visible {
  box-shadow: var(--shadow-md), var(--shadow-glow);
}

.skill-header { display: flex; align-items: center; gap: 10px; }
.skill-icon {
  width: 32px; height: 32px; border-radius: 8px;
  background: linear-gradient(135deg, var(--accent), var(--accent-secondary));
  color: white; display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; flex-shrink: 0;
  box-shadow: var(--shadow-xs);
}
.skill-meta { min-width: 0; }
.skill-name-row {
  display: flex; align-items: center; gap: 8px;
}
.skill-name { font-size: 14px; font-weight: 650; color: var(--text-primary); }
.skill-version {
  font-size: 10px; font-family: var(--font-mono); color: var(--text-faint);
  background: var(--bg-input); padding: 1px 6px; border-radius: 3px;
  border: 1px solid var(--border-light);
}

.skill-desc {
  font-size: 12px; color: var(--text-secondary); line-height: 1.6;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.skill-tags, .detail-tags {
  display: flex; flex-wrap: wrap; gap: 6px;
}
.tag {
  font-size: 10.5px; line-height: 1.4;
  padding: 3px 8px; border-radius: 4px;
}
.tag-cap {
  color: var(--accent);
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
}
.tag-more {
  color: var(--text-muted);
  background: var(--bg-input);
  border: 1px solid var(--border-light);
}
.tag-trigger {
  color: var(--text-muted);
  background: var(--bg-input);
  border: 1px solid var(--border-light);
}
.tag-tool {
  color: var(--text-secondary);
  background: var(--bg-input);
  border: 1px solid var(--border-light);
  font-family: var(--font-mono);
}

.card-hint {
  display: flex; align-items: center; gap: 5px;
  font-size: 11px; color: var(--text-faint);
  margin-top: 2px;
  transition: color 0.18s var(--ease-out-expo);
}
.skill-card:hover .card-hint {
  color: var(--accent);
}

/* 详情弹窗 */
.skill-modal-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(6px);
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
}
.skill-modal {
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
.modal-icon { width: 40px; height: 40px; font-size: 13px; }
.modal-name { font-size: 16px; font-weight: 650; color: var(--text-primary); }
.modal-version { font-size: 11px; color: var(--text-faint); margin-top: 2px; font-family: var(--font-mono); }
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
.modal-desc {
  font-size: 13px; color: var(--text-secondary); line-height: 1.7; margin: 0;
}

.modal-enter-active, .modal-leave-active { transition: opacity 0.2s var(--ease-out-expo); }
.modal-enter-from, .modal-leave-to { opacity: 0; }

@media (max-width: 900px) {
  .skills-grid { grid-template-columns: 1fr; }
  .skills-view { padding: 24px 18px; }
}

@media (max-width: 768px) {
  .skills-view { padding: 18px 14px; }
  .skill-modal { max-height: 90vh; }
}
</style>
