import type { ChatAttachment, ChatMediaItem, CompanionBubble, CompanionMediaBubble, SessionMessage } from '@protocol'

import { chatDisplayText } from './chat-display-text'
import { isPositiveInt } from './chat-message-rules'
import type { ConversationState } from './chat-runtime-state'
import type { ChatMessageBody, ChatMessageListItem } from './chat-runtime-types'
import { nextChatMessageId } from './conversation-state'
import { conversationVoiceSink } from './voice-link'
import { removeVoicePlayback } from './voice-playback'

export function createChatMessageController(state: ConversationState) {
  const {
    $chatMessageList,
    $chatMessageBodies,
    $lastAssistantStreaming,
    $chatStreamingTick,
    $chatSessionId,
    $chatSessionPresetId,
    $turnHadBubbleBreak
  } = state.stores

  // 媒体终态可能早于完成帧；保留更新，避免迟到的等待快照把已就绪卡片覆盖回去。
  const mediaUpdates = new Map<string, CompanionMediaBubble>()

  const mediaUpdateKey = (messageId: number, mediaId: string): string => `${messageId}:${mediaId}`

  // 逐泡交付中的回复组（首泡行 id）：后台交付插入与完成帧对账的定位锚点。
  let inflightReply: { messageId: number; firstId: string } | null = null

  function companionBubbleBody(bubble: CompanionBubble, messageId?: number): Partial<ChatMessageBody> {
    if (bubble.type === 'link') {
      return { text: `${bubble.title}\n${bubble.url}`, replyType: 'link', replyLink: bubble }
    }

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

  // 先写 body 再入列：列表订阅者据 id 取 body 时必须已存在。
  function appendMessage(item: Omit<ChatMessageListItem, 'id' | 'timestamp'>, body: ChatMessageBody): string {
    const id = nextChatMessageId()
    $chatMessageBodies.setKey(
      id,
      item.role === 'assistant' && !item.subtype && state.submission.activeRequestId
        ? { ...body, requestId: state.submission.activeRequestId }
        : body
    )
    const list = $chatMessageList.get()
    const next = { id, ...item, timestamp: Date.now() }

    // 后台交付插在活动回复（流式等待气泡或在途逐泡回复组）之前，保持回复组连续。
    const insertAt = item.subtype ? activeReplyInsertIndex(list) : null
    $chatMessageList.set(
      insertAt === null ? [...list, next] : [...list.slice(0, insertAt), next, ...list.slice(insertAt)]
    )

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

  // 陪伴会话连发用户消息保留独立气泡（与 bubble.break 助手拆分对称），专业对话整段阅读不拆，避免误拆粘贴的多段内容。
  function splitUserBubblesEnabled(): boolean {
    return $chatSessionPresetId.get() === 'companion'
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

  function setTurnHadBubbleBreak(v: boolean): void {
    $turnHadBubbleBreak.set(v)
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
    inflightReply = null
    const last = lastAssistantMessage()

    if (last?.body.error && last.body.retryMessageId) {
      $chatMessageBodies.setKey(last.item.id, {
        text: '',
        streaming: true,
        toolName: null,
        requestId: state.submission.activeRequestId ?? undefined
      })
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

    appendMessage(
      { role: 'assistant' },
      { text: '', streaming: true, toolName: null, requestId: state.submission.activeRequestId ?? undefined }
    )
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

  // 在途回复组位置：流式等待气泡或逐泡交付中回复组的首泡。后台 subtype 行插到其前，保持回复组连续。
  function activeReplyInsertIndex(list: ChatMessageListItem[]): number | null {
    const last = list.at(-1)

    if (last?.role === 'assistant' && $chatMessageBodies.get()[last.id]?.streaming) {
      return list.length - 1
    }

    const inflight = inflightReply

    if (inflight) {
      const idx = list.findIndex(item => item.id === inflight.firstId)

      return idx >= 0 ? idx : null
    }

    return null
  }

  // 同消息气泡组内按 replyIndex 空位插入（完成帧对账补建）；无组时落到在途回复位置。
  function companionBubbleInsertAt(list: ChatMessageListItem[], messageId: number, index: number): number {
    let groupStart = -1
    let insertAt = -1

    for (let i = 0; i < list.length; i += 1) {
      const body = $chatMessageBodies.get()[list[i].id]

      if (list[i].backendMessageId !== messageId || body?.replyIndex === undefined) {
        continue
      }

      if (groupStart < 0) {
        groupStart = i
        insertAt = i - 1
      }

      if (body.replyIndex < index) {
        insertAt = i
      }
    }

    return groupStart >= 0 ? insertAt + 1 : (activeReplyInsertIndex(list) ?? list.length)
  }

  function insertCompanionBubbleRow(
    list: ChatMessageListItem[],
    messageId: number,
    index: number,
    bubble: CompanionBubble,
    extras?: { reasoning?: string; proactive?: boolean }
  ): string {
    const id = nextChatMessageId()

    list.splice(companionBubbleInsertAt(list, messageId, index), 0, {
      id,
      role: 'assistant',
      backendMessageId: messageId,
      timestamp: Date.now(),
      ...(extras?.proactive ? { subtype: 'status_proactive' } : {})
    })
    $chatMessageBodies.setKey(id, {
      text: '',
      ...companionBubbleBody(bubble, messageId),
      replyIndex: index,
      streaming: false,
      toolName: null,
      ...(extras?.reasoning ? { reasoning: extras.reasoning } : {})
    })

    return id
  }

  // 已存在的气泡按补丁处理（与 message.voice 同语义）；返回是否命中。
  function patchCompanionBubble(messageId: number, index: number, bubble: CompanionBubble): boolean {
    for (const item of $chatMessageList.get()) {
      const body = $chatMessageBodies.get()[item.id]

      if (item.backendMessageId !== messageId || body?.replyIndex !== index) {
        continue
      }

      if (bubble.type === 'image' || bubble.type === 'video') {
        updateMediaBubble(messageId, bubble.media_id, bubble)
      } else if (bubble.type === 'link') {
        $chatMessageBodies.setKey(item.id, { ...body, ...companionBubbleBody(bubble, messageId) })
      } else {
        updateVoiceBubble(messageId, index, bubble)
      }

      return true
    }

    return false
  }

  // 逐泡交付（message.bubble）：按到达顺序追加单个气泡，重复到达按补丁处理；首个气泡替换流式等待气泡。
  function appendCompanionBubble(messageId: number, index: number, bubble: CompanionBubble): void {
    if (patchCompanionBubble(messageId, index, bubble)) {
      return
    }

    const list = $chatMessageList.get()
    const last = list.at(-1)
    const streaming = last?.role === 'assistant' && $chatMessageBodies.get()[last.id]?.streaming
    const next = streaming ? list.slice(0, -1) : [...list]

    if (streaming && last) {
      $chatMessageBodies.setKey(last.id, undefined)
    }

    const id = insertCompanionBubbleRow(next, messageId, index, bubble)

    if (!inflightReply || inflightReply.messageId !== messageId) {
      inflightReply = { messageId, firstId: id }
    }

    $chatMessageList.set(next)
    $lastAssistantStreaming.set(false)

    if (bubble.type === 'voice') {
      conversationVoiceSink().enqueue([id])
    }
  }

  function finalizeCompanionReply(
    bubbles: CompanionBubble[],
    messageId: number,
    reasoning?: string,
    proactive = false
  ): void {
    if (inflightReply?.messageId === messageId) {
      inflightReply = null
    }

    const list = $chatMessageList.get()

    if (list.some(item => item.backendMessageId === messageId)) {
      const placeholder = list.at(-1)

      if (!proactive && placeholder?.role === 'assistant' && $chatMessageBodies.get()[placeholder.id]?.streaming) {
        $chatMessageBodies.setKey(placeholder.id, undefined)
        $chatMessageList.set(list.slice(0, -1))
        $lastAssistantStreaming.set(false)
      }

      // 已逐泡交付的气泡按补丁对账；丢帧缺失的下标按 replyIndex 空位补建（PROTOCOL「完整气泡对账」）。
      const voiceIds: string[] = []

      bubbles.forEach((bubble, index) => {
        if (patchCompanionBubble(messageId, index, bubble)) {
          return
        }

        const next = [...$chatMessageList.get()]
        const id = insertCompanionBubbleRow(next, messageId, index, bubble)

        $chatMessageList.set(next)

        if (bubble.type === 'voice') {
          voiceIds.push(id)
        }
      })

      conversationVoiceSink().enqueue(voiceIds)

      // 逐泡交付先建气泡、reasoning 随完成帧到达：挂到首个气泡，与整体交付一致。
      if (reasoning) {
        const first = $chatMessageList.get().find(item => {
          const body = $chatMessageBodies.get()[item.id]

          return item.backendMessageId === messageId && body?.replyIndex === 0
        })

        const body = first ? $chatMessageBodies.get()[first.id] : undefined

        if (first && body && !body.reasoning) {
          $chatMessageBodies.setKey(first.id, { ...body, reasoning })
        }
      }

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
      const id = insertCompanionBubbleRow(next, messageId, index, bubble, {
        proactive,
        reasoning: index === 0 ? reasoning : undefined
      })

      if (bubble.type === 'voice') {
        voiceIds.push(id)
      }
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
    retryMessageId,
    beforePending = false
  }: {
    error?: string
    cancelled?: boolean
    retryMessageId?: number
    beforePending?: boolean
  } = {}): void {
    inflightReply = null
    conversationVoiceSink().cancel($chatSessionId.get())

    const list = $chatMessageList.get()

    const existing = state.submission.activeRequestId
      ? list.findLast(item => $chatMessageBodies.get()[item.id]?.requestId === state.submission.activeRequestId)
      : undefined

    const last = lastAssistantMessage()

    const terminal = {
      ...(state.submission.activeRequestId && { requestId: state.submission.activeRequestId }),
      ...(error !== undefined && { error }),
      ...(cancelled && { cancelled: true }),
      ...(retryMessageId !== undefined && { retryMessageId })
    }

    if (existing && !$chatMessageBodies.get()[existing.id]?.streaming) {
      $chatMessageBodies.setKey(existing.id, { ...$chatMessageBodies.get()[existing.id], ...terminal })

      return
    }

    if (
      last &&
      !last.body.streaming &&
      ((error !== undefined && last.body.error === error && last.body.retryMessageId === retryMessageId) ||
        (cancelled && last.body.cancelled))
    ) {
      return
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

    const id = appendMessage({ role: 'assistant' }, { text: '', ...terminal, streaming: false, toolName: null })
    const updated = $chatMessageList.get()
    const pendingIndex = updated.findIndex(item => item.role === 'user' && item.backendMessageId === undefined)

    if (beforePending && pendingIndex >= 0) {
      const terminalRow = updated.find(item => item.id === id)

      if (terminalRow) {
        $chatMessageList.set([...updated.slice(0, pendingIndex), terminalRow, ...updated.slice(pendingIndex, -1)])
      }
    }

    $lastAssistantStreaming.set(false)
  }

  const clearReplyState = (): void => {
    mediaUpdates.clear()
    inflightReply = null
  }

  const clearInflightReply = (): void => {
    inflightReply = null
  }

  return {
    api: {
      pushProactiveMessage,
      pushMediaMessage,
      pushUserMessage,
      bindTrailingAssistantMessageId,
      pushStatusPill,
      setTurnHadBubbleBreak,
      beginAssistantMessage,
      appendAssistantDelta,
      appendAssistantReasoningDelta,
      setAssistantTool,
      finalizeAssistantMessage,
      appendCompanionBubble,
      finalizeCompanionReply,
      updateMediaBubble,
      updateVoiceBubble,
      forgetDeletedVoiceMessages,
      markAssistantTerminal
    },
    internal: {
      companionBubbleBody,
      appendMessage,
      splitUserBubblesEnabled,
      reconcilePersistedMessages,
      clearReplyState,
      clearInflightReply
    }
  }
}

export type ChatMessageController = ReturnType<typeof createChatMessageController>
