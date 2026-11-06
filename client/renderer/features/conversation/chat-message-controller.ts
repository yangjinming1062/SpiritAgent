import type { ConversationState } from './chat-runtime-state'
import { createAssistantProjection } from './projection/assistant'
import { createRowWriter } from './projection/base'
import { createCompanionBubbleProjection } from './projection/companion-bubble'
import type { ProjectionShared } from './projection/context'
import { createMediaProjection } from './projection/media-bubble'
import { createTerminalProjection } from './projection/terminal'
import { createUserMessageProjection } from './projection/user-message'

/** 消息投影控制器：按依赖顺序装配写入、用户消息、助手回复、逐泡气泡、媒体与终态能力。 */
export function createChatMessageController(state: ConversationState) {
  const shared: ProjectionShared = {
    state,
    mediaUpdates: new Map(),
    videoQueueUpdates: new Map(),
    inflight: { value: null }
  }

  const rows = createRowWriter(shared)
  const users = createUserMessageProjection(shared, rows)
  const assistant = createAssistantProjection(shared, rows, users)
  const media = createMediaProjection(shared, rows)
  const bubbles = createCompanionBubbleProjection(shared, rows, media)
  const terminal = createTerminalProjection(shared, rows, assistant)

  return {
    api: {
      pushMediaMessage: users.pushMediaMessage,
      pushUserMessage: users.pushUserMessage,
      bindTrailingAssistantMessageId: assistant.bindTrailingAssistantMessageId,
      pushStatusPill: assistant.pushStatusPill,
      setTurnHadBubbleBreak: assistant.setTurnHadBubbleBreak,
      beginAssistantMessage: assistant.beginAssistantMessage,
      appendAssistantDelta: assistant.appendAssistantDelta,
      appendAssistantReasoningDelta: assistant.appendAssistantReasoningDelta,
      setAssistantTool: assistant.setAssistantTool,
      finalizeAssistantMessage: assistant.finalizeAssistantMessage,
      appendCompanionBubble: bubbles.appendCompanionBubble,
      finalizeCompanionReply: bubbles.finalizeCompanionReply,
      updateMediaBubble: media.updateMediaBubble,
      updateVideoQueue: media.updateVideoQueue,
      updateVoiceBubble: media.updateVoiceBubble,
      forgetDeletedVoiceMessages: media.forgetDeletedVoiceMessages,
      markAssistantTerminal: terminal.markAssistantTerminal
    },
    internal: {
      companionBubbleBody: rows.companionBubbleBody,
      appendMessage: rows.appendMessage,
      splitUserBubblesEnabled: users.splitUserBubblesEnabled,
      reconcilePersistedMessages: users.reconcilePersistedMessages,
      clearReplyState: terminal.clearReplyState,
      clearInflightReply: terminal.clearInflightReply
    }
  }
}

export type ChatMessageController = ReturnType<typeof createChatMessageController>
