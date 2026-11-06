import type { ConversationState } from './chat-runtime-state'
import { installAssistant } from './projection/assistant'
import { installBase } from './projection/base'
import { installCompanionBubble } from './projection/companion-bubble'
import type { ProjectionContext } from './projection/context'
import { installMediaBubble } from './projection/media-bubble'
import { installTerminal } from './projection/terminal'
import { installUserMessage } from './projection/user-message'

/** 消息投影控制器：装配写入、用户消息、助手回复、逐泡气泡、媒体与终态模块。 */
export function createChatMessageController(state: ConversationState) {
  const ctx = {
    state,
    mediaUpdates: new Map(),
    videoQueueUpdates: new Map(),
    inflight: { value: null }
  } as unknown as ProjectionContext

  installBase(ctx)
  installUserMessage(ctx)
  installAssistant(ctx)
  installCompanionBubble(ctx)
  installMediaBubble(ctx)
  installTerminal(ctx)

  return {
    api: {
      pushMediaMessage: ctx.pushMediaMessage,
      pushUserMessage: ctx.pushUserMessage,
      bindTrailingAssistantMessageId: ctx.bindTrailingAssistantMessageId,
      pushStatusPill: ctx.pushStatusPill,
      setTurnHadBubbleBreak: ctx.setTurnHadBubbleBreak,
      beginAssistantMessage: ctx.beginAssistantMessage,
      appendAssistantDelta: ctx.appendAssistantDelta,
      appendAssistantReasoningDelta: ctx.appendAssistantReasoningDelta,
      setAssistantTool: ctx.setAssistantTool,
      finalizeAssistantMessage: ctx.finalizeAssistantMessage,
      appendCompanionBubble: ctx.appendCompanionBubble,
      finalizeCompanionReply: ctx.finalizeCompanionReply,
      updateMediaBubble: ctx.updateMediaBubble,
      updateVideoQueue: ctx.updateVideoQueue,
      updateVoiceBubble: ctx.updateVoiceBubble,
      forgetDeletedVoiceMessages: ctx.forgetDeletedVoiceMessages,
      markAssistantTerminal: ctx.markAssistantTerminal
    },
    internal: {
      companionBubbleBody: ctx.companionBubbleBody,
      appendMessage: ctx.appendMessage,
      splitUserBubblesEnabled: ctx.splitUserBubblesEnabled,
      reconcilePersistedMessages: ctx.reconcilePersistedMessages,
      clearReplyState: ctx.clearReplyState,
      clearInflightReply: ctx.clearInflightReply
    }
  }
}

export type ChatMessageController = ReturnType<typeof createChatMessageController>
