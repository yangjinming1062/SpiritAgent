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

/** 投影共享的可变引用：媒体补丁缓存与在途逐泡回复身份，由控制器创建后各模块共用。 */
export interface ProjectionShared {
  state: ConversationState
  mediaUpdates: Map<string, CompanionMediaBubble>
  videoQueueUpdates: Map<string, VideoQueueState | null>
  inflight: { value: InflightReply | null }
}

/** 行写入原语：先写 body 再入列，列表订阅者取 body 时已存在。 */
export interface RowWriter {
  mediaUpdateKey: (messageId: string, mediaId: string) => string
  companionBubbleBody: (bubble: CompanionBubble, messageId?: string) => Partial<ChatMessageBody>
  appendMessage: (item: Omit<ChatMessageListItem, 'id' | 'timestamp'>, body: ChatMessageBody) => string
  activeReplyInsertIndex: (list: ChatMessageListItem[]) => number | null
  companionBubbleInsertAt: (list: ChatMessageListItem[], messageId: string, index: number) => number
}

export interface UserMessageProjection {
  pushMediaMessage: (media: ChatMediaItem[]) => string
  pushUserMessage: (text: string, attachments?: ChatAttachment[]) => string
  splitUserBubblesEnabled: () => boolean
  reconcilePersistedMessages: (list: ChatMessageListItem[], boundIds: Set<string>) => void
}

export interface AssistantProjection {
  lastAssistantMessage: () => AssistantSnapshot | null
  ensureAssistantMessage: () => AssistantSnapshot | null
  beginAssistantMessage: () => void
  appendAssistantDelta: (text: string) => void
  appendAssistantReasoningDelta: (text: string) => void
  setAssistantTool: (name: string | null) => void
  finalizeAssistantMessage: (text?: string, media?: ChatMediaItem[], reasoning?: string) => void
  bindTrailingAssistantMessageId: (messageId: string) => void
  pushStatusPill: (subtype: string, text: string, backendMessageId?: string) => void
  setTurnHadBubbleBreak: (value: boolean) => void
}

export interface MediaProjection {
  updateVideoQueue: (mediaId: string, videoQueue: VideoQueueState | null) => void
  updateMediaBubble: (messageId: string, mediaId: string, bubble: CompanionMediaBubble) => void
  updateVoiceBubble: (messageId: string, index: number, bubble: CompanionBubble) => void
  forgetDeletedVoiceMessages: (messages: SessionMessage[]) => void
}

export interface CompanionBubbleProjection {
  appendCompanionBubble: (messageId: string, index: number, bubble: CompanionBubble) => void
  finalizeCompanionReply: (
    bubbles: CompanionBubble[],
    messageId: string,
    reasoning?: string,
    proactive?: boolean
  ) => void
}

export interface TerminalProjection {
  markAssistantTerminal: (options?: {
    error?: string
    cancelled?: boolean
    retryMessageId?: string
    beforePending?: boolean
  }) => void
  clearReplyState: () => void
  clearInflightReply: () => void
}
