import type { ChatAttachment, ChatMediaItem } from '@protocol'

import type { ChatMessageListItem } from '../chat-runtime-types'

import type { ProjectionContext } from './context'

/** 用户消息与历史对账：活气泡按后端消息快照替换，保持历史位置。 */
export function installUserMessage(ctx: ProjectionContext): void {
  const { $chatMessageBodies, $chatMessageList, $chatSessionPresetId } = ctx.state.stores

  // 后台视频完成的实时送达行，只带媒体；历史水合的同类 system 行同样不显示正文。
  function pushMediaMessage(media: ChatMediaItem[]): string {
    return ctx.appendMessage(
      { role: 'assistant', subtype: 'status_media' },
      { text: '', media, streaming: false, toolName: null }
    )
  }

  function pushUserMessage(text: string, attachments?: ChatAttachment[]): string {
    return ctx.appendMessage(
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
    const groups = new Map<string, ChatMessageListItem[]>()

    for (const item of list) {
      if (boundIds.has(item.id) && item.backendMessageId !== undefined) {
        const group = groups.get(item.backendMessageId) ?? []
        group.push(item)
        groups.set(item.backendMessageId, group)
      }
    }

    const inserted = new Set<string>()

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

  Object.assign(ctx, {
    pushMediaMessage,
    pushUserMessage,
    splitUserBubblesEnabled,
    reconcilePersistedMessages
  })
}
