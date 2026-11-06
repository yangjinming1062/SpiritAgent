import type { CompanionBubble } from '@protocol'

import type { ChatMessageBody, ChatMessageListItem } from '../chat-runtime-types'
import { nextChatMessageId } from '../conversation-state'

import type { ProjectionShared, RowWriter } from './context'

/** 行与 body 的写入原语：先写 body 再入列，保证列表订阅者取 body 时已存在。 */
export function createRowWriter(shared: ProjectionShared): RowWriter {
  const { $chatMessageBodies, $chatMessageList } = shared.state.stores

  const mediaUpdateKey = (messageId: string, mediaId: string): string => `${messageId}:${mediaId}`

  function companionBubbleBody(bubble: CompanionBubble, messageId?: string): Partial<ChatMessageBody> {
    if (bubble.type === 'link') {
      return { text: `${bubble.title}\n${bubble.url}`, replyType: 'link', replyLink: bubble }
    }

    if (bubble.type === 'image' || bubble.type === 'video') {
      const current =
        messageId === undefined ? undefined : shared.mediaUpdates.get(mediaUpdateKey(messageId, bubble.media_id))

      const media =
        current ??
        (bubble.status === 'pending' && shared.videoQueueUpdates.has(bubble.media_id)
          ? { ...bubble, video_queue: shared.videoQueueUpdates.get(bubble.media_id) ?? null }
          : bubble)

      if (messageId !== undefined && media.status !== 'pending') {
        shared.mediaUpdates.set(mediaUpdateKey(messageId, media.media_id), media)
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

  function activeReplyInsertIndex(list: ChatMessageListItem[]): number | null {
    const last = list.at(-1)

    if (last?.role === 'assistant' && $chatMessageBodies.get()[last.id]?.streaming) {
      return list.length - 1
    }

    const inflight = shared.inflight.value

    if (inflight) {
      const idx = list.findIndex(item => item.id === inflight.firstId)

      return idx >= 0 ? idx : null
    }

    return null
  }

  function appendMessage(item: Omit<ChatMessageListItem, 'id' | 'timestamp'>, body: ChatMessageBody): string {
    const id = nextChatMessageId()
    $chatMessageBodies.setKey(
      id,
      item.role === 'assistant' && !item.subtype && shared.state.submission.activeRequestId
        ? { ...body, requestId: shared.state.submission.activeRequestId }
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

  // 同消息气泡组内按 replyIndex 空位插入（完成帧对账补建）；无组时落到在途回复位置。
  function companionBubbleInsertAt(list: ChatMessageListItem[], messageId: string, index: number): number {
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

  return {
    mediaUpdateKey,
    companionBubbleBody,
    appendMessage,
    activeReplyInsertIndex,
    companionBubbleInsertAt
  }
}
