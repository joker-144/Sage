<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  contextUsage: { type: Object, default: null },
})
const emit = defineEmits(['send', 'stop', 'open-settings', 'model-changed'])

const text = ref('')
const textareaRef = ref(null)
const focused = ref(false)

// ── 模型选择器 ──
const currentModel = ref('')
const currentProvider = ref('deepseek')
const models = ref([])
const showModelPicker = ref(false)
const customModelInput = ref('')
const pickerRef = ref(null)

function loadSettings() {
  try {
    const stored = localStorage.getItem('sage-settings')
    if (stored) {
      const parsed = JSON.parse(stored)
      currentModel.value = parsed.model || ''
      currentProvider.value = parsed.provider || 'deepseek'
    }
  } catch { /* ignore */ }
}

function loadModels() {
  try {
    const cached = localStorage.getItem('sage-models-cache')
    if (cached) {
      const parsed = JSON.parse(cached)
      models.value = parsed[currentProvider.value] || []
    }
  } catch { /* ignore */ }
}

function selectModel(modelId) {
  if (!modelId) return
  try {
    const stored = localStorage.getItem('sage-settings')
    if (stored) {
      const parsed = JSON.parse(stored)
      parsed.model = modelId
      localStorage.setItem('sage-settings', JSON.stringify(parsed))
    }
  } catch { /* ignore */ }
  currentModel.value = modelId
  showModelPicker.value = false
  // 先同步模型到后端（写 .env 并清空 Agent 缓存），再通知上层触发上下文重查/压缩
  syncModelToBackend(modelId).finally(() => emit('model-changed', modelId))
}

// 将选中的模型同步到后端（写 .env 并重载配置），
// 复用现有 /api/user-settings 接口，只更新 model 字段，不改变其它配置。
async function syncModelToBackend(modelId) {
  try {
    const resp = await fetch('/api/user-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelId }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  } catch (e) {
    console.error('[ChatInput] 同步模型到后端失败:', e)
  }
}

function useCustomModel() {
  const val = customModelInput.value.trim()
  if (val) {
    selectModel(val)
    customModelInput.value = ''
  }
}

function toggleModelPicker() {
  if (showModelPicker.value) {
    showModelPicker.value = false
  } else {
    loadSettings()
    loadModels()
    showModelPicker.value = true
  }
}

function handleModelKeydown(e) {
  if (e.key === 'Enter') { e.preventDefault(); useCustomModel() }
  if (e.key === 'Escape') { showModelPicker.value = false }
}

// 点击外部关闭
function handleClickOutside(e) {
  if (pickerRef.value && !pickerRef.value.contains(e.target)) {
    showModelPicker.value = false
  }
}

onMounted(() => {
  loadSettings()
  loadModels()
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})

// ── 原有逻辑 ──
function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  if (e.key === 'Escape') { e.target.blur() }
}

function submit() {
  const value = text.value.trim()
  if (!value || props.disabled) return
  emit('send', value)
  text.value = ''
  nextTick(autoResize)
}

function goSettings() {
  showModelPicker.value = false
  emit('open-settings')
}

// 截断显示
function shortLabel(modelId) {
  if (!modelId) return '未选择'
  return modelId.length > 28 ? modelId.slice(0, 26) + '…' : modelId
}

// ── 上下文用量环形指示器 ──
const showCtxPop = ref(false)
const RING_R = 7
const RING_CIRC = 2 * Math.PI * RING_R

// 使用率：以「完整上下文窗口」为满环（后端同样按完整窗口下发 percent）
const ctxPercent = computed(() => {
  const cu = props.contextUsage
  if (!cu) return 0
  if (cu.maxTokens) return Math.min((cu.currentTokens || 0) * 100 / cu.maxTokens, 100)
  return Math.min(cu.percent ?? 0, 100)
})
// 压缩阈值在满环上的位置（百分比），用于绘制刻度线
const triggerPercent = computed(() => {
  const cu = props.contextUsage
  if (!cu || !cu.maxTokens) return 0
  return Math.min((cu.triggerTokens || 0) * 100 / cu.maxTokens, 100)
})
// 阈值刻度线旋转角度（以环形顶部为 0°，顺时针）
const triggerAngle = computed(() => triggerPercent.value * 3.6)
// 是否已达压缩阈值（驱动浮窗提示文案）
const reachedTrigger = computed(() => {
  const cu = props.contextUsage
  return !!(cu && cu.triggerTokens && (cu.currentTokens || 0) >= cu.triggerTokens)
})
// 环形进度：dasharray = "已走弧长 周长"
const ctxDash = computed(() => {
  const used = (RING_CIRC * ctxPercent.value / 100).toFixed(2)
  return `${used} ${RING_CIRC.toFixed(2)}`
})
// 颜色分级：<70% 正常 / 70-95% 偏高 / ≥95% 临近压缩
const ctxLevel = computed(() => {
  const p = ctxPercent.value
  if (p >= 95) return 'danger'
  if (p >= 70) return 'warn'
  return 'ok'
})
// token 数值格式化（12,300 → 12.3k）
function fmtTokens(n) {
  if (n == null) return '0'
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k'
  return String(n)
}
</script>

