import { ref, nextTick } from 'vue'

const SSE_TIMEOUT_MS = 60000   // 60 秒无响应超时（配合后端 10s 心跳保活）
const CONV_ID_KEY = 'sage-conversation-id'

function loadConvId() {
  try { return localStorage.getItem(CONV_ID_KEY) } catch { return null }
}
function saveConvId(id) {
  try { id ? localStorage.setItem(CONV_ID_KEY, id) : localStorage.removeItem(CONV_ID_KEY) } catch {}
}

export function useChat() {
  const messages = ref([])
  const isProcessing = ref(false)
  const statusText = ref('系统就绪')
  const conversationId = ref(loadConvId())  // 从 localStorage 恢复
  const messagesRef = ref(null)
  const messageSentCount = ref(0)  // 每发一条消息+1，父组件 watch 后刷新侧栏
  const collaborateMode = ref(false)  // 多智能体协作模式开关

  let currentAssistant = null
  let abortController = null

  function scrollToBottom() {
    nextTick(() => {
      const el = messagesRef.value
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  function reset() {
    messages.value = []
    conversationId.value = null
    saveConvId(null)
  }

  async function loadConversation(convId) {
    if (!convId || isProcessing.value) return
    try {
      const res = await fetch(`/conversations/${convId}/messages?limit=200`)
      if (!res.ok) return
      const data = await res.json()
      conversationId.value = convId
      saveConvId(convId)
      // 策略：将所有相同 user/assistant 轮的工具调用合并到同一条消息中
      const loaded = []
      let currentAssistant = null  // 当前正在构建的 assistant 消息

      for (const msg of (data.messages || [])) {
        if (msg.role === 'user') {
          currentAssistant = null
          loaded.push({ role: 'user', content: msg.content, tools: [] })
        } else if (msg.role === 'assistant') {
          // 检查 tool_args 中是否包含 tool_calls（OpenAI 格式）
          const toolCallsFromArgs = safeParseJson(msg.tool_args)
          const hasToolCalls = Array.isArray(toolCallsFromArgs) && toolCallsFromArgs.length > 0

          if (msg.tool_name || hasToolCalls) {
            // 工具调用消息 — 合并到当前 assistant
            if (!currentAssistant) {
              currentAssistant = { role: 'assistant', content: '', tools: [] }
              loaded.push(currentAssistant)
            }
            if (hasToolCalls) {
              // 从 tool_args 中提取工具调用信息
              for (const tc of toolCallsFromArgs) {
                const fn = tc.function || tc
                const tName = fn.name || msg.tool_name || ''
                const tArgs = fn.arguments ? safeParseJson(fn.arguments) : {}
                const agent = makeAgentFlag(tName, tArgs)
                currentAssistant.tools.push({
                  name: tName,
                  args: tArgs,
                  content: '',
                  result: '',
                  expanded: false,
                  done: false,
                  tokens: {},
                  isAgent: agent.isAgent,
                  agentName: agent.agentName,
                })
              }
            } else if (msg.tool_name) {
              const agent = makeAgentFlag(msg.tool_name, {})
              currentAssistant.tools.push({
                name: msg.tool_name,
                args: {},
                content: '',
                result: msg.content || '',
                expanded: false,
                done: true,
                tokens: {},
                isAgent: agent.isAgent,
                agentName: agent.agentName,
              })
            }
          } else {
            // 纯文本 assistant 消息（工具调用后的最终回复）
            if (currentAssistant) {
              currentAssistant.content = msg.content || ''
              currentAssistant = null
            } else {
              loaded.push({ role: 'assistant', content: msg.content || '', tools: [] })
            }
          }
        } else if (msg.role === 'tool') {
          // 工具执行结果 — 更新当前 assistant 中对应工具的结果
          if (!currentAssistant) {
            currentAssistant = { role: 'assistant', content: '', tools: [] }
            loaded.push(currentAssistant)
          }
          // 查找通过 tool_args 创建的占位符工具（name 匹配且 done=false）
          const tool = currentAssistant.tools.find(
            t => t.name === (msg.tool_name || '') && !t.done
          )
          if (tool) {
            tool.result = msg.content || ''
            tool.done = true
          } else {
            const agent = makeAgentFlag(msg.tool_name || '', {})
            currentAssistant.tools.push({
              name: msg.tool_name || '',
              args: {},
              content: '',
              result: msg.content || '',
              expanded: false,
              done: true,
              tokens: {},
              isAgent: agent.isAgent,
              agentName: agent.agentName,
            })
          }
        }
      }

      messages.value = loaded.length > 0 ? loaded : []
      scrollToBottom()
    } catch {
      // 静默失败
    }
  }

  function safeParseJson(str) {
    if (!str) return {}
    try { return JSON.parse(str) } catch { return {} }
  }

  // 智能体调用识别：只有 load_skill(name="xxx") 指定了技能名时才标记
  function makeAgentFlag(toolName, args) {
    if (toolName === 'load_skill') {
      const agentName = (args && args.name) || ''
      return { isAgent: !!agentName, agentName }
    }
    return { isAgent: false, agentName: '' }
  }

  async function deleteConversation(convId) {
    if (!convId) return false
    try {
      const res = await fetch(`/conversations/${convId}`, { method: 'DELETE' })
      if (res.ok) {
        // 如果删除的是当前对话，清空消息
        if (conversationId.value === convId) {
          reset()
        }
        return true
      }
    } catch {
      // 静默失败
    }
    return false
  }

  function cancel() {
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    if (isProcessing.value) {
      isProcessing.value = false
      statusText.value = '已取消'
      // 标记最后一条助手消息
      if (currentAssistant && !currentAssistant.content && currentAssistant.tools.length === 0) {
        currentAssistant.content = '（已取消）'
      }
    }
  }

  async function sendMessage(text) {
    if (!text.trim() || isProcessing.value) return

    isProcessing.value = true
    statusText.value = 'Agent 思考中...'

    // 添加用户消息
    messages.value.push({
      role: 'user',
      content: text,
      tools: [],
    })
    scrollToBottom()

    // 创建助手消息容器（push 后重新获取响应式代理引用）
    messages.value.push({
      role: 'assistant',
      content: '',
      tools: [],
    })
    currentAssistant = messages.value[messages.value.length - 1]
    scrollToBottom()

    try {
      await streamChat(text)
    } catch (err) {
      if (err.name === 'AbortError') {
        currentAssistant.content += `\n\n（已取消）`
      } else {
        currentAssistant.content += `\n\n**错误:** ${err.message}`
      }
    } finally {
      abortController = null
      isProcessing.value = false
      if (statusText.value === 'Agent 思考中...' || statusText.value.startsWith('执行:')) {
        statusText.value = '系统就绪'
      }
    }
  }

  function streamChat(message) {
    abortController = new AbortController()

    return new Promise((resolve, reject) => {
      // 读取前端设置并传递给后端
      let settings = null
      try {
        const stored = localStorage.getItem('sage-settings')
        if (stored) {
          const parsed = JSON.parse(stored)
          settings = {
            api_key: parsed.apiKeys?.[parsed.provider] || '',
            base_url: parsed.baseUrl || '',
            model: parsed.model || '',
            temperature: parsed.temperature,
            max_tokens: parsed.maxTokens,
          }
        }
      } catch { /* ignore */ }

      const body = { message }
      if (conversationId.value) {
        body.conversation_id = conversationId.value
      }
      if (settings) {
        body.settings = settings
      }
      if (collaborateMode.value) {
        body.mode = 'collaborate'
      }

      // SSE 超时兜底：3 分钟无任何数据则中止
      let sseTimer = setTimeout(() => {
        if (abortController) {
          abortController.abort()
        }
      }, SSE_TIMEOUT_MS)

      fetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: abortController.signal,
      }).then(res => {
        if (!res.ok) {
          clearTimeout(sseTimer)
          reject(new Error(`HTTP ${res.status}`))
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let currentEvent = null

        function read() {
          reader.read().then(({ done, value }) => {
            if (done) {
              clearTimeout(sseTimer)
              resolve()
              return
            }

            // 收到数据，重置超时
            clearTimeout(sseTimer)
            sseTimer = setTimeout(() => {
              if (abortController) abortController.abort()
            }, SSE_TIMEOUT_MS)

            buffer += decoder.decode(value, { stream: true })
            const lines = buffer.split('\n')
            buffer = lines.pop() || ''

            for (const line of lines) {
              if (line.startsWith('event: ')) {
                currentEvent = line.slice(7).trim()
              } else if (line.startsWith('data: ') && currentEvent) {
                try {
                  const data = JSON.parse(line.slice(6))
                  handleEvent(currentEvent, data)
                } catch (e) {
                  // 忽略解析错误
                }
                currentEvent = null
              }
            }

            read()
          }).catch((err) => {
            clearTimeout(sseTimer)
            reject(err)
          })
        }

        read()
      }).catch((err) => {
        clearTimeout(sseTimer)
        reject(err)
      })
    })
  }

  function handleEvent(type, data) {
    switch (type) {
      case 'tool_start':
        currentAssistant.tools.push({
          name: data.tool,
          args: data.args || {},
          content: data.content || '',
          result: '',
          expanded: false,
          done: false,
          tokens: data.tokens || {},
          isAgent: data.is_agent || false,
          agentName: data.agent_name || '',
        })
        statusText.value = `执行: ${data.tool}`
        scrollToBottom()
        break

      case 'tool_result':
        if (currentAssistant.tools.length > 0) {
          const last = currentAssistant.tools[currentAssistant.tools.length - 1]
          last.result = data.content || ''
          last.done = true
        }
        statusText.value = 'Agent 思考中...'
        scrollToBottom()
        break

      case 'text':
        // 协作模式下 text 事件带 role 字段，在内容前标注角色
        if (data.role) {
          const roleLabels = {
            supervisor: '主编', planner: '方法论专家', coder: '撰写员',
            reviewer: '审校核查员', debugger: '修订员',
            literature: '文献调研员', citation: '引用管理员', consolidator: '整理汇报员',
          }
          const label = roleLabels[data.role] || data.role
          if (!currentAssistant.content) {
            currentAssistant.content = `**[${label}]** ${data.content}`
          } else {
            currentAssistant.content += data.content
          }
        } else {
          currentAssistant.content += data.content
        }
        scrollToBottom()
        break

      case 'collaborate':
        // 多智能体协作事件 — 展示协作进度
        {
          const phaseLabels = {
            plan: '规划', start: '开始', done: '完成', reflection: '反思',
          }
          const roleLabels = {
            supervisor: '主编', planner: '方法论专家', coder: '撰写员',
            reviewer: '审校核查员', debugger: '修订员',
            literature: '文献调研员', citation: '引用管理员', consolidator: '整理汇报员',
          }
          const label = roleLabels[data.role] || data.role
          const phase = phaseLabels[data.phase] || data.phase
          statusText.value = `[${label}] ${phase}: ${data.content || ''}`

          // 将协作进度作为工具调用展示（便于用户看到流程）
          if (data.phase === 'start' || data.phase === 'plan') {
            currentAssistant.tools.push({
              name: `collaborate_${data.role}`,
              args: { phase: data.phase },
              content: data.content || '',
              result: '',
              expanded: false,
              done: false,
              isCollaborate: true,
              agentName: label,
            })
          } else if (data.phase === 'done' || data.phase === 'reflection') {
            // 更新最后一个对应角色的协作工具状态
            const last = [...currentAssistant.tools].reverse().find(
              t => t.isCollaborate && t.name === `collaborate_${data.role}` && !t.done
            )
            if (last) {
              last.result = data.content || ''
              last.done = true
            } else {
              currentAssistant.tools.push({
                name: `collaborate_${data.role}`,
                args: { phase: data.phase },
                content: '',
                result: data.content || '',
                expanded: false,
                done: true,
                isCollaborate: true,
                agentName: label,
              })
            }
          }
          scrollToBottom()
        }
        break

      case 'error':
        if (data.conversation_id) {
          conversationId.value = data.conversation_id
          saveConvId(data.conversation_id)
        }
        currentAssistant.tools.push({
          name: 'error',
          args: {},
          content: data.content,
          result: '',
          expanded: false,
          done: true,
          isError: true,
        })
        scrollToBottom()
        break

      case 'done':
        if (data.conversation_id) {
          conversationId.value = data.conversation_id
          saveConvId(data.conversation_id)
        }
        if (!currentAssistant.content && currentAssistant.tools.length === 0) {
          currentAssistant.content = '（无回复）'
        }
        messageSentCount.value++
        break
    }
  }

  return {
    messages,
    isProcessing,
    statusText,
    conversationId,
    messagesRef,
    messageSentCount,
    collaborateMode,
    sendMessage,
    cancel,
    reset,
    loadConversation,
    deleteConversation,
    scrollToBottom,
  }
}
