import type { CompanionBubble, CompanionMediaBubble, SessionMessage, VideoQueueState } from '@protocol'

import { conversationVoiceSink } from '../voice-link'
import { removeVoicePlayback } from '../voice-playback'

import type { ProjectionContext } from './context'

/** 媒体与语音气泡的终态更新：重复完成事件的空音频不能覆盖已就绪内容。 */
export function installMediaBubble(ctx: ProjectionContext): void {
  const { $chatMessageList, $chatMessageBodies, $chatSessionId } = ctx.state.stores

  function updateVideoQueue(mediaId: string, videoQueue: VideoQueueState | null): void {
    ctx.videoQueueUpdates.set(mediaId, videoQueue)

    for (const item of $chatMessageList.get()) {
      const body = $chatMessageBodies.get()[item.id]

      if (body?.replyMedia?.media_id === mediaId && body.replyMedia.status === 'pending') {
        $chatMessageBodies.setKey(item.id, { ...body, replyMedia: { ...body.replyMedia, video_queue: videoQueue } })
      }
    }
  }

  function updateMediaBubble(messageId: string, mediaId: string, bubble: CompanionMediaBubble): void {
    if (bubble.media_id !== mediaId) {
      return
    }

    const key = ctx.mediaUpdateKey(messageId, mediaId)
    const previous = ctx.mediaUpdates.get(key)

    if (previous && (previous.status === 'ready' || previous.media_revision >= bubble.media_revision)) {
      return
    }

    ctx.mediaUpdates.set(key, bubble)

    for (const item of $chatMessageList.get()) {
      const body = $chatMessageBodies.get()[item.id]

      if (item.backendMessageId !== messageId || body?.replyMedia?.media_id !== mediaId) {
        continue
      }

      if (body.replyMedia.status === 'ready' || body.replyMedia.media_revision >= bubble.media_revision) {
        continue
      }

      $chatMessageBodies.setKey(item.id, { ...body, ...ctx.companionBubbleBody(bubble, messageId) })
    }
  }

  function updateVoiceBubble(messageId: string, index: number, bubble: CompanionBubble): void {
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

  Object.assign(ctx, {
    updateVideoQueue,
    updateMediaBubble,
    updateVoiceBubble,
    forgetDeletedVoiceMessages
  })
}
