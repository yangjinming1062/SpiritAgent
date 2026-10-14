import { computed, type ReadableAtom } from 'nanostores'

import type { ChatMessageBody, ChatMessageListItem } from './chat-runtime-types'

export type ConversationVariant = 'companion' | 'assistant'
export const SYSTEM_PILL_SUBTYPES = new Set(['status_cleared', 'status_command_result', 'status_media_failed'])

export function isMessageGroupRow(item: ChatMessageListItem): boolean {
  return (
    item.subtype !== 'compress_summary' &&
    item.subtype !== 'status_media' &&
    !SYSTEM_PILL_SUBTYPES.has(item.subtype ?? '')
  )
}

export function isMessageVisible(
  item: ChatMessageListItem,
  body: ChatMessageBody | undefined,
  variant: ConversationVariant
): boolean {
  if (!body) {
    return false
  }

  if (!isMessageGroupRow(item) || item.role === 'user') {
    return true
  }

  const text = body.text.trim()
  const tools = body.tools?.length || body.toolName
  const toolOnly = tools && !text && !body.error && !body.cancelled

  if (variant === 'companion' && toolOnly && !body.streaming) {
    return false
  }

  return Boolean(
    text ||
    body.streaming ||
    body.attachments?.length ||
    body.media?.length ||
    body.replyMedia ||
    body.error ||
    body.cancelled ||
    (variant === 'assistant' && body.reasoning?.trim())
  )
}

// 仅可见行的身份或顺序变化才通知列表；token 和播放进度仍只刷新对应气泡。
export function createVisibleMessageList(
  $list: ReadableAtom<ChatMessageListItem[]>,
  $bodies: ReadableAtom<Record<string, ChatMessageBody>>,
  variant: ConversationVariant
) {
  let previous: ChatMessageListItem[] = []

  return computed([$list, $bodies], (list, bodies) => {
    const next = list.filter(item => isMessageVisible(item, bodies[item.id], variant))

    if (next.length !== previous.length || next.some((item, index) => item !== previous[index])) {
      previous = next
    }

    return previous
  })
}
