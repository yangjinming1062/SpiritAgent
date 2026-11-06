import type {
  ChatAttachment,
  ChatMediaItem,
  CompanionBubble,
  CompanionLinkBubble,
  CompanionMediaBubble,
  ReplyAudio
} from '@protocol'

export interface ChatMessageListItem {
  id: string
  role: 'user' | 'assistant'
  subtype?: string
  /** 后端 Message.id——fork/undo 回传 source_message_id；hydrate 历史行与活路径绑定后都有值。 */
  backendMessageId?: string
  timestamp?: number
}

export interface ChatMessageBody {
  canReplaceReply?: boolean
  /** 完整用户输入；陪伴拆泡与附件展示文案不能用作编辑原文。 */
  editableText?: string
  streamingText?: string
  replyType?: CompanionBubble['type']
  replyMedia?: CompanionMediaBubble
  replyLink?: CompanionLinkBubble
  replyIndex?: number
  replyAudio?: ReplyAudio | null
  text: string
  reasoning?: string
  streaming?: boolean
  toolName?: string | null
  tools?: string[]
  error?: string
  retryMessageId?: string
  cancelled?: boolean
  requestId?: string
  attachments?: ChatAttachment[]
  media?: ChatMediaItem[]
}

export interface ConversationHistorySync {
  revision: number
  historyRevision: number
  messages: ChatMessageListItem[]
  bodies: Record<string, ChatMessageBody>
}

// 会话权限由 hydrate 注入服务端 kind 和自动化标记。
export type ChatSessionKind = 'special' | 'standard'

export interface PendingPromptItem {
  text: string
  attachments?: ChatAttachment[]
  messageId?: string
}

export interface ChatEditDraft {
  sessionId: string
  sourceMessageId: string
  text: string
  /** 原消息的多模态附件；编辑期只读展示，提交时由服务端随修订消息保留。 */
  attachments?: ChatAttachment[]
}

export interface ChatUndoDraft {
  session_id: string
  text: string
  attachments?: ChatAttachment[]
}

export interface SessionSettings {
  temperature?: number
  context_compression_threshold?: number
  enable_context_compression?: boolean
  reasoning_effort?: string
}

export interface SessionContextUsage {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  contextLimit: number
}

export type PendingAttachment =
  | { type: 'image'; value: string; fileName?: string }
  | {
      type: 'video'
      fileName: string
      path: string
      status: 'error' | 'ready' | 'uploading'
      url?: string
      error?: string
    }
  | {
      type: 'file'
      fileName: string
      path: string
    }
  | {
      type: 'folder'
      folderName: string
      path: string
    }

// 伙伴主动说出的瞬时消息，聊天面板收起时以气泡浮出，说完清空；sessionId 存在时点击切到该会话（媒体送达跳转用）。
export interface ProactiveBubbleState {
  text: string
  sessionId?: string
}

// 外部投喂（DESIGN「拖拽与直接交互」）：精灵拖入或经主进程信箱转交的文件路径，对话输入订阅后并入待发附件。
export interface PendingExternalAttachment {
  paths: string[]
  nonce: number
}
