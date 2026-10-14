import type { CompanionBubble, SessionMessage } from '@protocol'

import { chatDisplayText } from './chat-display-text'
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
    projectBubble: (bubble: CompanionBubble, messageId?: number) => Partial<ChatMessageBody>
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
        subtype: m.subtype,
        backendMessageId: typeof m.id === 'number' ? m.id : undefined,
        timestamp: m.timestamp
      })

      // 拆分后附件只挂首个气泡（附件伴随连发首条发出，每段都挂会重复渲染媒体卡）。
      bodies[id] = {
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
  history: ChatMessageListItem[],
  liveList: ChatMessageListItem[]
): ChatMessageListItem[] {
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
    if (item.backendMessageId === undefined || !liveGroups.has(item.backendMessageId)) {
      return [item]
    }

    if (replaced.has(item.backendMessageId)) {
      return []
    }

    replaced.add(item.backendMessageId)

    return liveGroups.get(item.backendMessageId) ?? []
  })

  return [
    ...merged,
    ...liveList.filter(item => item.backendMessageId === undefined || !replaced.has(item.backendMessageId))
  ]
}

export function mergeIncrementalHistoryRows(
  original: ChatMessageListItem[],
  incoming: ChatMessageListItem[],
  incomingIds: ReadonlySet<number | undefined>
): ChatMessageListItem[] {
  const persisted = [
    ...original.filter(item => item.backendMessageId !== undefined && !incomingIds.has(item.backendMessageId)),
    ...incoming
  ]

  persisted.sort((a, b) => (a.backendMessageId ?? 0) - (b.backendMessageId ?? 0))

  return [...persisted, ...original.filter(item => item.backendMessageId === undefined)]
}
