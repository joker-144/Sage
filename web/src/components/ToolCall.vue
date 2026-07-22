<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  tool: { type: Object, required: true },
})

const expanded = ref(false)
const copied = ref(false)

function getIcon(name) {
  const map = {
    read_file: 'R', write_file: 'W', edit_file: 'E', list_dir: 'D',
    search_code: 'S', run_command: 'C', git_status: 'G', git_diff: 'Df',
    git_log: 'L', git_commit: 'Cm', git_branch: 'B',
    git_add: '+', git_create_branch: 'Br', error: '!',
    web_search: 'WS', web_search_pro: 'WS', web_fetch: 'WF',
    list_skills: 'SL', install_skill: 'SI', search_remote_skills: 'SR',
  }
  return map[name] || '?'
}

function getColor(name) {
  if (name === 'error') return 'var(--error)'
  if (name.startsWith('git')) return '#8b5cf6'
  if (name.includes('search')) return '#0ea5e9'
  if (name.includes('write') || name.includes('edit')) return '#f97316'
  if (name.includes('read') || name.includes('list')) return '#22c55e'
  return 'var(--accent)'
}

function getCategory(tool) {
  if (tool.isAgent) return '智能体'
  const name = tool.name
  if (name === 'error') return '错误'
  if (name.startsWith('git')) return 'Git'
  if (name.includes('search')) return '搜索'
  if (name.includes('write') || name.includes('edit')) return '编辑'
  if (name.includes('read') || name.includes('list')) return '读取'
  if (name === 'run_command') return '命令'
  return '工具'
}

// 格式化 token 显示
const tokenText = computed(() => {
  const t = props.tool.tokens
  if (!t || (!t.total && !t.prompt && !t.completion)) return ''
  const total = t.total || ((t.prompt || 0) + (t.completion || 0))
  return `${total}`
})

function copyResult() {
  navigator.clipboard.writeText(props.tool.result || '')
  copied.value = true
  setTimeout(() => copied.value = false, 1500)
}
</script>

<template>
  <!-- 智能体调用：简洁展示，不展开工具详情 -->
  <div v-if="tool.isAgent" class="tool-call agent" @click="expanded = !expanded" :class="{ expanded }">
    <div class="tool-header">
      <div class="tool-badge agent-badge">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="8" r="4" stroke="currentColor" stroke-width="2"/>
          <path d="M4 20c0-4 4-7 8-7s8 3 8 7" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </div>
      <span class="tool-name agent-name">{{ tool.agentName || tool.name }}</span>
      <span class="tool-category agent-cat">智能体</span>
      <span class="tool-desc">加载技能</span>
      <div class="tool-right">
        <span v-if="tokenText" class="tool-tokens" title="该轮 LLM 调用消耗的 Token">{{ tokenText }} tok</span>
        <span v-if="!tool.done" class="tool-spinner"></span>
        <span v-else class="tool-done">&#10003;</span>
        <svg class="chevron" :class="{ rotated: expanded }" width="12" height="12" viewBox="0 0 24 24" fill="none">
          <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </div>
    </div>
    <transition name="fade">
      <div v-if="expanded" class="tool-body">
        <div v-if="tool.result" class="tool-section">
          <div class="section-header">
            <span class="section-label">技能信息</span>
          </div>
          <pre class="code-block result">{{ tool.result }}</pre>
        </div>
      </div>
    </transition>
  </div>

  <!-- 普通工具调用 -->
  <div v-else class="tool-call" :class="{ error: tool.isError, expanded }">
    <div class="tool-header" @click="expanded = !expanded">
      <div class="tool-badge" :style="{ background: getColor(tool.name) }">
        {{ getIcon(tool.name) }}
      </div>
      <span class="tool-name">{{ tool.name }}</span>
      <span class="tool-category">{{ getCategory(tool) }}</span>
      <span v-if="tool.content" class="tool-desc">{{ tool.content }}</span>
      <div class="tool-right">
        <span v-if="tokenText" class="tool-tokens" title="该轮 LLM 调用消耗的 Token">{{ tokenText }} tok</span>
        <span v-if="!tool.done" class="tool-spinner"></span>
        <span v-else class="tool-done">&#10003;</span>
        <svg class="chevron" :class="{ rotated: expanded }" width="12" height="12" viewBox="0 0 24 24" fill="none">
          <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </div>
    </div>

    <transition name="fade">
      <div v-if="expanded" class="tool-body">
        <div v-if="Object.keys(tool.args).length > 0" class="tool-section">
          <div class="section-label">参数</div>
          <pre class="code-block">{{ JSON.stringify(tool.args, null, 2) }}</pre>
        </div>
        <div v-if="tool.result" class="tool-section">
          <div class="section-header">
            <span class="section-label">结果</span>
            <button class="copy-btn" :class="{ copied }" @click.stop="copyResult">
              {{ copied ? '已复制' : '复制' }}
            </button>
          </div>
          <pre class="code-block result">{{ tool.result }}</pre>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.tool-call {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  margin: 4px 0 4px 26px;
  overflow: hidden;
  transition: all 0.16s var(--ease-out-expo);
  animation: slideInRight 0.22s var(--ease-out-expo);
}
.tool-call:hover { border-color: var(--border-strong); }
.tool-call.expanded {
  border-color: var(--accent-border);
  box-shadow: 0 0 0 1px var(--accent-soft);
}
.tool-call.error { border-left: 2px solid var(--error); }

