<script setup>
import { ref, onMounted } from 'vue'

const skills = ref({})
const loading = ref(true)
const installName = ref('')
const installing = ref(false)
const installResult = ref('')

const icons = { planner: '🧠', coder: '⚡', reviewer: '🔍' }
const colors = { planner: 'planner', coder: 'coder', reviewer: 'reviewer' }

async function loadSkills() {
  loading.value = true
  try {
    const res = await fetch('http://localhost:8000/skills')
    const data = await res.json()
    skills.value = data.skills || {}
  } catch (e) {
    console.error('加载技能失败:', e)
  } finally {
    loading.value = false
  }
}

async function installSkill() {
  if (!installName.value.trim() || installing.value) return
  installing.value = true
  installResult.value = ''
  try {
    const res = await fetch('http://localhost:8000/skills/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: installName.value.trim() }),
    })
    const data = await res.json()
    if (data.success) {
      installResult.value = `技能 "${installName.value}" 安装成功！`
      installName.value = ''
      await loadSkills()
    } else {
      installResult.value = `安装失败: ${data.error || '未知错误'}`
    }
  } catch (e) {
    installResult.value = `网络错误: ${e.message}`
  } finally {
    installing.value = false
  }
}

onMounted(loadSkills)
</script>

<template>
  <div class="skills-panel">
    <h2>技能管理</h2>
    <p class="skills-subtitle">
      来自 <a href="https://skillhub.cn" target="_blank" style="color:var(--accent)">SkillHub</a> 的技能市场，
      为智能体配备专业能力。每个技能包含 SKILL.md 指导文件和 scripts/ 执行脚本。
    </p>

    <div v-if="loading" class="typing" style="padding:24px">
      <span></span><span></span><span></span>
    </div>

    <div v-else-if="Object.keys(skills).length === 0" style="color:var(--text-muted);padding:24px">
      暂无已安装技能。请在下方安装或在对话中让 AI 自动安装。
    </div>

    <div v-else class="skills-grid">
      <div v-for="(skill, key) in skills" :key="key" class="skill-card">
        <div class="skill-card-header">
          <div class="skill-card-icon" :class="colors[key] || 'coder'">
            {{ icons[key] || '📦' }}
          </div>
          <div>
            <div class="skill-card-name">{{ skill.name }}</div>
            <div class="skill-card-version">v{{ skill.version }} · {{ key }} Agent</div>
          </div>
        </div>
        <div class="skill-card-desc">{{ skill.description?.slice(0, 120) || '无描述' }}</div>
        <div class="skill-card-caps">
          <span v-for="cap in (skill.capabilities || []).slice(0, 6)" :key="cap" class="cap-tag">
            {{ cap }}
          </span>
        </div>
      </div>
    </div>

    <div class="install-section">
      <h3>安装新技能</h3>
      <div class="install-form">
        <input
          v-model="installName"
          placeholder="输入技能名称，如 code-reviewer"
          @keydown.enter="installSkill"
          :disabled="installing"
        />
        <button class="install-btn" @click="installSkill" :disabled="installing">
          {{ installing ? '安装中...' : '安装' }}
        </button>
      </div>
      <div v-if="installResult" class="install-result" :class="installResult.includes('成功') ? 'success' : 'error'">
        {{ installResult }}
      </div>
    </div>
  </div>
</template>
