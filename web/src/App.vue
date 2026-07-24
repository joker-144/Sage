<script setup>
import { ref, onMounted, provide, watch } from 'vue'
import { useChat } from './composables/useChat'
import TitleBar from './components/TitleBar.vue'
import StatusBar from './components/StatusBar.vue'
import Sidebar from './components/Sidebar.vue'
import ChatMessage from './components/ChatMessage.vue'
import ChatInput from './components/ChatInput.vue'
import DashboardView from './components/DashboardView.vue'
import AgentsView from './components/AgentsView.vue'
import SkillsView from './components/SkillsView.vue'
import WorkspaceView from './components/WorkspaceView.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import StatsModal from './components/StatsModal.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'

const {
  messages, isProcessing, statusText, conversationId, messagesRef, messageSentCount,
  collaborateMode, sendMessage, cancel, reset, loadConversation, deleteConversation,
} = useChat()

const activeView = ref('chat')
const showStats = ref(false)
const sidebarExpanded = ref(true)
const sidebarRefreshKey = ref(0)
const confirmDelete = ref({ show: false, convId: null })

function handleNavigate(view) {
  if (view === 'chat' && view === activeView.value) {
    // 已在对话页面 — 切换侧边栏展开/收缩
    sidebarExpanded.value = !sidebarExpanded.value
  } else {
    activeView.value = view
    // 切到对话页时自动展开侧边栏，工作区页面不展开侧边栏
    if (view === 'chat') {
      sidebarExpanded.value = true
    } else if (view === 'workspace') {
      sidebarExpanded.value = false
    }
  }
}

function handleDeleteConversation(convId) {
  confirmDelete.value = { show: true, convId }
}

async function doDelete() {
  const convId = confirmDelete.value.convId
  confirmDelete.value = { show: false, convId: null }
  if (!convId) return
  const ok = await deleteConversation(convId)
  if (ok) sidebarRefreshKey.value++
}

async function handleLoadConversation(convId) {
  await loadConversation(convId)
  sidebarRefreshKey.value++
}

// 每次用户发送消息完成（'done'事件），刷新侧栏
watch(messageSentCount, () => {
  sidebarRefreshKey.value++
})

// 新建对话时也刷新侧栏
function handleNewChat() {
  reset()
  sidebarRefreshKey.value++
}

onMounted(() => { reset() })
</script>

<template>
  <div class="app-shell">
    <TitleBar />

    <div class="app-body">
      <Sidebar
        :status-text="statusText"
        :is-processing="isProcessing"
        :active-view="activeView"
        :sidebar-expanded="sidebarExpanded"
        :current-conversation-id="conversationId"
        :refresh-key="sidebarRefreshKey"
        @new-chat="handleNewChat"
        @stats="showStats = true"
        @navigate="handleNavigate"
        @load-conversation="handleLoadConversation"
        @delete-conversation="handleDeleteConversation"
      />

      <main class="main-content">
        <div v-show="activeView === 'chat'" class="chat-area">
          <div class="messages" ref="messagesRef">
            <div class="messages-inner">
              <ChatMessage v-for="(msg, i) in messages" :key="i" :message="msg" />
            </div>
          </div>
          <div class="input-section">
            <div class="input-toolbar">
              <button
                class="collab-toggle"
                :class="{ active: collaborateMode }"
                :title="collaborateMode ? '当前为多智能体协作模式，点击切换单 Agent' : '点击切换到多智能体协作模式'"
                @click="collaborateMode = !collaborateMode"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="6" r="2.5" stroke="currentColor" stroke-width="1.8"/>
                  <circle cx="6" cy="18" r="2.5" stroke="currentColor" stroke-width="1.8"/>
                  <circle cx="18" cy="18" r="2.5" stroke="currentColor" stroke-width="1.8"/>
                  <path d="M12 8.5v3M9.5 15.5l-2-1M14.5 15.5l2-1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                </svg>
                {{ collaborateMode ? '协作模式' : '单 Agent' }}
              </button>
            </div>
            <ChatInput :disabled="isProcessing" @send="sendMessage" @stop="cancel" @open-settings="activeView = 'settings'" />
          </div>
        </div>

        <DashboardView v-show="activeView === 'dashboard'" />
        <AgentsView v-show="activeView === 'agents'" />
        <SkillsView v-show="activeView === 'skills'" />
        <WorkspaceView v-show="activeView === 'workspace'" />
        <SettingsPanel v-show="activeView === 'settings'" />
      </main>
    </div>

    <StatusBar :is-processing="isProcessing" />

    <transition name="fade"><StatsModal v-if="showStats" @close="showStats = false" /></transition>
    <ConfirmDialog
      :visible="confirmDelete.show"
      title="删除对话"
      message="确定要删除此对话吗？此操作不可恢复。"
      @confirm="doDelete"
      @cancel="confirmDelete = { show: false, convId: null }"
    />
  </div>
</template>

<style scoped>
.app-shell { height: 100vh; display: flex; flex-direction: column; background: var(--bg-base); overflow: hidden; }

.app-body { flex: 1; display: flex; overflow: hidden; min-height: 0; }
.main-content { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: var(--bg-base); position: relative; min-width: 0; }

.chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-height: 0; }
.messages { flex: 1; overflow-y: auto; padding: 0 28px; scroll-behavior: smooth; }
.messages-inner {
  max-width: 760px; margin: 0 auto;
  padding: 18px 0; display: flex; flex-direction: column; gap: 16px;
}
.input-section {
  flex-shrink: 0; padding: 0 28px 16px;
  background: linear-gradient(to top, var(--bg-base) 70%, rgba(248,250,252,0));
}
.input-section :deep(.input-area) { max-width: 760px; margin: 0 auto; }

.input-toolbar {
  max-width: 760px; margin: 0 auto 8px;
  display: flex; align-items: center; gap: 8px;
}
.collab-toggle {
  display: flex; align-items: center; gap: 5px;
  font-size: 11px; font-weight: 500;
  color: var(--text-muted);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  cursor: pointer;
  transition: all 0.18s var(--ease-out-expo);
}
.collab-toggle:hover {
  border-color: var(--accent-border);
  color: var(--text-secondary);
}
.collab-toggle.active {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: var(--accent-border);
}

@media (max-width: 1000px) {
  .messages { padding: 0 18px; }
  .input-section { padding: 0 18px 12px; }
  .messages-inner { gap: 14px; }
}
</style>
