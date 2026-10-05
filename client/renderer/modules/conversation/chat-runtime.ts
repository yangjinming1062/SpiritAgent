import { sleep } from '@runtime'
import { atom, computed, map } from 'nanostores'

import { SpiritAgentRpcError, SpiritAgentRpcErrorCode } from '@/shared/lib/gateway-protocol'
import { errorMessage } from '@/shared/lib/ipc-error'
import { currentClearEpoch } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { $gateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type {
  ChatAttachment,
  ChatMediaItem,
  CompanionBubble,
  CompanionMediaBubble,
  ReplyAudio,
  SessionMessage,
  SessionRuntimeInfo
} from '@/shared/types/spiritagent'

import { chatDisplayText } from './chat-display-text'
import { $companionSessionId, DEFAULT_CONTEXT_LIMIT, FLUSH_DEBOUNCE_MS, nextChatMessageId } from './conversation-state'
import { activeVoiceMessageId, conversationVoiceSink } from './voice-link'
import { releaseVoicePlaybackStore, removeVoicePlayback } from './voice-playback'

export interface ChatMessageListItem {
  id: string
  role: 'user' | 'assistant'
  subtype?: string
  /** 后端 Message.id——fork/undo 回传 source_message_id；hydrate 历史行与活路径绑定后都有值。 */
  backendMessageId?: number
  timestamp?: number
}

export interface ChatMessageBody {
  /** 完整用户输入；陪伴拆泡与附件展示文案不能用作编辑原文。 */
  editableText?: string
  streamingText?: string
  replyType?: CompanionBubble['type']
  replyMedia?: CompanionMediaBubble
  replyIndex?: number
  replyAudio?: ReplyAudio | null
  text: string
  reasoning?: string
  streaming?: boolean
  queued?: boolean
  discarded?: boolean
  toolName?: string | null
  tools?: string[]
  error?: string
  retryMessageId?: number
  cancelled?: boolean
  attachments?: ChatAttachment[]
  media?: ChatMediaItem[]
}

export interface ConversationHistorySync {
  revision: number
  historyRevision: number
  messages: ChatMessageListItem[]
  bodies: Record<string, ChatMessageBody>
}

// IM 守卫与语音入口的权威 kind 源，由 hydrate 注入服务端 info.kind（special / standard / im）。
export type ChatSessionKind = 'im' | 'special' | 'standard'

export interface PendingPromptItem {
  text: string
  attachments?: ChatAttachment[]
  messageId?: string
}

export interface ChatEditDraft {
  sessionId: string
  sourceMessageId: number
  text: string
}

export interface ChatUndoDraft {
  session_id: string
  text: string
  attachments?: ChatAttachment[]
}

export interface SessionSettings {
  temperature?: number
  context_compression_threshold?: number
  enable_context_compression?: boolean
  reasoning_effort?: string
}

export interface SessionContextUsage {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  contextLimit: number
}

export type PendingAttachment =
  | { type: 'image'; value: string; fileName?: string }
  | {
      type: 'video'
      fileName: string
      path: string
      status: 'error' | 'ready' | 'uploading'
      url?: string
      error?: string
    }
  | {
      type: 'file'
      fileName: string
      path: string
    }
  | {
      type: 'folder'
      folderName: string
      path: string
    }

// 伙伴主动说出的瞬时消息，聊天面板收起时以气泡浮出，说完清空；sessionId 存在时点击切到该会话（媒体送达跳转用）。
export interface ProactiveBubbleState {
  text: string
  sessionId?: string
}

// 外部投喂（DESIGN「拖拽与直接交互」）：精灵拖入或经主进程信箱转交的文件路径，对话输入订阅后并入待发附件。
export interface PendingExternalAttachment {
  paths: string[]
  nonce: number
}

export function createConversationRuntime(sessionId: string | null) {
  const epoch = currentClearEpoch()
  let alive = true
  let submissionGeneration = 0
  const isCurrent = (): boolean => alive && epoch === currentClearEpoch()
  const $historyHydrated = atom(false)
  const $runtimeRevision = atom(0)

  // 媒体终态可能早于完成帧；保留更新，避免迟到的等待快照把已就绪卡片覆盖回去。
  const mediaUpdates = new Map<string, CompanionMediaBubble>()
  const mediaUpdateKey = (messageId: number, mediaId: string): string => `${messageId}:${mediaId}`

  function companionBubbleBody(bubble: CompanionBubble, messageId?: number): Partial<ChatMessageBody> {
    if (bubble.type === 'image' || bubble.type === 'video') {
      const current = messageId === undefined ? undefined : mediaUpdates.get(mediaUpdateKey(messageId, bubble.media_id))
      const media = current ?? bubble

      if (messageId !== undefined && media.status !== 'pending') {
        mediaUpdates.set(mediaUpdateKey(messageId, media.media_id), media)
      }

      return {
        text: '',
        replyType: media.type,
        replyMedia: media,
        media: media.status === 'ready' && media.url ? [{ type: media.type, url: media.url }] : undefined
      }
    }

    return 'text' in bubble
      ? { text: bubble.text, replyType: bubble.type, replyAudio: bubble.type === 'voice' ? bubble.audio : undefined }
      : {}
  }

  let flushTimer: ReturnType<typeof setTimeout> | null = null
  // 最近一次已提交批对应的用户气泡 id（工作台合并后只剩首条）；message.persisted 只按本集合绑定，失败回合孤儿气泡不会被下一轮错绑。
  let submittedBubbleIds: Set<string> = new Set()
  let historyEditRevision = 0
  let historyReplacementRevision = 0

  const $chatMessageList = atom<ChatMessageListItem[]>([])
  const $chatMessageBodies = map<Record<string, ChatMessageBody>>({})
  const $lastAssistantStreaming = atom<boolean>(false)
  const $chatStreamingTick = atom<number>(0)
  const $chatSessionId = atom<string | null>(sessionId)

  function normalizeChatSessionKind(raw: unknown): ChatSessionKind {
    return raw === 'im' || raw === 'special' || raw === 'standard' ? raw : 'standard'
  }

  const $chatSessionKind = atom<ChatSessionKind>('standard')
  const $chatSessionPresetId = atom<string | null>(null)
  const $chatSessionReadOnly = atom(false)

  const $pendingPromptBatch = atom<PendingPromptItem[]>([])

  const $chatTurnInFlight = atom<boolean>(false)

  const $lastEditableUserMessage = computed(
    [$chatMessageList, $chatTurnInFlight, $pendingPromptBatch, $chatSessionReadOnly],
    (list, inFlight, pending, readOnly): ChatMessageListItem | null => {
      if (readOnly || inFlight || pending.length > 0) {
        return null
      }

      const last = list.findLast(item => item.role === 'user')

      return last?.backendMessageId && !last.subtype ? last : null
    }
  )

  const $retryableAssistantMessage = computed(
    [$chatMessageList, $chatMessageBodies, $chatTurnInFlight, $pendingPromptBatch, $chatSessionReadOnly],
    (list, bodies, inFlight, pending, readOnly): ChatMessageListItem | null => {
      if (readOnly || inFlight || pending.length > 0) {
        return null
      }

      const last = list.at(-1)
      const body = last ? bodies[last.id] : undefined
      const user = list.findLast(item => item.role === 'user')

      return last?.role === 'assistant' &&
        body?.error &&
        body.retryMessageId === user?.backendMessageId &&
        body.retryMessageId
        ? last
        : null
    }
  )

  async function retryAssistantReply(messageId: string): Promise<void> {
    const sessionId = $chatSessionId.get()
    const gateway = $gateway.get()
    const body = $chatMessageBodies.get()[messageId]

    if (
      !sessionId ||
      !gateway ||
      gateway.connectionState !== 'open' ||
      $retryableAssistantMessage.get()?.id !== messageId ||
      !body?.retryMessageId
    ) {
      return
    }

    const epoch = currentClearEpoch()
    $chatTurnInFlight.set(true)
    conversationVoiceSink().cancel($chatSessionId.get())

    try {
      await gateway.request('prompt.submit', {
        session_id: sessionId,
        retry_message_id: body.retryMessageId,
        response_preference: presentationPorts().getResponsePreference()
      })
    } catch (error) {
      // 已收到开始/完成事件时请求已被接受，迟到的 RPC 失败不能覆盖新回复。
      if (
        isCurrent() &&
        epoch === currentClearEpoch() &&
        $chatSessionId.get() === sessionId &&
        $chatMessageBodies.get()[messageId] === body
      ) {
        $chatTurnInFlight.set(false)
        notifyError(error, getStrings().chat.sendFailed)
      }
    }
  }

  // 当后端在 in-flight 回合期间发出 bubble.break 时置位，防止 message.complete 的全文/推理覆盖末尾气泡。
  const $turnHadBubbleBreak = atom<boolean>(false)

  const $sessionSettings = atom<SessionSettings>({})

  // 服务端 settings 是开放字典，只保留客户端消费且类型相符的键。
  function toSessionSettings(raw: Record<string, unknown> = {}): SessionSettings {
    const settings: SessionSettings = {}

    if (typeof raw.temperature === 'number') {
      settings.temperature = raw.temperature
    }

    if (typeof raw.context_compression_threshold === 'number') {
      settings.context_compression_threshold = raw.context_compression_threshold
    }

    if (typeof raw.enable_context_compression === 'boolean') {
      settings.enable_context_compression = raw.enable_context_compression
    }

    if (typeof raw.reasoning_effort === 'string') {
      settings.reasoning_effort = raw.reasoning_effort
    }

    return settings
  }

  function hydrateSessionSettings(info: SessionRuntimeInfo): void {
    $chatSessionPresetId.set(info.system_preset_id ?? null)
    $sessionSettings.set(toSessionSettings(info.settings))
  }

  function updateSessionSetting<K extends keyof SessionSettings>(key: K, value: SessionSettings[K]): void {
    $sessionSettings.set({
      ...$sessionSettings.get(),
      [key]: value
    })
  }

  const $sessionContextUsage = atom<SessionContextUsage>({
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    contextLimit: DEFAULT_CONTEXT_LIMIT
  })

  function setSessionContextUsage(usage: Partial<SessionContextUsage>): void {
    const current = $sessionContextUsage.get()
    const promptTokens = usage.promptTokens ?? current.promptTokens
    const completionTokens = usage.completionTokens ?? current.completionTokens

    const totalTokens =
      usage.totalTokens ??
      (usage.promptTokens !== undefined || usage.completionTokens !== undefined
        ? promptTokens + completionTokens
        : current.totalTokens)

    const contextLimit = usage.contextLimit ?? current.contextLimit

    $sessionContextUsage.set({
      promptTokens,
      completionTokens,
      totalTokens,
      contextLimit
    })
  }

  function resetSessionContextUsage(contextLimit?: number, totalTokens = 0): void {
    $sessionContextUsage.set({
      promptTokens: 0,
      completionTokens: 0,
      totalTokens,
      contextLimit: contextLimit ?? DEFAULT_CONTEXT_LIMIT
    })
  }

  // 用从后端加载的会话替换面板的聊天记录；其他窗口可能正在连发或等待提交确认，历史修订不能删掉未落库的输入。
  function hydrateEditedChatMessages(messages: SessionMessage[]): void {
    forgetDeletedVoiceMessages(messages)
    historyEditRevision++
    const pendingIds = new Set($pendingPromptBatch.get().map(item => item.messageId))

    const pendingRows = $chatMessageList
      .get()
      .filter(
        item => pendingIds.has(item.id) || (submittedBubbleIds.has(item.id) && item.backendMessageId === undefined)
      )

    const previousBodies = $chatMessageBodies.get()

    $chatTurnInFlight.set(true)
    hydrateChatMessages(messages)

    for (const item of pendingRows) {
      $chatMessageBodies.setKey(item.id, previousBodies[item.id])
    }

    $chatMessageList.set([...$chatMessageList.get(), ...pendingRows])
  }

  function hydrateChatMessages(messages: SessionMessage[], info?: SessionRuntimeInfo): void {
    historyReplacementRevision++
    replaceChatMessages(messages, info)
  }

  function replaceChatMessages(messages: SessionMessage[], info?: SessionRuntimeInfo, cancelVoice = true): void {
    if (cancelVoice) {
      conversationVoiceSink().cancel($chatSessionId.get())
    }

    const items: ChatMessageListItem[] = []
    const bodies: Record<string, ChatMessageBody> = {}

    let totalChars = 0
    const pendingReasoning: string[] = []

    const takeReasoning = (current?: string): string | undefined => {
      const parts = [...pendingReasoning, current].filter((part): part is string => Boolean(part?.trim()))
      pendingReasoning.length = 0

      return parts.length ? parts.join('\n\n') : undefined
    }

    const flushPendingReasoning = (timestamp?: number): void => {
      const reasoning = takeReasoning()

      if (!reasoning) {
        return
      }

      const id = nextChatMessageId()
      items.push({ id, role: 'assistant', timestamp })
      bodies[id] = { text: '', reasoning, streaming: false, toolName: null }
    }

    for (const m of messages) {
      if (m.role === 'tool') {
        continue
      }

      const companionBubbles = m.role === 'assistant' && m.content_type === 'companion_reply' ? m.bubbles : undefined

      const { text, attachments } = extractMessageContent(m)
      const textContent = m.role === 'assistant' ? chatDisplayText(text) : text

      const reasoningContent = typeof m.reasoning === 'string' ? m.reasoning : ''

      // 无正文无媒体的助手行（工具中间帧）不单独占气泡，推理并到下一可见助手行。
      if (m.role === 'assistant' && !companionBubbles?.length && !textContent.trim() && !m.media?.length) {
        if (reasoningContent.trim()) {
          pendingReasoning.push(reasoningContent)
        }

        continue
      }

      if (m.role === 'user') {
        flushPendingReasoning(m.timestamp)
      }

      totalChars +=
        m.content_type === 'companion_reply' && typeof m.content === 'string' ? m.content.length : textContent.length

      // 陪伴用户行按空行拆分（与实时呈现对齐），工作台整段阅读不拆。
      const canSplit = !m.subtype && m.role === 'user' && splitUserBubblesEnabled()
      // 后台视频送达的 system 行正文是给模型的任务记录，与实时送达一致只显示媒体卡。
      const hideText = m.role === 'system' && m.subtype === 'status_media'

      const segments = companionBubbles
        ? companionBubbles.map(bubble => ('text' in bubble ? bubble.text : ''))
        : canSplit
          ? textContent
              .split(/\r?\n(?:[ \t]*\r?\n)+/)
              .map(part => part.trim())
              .filter(Boolean)
          : [hideText ? '' : textContent]

      if (segments.length === 0) {
        segments.push('')
      }

      for (const [index, segment] of segments.entries()) {
        const id = nextChatMessageId()

        items.push({
          id,
          role: m.role === 'user' ? 'user' : 'assistant',
          subtype: m.subtype,
          backendMessageId: typeof m.id === 'number' ? m.id : undefined,
          timestamp: m.timestamp
        })

        // 拆分后附件只挂首个气泡（附件伴随连发首条发出，每段都挂会重复渲染媒体卡）。
        bodies[id] = {
          text: segment,
          editableText: m.role === 'user' ? textContent : undefined,
          replyIndex: companionBubbles ? index : undefined,
          ...(companionBubbles?.[index] ? companionBubbleBody(companionBubbles[index], m.id) : {}),
          reasoning: m.role === 'assistant' && index === 0 ? takeReasoning(reasoningContent || undefined) : undefined,
          toolName: m.tool_name ?? null,
          tools: m.tool_name ? [m.tool_name] : undefined,
          streaming: false,
          queued: m.role === 'user' && m.queued,
          discarded: m.role === 'user' && m.discarded,
          attachments: index === 0 ? attachments : undefined,
          ...(!companionBubbles && index === segments.length - 1 && m.media?.length ? { media: m.media } : {})
        }
      }
    }

    flushPendingReasoning()

    $chatMessageBodies.set(bodies)
    $chatMessageList.set(items)
    $lastAssistantStreaming.set(false)

    if (info) {
      hydrateSessionSettings(info)
      // 缺字段/未知值回落 standard 以免 IM 守卫误判；无 info 的本会话内操作（撤回/清空/压缩重水合）沿用当前 kind，重置会解除 IM 只读。
      $chatSessionKind.set(normalizeChatSessionKind(info.kind))
      $chatSessionReadOnly.set(info.kind === 'im' || info.is_automation === true)
    }

    // 估算 Token 占用（~3 字符/Token）；分项清零避免切换会话残留，无 info 的本会话重水合沿用当前上下文上限。
    const approxTokens = Math.round(totalChars / 3)
    const contextLimit = info ? info.context_window || DEFAULT_CONTEXT_LIMIT : $sessionContextUsage.get().contextLimit
    resetSessionContextUsage(contextLimit, approxTokens)
    $historyHydrated.set(true)
  }

  // 多模态正文和用户附件共用一次解析；附件只交给该消息的首个气泡。
  function extractMessageContent(m: SessionMessage): Pick<ChatMessageBody, 'text' | 'attachments'> {
    if (m.content_type === 'companion_reply' || typeof m.content !== 'string') {
      return { text: '' }
    }

    if (m.content_type !== 'multimodal_v1') {
      return { text: m.content }
    }

    let parsed: unknown

    try {
      parsed = JSON.parse(m.content)
    } catch {
      return { text: m.content }
    }

    if (!Array.isArray(parsed)) {
      return { text: m.content.trim() }
    }

    const parts: unknown[] = parsed
    const texts: string[] = []
    const attachments: ChatAttachment[] = []

    for (const part of parts) {
      if (!part || typeof part !== 'object' || !('type' in part)) {
        continue
      }

      if (part.type === 'input_text' && 'text' in part && typeof part.text === 'string') {
        texts.push(part.text)
      } else if (m.role === 'user') {
        if (
          part.type === 'input_image' &&
          'image_url' in part &&
          typeof part.image_url === 'string' &&
          part.image_url
        ) {
          attachments.push({ type: 'image', url: part.image_url })
        } else if (
          part.type === 'input_video' &&
          'video_url' in part &&
          typeof part.video_url === 'string' &&
          part.video_url
        ) {
          attachments.push({ type: 'video', url: part.video_url })
        }
      }
    }

    return { text: texts.join('\n').trim(), attachments: attachments.length ? attachments : undefined }
  }

  // 先写 body 再入列：列表订阅者据 id 取 body 时必须已存在。
  function appendMessage(item: Omit<ChatMessageListItem, 'id' | 'timestamp'>, body: ChatMessageBody): string {
    const id = nextChatMessageId()
    $chatMessageBodies.setKey(id, body)
    const list = $chatMessageList.get()
    const last = list.at(-1)
    const next = { id, ...item, timestamp: Date.now() }

    // 后台交付插在活动回复之前，流式增量与完成帧仍以末行定位自己的气泡。
    const preserveStreaming = item.subtype && last?.role === 'assistant' && $chatMessageBodies.get()[last.id]?.streaming
    $chatMessageList.set(preserveStreaming ? [...list.slice(0, -1), next, last] : [...list, next])

    return id
  }

  function pushProactiveMessage(text: string, media?: ChatMediaItem[], messageId?: number): void {
    if (messageId && $chatMessageList.get().some(item => item.backendMessageId === messageId)) {
      return
    }

    appendMessage(
      {
        role: 'assistant',
        subtype: media?.length ? 'status_media' : 'status_proactive',
        backendMessageId: messageId
      },
      { text: chatDisplayText(text), media, streaming: false, toolName: null }
    )
  }

  // 后台视频完成的实时送达行，只带媒体；历史水合的同类 system 行同样不显示正文。
  function pushMediaMessage(media: ChatMediaItem[]): string {
    return appendMessage(
      { role: 'assistant', subtype: 'status_media' },
      { text: '', media, streaming: false, toolName: null }
    )
  }

  function pushUserMessage(text: string, attachments?: ChatAttachment[]): string {
    return appendMessage(
      { role: 'user' },
      { text, attachments: attachments?.length ? attachments : undefined, streaming: false, toolName: null }
    )
  }

  function isPositiveInt(value: unknown): value is number {
    return typeof value === 'number' && Number.isInteger(value) && value > 0
  }

  // 陪伴会话连发用户消息保留独立气泡（与 bubble.break 助手拆分对称），工作台整段阅读不拆，避免误拆粘贴的多段内容。
  function splitUserBubblesEnabled(): boolean {
    const id = $chatSessionId.get()

    return id !== null && id === $companionSessionId.get()
  }

  // 历史可能先于落库事件返回；活气泡替换同一后端消息的快照，沿用历史位置。
  function reconcilePersistedMessages(list: ChatMessageListItem[], boundIds: Set<string>): void {
    const groups = new Map<number, ChatMessageListItem[]>()

    for (const item of list) {
      if (boundIds.has(item.id) && item.backendMessageId !== undefined) {
        const group = groups.get(item.backendMessageId) ?? []
        group.push(item)
        groups.set(item.backendMessageId, group)
      }
    }

    const inserted = new Set<number>()

    const merged = list.flatMap(item => {
      const group = item.backendMessageId === undefined ? undefined : groups.get(item.backendMessageId)

      if (!group || item.backendMessageId === undefined) {
        return [item]
      }

      if (!boundIds.has(item.id)) {
        $chatMessageBodies.setKey(item.id, undefined)
      }

      if (inserted.has(item.backendMessageId)) {
        return []
      }

      inserted.add(item.backendMessageId)

      return group
    })

    $chatMessageList.set(merged)
  }

  function bindTrailingUserMessageIds(ids: number[]): void {
    // 只绑本次提交的气泡，失败回合孤儿气泡不被下一轮错绑（错绑会让撤回截断别人的消息）；连发拆泡时超出 id 数的气泡挂最后一个 id，与 hydrate 同行同 id 语义一致。
    const validIds = ids.filter(isPositiveInt)

    if (validIds.length === 0) {
      return
    }

    const list = $chatMessageList.get()
    const unboundIndexes: number[] = []

    for (let i = 0; i < list.length; i++) {
      const item = list[i]

      if (item.backendMessageId === undefined && item.role === 'user' && submittedBubbleIds.has(item.id)) {
        unboundIndexes.push(i)
      }
    }

    // 编辑已水合的落库通知不属于本窗口待确认的提交。
    if (unboundIndexes.length === 0) {
      return
    }

    const next = list.slice()

    for (const [index, idx] of unboundIndexes.entries()) {
      const messageId = validIds[index] ?? validIds[validIds.length - 1]
      next[idx] = { ...next[idx], backendMessageId: messageId }
    }

    reconcilePersistedMessages(next, new Set(unboundIndexes.map(index => next[index].id)))
  }

  function bindTrailingAssistantMessageId(messageId: number): void {
    // bubble.break 会拆出多段助手气泡，但 DB 只有一行；同一 id 挂到上次用户之后所有未绑定的普通助手行。
    if (!isPositiveInt(messageId)) {
      return
    }

    const list = $chatMessageList.get()
    const lastUserIndex = list.findLastIndex(item => item.role === 'user')
    const next = list.slice()
    const boundIds = new Set<string>()

    for (let i = lastUserIndex + 1; i < next.length; i++) {
      const item = next[i]

      // 压缩卡片等 subtype 行不是终端助手气泡，不能挂上同一条 message_id。
      if (item.role === 'assistant' && item.backendMessageId === undefined && !item.subtype) {
        next[i] = { ...item, backendMessageId: messageId }
        boundIds.add(item.id)
      }
    }

    if (boundIds.size > 0) {
      reconcilePersistedMessages(next, boundIds)
    }
  }

  /** 追加一行本地状态行（如 `status_command_result`、`compress_summary`），渲染层按 subtype 显示为居中 pill 或摘要卡片。 */
  function pushStatusPill(subtype: string, text: string, backendMessageId?: number): void {
    if (
      backendMessageId !== undefined &&
      $chatMessageList.get().some(item => item.backendMessageId === backendMessageId)
    ) {
      return
    }

    appendMessage({ role: 'assistant', subtype, backendMessageId }, { text, streaming: false, toolName: null })
  }

  function pushPendingPrompt(item: PendingPromptItem): void {
    const last = $chatMessageList.get().at(-1)
    $pendingPromptBatch.set([
      ...$pendingPromptBatch.get(),
      { ...item, messageId: last?.role === 'user' ? last.id : undefined }
    ])
  }

  function drainPendingPrompts(): PendingPromptItem[] {
    const items = $pendingPromptBatch.get()
    $pendingPromptBatch.set([])

    return items
  }

  function clearPendingPrompts(): void {
    $pendingPromptBatch.set([])
  }

  function setTurnHadBubbleBreak(v: boolean): void {
    $turnHadBubbleBreak.set(v)
  }

  function schedulePendingFlush(): void {
    if ($pendingPromptBatch.get().length === 0) {
      return
    }

    if (flushTimer) {
      clearTimeout(flushTimer)
    }

    flushTimer = setTimeout(() => {
      flushTimer = null
      submitPendingBatch()
    }, FLUSH_DEBOUNCE_MS)
  }

  function cancelPendingFlush(): void {
    if (flushTimer) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
  }

  function submitPendingBatch(): void {
    if ($chatTurnInFlight.get() || flushTimer !== null) {
      return
    }

    const sessionId = $chatSessionId.get()
    const gateway = $gateway.get()

    if (!sessionId || !gateway || gateway.connectionState !== 'open') {
      return
    }

    const pendingBatch = drainPendingPrompts()

    if (pendingBatch.length === 0) {
      return
    }

    $chatTurnInFlight.set(true)

    const pendingIds = new Set(pendingBatch.map(p => p.messageId).filter((id): id is string => Boolean(id)))
    const list = $chatMessageList.get()
    const pendingRows = list.filter(item => pendingIds.has(item.id))
    const first = pendingRows[0]

    // 工作台连发合回首条再提交；陪伴会话每条连发保留独立气泡（DB 仍合并为一行）。
    if (first && !splitUserBubblesEnabled()) {
      const bodies = $chatMessageBodies.get()
      const displayAttachments = pendingRows.flatMap(item => bodies[item.id]?.attachments ?? [])
      $chatMessageBodies.setKey(first.id, {
        ...bodies[first.id],
        text: pendingRows
          .map(item => bodies[item.id]?.text ?? '')
          .filter(Boolean)
          .join('\n\n'),
        attachments: displayAttachments.length ? displayAttachments : undefined
      })
      $chatMessageList.set([...list.filter(item => !pendingIds.has(item.id)), first])

      for (const item of pendingRows.slice(1)) {
        $chatMessageBodies.setKey(item.id, undefined)
      }
    }

    // 记录本批对应的存活气泡 id（合并路径只剩首条），persisted 据此精确绑定。
    submittedBubbleIds = new Set(pendingRows.map(item => item.id))

    const attachments = pendingBatch.flatMap(p => p.attachments ?? [])
    const promptText = pendingBatch.map(p => p.text).join('\n\n')

    for (const id of submittedBubbleIds) {
      const body = $chatMessageBodies.get()[id]

      if (body) {
        $chatMessageBodies.setKey(id, { ...body, editableText: promptText })
      }
    }

    const batchPayload = {
      session_id: sessionId,
      response_preference: presentationPorts().getResponsePreference(),
      batch: [
        {
          text: promptText,
          ...(attachments.length ? { attachments: attachments.map(a => ({ file_url: a.url, type: a.type })) } : {})
        }
      ]
    }

    const submittedRevision = historyEditRevision
    const submittedGeneration = submissionGeneration
    const epoch = currentClearEpoch()

    // 失败只收尾本批所属runtime；断连或账户清理后不再回写。
    const failSubmit = (err?: unknown): void => {
      if (!isCurrent() || epoch !== currentClearEpoch() || submittedGeneration !== submissionGeneration) {
        return
      }

      const sendFailed = getStrings().chat.sendFailed

      markAssistantTerminal({ error: errorMessage(err, sendFailed) })
      // thinking（50）> idle（10）：不带 force 会被优先级门控吞掉，精灵卡在思考态。
      presentationPorts().setSpriteState('idle', { force: true })
      $chatTurnInFlight.set(false)
    }

    const submitWithRetry = async (attempt = 0): Promise<void> => {
      if (!isCurrent() || submittedGeneration !== submissionGeneration) {
        return
      }

      const g = $gateway.get()

      // 首次提交前已确认连接；只有退避重试期间断连会走到这里。
      if (!g || g.connectionState !== 'open') {
        failSubmit()

        return
      }

      try {
        presentationPorts().setSpriteState('thinking')
        await g.request('prompt.submit', batchPayload)
      } catch (err: unknown) {
        const turnBusy = err instanceof SpiritAgentRpcError && err.code === SpiritAgentRpcErrorCode.TurnBusy

        if (turnBusy && historyEditRevision !== submittedRevision && $chatSessionId.get() === sessionId) {
          // 另一个窗口的编辑先被接受；本批尚未落库，回到队列等该回合结束。
          $pendingPromptBatch.set([...pendingBatch, ...$pendingPromptBatch.get()])
          submitPendingBatch()

          return
        }

        if (turnBusy && attempt < 3) {
          await sleep(50 * Math.pow(2, attempt))

          return submitWithRetry(attempt + 1)
        }

        failSubmit(err)
      }
    }

    void submitWithRetry()
  }

  function lastAssistantMessage(): {
    list: ChatMessageListItem[]
    item: ChatMessageListItem
    body: ChatMessageBody
  } | null {
    const list = $chatMessageList.get()
    const item = list.at(-1)
    const body = item?.role === 'assistant' ? $chatMessageBodies.get()[item.id] : undefined

    return item && body ? { list, item, body } : null
  }

  function beginAssistantMessage(): void {
    const last = lastAssistantMessage()

    if (last?.body.error && last.body.retryMessageId) {
      $chatMessageBodies.setKey(last.item.id, { text: '', streaming: true, toolName: null })
      $lastAssistantStreaming.set(true)

      return
    }

    if (last?.body.streaming) {
      const { body } = last

      if (!body.text.trim() && !body.toolName && !body.error && !body.cancelled) {
        return
      }

      finalizeAssistantMessage()
    }

    appendMessage({ role: 'assistant' }, { text: '', streaming: true, toolName: null })
    $lastAssistantStreaming.set(true)
  }

  function ensureAssistantMessage(): ReturnType<typeof lastAssistantMessage> {
    const last = lastAssistantMessage()

    if (last?.body.streaming) {
      return last
    }

    beginAssistantMessage()

    return lastAssistantMessage()
  }

  function patchLastAssistant(patch: (body: ChatMessageBody) => ChatMessageBody): void {
    const last = ensureAssistantMessage()

    if (!last) {
      return
    }

    $chatMessageBodies.setKey(last.item.id, patch(last.body))
    $chatStreamingTick.set($chatStreamingTick.get() + 1)
  }

  function appendAssistantDelta(text: string): void {
    // 仅更新流式 body 不动 list 引用；首个 delta 过滤前导空行，避免撑大气泡上方。
    patchLastAssistant(body => {
      const streamingText = (body.streamingText ?? body.text) + text

      return {
        ...body,
        streamingText,
        text: chatDisplayText(streamingText, true).trimStart()
      }
    })
  }

  function appendAssistantReasoningDelta(text: string): void {
    patchLastAssistant(body => ({
      ...body,
      reasoning: !body.reasoning ? text.trimStart() : body.reasoning + text
    }))
  }

  function setAssistantTool(name: string | null): void {
    const last = ensureAssistantMessage()

    if (!last) {
      return
    }

    const { body, item } = last
    const tools = name && name !== body.tools?.at(-1) ? [...(body.tools ?? []), name] : body.tools

    $chatMessageBodies.setKey(item.id, { ...body, toolName: name, tools })
  }

  function finalizeAssistantMessage(text?: string, media?: ChatMediaItem[], reasoning?: string): void {
    const last = lastAssistantMessage()

    if (!last) {
      return
    }

    const { list, item, body } = last

    const rawStr = typeof text === 'string' ? text : (body.streamingText ?? body.text)
    const finalStr = chatDisplayText(rawStr).trim()
    const finalMedia = media ?? body.media

    const finalReasoning =
      (typeof reasoning === 'string' && reasoning.trim() ? reasoning : body.reasoning)?.trim() || undefined

    const isEmpty =
      !finalStr &&
      !finalReasoning &&
      !body.toolName &&
      !body.error &&
      !body.cancelled &&
      !body.attachments?.length &&
      !finalMedia?.length

    if (isEmpty) {
      $chatMessageList.set(list.slice(0, -1))
      $chatMessageBodies.setKey(item.id, undefined)
      $lastAssistantStreaming.set(false)

      return
    }

    $chatMessageBodies.setKey(item.id, {
      ...body,
      text: finalStr,
      streamingText: undefined,
      reasoning: finalReasoning,
      media: finalMedia,
      streaming: false,
      toolName: null
    })
    $lastAssistantStreaming.set(false)
  }

  function finalizeCompanionReply(
    bubbles: CompanionBubble[],
    messageId: number,
    reasoning?: string,
    proactive = false
  ): void {
    const list = $chatMessageList.get()

    if (list.some(item => item.backendMessageId === messageId)) {
      const placeholder = list.at(-1)

      if (!proactive && placeholder?.role === 'assistant' && $chatMessageBodies.get()[placeholder.id]?.streaming) {
        $chatMessageBodies.setKey(placeholder.id, undefined)
        $chatMessageList.set(list.slice(0, -1))
        $lastAssistantStreaming.set(false)
      }

      bubbles.forEach((bubble, index) => {
        if (bubble.type === 'image' || bubble.type === 'video') {
          updateMediaBubble(messageId, bubble.media_id, bubble)
        } else {
          updateVoiceBubble(messageId, index, bubble)
        }
      })

      return
    }

    const last = list.at(-1)
    const streaming = last?.role === 'assistant' && $chatMessageBodies.get()[last.id]?.streaming
    const placeholder = !proactive && streaming
    const next = streaming ? list.slice(0, -1) : [...list]
    const voiceIds: string[] = []

    if (placeholder && last) {
      $chatMessageBodies.setKey(last.id, undefined)
    }

    bubbles.forEach((bubble, index) => {
      const id = nextChatMessageId()

      if (bubble.type === 'voice') {
        voiceIds.push(id)
      }

      next.push({
        id,
        role: 'assistant',
        backendMessageId: messageId,
        timestamp: Date.now(),
        ...(proactive ? { subtype: 'status_proactive' } : {})
      })
      $chatMessageBodies.setKey(id, {
        text: '',
        ...companionBubbleBody(bubble, messageId),
        replyIndex: index,
        streaming: false,
        toolName: null,
        ...(index === 0 ? { reasoning } : {})
      })
    })

    if (proactive && streaming && last) {
      next.push(last)
    }

    $chatMessageList.set(next)

    conversationVoiceSink().enqueue(voiceIds)

    if (!proactive) {
      $lastAssistantStreaming.set(false)
    }
  }

  function updateMediaBubble(messageId: number, mediaId: string, bubble: CompanionMediaBubble): void {
    if (bubble.media_id !== mediaId) {
      return
    }

    const key = mediaUpdateKey(messageId, mediaId)

    if (mediaUpdates.has(key)) {
      return
    }

    if (bubble.status !== 'pending') {
      mediaUpdates.set(key, bubble)
    }

    for (const item of $chatMessageList.get()) {
      const body = $chatMessageBodies.get()[item.id]

      if (item.backendMessageId !== messageId || body?.replyMedia?.media_id !== mediaId) {
        continue
      }

      if (body.replyMedia.status !== 'pending') {
        continue
      }

      $chatMessageBodies.setKey(item.id, { ...body, ...companionBubbleBody(bubble, messageId) })
    }
  }

  function updateVoiceBubble(messageId: number, index: number, bubble: CompanionBubble): void {
    if (bubble.type !== 'voice') {
      return
    }

    for (const item of $chatMessageList.get()) {
      const body = $chatMessageBodies.get()[item.id]

      if (item.backendMessageId === messageId && body?.replyIndex === index && body.replyType === 'voice') {
        $chatMessageBodies.setKey(item.id, { ...body, replyAudio: bubble.audio ?? body.replyAudio })
      }
    }
  }

  // 仅历史编辑/撤回的完整结果调用；普通水合可能截断，不能据此删除播放记录。
  function forgetDeletedVoiceMessages(messages: SessionMessage[]): void {
    conversationVoiceSink().cancel($chatSessionId.get())
    const sessionId = $chatSessionId.get()
    const remaining = new Set(messages.map(message => message.id))

    const removed = $chatMessageList
      .get()
      .flatMap(item => (item.backendMessageId && !remaining.has(item.backendMessageId) ? [item.backendMessageId] : []))

    if (sessionId) {
      removeVoicePlayback(sessionId, [...new Set(removed)])
    }
  }

  function markAssistantTerminal({
    error,
    cancelled,
    retryMessageId
  }: {
    error?: string
    cancelled?: boolean
    retryMessageId?: number
  } = {}): void {
    conversationVoiceSink().cancel($chatSessionId.get())

    const last = lastAssistantMessage()

    const terminal = {
      ...(error !== undefined && { error }),
      ...(cancelled && { cancelled: true }),
      ...(retryMessageId !== undefined && { retryMessageId })
    }

    if (last?.body.streaming) {
      const { body, item } = last
      $chatMessageBodies.setKey(item.id, {
        ...body,
        text: chatDisplayText(body.streamingText ?? body.text).trim(),
        streamingText: undefined,
        streaming: false,
        ...terminal
      })
      $lastAssistantStreaming.set(false)

      return
    }

    appendMessage({ role: 'assistant' }, { text: '', ...terminal, streaming: false, toolName: null })
    $lastAssistantStreaming.set(false)
  }

  // 重置消息列表与 bodies，不触碰 $chatSessionId 与 pending batch。
  function resetChatMessages(): void {
    historyReplacementRevision++
    mediaUpdates.clear()
    conversationVoiceSink().cancel($chatSessionId.get())
    $chatMessageList.set([])
    $chatMessageBodies.set({})
    $chatSessionPresetId.set(null)
    $lastAssistantStreaming.set(false)
    $chatTurnInFlight.set(false)
    $turnHadBubbleBreak.set(false)
  }

  const revise = (): void => $runtimeRevision.set($runtimeRevision.get() + 1)

  const revisionListeners = [
    $chatMessageList.listen(revise),
    $chatMessageBodies.listen(revise),
    $chatTurnInFlight.listen(revise)
  ]

  const captureHistorySync = (): ConversationHistorySync => ({
    revision: $runtimeRevision.get(),
    historyRevision: historyReplacementRevision,
    messages: $chatMessageList.get(),
    bodies: $chatMessageBodies.get()
  })

  const hydrateSyncedChatMessages = (
    messages: SessionMessage[],
    info?: SessionRuntimeInfo,
    snapshot?: ConversationHistorySync
  ): boolean => {
    // 编辑、撤回及清空的全量结果有独立权威，较早发起的同步不能恢复其已删除行。
    if (!isCurrent() || historyReplacementRevision !== (snapshot?.historyRevision ?? 0)) {
      return false
    }

    const liveBodies = $chatMessageBodies.get()
    const playingId = activeVoiceMessageId()
    const original = new Map(snapshot?.messages.map(item => [item.id, item]))
    const changed = (snapshot?.revision ?? 0) !== $runtimeRevision.get()

    const preserved = $chatMessageList
      .get()
      .filter(
        item =>
          item.backendMessageId === undefined ||
          item.id === playingId ||
          liveBodies[item.id]?.streaming ||
          (changed && (original.get(item.id) !== item || snapshot?.bodies[item.id] !== liveBodies[item.id]))
      )

    const preservedIds = new Set(preserved.map(item => item.id))

    const liveIds = new Set(
      preserved.flatMap(item => (item.backendMessageId === undefined ? [] : [item.backendMessageId]))
    )

    // 拆泡属于同一后端消息，不能用部分实时气泡覆盖其余气泡。
    const liveList = $chatMessageList
      .get()
      .filter(
        item => preservedIds.has(item.id) || (item.backendMessageId !== undefined && liveIds.has(item.backendMessageId))
      )

    const inFlight = $chatTurnInFlight.get()
    replaceChatMessages(messages, info, liveList.length === 0)

    if (liveList.length > 0) {
      const history = $chatMessageList.get()
      const liveGroups = new Map<number, ChatMessageListItem[]>()

      for (const item of liveList) {
        if (item.backendMessageId !== undefined) {
          const group = liveGroups.get(item.backendMessageId) ?? []
          group.push(item)
          liveGroups.set(item.backendMessageId, group)
        }
      }

      const replaced = new Set<number>()

      const merged = history.flatMap(item => {
        if (item.backendMessageId === undefined || !liveIds.has(item.backendMessageId)) {
          return [item]
        }

        if (replaced.has(item.backendMessageId)) {
          return []
        }

        replaced.add(item.backendMessageId)

        return liveGroups.get(item.backendMessageId) ?? []
      })

      const next = [
        ...merged,
        ...liveList.filter(item => item.backendMessageId === undefined || !replaced.has(item.backendMessageId))
      ]

      const hydratedBodies = $chatMessageBodies.get()
      $chatMessageBodies.set(
        Object.fromEntries(next.map(item => [item.id, liveBodies[item.id] ?? hydratedBodies[item.id]]))
      )
      $chatMessageList.set(next)
      $lastAssistantStreaming.set(liveList.some(item => liveBodies[item.id]?.streaming))
    }

    $chatTurnInFlight.set(changed ? inFlight : (info?.running ?? inFlight))

    return true
  }

  const abandonDisconnectedTurn = (): void => {
    submissionGeneration++
    const unfinished = $chatTurnInFlight.get() || $lastAssistantStreaming.get() || $pendingPromptBatch.get().length > 0
    cancelPendingFlush()
    clearPendingPrompts()

    if (unfinished) {
      markAssistantTerminal({ error: getStrings().chat.connectionInterrupted })
    }

    $chatTurnInFlight.set(false)
  }

  const dispose = (): void => {
    alive = false
    cancelPendingFlush()
    revisionListeners.forEach(stop => stop())
    releaseVoicePlaybackStore(sessionId)
  }

  return {
    $chatMessageList,
    $chatMessageBodies,
    $lastAssistantStreaming,
    $chatStreamingTick,
    $chatSessionId,
    $chatSessionKind,
    $chatSessionPresetId,
    $chatSessionReadOnly,
    $pendingPromptBatch,
    $chatTurnInFlight,
    $lastEditableUserMessage,
    $retryableAssistantMessage,
    retryAssistantReply,
    $turnHadBubbleBreak,
    $sessionSettings,
    hydrateSessionSettings,
    updateSessionSetting,
    $sessionContextUsage,
    setSessionContextUsage,
    resetSessionContextUsage,
    hydrateEditedChatMessages,
    hydrateChatMessages,
    pushProactiveMessage,
    pushMediaMessage,
    pushUserMessage,
    bindTrailingUserMessageIds,
    bindTrailingAssistantMessageId,
    pushStatusPill,
    pushPendingPrompt,
    clearPendingPrompts,
    setTurnHadBubbleBreak,
    schedulePendingFlush,
    cancelPendingFlush,
    submitPendingBatch,
    beginAssistantMessage,
    appendAssistantDelta,
    appendAssistantReasoningDelta,
    setAssistantTool,
    finalizeAssistantMessage,
    finalizeCompanionReply,
    updateMediaBubble,
    updateVoiceBubble,
    forgetDeletedVoiceMessages,
    markAssistantTerminal,
    resetChatMessages,
    isCurrent,
    dispose,
    $historyHydrated,
    $runtimeRevision,
    captureHistorySync,
    hydrateSyncedChatMessages,
    abandonDisconnectedTurn
  }
}

export type ConversationRuntime = ReturnType<typeof createConversationRuntime>
