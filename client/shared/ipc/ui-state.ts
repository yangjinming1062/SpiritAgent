import type { ChatAttachment } from '../../../shared/protocol'

export type MainConversationPendingAttachment =
  | { type: 'image'; value: string; fileName?: string }
  | {
      type: 'video'
      fileName: string
      path: string
      status: 'error' | 'ready' | 'uploading'
      url?: string
      error?: string
    }
  | { type: 'file'; fileName: string; path: string }
  | { type: 'folder'; folderName: string; path: string }

export interface ConversationScrollAnchor {
  messageKey?: string
  offset: number
  top: number
  followBottom: boolean
}

export interface MainConversationDraftSnapshot {
  selectedSessionId: string | null
  drafts: {
    sessionId: string | null
    text: string
    pending: MainConversationPendingAttachment | null
    externalPaths: string[]
    editing: {
      sessionId: string
      sourceMessageId: number
      text: string
      attachments?: ChatAttachment[]
    } | null
    scrollAnchor: ConversationScrollAnchor | null
  }[]
}
