import type {
  ChatAttachment,
  ChatMediaItem,
  CompanionBubble,
  CompanionMediaBubble,
  SessionMessage,
  VideoQueueState
} from '@protocol'

import type { ConversationState } from '../chat-runtime-state'
import type { ChatMessageBody, ChatMessageListItem } from '../chat-runtime-types'

export interface InflightReply {
  messageId: string
  firstId: string
}

export interface AssistantSnapshot {
  list: ChatMessageListItem[]
  item: ChatMessageListItem
  body: ChatMessageBody
}

/** 消息投影各模块共享的上下文；可变投影状态只在此声明一次。 */
export interface ProjectionContext {
  state: ConversationState
  mediaUpdates: Map<string, CompanionMediaBubble>
  videoQueueUpdates: Map<string, VideoQueueState | null>
  inflight: { value: InflightReply | null }

  mediaUpdateKey: (messageId: string, mediaId: string) => string
  companionBubbleBody: (bubble: CompanionBubble, messageId?: string) => Partial<ChatMessageBody>
  appendMessage: (item: Omit<ChatMessageListItem, 'id' | 'timestamp'>, body: ChatMessageBody) => string
  activeReplyInsertIndex: (list: ChatMessageListItem[]) => number | null
  companionBubbleInsertAt: (list: ChatMessageListItem[], messageId: string, index: number) => number
  lastAssistantMessage: () => AssistantSnapshot | null
  ensureAssistantMessage: () => AssistantSnapshot | null
  patchLastAssistant: (patch: (body: ChatMessageBody) => ChatMessageBody) => void

  beginAssistantMessage: () => void
  appendAssistantDelta: (text: string) => void
  appendAssistantReasoningDelta: (text: string) => void
  setAssistantTool: (name: string | null) => void
  finalizeAssistantMessage: (text?: string, media?: ChatMediaItem[], reasoning?: string) => void
  bindTrailingAssistantMessageId: (messageId: string) => void
  pushStatusPill: (subtype: string, text: string, backendMessageId?: string) => void
  setTurnHadBubbleBreak: (value: boolean) => void

  pushMediaMessage: (media: ChatMediaItem[]) => string
  pushUserMessage: (text: string, attachments?: ChatAttachment[]) => string
  splitUserBubblesEnabled: () => boolean
  reconcilePersistedMessages: (list: ChatMessageListItem[], boundIds: Set<string>) => void

  insertCompanionBubbleRow: (
    list: ChatMessageListItem[],
    messageId: string,
    index: number,
    bubble: CompanionBubble,
    extras?: { reasoning?: string; proactive?: boolean }
  ) => string
  patchCompanionBubble: (messageId: string, index: number, bubble: CompanionBubble) => boolean
  appendCompanionBubble: (messageId: string, index: number, bubble: CompanionBubble) => void
  finalizeCompanionReply: (
    bubbles: CompanionBubble[],
    messageId: string,
    reasoning?: string,
    proactive?: boolean
  ) => void

  updateVideoQueue: (mediaId: string, videoQueue: VideoQueueState | null) => void
  updateMediaBubble: (messageId: string, mediaId: string, bubble: CompanionMediaBubble) => void
  updateVoiceBubble: (messageId: string, index: number, bubble: CompanionBubble) => void
  forgetDeletedVoiceMessages: (messages: SessionMessage[]) => void

  markAssistantTerminal: (options?: {
    error?: string
    cancelled?: boolean
    retryMessageId?: string
    beforePending?: boolean
  }) => void
  clearReplyState: () => void
  clearInflightReply: () => void
}
