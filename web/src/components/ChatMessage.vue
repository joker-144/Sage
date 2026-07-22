<script setup>
import { ref, computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import ToolCall from './ToolCall.vue'

marked.setOptions({
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  },
  breaks: true,
})

const props = defineProps({
  message: { type: Object, required: true },
})

/* 打字机效果 */
const typedContent = ref('')
const isTyping = ref(false)
let typeTimer = null

const fullContent = computed(() => props.message.content || '')

// 当新文本到来时追加到打字机缓冲区
const lastFullContent = ref('')
const typeBuffer = ref('')
const typeIndex = ref(0)

function startTyping() {
  if (isTyping.value) return
  isTyping.value = true
  typeNext()
}

function typeNext() {
  if (typeIndex.value >= typeBuffer.value.length) {
    isTyping.value = false
    return
  }
  typedContent.value += typeBuffer.value[typeIndex.value]
  typeIndex.value++
  typeTimer = setTimeout(typeNext, 4 + Math.random() * 8)
}

// 监听 content 变化——增量打字
import { watch } from 'vue'
watch(() => props.message.content, (newVal, oldVal) => {
  if (!newVal) { typedContent.value = ''; typeIndex.value = 0; typeBuffer.value = ''; return }
  const oldLen = oldVal ? oldVal.length : 0
  if (newVal.length > oldLen) {
    const delta = newVal.slice(oldLen)
    typeBuffer.value += delta
    if (!isTyping.value) startTyping()
  }
})

const renderedContent = computed(() => {
  const text = typedContent.value
  if (!text) return ''
  try { return marked.parse(text) } catch { return text }
})
</script>

<template>
  <div class="message" :class="message.role">
    <div v-if="message.role === 'user'" class="user-bubble">
      <div class="user-label">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="8" r="4" stroke="currentColor" stroke-width="2"/><path d="M4 20c0-4 4-6 8-6s8 2 8 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        <span>你</span>
      </div>
      <div class="user-content">{{ message.content }}</div>
    </div>

    <div v-else class="assistant-block">
      <div class="assistant-label">
        <div class="avatar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M2 12l10 5 10-5" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
        </div>
        <span>Sage</span>
      </div>

      <ToolCall v-for="(tool, i) in message.tools" :key="i" :tool="tool" />

      <div v-if="renderedContent" class="assistant-content markdown" v-html="renderedContent"></div>

      <div v-if="(!message.content || isTyping) && message.tools.length === 0 && !typedContent" class="typing">
        <span></span><span></span><span></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message { animation: fadeIn 0.3s var(--ease-out-expo) forwards; opacity: 0; }

.user-bubble {
  max-width: 76%; margin-left: auto;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 14px 14px 4px 14px;
  padding: 9px 14px;
  box-shadow: var(--shadow-sm);
}
.user-label {
  display: flex; align-items: center; gap: 4px;
  font-size: 9.5px; font-weight: 600; color: var(--text-faint);
  margin-bottom: 3px; letter-spacing: 0.04em; text-transform: uppercase;
}
.user-content {
  font-size: 12.5px; color: var(--text-primary); line-height: 1.65;
}

.assistant-block { width: 100%; }
.assistant-label {
  display: flex; align-items: center; gap: 7px; margin-bottom: 6px;
}
.avatar {
  width: 19px; height: 19px;
  background: linear-gradient(135deg, var(--accent), var(--accent-secondary));
  border-radius: 4.5px;
  display: flex; align-items: center; justify-content: center;
  color: white;
  box-shadow: 0 1px 4px var(--accent-glow);
}
.assistant-label span {
  font-size: 10.5px; font-weight: 600; color: var(--accent);
  letter-spacing: 0.02em;
}
.assistant-content {
  font-size: 12.5px; line-height: 1.72; color: var(--text-secondary);
  padding-left: 26px;
}

/* Markdown */
.markdown :deep(h1),.markdown :deep(h2),.markdown :deep(h3) {
  color: var(--text-primary); font-weight: 600; margin: 14px 0 6px;
  letter-spacing: -0.01em;
}
.markdown :deep(h1) { font-size: 17px; padding-bottom: 4px; border-bottom: 1px solid var(--border); }
.markdown :deep(h2) { font-size: 14.5px; }
.markdown :deep(h3) { font-size: 13.5px; }
.markdown :deep(p) { margin: 5px 0; }
.markdown :deep(ul),.markdown :deep(ol) { margin: 5px 0; padding-left: 18px; }
.markdown :deep(li) { margin: 2px 0; }
.markdown :deep(strong) { color: var(--text-primary); font-weight: 600; }
.markdown :deep(pre) {
  background: var(--bg-code);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 11px 13px; margin: 8px 0; overflow-x: auto;
  font-family: var(--font-mono); font-size: 11.5px; line-height: 1.62;
}
.markdown :deep(pre code) { background: none; padding: 0; color: var(--text-primary); }
.markdown :deep(code) {
  font-family: var(--font-mono); background: var(--bg-card);
  padding: 1px 5px; border-radius: 3px; font-size: 11px;
  color: var(--accent); border: 1px solid var(--border-light);
}
.markdown :deep(a) { color: var(--accent); text-decoration: none; }
.markdown :deep(a:hover) { text-decoration: underline; }
.markdown :deep(blockquote) {
  border-left: 2px solid var(--accent-border);
  padding-left: 10px; margin: 6px 0; color: var(--text-muted);
}
.markdown :deep(table) {
  border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 12px;
}
.markdown :deep(th),.markdown :deep(td) {
  border: 1px solid var(--border); padding: 6px 10px; text-align: left;
}
.markdown :deep(th) {
  background: var(--bg-card); font-weight: 600; color: var(--text-primary);
}
.markdown :deep(td) { color: var(--text-secondary); }

/* 打字指示器 */
.typing {
  display: flex; align-items: center; gap: 4px;
  padding: 6px 26px;
}
.typing span {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--text-muted);
  animation: pulse 1.4s ease-in-out infinite;
}
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }
</style>
