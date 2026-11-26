import type { CompanionBubble, SessionMessage } from '@protocol'

import { isResourceId } from '../../../../shared/resource-id'

import { extractMessageContent, splitUserText } from './chat-message-rules'
import type { ChatMessageBody, ChatMessageListItem, ConversationHistorySync } from './chat-runtime-types'

// 投影只消费历史与规则；调用方选择会话拆泡策略、ID 分配和已收到的媒体终态。
export function projectSessionMessages(
  messages: SessionMessage[],
  {
    splitUserBubbles,
    nextMessageId,
    projectBubble
  }: {
    splitUserBubbles: boolean
    nextMessageId: () => string
    projectBubble: (bubble: CompanionBubble, messageId?: string) => Partial<ChatMessageBody>
  }
) {
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

    const id = nextMessageId()
    items.push({ id, role: 'assistant', timestamp })
    bodies[id] = { text: '', reasoning, streaming: false, toolName: null }
  }

  for (const m of messages) {
    if (m.role === 'tool') {
      continue
    }

    const companionBubbles = m.role === 'assistant' && m.content_type === 'companion_reply' ? m.bubbles : undefined

    const { text: textContent, attachments } = extractMessageContent(m)

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

    // 陪伴用户行按空行拆分（与实时呈现对齐），专业对话整段阅读不拆。
    const canSplit = !m.subtype && m.role === 'user' && splitUserBubbles
    // 后台视频送达的 system 行正文是给模型的任务记录，与实时送达一致只显示媒体卡。
    const hideText = m.role === 'system' && m.subtype === 'status_media'

    const segments = companionBubbles
      ? companionBubbles.map(bubble => ('text' in bubble ? bubble.text : ''))
      : canSplit
        ? splitUserText(textContent)
        : [hideText ? '' : textContent]

    if (segments.length === 0) {
      segments.push('')
    }

    for (const [index, segment] of segments.entries()) {
      const id = nextMessageId()

      items.push({
        id,
        role: m.role === 'user' ? 'user' : 'assistant',
        // 普通消息的空 subtype 与实时消息统一，供编辑资格判断使用。
        subtype: m.subtype || undefined,
        backendMessageId: isResourceId(m.id) ? m.id : undefined,
        timestamp: m.timestamp
      })

      // 拆分后附件只挂首个气泡（附件伴随连发首条发出，每段都挂会重复渲染媒体卡）。
      bodies[id] = {
        callDurationMs: m.content_type === 'text' ? (m.call_duration_ms ?? undefined) : undefined,
        canReplaceReply: m.can_replace_reply === true && index === segments.length - 1,
        text: segment,
        editableText: m.role === 'user' ? textContent : undefined,
        replyIndex: companionBubbles ? index : undefined,
        ...(companionBubbles?.[index] ? projectBubble(companionBubbles[index], m.id) : {}),
        reasoning: m.role === 'assistant' && index === 0 ? takeReasoning(reasoningContent || undefined) : undefined,
        toolName: m.tool_name ?? null,
        tools: m.tool_name ? [m.tool_name] : undefined,
        streaming: false,
        attachments: index === 0 ? attachments : undefined,
        ...(!companionBubbles && index === segments.length - 1 && m.media?.length ? { media: m.media } : {})
      }
    }
  }

  flushPendingReasoning()

  return { items, bodies, totalChars }
}

export function preserveLiveHistoryRows(
  list: ChatMessageListItem[],
  bodies: Record<string, ChatMessageBody>,
  snapshot: ConversationHistorySync | undefined,
  playingId: string | null,
  revision: number
) {
  const original = new Map(snapshot?.messages.map(item => [item.id, item]))
  const changed = (snapshot?.revision ?? 0) !== revision

  const preserved = list.filter(
    item =>
      item.backendMessageId === undefined ||
      item.id === playingId ||
      bodies[item.id]?.streaming ||
      (changed && (original.get(item.id) !== item || snapshot?.bodies[item.id] !== bodies[item.id]))
  )

  const preservedIds = new Set(preserved.map(item => item.id))

  const liveIds = new Set(
    preserved.flatMap(item => (item.backendMessageId === undefined ? [] : [item.backendMessageId]))
  )

  // 拆泡属于同一后端消息，保留任一行时须保留其完整气泡组。
  return {
    changed,
    liveList: list.filter(
      item => preservedIds.has(item.id) || (item.backendMessageId !== undefined && liveIds.has(item.backendMessageId))
    )
  }
}

export function mergeLiveHistoryRows(
  history: ReturnType<typeof projectSessionMessages>,
  liveList: ChatMessageListItem[],
  liveBodies: Record<string, ChatMessageBody>
): ReturnType<typeof projectSessionMessages> {
  const liveGroups = new Map<string, ChatMessageListItem[]>()

  for (const item of liveList) {
    if (item.backendMessageId !== undefined) {
      const group = liveGroups.get(item.backendMessageId) ?? []
      group.push(item)
      liveGroups.set(item.backendMessageId, group)
    }
  }

  const savedReplyGroups = new Map<string, Map<number, ChatMessageListItem>>()

  for (const item of history.items) {
    const messageId = item.backendMessageId
    const index = history.bodies[item.id].replyIndex

    if (messageId && liveGroups.has(messageId) && index !== undefined) {
      const group = savedReplyGroups.get(messageId) ?? new Map<number, ChatMessageListItem>()
      group.set(index, item)
      savedReplyGroups.set(messageId, group)
    }
  }

  const replaced = new Set<string>()
  const mergedBodies: Record<string, ChatMessageBody> = {}

  const merged = history.items.flatMap(item => {
    const messageId = item.backendMessageId
    const liveGroup = messageId === undefined ? undefined : liveGroups.get(messageId)

    if (messageId === undefined || !liveGroup) {
      return [item]
    }

    if (replaced.has(messageId)) {
      return []
    }

    replaced.add(messageId)
    const reply = savedReplyGroups.get(messageId)

    if (!reply) {
      return liveGroup
    }

    // 快照补齐持久回复的气泡，已交付的行沿用原 ID，避免打断正在播放的语音。
    for (const live of liveGroup) {
      const body = liveBodies[live.id]
      const index = body.replyIndex

      if (index === undefined) {
        continue
      }

      const saved = reply.get(index)

      if (saved) {
        const projected = history.bodies[saved.id]
        mergedBodies[live.id] = {
          ...projected,
          reasoning: projected.reasoning ?? body.reasoning,
          ...(projected.replyType === 'voice' && body.replyType === 'voice' && !projected.replyAudio
            ? { replyAudio: body.replyAudio }
            : {})
        }
      }

      reply.set(index, saved ? { ...saved, id: live.id, timestamp: saved.timestamp ?? live.timestamp } : live)
    }

    return [...reply.entries()].sort(([left], [right]) => left - right).map(([, row]) => row)
  })

  const items = [
    ...merged,
    ...liveList.filter(item => item.backendMessageId === undefined || !replaced.has(item.backendMessageId))
  ]

  const bodies = Object.fromEntries(
    items.map(item => [item.id, mergedBodies[item.id] ?? liveBodies[item.id] ?? history.bodies[item.id]])
  )

  return { ...history, items, bodies }
}
