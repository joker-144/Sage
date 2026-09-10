<script setup>
import { ref, watch, computed } from 'vue'

// P2-2：意图确认闸门对话框
// 高风险任务（复杂 / 低置信度）在执行前暂停，回显系统分析出的意图，
// 供用户「确认继续」或「修正角色/复杂度 + 补充说明重新分析」，形成纠错循环。
const props = defineProps({
  visible: Boolean,
  intent: { type: Object, default: null },
})
const emit = defineEmits(['confirm', 'correct', 'cancel'])

const ROLE_OPTIONS = [
  { value: 'supervisor', label: '主编' },
  { value: 'literature', label: '文献调研员' },
  { value: 'planner', label: '方法论专家' },
  { value: 'coder', label: '撰写员' },
  { value: 'reviewer', label: '审校核查员' },
  { value: 'debugger', label: '修订员' },
  { value: 'citation', label: '引用管理员' },
  { value: 'consolidator', label: '整理汇报员' },
  { value: 'general', label: '通用助手' },
]
const ROLE_LABELS = Object.fromEntries(ROLE_OPTIONS.map(r => [r.value, r.label]))
const CONF_LABELS = { high: '高', medium: '中', low: '低' }

// 本地可编辑控件状态（角色 / 复杂度 / 补充说明）
const selRole = ref('general')
const selComplexity = ref('simple')
const supplement = ref('')

// 每次意图变化（弹出）时，用后端回显的分析结果初始化本地控件
watch(() => props.intent, (val) => {
  if (val) {
    selRole.value = val.role || 'general'
    selComplexity.value = val.complexity || 'simple'
    supplement.value = ''
  }
}, { immediate: true })

const analyzedRoleLabel = computed(() => ROLE_LABELS[props.intent?.role] || props.intent?.role || '—')
const analyzedComplexityLabel = computed(() =>
  props.intent?.complexity === 'complex' ? '完整论文（多智能体协作）' : '简单任务（单智能体）')
const confidenceLabel = computed(() => CONF_LABELS[props.intent?.confidence] || props.intent?.confidence || '中')
const confidenceKey = computed(() => props.intent?.confidence || 'medium')
const echoMessage = computed(() => props.intent?.echoMessage || '')
const hasSupplement = computed(() => !!supplement.value.trim())

// 复杂度切到「完整论文」时角色锁定为主编（复杂任务恒由主编调度子智能体）
function onComplexityChange(c) {
  selComplexity.value = c
  if (c === 'complex') selRole.value = 'supervisor'
}

function onConfirm() {
  emit('confirm')
}
function onCorrect() {
  emit('correct', {
    complexity: selComplexity.value,
    role: selRole.value,
    supplement: supplement.value.trim(),
  })
}
function onCancel() {
  emit('cancel')
}
</script>

