import type { CompanionBubble } from '@protocol'

import type { ChatMessageListItem } from '../chat-runtime-types'
import { nextChatMessageId } from '../conversation-state'
import { conversationVoiceSink } from '../voice-link'

import type { ProjectionContext } from './context'

/** 逐泡交付：按到达顺序插入气泡，重复到达按补丁处理，完成帧做空位对账。 */
export function installCompanionBubble(ctx: ProjectionContext): void {
  const { $chatMessageBodies, $chatMessageList, $lastAssistantStreaming } = ctx.state.stores

  function insertCompanionBubbleRow(
    list: ChatMessageListItem[],
    messageId: string,
    index: number,
    bubble: CompanionBubble,
    extras?: { reasoning?: string; proactive?: boolean }
  ): string {
    const id = nextChatMessageId()

    list.splice(ctx.companionBubbleInsertAt(list, messageId, index), 0, {
      id,
      role: 'assistant',
      backendMessageId: messageId,
      timestamp: Date.now(),
      ...(extras?.proactive ? { subtype: 'status_proactive' } : {})
    })
    $chatMessageBodies.setKey(id, {
      text: '',
      ...ctx.companionBubbleBody(bubble, messageId),
      replyIndex: index,
      streaming: false,
      toolName: null,
      ...(extras?.reasoning ? { reasoning: extras.reasoning } : {})
    })

    return id
  }

  // 已存在的气泡按补丁处理（与 message.voice 同语义）；返回是否命中。
  function patchCompanionBubble(messageId: string, index: number, bubble: CompanionBubble): boolean {
    for (const item of $chatMessageList.get()) {
      const body = $chatMessageBodies.get()[item.id]

      if (item.backendMessageId !== messageId || body?.replyIndex !== index) {
        continue
      }

      if (bubble.type === 'image' || bubble.type === 'video') {
        ctx.updateMediaBubble(messageId, bubble.media_id, bubble)
      } else if (bubble.type === 'link') {
        $chatMessageBodies.setKey(item.id, { ...body, ...ctx.companionBubbleBody(bubble, messageId) })
      } else {
        ctx.updateVoiceBubble(messageId, index, bubble)
      }

      return true
    }

    return false
  }

  // 逐泡交付（message.bubble）：按到达顺序追加单个气泡，重复到达按补丁处理；首个气泡替换流式等待气泡。
  function appendCompanionBubble(messageId: string, index: number, bubble: CompanionBubble): void {
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

    if (!ctx.inflight.value || ctx.inflight.value.messageId !== messageId) {
      ctx.inflight.value = { messageId, firstId: id }
    }

    $chatMessageList.set(next)
    $lastAssistantStreaming.set(false)

    if (bubble.type === 'voice') {
      conversationVoiceSink().enqueue([id])
    }
  }

  function finalizeCompanionReply(
    bubbles: CompanionBubble[],
    messageId: string,
    reasoning?: string,
    proactive = false
  ): void {
    if (ctx.inflight.value?.messageId === messageId) {
      ctx.inflight.value = null
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

  Object.assign(ctx, {
    insertCompanionBubbleRow,
    patchCompanionBubble,
    appendCompanionBubble,
    finalizeCompanionReply
  })
}