<template>
  <div class="input-area" :class="{ focused }">
    <div class="input-shell">
      <div class="input-prefix">
        <span class="prefix-label">></span>
      </div>

      <!-- 模型选择器 -->
      <div class="model-selector" ref="pickerRef">
        <button class="model-chip" @click="toggleModelPicker" title="切换模型">
          <span class="model-chip-dot"></span>
          <span class="model-chip-text">{{ shortLabel(currentModel) }}</span>
          <svg class="model-chip-chevron" :class="{ open: showModelPicker }" width="10" height="10" viewBox="0 0 24 24" fill="none">
            <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          </svg>
        </button>

        <transition name="picker-fade">
          <div v-if="showModelPicker" class="model-picker">
            <div class="picker-header">当前供应商模型</div>
            <div v-if="models.length" class="picker-list">
              <button
                v-for="m in models"
                :key="m.value"
                class="picker-option"
                :class="{ active: m.value === currentModel }"
                @click="selectModel(m.value)"
              >
                <span class="option-check">{{ m.value === currentModel ? '✓' : '' }}</span>
                <span class="option-label">{{ m.label }}</span>
              </button>
            </div>
            <div v-else class="picker-empty">暂无模型缓存，请先在设置中配置</div>
            <div class="picker-footer">
              <div class="picker-custom-row">
                <input
                  v-model="customModelInput"
                  class="picker-custom-input"
                  placeholder="自定义模型 ID…"
                  @keydown="handleModelKeydown"
                />
                <button class="picker-custom-btn" @click="useCustomModel">确定</button>
              </div>
              <button class="picker-settings-link" @click="goSettings">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2"/>
                  <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z" stroke="currentColor" stroke-width="2"/>
                </svg>
                模型设置
              </button>
            </div>
          </div>
        </transition>
      </div>

      <!-- 上下文用量环形指示器（模型选择后） -->
      <div
        v-if="contextUsage"
        class="ctx-ring-wrap"
        @mouseenter="showCtxPop = true"
        @mouseleave="showCtxPop = false"
      >
        <svg
          class="ctx-ring"
          :class="{ 'just-compressed': contextUsage.justCompressed }"
          width="18" height="18" viewBox="0 0 18 18"
        >
          <circle class="ctx-ring-bg" cx="9" cy="9" :r="RING_R" />
          <circle
            class="ctx-ring-fg" :class="ctxLevel" cx="9" cy="9" :r="RING_R"
            :stroke-dasharray="ctxDash"
          />
          <line v-if="triggerPercent > 0" class="ctx-ring-thresh" x1="9" y1="1.2" x2="9" y2="2.8" :transform="`rotate(${triggerAngle} 9 9)`" />
        </svg>

        <transition name="picker-fade">
          <div v-if="showCtxPop" class="ctx-popover">
            <div class="ctx-pop-header">
              <span>上下文使用情况</span>
              <span v-if="contextUsage.role" class="ctx-pop-role">{{ contextUsage.role }}</span>
            </div>

            <div class="ctx-pop-bar">
              <div class="ctx-pop-bar-fill" :class="ctxLevel" :style="{ width: ctxPercent + '%' }"></div>
              <span v-if="triggerPercent > 0" class="ctx-pop-bar-thresh" :style="{ left: triggerPercent + '%' }"></span>
            </div>
            <div class="ctx-pop-percent" :class="ctxLevel">{{ ctxPercent.toFixed(1) }}%</div>

            <div class="ctx-pop-row">
              <span class="ctx-pop-label">当前占用</span>
              <span class="ctx-pop-value">{{ fmtTokens(contextUsage.currentTokens) }} / {{ fmtTokens(contextUsage.maxTokens) }} tokens</span>
            </div>
            <div class="ctx-pop-row">
              <span class="ctx-pop-label">压缩阈值</span>
              <span class="ctx-pop-value">{{ fmtTokens(contextUsage.triggerTokens) }} tokens（{{ Math.round((contextUsage.triggerTokens || 0) * 100 / (contextUsage.maxTokens || 1)) }}% 处触发）</span>
            </div>

            <div class="ctx-pop-divider"></div>

            <div class="ctx-pop-note">
              <template v-if="!reachedTrigger">
                达到阈值后将<b>自动压缩</b>：早期对话摘要化，最近对话保持完整，对话不会中断
              </template>
              <template v-else>
                已达压缩阈值，本轮将触发自动压缩
              </template>
            </div>

            <div class="ctx-pop-stats" v-if="contextUsage.compressedRounds > 0">
              <span>已压缩 {{ contextUsage.compressedRounds }} 轮</span>
              <span class="ctx-pop-dot">·</span>
              <span>累计节省 {{ fmtTokens(contextUsage.savedTokens) }} tokens</span>
            </div>
            <div class="ctx-pop-stats muted" v-else>
              尚未触发压缩
            </div>

            <div class="ctx-pop-flash" v-if="contextUsage.justCompressed">
              刚完成一轮压缩，上下文已回落
            </div>
          </div>
        </transition>
      </div>

      <textarea
        ref="textareaRef" v-model="text" :disabled="disabled"
        placeholder="输入指令或描述需求… (Enter 发送 · Shift+Enter 换行 · Esc 退出)" rows="1"
        @input="autoResize" @keydown="handleKeydown"
        @focus="focused = true" @blur="focused = false"
      ></textarea>
      <div class="input-actions">
        <span class="char-count" v-if="text">{{ text.length }}</span>
        <button v-if="disabled" class="stop-btn" @click="emit('stop')" title="停止生成 (Esc)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <rect x="5" y="5" width="14" height="14" rx="2" fill="currentColor"/>
          </svg>
        </button>
        <button v-else class="send-btn" :disabled="!text.trim()" @click="submit" title="发送 (Enter)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
      </div>
    </div>
    <div class="input-hint">
      <span class="hint-left"><kbd>Enter</kbd> 发送 · <kbd>Shift</kbd>+<kbd>Enter</kbd> 换行</span>
      <span class="hint-right">可随时切换模型</span>
    </div>
  </div>
