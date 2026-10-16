import type { ChatAttachment } from '../../../shared/protocol'
import { type DesktopResolution, isDesktopResolution } from '../desktop-media-geometry'

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
    targetDisplayId: number | null
    targetResolution: DesktopResolution | null
  }
  feedback: Record<string, string>
  design: { name: string; description: string; kind: 'loop' | 'once'; duration: number }
  addActionOpen: boolean
  externalActionId: number | null
  externalRequirements: string
  externalFilePath: string | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isDesktopLifeUiSnapshot(value: unknown): value is DesktopLifeUiSnapshot {
  if (!isRecord(value) || !isRecord(value.create) || !isRecord(value.feedback) || !isRecord(value.design)) {
    return false
  }

  const create = value.create
  const design = value.design

  return (
    [value.selectedSetId, value.selectedActionId, value.externalActionId].every(
      id => id === null || (typeof id === 'number' && Number.isSafeInteger(id) && id > 0)
    ) &&
    typeof value.creating === 'boolean' &&
    typeof value.addActionOpen === 'boolean' &&
    typeof value.externalRequirements === 'string' &&
    (value.externalFilePath === null || typeof value.externalFilePath === 'string') &&
    ['name', 'sceneId', 'sceneLabel', 'outfitId', 'feedback', 'requestId'].every(
      key => typeof create[key] === 'string'
    ) &&
    (create.targetDisplayId === null ||
      (typeof create.targetDisplayId === 'number' && Number.isSafeInteger(create.targetDisplayId))) &&
    (create.targetResolution === null || isDesktopResolution(create.targetResolution)) &&
    (create.source === 'ai' || create.source === 'image_upload' || create.source === 'video_upload') &&
    Object.values(value.feedback).every(text => typeof text === 'string') &&
    typeof design.name === 'string' &&
    typeof design.description === 'string' &&
    (design.kind === 'loop' || design.kind === 'once') &&
    typeof design.duration === 'number' &&
    Number.isFinite(design.duration)
  )
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