/* 智能体样式 */
.tool-call.agent {
  border-left: 2px solid var(--agent);
  background: linear-gradient(135deg, var(--agent-soft), var(--bg-card));
}
.tool-call.agent:hover { border-color: var(--agent); }
.tool-call.agent.expanded {
  border-color: var(--agent);
  box-shadow: 0 0 0 1px rgba(249, 115, 22, 0.18);
}

.tool-header {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 9px; cursor: pointer; user-select: none;
}
.tool-header:hover { background: var(--bg-hover); }
.tool-call.agent .tool-header:hover { background: var(--agent-soft); }

.tool-badge {
  width: 19px; height: 19px; border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  color: white; font-size: 8.5px; font-weight: 700; font-family: var(--font-mono);
  flex-shrink: 0;
}
.agent-badge {
  width: 22px; height: 22px; border-radius: 50%;
  background: var(--agent);
}

.tool-name {
  font-size: 10.5px; font-weight: 600; color: var(--text-primary);
  font-family: var(--font-mono); flex-shrink: 0;
  letter-spacing: 0.01em;
}
.tool-call.error .tool-name { color: var(--error); }
.agent-name { color: var(--agent); }

.tool-desc {
  font-size: 10px; color: var(--text-muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
}

.tool-category {
  font-size: 8.5px; font-weight: 500;
  color: var(--text-faint);
  background: var(--bg-hover);
  padding: 1px 5px;
  border-radius: 3px;
  flex-shrink: 0;
  letter-spacing: 0.03em;
}
.agent-cat {
  color: var(--agent);
  background: var(--agent-soft);
}

.tool-right { display: flex; align-items: center; gap: 5px; flex-shrink: 0; }

.tool-tokens {
  font-size: 9px; font-weight: 600;
  font-family: var(--font-mono);
  color: var(--text-muted);
  background: var(--bg-hover);
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid var(--border-light);
  flex-shrink: 0;
}

.tool-spinner {
  width: 10px; height: 10px; border: 1.5px solid var(--border);
  border-top-color: var(--accent); border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.tool-done { color: var(--success); font-size: 10.5px; }

.chevron { color: var(--text-faint); transition: transform 0.16s var(--ease-out-expo); }
.chevron.rotated { transform: rotate(180deg); }

.tool-body { padding: 0 9px 9px; }

.tool-section { margin-top: 5px; }

.section-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 2px;
}

.section-label {
  font-size: 9px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--text-faint);
}

.copy-btn {
  font-size: 9px; color: var(--text-muted);
  background: var(--bg-hover); border: 1px solid var(--border);
  padding: 1px 6px; border-radius: 2.5px; cursor: pointer;
  transition: all 0.15s var(--ease-out-expo); font-family: var(--font-sans);
}
.copy-btn:hover { color: var(--text-primary); border-color: var(--border-strong); }
.copy-btn.copied { color: var(--success); border-color: var(--success); }

.code-block {
  background: var(--bg-code); border: 1px solid var(--border-light);
  border-radius: var(--radius-sm); padding: 7px 9px;
  font-family: var(--font-mono); font-size: 10px; color: var(--text-secondary);
  overflow-x: auto; max-height: 240px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-all;
  line-height: 1.6;
}
</style>