</template>

<style scoped>
.input-area { padding: 6px 0; }

.input-shell {
  position: relative;
  display: flex; align-items: flex-end;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 3px 8px 3px 12px;
  transition: all 0.2s var(--ease-out-expo);
  box-shadow: var(--shadow-sm);
}
.input-area.focused .input-shell {
  border-color: var(--accent-border);
  box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow-md);
}

.input-prefix {
  display: flex; align-items: center; padding-bottom: 7px; flex-shrink: 0;
  margin-right: 5px;
}
.prefix-label {
  font-family: var(--font-mono); font-size: 14px; font-weight: 700;
  color: var(--accent); opacity: 0.85;
}

/* ── 模型选择器 ── */
.model-selector { flex-shrink: 0; padding-bottom: 3px; margin-right: 2px; }

.model-chip {
  display: inline-flex; align-items: center; gap: 4px;
  height: 27px; padding: 0 9px;
  background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-xs);
  cursor: pointer; font-family: var(--font-mono); font-size: 10.5px;
  color: var(--text-muted); transition: all var(--transition);
  white-space: nowrap; user-select: none;
}
.model-chip:hover {
  border-color: var(--accent-border); color: var(--text-secondary);
  background: var(--bg-hover);
}
.model-chip-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--accent); flex-shrink: 0;
}
.model-chip-text {
  max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.model-chip-chevron {
  flex-shrink: 0; color: var(--text-faint); transition: transform var(--transition);
}
.model-chip-chevron.open { transform: rotate(180deg); }

/* ── 下拉面板（向上展开，避免被底部视口截断）── */
.model-picker {
  position: absolute; bottom: calc(100% + 6px); left: 48px;
  width: 290px; max-height: 380px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  z-index: 1000; overflow: hidden;
  display: flex; flex-direction: column;
}
.picker-header {
  font-size: 9.5px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted);
  padding: 10px 14px 5px; border-bottom: 1px solid var(--border-light);
}
.picker-list { flex: 1; overflow-y: auto; padding: 4px; max-height: 200px; }
.picker-option {
  display: flex; align-items: center; gap: 8px;
  width: 100%; padding: 6px 10px; background: none; border: none;
  border-radius: var(--radius-sm); cursor: pointer;
  font-family: var(--font-mono); font-size: 11.5px;
  color: var(--text-secondary); text-align: left;
  transition: all var(--transition);
}
.picker-option:hover { background: var(--bg-hover); color: var(--text-primary); }
.picker-option.active { background: var(--accent-soft); color: var(--accent); }
.option-check { width: 14px; flex-shrink: 0; font-size: 10px; }

