<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'

const props = defineProps({ disabled: { type: Boolean, default: false } })
const emit = defineEmits(['send', 'stop', 'open-settings'])

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

/* ── 下拉面板 ── */
.model-picker {
  position: absolute; top: calc(100% + 6px); left: 48px;
  width: 290px; max-height: 380px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  z-index: 100; overflow: hidden;
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

/* ── 下拉动画 ── */
.picker-fade-enter-active { transition: all 0.15s var(--ease-out-expo); }
.picker-fade-leave-active { transition: all 0.1s ease-in; }
.picker-fade-enter-from,
.picker-fade-leave-to {
  opacity: 0; transform: translateY(-4px);
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
