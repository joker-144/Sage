import { ref, reactive, nextTick } from 'vue'
import { usePoolMode } from './usePoolMode'

const SSE_TIMEOUT_MS = 60000   // 60 秒无响应超时（配合后端 10s 心跳保活）
const CONV_ID_KEY = 'sage-conversation-id'

// 池模式状态（单例，与 WorkspaceView 共享）
const { poolMode } = usePoolMode()

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
  const writingMode = ref(false)  // 写作模式开关（智能选择流程：简单任务单Agent，复杂任务多智能体）

  // 当前正在调用的智能体 role 集合（用于侧边栏圆圈高亮）
  // 写作模式：collaborate 事件的 role；单 Agent 模式：load_skill 触发 'general'
  const activeAgentRoles = reactive(new Set())

  let currentAssistant = null
  let abortController = null
  let sseTimedOut = false   // 标记是否因 SSE 超时而 abort（区分用户主动取消）

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
    // 新建对话时同样重置模块级引用 + 中断残留 SSE
    currentAssistant = null
    sseTimedOut = false
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    // 清空智能体高亮状态
    activeAgentRoles.clear()
  }

  async function loadConversation(convId) {
    if (!convId) return
    // 即使当前对话仍在处理中，也允许切换：先中断残留 SSE 流
    if (isProcessing.value) {
      if (abortController) {
        abortController.abort()
        abortController = null
      }
      isProcessing.value = false
    }
    try {
      const res = await fetch(`/conversations/${convId}/messages?limit=200`, {
        signal: AbortSignal.timeout(15000),
      })
      if (!res.ok) return
      const data = await res.json()
      conversationId.value = convId
      saveConvId(convId)
      // 策略：将所有相同 user/assistant 轮的工具调用合并到同一条消息中
      // 注意：使用独立局部变量 buildingAssistant，避免与模块级 currentAssistant 重名混淆
      const loaded = []
      let buildingAssistant = null  // 当前正在构建的 assistant 消息（局部）

      for (const msg of (data.messages || [])) {
        if (msg.role === 'user') {
          buildingAssistant = null
          loaded.push({ role: 'user', content: msg.content, tools: [] })
        } else if (msg.role === 'assistant') {
          // 检查 tool_args 中是否包含 tool_calls（OpenAI 格式）
          const toolCallsFromArgs = safeParseJson(msg.tool_args)
          const hasToolCalls = Array.isArray(toolCallsFromArgs) && toolCallsFromArgs.length > 0

          if (msg.tool_name || hasToolCalls) {
            // 工具调用消息 — 合并到当前 assistant
            // 该轮 LLM 调用的 token 用量（DB 存在 assistant 消息上）
            const roundTokens = msg.tokens > 0 ? { total: msg.tokens } : {}
            if (!buildingAssistant) {
              buildingAssistant = { role: 'assistant', content: '', tools: [], reasoning: msg.reasoning || '', _roundTokens: roundTokens }
              loaded.push(buildingAssistant)
            } else if (msg.reasoning && !buildingAssistant.reasoning) {
              // 补充该轮的思考内容（首次设置）
              buildingAssistant.reasoning = msg.reasoning
            }
            if (hasToolCalls) {
              // 从 tool_args 中提取工具调用信息
              for (const tc of toolCallsFromArgs) {
                const fn = tc.function || tc
                const tName = fn.name || msg.tool_name || ''
                const tArgs = fn.arguments ? safeParseJson(fn.arguments) : {}
                const agent = makeAgentFlag(tName, tArgs)
                buildingAssistant.tools.push({
                  name: tName,
                  args: tArgs,
                  content: '',
                  result: '',
                  expanded: false,
                  done: false,
                  tokens: roundTokens,
                  isAgent: agent.isAgent,
                  agentName: agent.agentName,
                })
              }
            } else if (msg.tool_name) {
              const agent = makeAgentFlag(msg.tool_name, {})
              buildingAssistant.tools.push({
                name: msg.tool_name,
                args: {},
                content: '',
                result: msg.content || '',
                expanded: false,
                done: true,
                tokens: roundTokens,
                isAgent: agent.isAgent,
                agentName: agent.agentName,
              })
            }
          } else {
            // 纯文本 assistant 消息（工具调用后的最终回复）
            if (buildingAssistant) {
              buildingAssistant.content = msg.content || ''
              // 最终回复轮的思考内容（追加，保留工具调用轮的思考）
              if (msg.reasoning) {
                buildingAssistant.reasoning = (buildingAssistant.reasoning || '') + (buildingAssistant.reasoning ? '\n' : '') + msg.reasoning
              }
              buildingAssistant = null
            } else {
              loaded.push({ role: 'assistant', content: msg.content || '', tools: [], reasoning: msg.reasoning || '' })
            }
          }
        } else if (msg.role === 'tool') {
          // 工具执行结果 — 更新当前 assistant 中对应工具的结果
          if (!buildingAssistant) {
            buildingAssistant = { role: 'assistant', content: '', tools: [], reasoning: '' }
            loaded.push(buildingAssistant)
          }
          // 查找通过 tool_args 创建的占位符工具（name 匹配且 done=false）
          const tool = buildingAssistant.tools.find(
            t => t.name === (msg.tool_name || '') && !t.done
          )
          if (tool) {
            tool.result = msg.content || ''
            tool.done = true
          } else {
            const agent = makeAgentFlag(msg.tool_name || '', {})
            buildingAssistant.tools.push({
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
      // 切换对话时必须重置模块级 currentAssistant 引用，
      // 防止上一对话的 assistant 引用残留导致后续事件追加到错误对话。
      currentAssistant = null
      // 中断上一对话可能残留的 SSE 连接，避免跨对话事件串扰
      if (abortController) {
        abortController.abort()
        abortController = null
      }
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
    // 用户主动取消 — 标记为非超时，以便 catch 块区分
    sseTimedOut = false
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
      // 取消时清空所有智能体高亮
      activeAgentRoles.clear()
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
      reasoning: '',  // 模型思考内容（reasoning_content）
    })
    currentAssistant = messages.value[messages.value.length - 1]
    scrollToBottom()

    try {
      await streamChat(text)
    } catch (err) {
      // 切换对话时 currentAssistant 可能已被重置为 null，需做 null 检查
      if (currentAssistant) {
        if (err.name === 'AbortError' && sseTimedOut) {
          // SSE 超时 — 后端长时间无响应（含心跳），提示用户检查后端
          currentAssistant.content += `\n\n**请求超时：** 后端 ${SSE_TIMEOUT_MS / 1000} 秒内无响应，可能已停止运行或出现异常。请检查后端服务状态后重试。`
        } else if (err.name === 'AbortError') {
          currentAssistant.content += `\n\n（已取消）`
        } else {
          currentAssistant.content += `\n\n**错误:** ${err.message}`
        }
      }
    } finally {
      abortController = null
      sseTimedOut = false
      isProcessing.value = false
      if (statusText.value === 'Agent 思考中...' || statusText.value.startsWith('执行:')) {
        statusText.value = '系统就绪'
      }
      // 流结束/出错时确保所有高亮复位
      activeAgentRoles.clear()
    }
  }

  function streamChat(message) {
    abortController = new AbortController()
    sseTimedOut = false   // 重置超时标记

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
      if (writingMode.value) {
        body.mode = 'writing'
      }
      // 池模式标记：后端据此将 search_literature 路由到跨工作空间检索
      if (poolMode.value) {
        body.pool_mode = true
      }

      // SSE 超时兜底：60 秒无任何数据则中止（后端 10s 心跳保活，超时说明后端异常）
      let sseTimer = setTimeout(() => {
        sseTimedOut = true   // 标记为超时（区别于用户主动取消）
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

      case 'delete_confirm_required': {
        // delete_file 工具请求用户确认删除
        const { token, path, type } = data
        const typeLabel = type === '目录' ? '目录及其所有内容' : '文件'
        const confirmed = window.confirm(
          `智能体请求删除以下${typeLabel}：\n\n${path}\n\n确认删除吗？此操作不可恢复。`
        )
        // 调用后端确认端点
        fetch('/api/sage/confirm-delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, confirmed }),
        }).catch(() => {})
        // 在工具区显示确认状态
        if (currentAssistant.tools.length > 0) {
          const last = currentAssistant.tools[currentAssistant.tools.length - 1]
          last.result = confirmed ? `已确认删除: ${path}` : `已取消删除: ${path}`
          last.done = true
        }
        scrollToBottom()
        break
      }

      case 'reasoning':
        // 模型思考内容（reasoning_content）— 拼接到 currentAssistant.reasoning
        currentAssistant.reasoning = (currentAssistant.reasoning || '') + (data.content || '')
        scrollToBottom()
        break

      case 'retry': {
        // LLM 调用失败重试 — 在状态栏显示 "重试中 (1/3)..." 并在工具区记录
        const attempt = data.attempt || 0
        const maxRetries = data.max_retries || 0
        const delay = data.delay || 0
        const roleLabel = data.role ? {
          supervisor: '主编', planner: '方法论专家', coder: '撰写员',
          reviewer: '审校核查员', debugger: '修订员',
          literature: '文献调研员', citation: '引用管理员', consolidator: '整理汇报员',
          general: '通用助手',
        }[data.role] || data.role : ''
        const rolePrefix = roleLabel ? `[${roleLabel}] ` : ''
        statusText.value = `${rolePrefix}重试中 (${attempt}/${maxRetries})，${delay}秒后重试...`
        // 在工具区展示重试信息（作为一条特殊的工具调用卡片）
        currentAssistant.tools.push({
          name: 'llm_retry',
          args: { attempt, max_retries: maxRetries, delay },
          content: data.content || '',
          result: data.error ? `错误: ${data.error}` : '',
          expanded: false,
          done: true,
          isRetry: true,
          agentName: roleLabel,
        })
        scrollToBottom()
        break
      }

      case 'busy':
        // 同一会话已有请求在处理中 — 后端拒绝本次请求，提示用户稍候
        currentAssistant.content = data.content || '该对话上一条消息仍在处理中，请稍候再发送。'
        statusText.value = '上一条消息仍在处理中'
        break

      case 'progress': {
        // 长任务进度通知（索引/OCR/查重等）— 更新状态栏 + 进行中工具卡片
        const roleLabels = {
          supervisor: '主编', planner: '方法论专家', coder: '撰写员',
          reviewer: '审校核查员', debugger: '修订员',
          literature: '文献调研员', citation: '引用管理员', consolidator: '整理汇报员',
          general: '通用助手',
        }
        const current = data.current || 0
        const total = data.total || 0
        const fraction = total > 0 && current > 0 ? `（${current}/${total}）` : ''
        if (data.role) {
          const label = roleLabels[data.role] || data.role
          statusText.value = `[${label}] ${data.content || ''}${fraction}`
        } else {
          statusText.value = `执行: ${data.tool || ''} ${data.content || ''}${fraction}`.trim()
        }
        // 将进度附加到对应的进行中工具卡片（找不到时退回最后一张未完成卡片）
        let target = null
        if (data.tool) {
          target = [...currentAssistant.tools].reverse().find(t => !t.done && t.name === data.tool)
        }
        if (!target) {
          target = [...currentAssistant.tools].reverse().find(t => !t.done)
        }
        if (target) {
          target.progress = {
            message: data.content || '',
            current,
            total,
          }
        }
        break
      }

      case 'text':
        // 协作模式下 text 事件带 role 字段，在内容前标注角色
        if (data.role) {
          const roleLabels = {
            supervisor: '主编', planner: '方法论专家', coder: '撰写员',
            reviewer: '审校核查员', debugger: '修订员',
            literature: '文献调研员', citation: '引用管理员', consolidator: '整理汇报员',
            general: '通用助手',
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
            general: '通用助手',
          }
          const label = roleLabels[data.role] || data.role
          const phase = phaseLabels[data.phase] || data.phase
          statusText.value = `[${label}] ${phase}: ${data.content || ''}`

          // 将协作进度作为工具调用展示（便于用户看到流程）
          if (data.phase === 'start' || data.phase === 'plan') {
            // 智能体开始工作 → 高亮
            activeAgentRoles.add(data.role)
            currentAssistant.tools.push({
              name: `collaborate_${data.role}`,
              args: { phase: data.phase },
              content: data.content || '',
              result: '',
              expanded: false,
              done: false,
              tokens: data.tokens || {},
              isCollaborate: true,
              agentName: label,
            })
          } else if (data.phase === 'done' || data.phase === 'reflection') {
            // 智能体完成 → 取消高亮
            activeAgentRoles.delete(data.role)
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
                tokens: {},
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
    writingMode,
    activeAgentRoles,
    sendMessage,
    cancel,
    reset,
    loadConversation,
    deleteConversation,
    scrollToBottom,
  }
}