.picker-empty {
  padding: 20px 14px; text-align: center;
  font-size: 11px; color: var(--text-faint);
}

.picker-footer {
  border-top: 1px solid var(--border-light);
  padding: 7px;
}
.picker-custom-row {
  display: flex; gap: 5px; margin-bottom: 5px;
}
.picker-custom-input {
  flex: 1; height: 28px; padding: 0 9px;
  background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary); font-family: var(--font-mono); font-size: 11px;
  outline: none; transition: border-color var(--transition);
}
.picker-custom-input:focus { border-color: var(--accent-border); }
.picker-custom-input::placeholder { color: var(--text-faint); }
.picker-custom-btn {
  height: 28px; padding: 0 11px;
  background: var(--accent); color: white; border: none;
  border-radius: var(--radius-sm); font-size: 11px; font-weight: 600;
  cursor: pointer; transition: background var(--transition); flex-shrink: 0;
}
.picker-custom-btn:hover { background: var(--accent-hover); }

.picker-settings-link {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 9px; background: none; border: none;
  border-radius: var(--radius-sm); cursor: pointer;
  font-size: 11px; color: var(--text-muted);
  transition: all var(--transition); width: 100%;
}
.picker-settings-link:hover { background: var(--bg-hover); color: var(--text-secondary); }

/* ── 下拉动画（向上展开）── */
.picker-fade-enter-active { transition: all 0.15s var(--ease-out-expo); }
.picker-fade-leave-active { transition: all 0.1s ease-in; }
.picker-fade-enter-from,
.picker-fade-leave-to {
  opacity: 0; transform: translateY(4px);
}