<template>
  <transition name="fade">
    <div v-if="visible" class="intent-overlay" @click.self="onCancel">
      <div class="intent-dialog">
        <div class="intent-title">确认任务意图</div>
        <p class="intent-hint">
          系统对本次请求的意图分析如下。高风险任务需你确认后才会开始执行，如判断有误可直接修正。
        </p>

        <div class="intent-card">
          <div class="intent-row">
            <span class="k">原始请求</span>
            <span class="v echo">{{ echoMessage }}</span>
          </div>
          <div class="intent-row">
            <span class="k">分析意图</span>
            <span class="v strong">{{ analyzedComplexityLabel }} → {{ analyzedRoleLabel }}</span>
          </div>
          <div class="intent-row">
            <span class="k">置信度</span>
            <span class="v"><span :class="['badge', 'conf-' + confidenceKey]">{{ confidenceLabel }}</span></span>
          </div>
          <div class="intent-row" v-if="intent?.reason">
            <span class="k">理由</span>
            <span class="v">{{ intent.reason }}</span>
          </div>
        </div>

        <div class="intent-edit">
          <div class="field">
            <label>任务类型</label>
            <div class="seg">
              <button type="button" :class="{ active: selComplexity === 'simple' }" @click="onComplexityChange('simple')">简单任务</button>
              <button type="button" :class="{ active: selComplexity === 'complex' }" @click="onComplexityChange('complex')">完整论文</button>
            </div>
          </div>
          <div class="field">
            <label>处理角色</label>
            <select v-model="selRole" :disabled="selComplexity === 'complex'">
              <option v-for="r in ROLE_OPTIONS" :key="r.value" :value="r.value">{{ r.label }}</option>
            </select>
          </div>
          <div class="field">
            <label>补充说明</label>
            <textarea
              v-model="supplement"
              rows="2"
              placeholder="可补充描述你的真实意图，系统将据此重新分析（可选）"
            ></textarea>
          </div>
        </div>

        <div class="intent-actions">
          <button type="button" class="btn-cancel" @click="onCancel">取消</button>
          <button type="button" class="btn-reanalyze" @click="onCorrect">
            {{ hasSupplement ? '补充并重新分析' : '按修正继续' }}
          </button>
          <button type="button" class="btn-confirm" @click="onConfirm">✓ 意图正确，继续</button>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.intent-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
}
.intent-dialog {
  background: var(--bg-elevated, #ffffff);
  border: 1px solid var(--border, rgba(15, 23, 42, 0.08));
  border-radius: var(--radius-lg);
  padding: 22px 24px;
  width: 460px;
  max-width: calc(100vw - 40px);
  max-height: calc(100vh - 80px);
  overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.intent-title {
  font-size: 15px; font-weight: 650;
  color: var(--text-primary, #0f172a);
  margin-bottom: 6px;
}
.intent-hint {
  font-size: 12px; color: var(--text-muted, #64748b);
  line-height: 1.5; margin: 0 0 14px;
}
.intent-card {
  background: var(--bg-surface, #f8fafc);
  border: 1px solid var(--border, rgba(15, 23, 42, 0.08));
  border-radius: var(--radius-md, 8px);
  padding: 12px 14px;
  margin-bottom: 14px;
  display: flex; flex-direction: column; gap: 8px;
}
.intent-row { display: flex; gap: 10px; font-size: 12.5px; line-height: 1.5; }
.intent-row .k {
  flex: 0 0 62px; color: var(--text-faint, #94a3b8);
}
.intent-row .v { flex: 1; color: var(--text-secondary, #475569); word-break: break-word; }
.intent-row .v.strong { color: var(--text-primary, #0f172a); font-weight: 600; }
.intent-row .v.echo {
  max-height: 60px; overflow-y: auto;
  font-family: var(--font-mono, monospace); font-size: 11.5px;
}
.badge {
  display: inline-block; padding: 1px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.conf-high { background: rgba(34, 197, 94, 0.14); color: #16a34a; }
.conf-medium { background: rgba(234, 179, 8, 0.16); color: #ca8a04; }
.conf-low { background: rgba(239, 68, 68, 0.14); color: #dc2626; }

.intent-edit { display: flex; flex-direction: column; gap: 12px; margin-bottom: 18px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field > label { font-size: 11.5px; color: var(--text-faint, #94a3b8); font-weight: 600; }
.seg { display: inline-flex; border: 1px solid var(--border, #cbd5e1); border-radius: 6px; overflow: hidden; }
.seg button {
  flex: 1; padding: 6px 12px; border: none; cursor: pointer;
  background: var(--bg-surface, #ffffff); color: var(--text-secondary, #475569);
  font-family: var(--font-sans); font-size: 12px; font-weight: 500;
  transition: all 0.15s var(--ease-out-expo);
}
.seg button.active { background: var(--accent, #4f46e5); color: #fff; }
.field select, .field textarea {
  font-family: var(--font-sans); font-size: 12.5px;
  color: var(--text-primary, #0f172a);
  background: var(--bg-surface, #ffffff);
  border: 1px solid var(--border, #cbd5e1);
  border-radius: 6px; padding: 6px 9px; resize: vertical;
}
.field select:disabled { opacity: 0.55; cursor: not-allowed; }

.intent-actions { display: flex; justify-content: flex-end; gap: 8px; }
.btn-cancel, .btn-reanalyze, .btn-confirm {
  padding: 7px 14px; border-radius: 6px;
  font-family: var(--font-sans); font-size: 12px; font-weight: 500;
  cursor: pointer; border: 1px solid var(--border, #333);
  transition: all 0.15s var(--ease-out-expo);
}
.btn-cancel { background: var(--bg-surface, #ffffff); color: var(--text-secondary, #475569); }
.btn-cancel:hover { background: var(--bg-hover, #f1f5f9); }
.btn-reanalyze { background: var(--bg-surface, #ffffff); color: var(--accent, #4f46e5); border-color: var(--accent, #4f46e5); }
.btn-reanalyze:hover { background: rgba(79, 70, 229, 0.08); }
.btn-confirm { background: var(--accent, #4f46e5); color: #fff; border-color: var(--accent, #4f46e5); }
.btn-confirm:hover { opacity: 0.88; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.15s var(--ease-out-expo); }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
