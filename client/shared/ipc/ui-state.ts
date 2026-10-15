import type { ChatAttachment } from '../../../shared/protocol'

export interface DesktopLifeUiSnapshot {
  selectedSetId: number | null
  selectedActionId: number | null
  creating: boolean
  create: {
    name: string
    sceneId: string
    sceneLabel: string
    outfitId: string
    source: 'ai' | 'image_upload' | 'video_upload'
    feedback: string
    requestId: string
    targetWidth: string
    targetHeight: string
  }
  feedback: Record<string, string>
  design: { name: string; description: string; kind: 'loop' | 'once'; duration: number }
  addActionOpen: boolean
  externalActionId: number | null
  externalRequirements: string
  externalFilePath: string | null
}

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