/* ── 上下文用量环形指示器 ── */
.ctx-ring-wrap {
  position: relative; flex-shrink: 0;
  display: flex; align-items: center;
  padding-bottom: 7px; margin-left: 6px;
  cursor: default;
}
.ctx-ring { display: block; }
.ctx-ring-bg {
  fill: none; stroke: var(--border); stroke-width: 2.5;
}
.ctx-ring-fg {
  fill: none; stroke-width: 2.5; stroke-linecap: round;
  transform: rotate(-90deg); transform-origin: 9px 9px;
  transition: stroke-dasharray 0.4s var(--ease-out-expo), stroke 0.3s;
}
.ctx-ring-fg.ok { stroke: var(--accent); }
.ctx-ring-fg.warn { stroke: #f59e0b; }
.ctx-ring-fg.danger { stroke: #e74c3c; }
/* 环形上的压缩阈值刻度线（80% 处） */
.ctx-ring-thresh {
  stroke: #e74c3c; stroke-width: 1.6; stroke-linecap: round;
}

/* 刚完成压缩 — 环形闪烁提示 */
.ctx-ring.just-compressed { animation: ctx-pulse 0.9s ease-out 2; }
@keyframes ctx-pulse {
  0% { filter: drop-shadow(0 0 0 rgba(46, 204, 113, 0)); }
  50% { filter: drop-shadow(0 0 4px rgba(46, 204, 113, 0.8)); }
  100% { filter: drop-shadow(0 0 0 rgba(46, 204, 113, 0)); }
}

/* 悬停浮窗（向上展开） */
.ctx-popover {
  position: absolute; bottom: calc(100% + 8px); left: -8px;
  width: 248px; padding: 11px 12px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  z-index: 1000;
}
.ctx-pop-header {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 9.5px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted);
  margin-bottom: 8px;
}
.ctx-pop-role {
  font-size: 9px; color: var(--accent);
  text-transform: none; letter-spacing: 0;
  background: var(--accent-soft);
  padding: 1px 6px; border-radius: var(--radius-xs);
}
.ctx-pop-bar {
  position: relative;
  height: 4px; background: var(--bg-input);
  border-radius: 2px; overflow: hidden; margin-bottom: 5px;
}
.ctx-pop-bar-fill {
  height: 100%; border-radius: 2px;
  transition: width 0.4s var(--ease-out-expo);
}
/* 进度条上的压缩阈值竖线（80% 处） */
.ctx-pop-bar-thresh {
  position: absolute; top: 0; bottom: 0;
  width: 1.5px; background: #e74c3c;
  transform: translateX(-50%);
}
.ctx-pop-bar-fill.ok { background: var(--accent); }
.ctx-pop-bar-fill.warn { background: #f59e0b; }
.ctx-pop-bar-fill.danger { background: #e74c3c; }
.ctx-pop-percent {
  font-family: var(--font-mono); font-size: 13px; font-weight: 700;
  text-align: right; margin-bottom: 7px;
}
.ctx-pop-percent.ok { color: var(--accent); }
.ctx-pop-percent.warn { color: #f59e0b; }
.ctx-pop-percent.danger { color: #e74c3c; }
.ctx-pop-row {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 10.5px;
}
.ctx-pop-label { color: var(--text-muted); }
.ctx-pop-value {
  font-family: var(--font-mono); font-size: 10px;
  color: var(--text-secondary);
}
.ctx-pop-divider {
  height: 1px; background: var(--border-light); margin: 8px 0;
}
.ctx-pop-note {
  font-size: 10px; line-height: 1.55; color: var(--text-muted);
}
.ctx-pop-note b { color: var(--text-secondary); }
.ctx-pop-stats {
  display: flex; align-items: center; gap: 5px;
  margin-top: 7px; font-size: 10px;
  font-family: var(--font-mono); color: var(--accent);
}
.ctx-pop-stats.muted { color: var(--text-faint); font-family: var(--font-sans); }
.ctx-pop-dot { color: var(--text-faint); }
.ctx-pop-flash {
  margin-top: 7px; padding: 4px 8px;
  background: rgba(46, 204, 113, 0.12);
  border-radius: var(--radius-xs);
  font-size: 9.5px; color: #2ecc71;
  text-align: center;
}

/* ── textarea ── */
textarea {
  flex: 1; background: none; border: none; outline: none;
  color: var(--text-primary); font-family: var(--font-mono);
  font-size: 12.5px; line-height: 1.6; padding: 7px 0;
  resize: none; min-height: 20px; max-height: 180px;
}
textarea::placeholder { color: var(--text-faint); font-family: var(--font-sans); }
textarea:disabled { color: var(--text-muted); }

.input-actions { display: flex; align-items: center; gap: 5px; padding-bottom: 3px; flex-shrink: 0; }
.char-count { font-family: var(--font-mono); font-size: 9.5px; color: var(--text-faint); }

.send-btn {
  width: 30px; height: 30px; background: var(--accent);
  border: none; border-radius: 7px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  color: white; flex-shrink: 0; transition: all 0.16s var(--ease-spring);
}
.send-btn:hover:not(:disabled) {
  background: var(--accent-hover); transform: scale(1.07);
}
.send-btn:active:not(:disabled) { transform: scale(0.94); }
.send-btn:disabled { background: var(--bg-hover); color: var(--text-faint); cursor: not-allowed; }

.stop-btn {
  width: 30px; height: 30px; background: var(--error);
  border: none; border-radius: 7px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  color: white; flex-shrink: 0; transition: all 0.16s var(--ease-spring);
  animation: pulse-stop 1s ease-in-out infinite;
}
.stop-btn:hover { background: #c0392b; transform: scale(1.07); }
.stop-btn:active { transform: scale(0.94); }

@keyframes pulse-stop {
  0%, 100% { box-shadow: 0 0 0 0 rgba(231, 76, 60, 0.4); }
  50% { box-shadow: 0 0 0 6px rgba(231, 76, 60, 0); }
}

.input-hint {
  display: flex; align-items: center; justify-content: space-between;
  margin-top: 6px; font-size: 9.5px; color: var(--text-faint); padding: 0 4px;
}
kbd {
  display: inline-block; padding: 1px 4px;
  font-family: var(--font-mono); font-size: 8.5px; line-height: 1.4;
  color: var(--text-muted); background: var(--bg-card);
  border: 1px solid var(--border); border-radius: 2.5px;
}
.hint-right { color: var(--text-faint); opacity: 0.5; }
</style>
