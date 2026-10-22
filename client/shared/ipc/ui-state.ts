import type {
  CharacterFeatures,
  CharacterOverrides,
  ChatAttachment,
  MemoryListRequest,
  PersonaEditableField
} from '../../../shared/protocol'
import { type DesktopResolution, isDesktopResolution } from '../desktop-media-geometry'
import { isRecord } from '../is-record'

export interface CompanionMemoryDraft {
  content: string
  baseContent: string
  baseVersion: number
}

export interface CompanionMemoryBrowser {
  status: NonNullable<MemoryListRequest['status']>
  query: string
  page: number
  selectedId: number | null
  editorOpen: boolean
  scrollTop: number
}

export interface CompanionEditorUiSnapshot {
  persona: {
    values: Record<PersonaEditableField, string>
    base: Record<PersonaEditableField, string | null>
  } | null
  characterCards: Record<
    string,
    {
      revision: number
      features: CharacterFeatures
      automatic: CharacterFeatures
      overrides: CharacterOverrides
      changes: CharacterOverrides
    }
  >
  memory: {
    presetId: string
    section: 'recall' | 'profile'
    browsers: Record<string, CompanionMemoryBrowser>
    drafts: Record<string, CompanionMemoryDraft>
    profileDrafts: Record<string, CompanionMemoryDraft>
  }
}

const PERSONA_FIELDS = ['name', 'personality', 'relationship', 'speaking_style']

const CHARACTER_FIELDS = [
  'head_shape',
  'facial_features',
  'facial_surface',
  'head_identifiers',
  'body_shape',
  'proportions',
  'limbs_and_appendages',
  'body_surface'
]

export function isCompanionEditorUiSnapshot(value: unknown): value is CompanionEditorUiSnapshot {
  if (!isRecord(value) || !isRecord(value.characterCards) || !isRecord(value.memory)) {
    return false
  }

  const persona = value.persona
  const memory = value.memory

  const validDraft = (draft: unknown): boolean =>
    isRecord(draft) &&
    typeof draft.content === 'string' &&
    typeof draft.baseContent === 'string' &&
    typeof draft.baseVersion === 'number' &&
    Number.isSafeInteger(draft.baseVersion) &&
    draft.baseVersion >= 0

  const validChanges = (changes: unknown): boolean =>
    isRecord(changes) &&
    Object.entries(changes).every(
      ([key, text]) => CHARACTER_FIELDS.includes(key) && (text === null || typeof text === 'string')
    )

  const validFields = (fields: unknown, keys: string[], nullable = false): boolean =>
    isRecord(fields) && keys.every(key => typeof fields[key] === 'string' || (nullable && fields[key] === null))

  return (
    (persona === null ||
      (isRecord(persona) &&
        validFields(persona.values, PERSONA_FIELDS) &&
        validFields(persona.base, PERSONA_FIELDS, true))) &&
    Object.values(value.characterCards).every(
      card =>
        isRecord(card) &&
        typeof card.revision === 'number' &&
        Number.isSafeInteger(card.revision) &&
        card.revision > 0 &&
        validFields(card.features, CHARACTER_FIELDS) &&
        validFields(card.automatic, CHARACTER_FIELDS) &&
        validChanges(card.overrides) &&
        validChanges(card.changes)
    ) &&
    typeof memory.presetId === 'string' &&
    (memory.section === 'recall' || memory.section === 'profile') &&
    isRecord(memory.browsers) &&
    Object.values(memory.browsers).every(
      browser =>
        isRecord(browser) &&
        ['active', 'candidate', 'invalidated', 'expired'].includes(String(browser.status)) &&
        typeof browser.query === 'string' &&
        typeof browser.page === 'number' &&
        Number.isSafeInteger(browser.page) &&
        browser.page >= 0 &&
        (browser.selectedId === null ||
          (typeof browser.selectedId === 'number' &&
            Number.isSafeInteger(browser.selectedId) &&
            browser.selectedId > 0)) &&
        typeof browser.editorOpen === 'boolean' &&
        typeof browser.scrollTop === 'number' &&
        Number.isFinite(browser.scrollTop) &&
        browser.scrollTop >= 0
    ) &&
    isRecord(memory.drafts) &&
    Object.values(memory.drafts).every(validDraft) &&
    isRecord(memory.profileDrafts) &&
    Object.values(memory.profileDrafts).every(validDraft)
  )
}

export interface DesktopLifeUiSnapshot {
  libraryScrollTop?: number
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
}

export function isDesktopLifeUiSnapshot(value: unknown): value is DesktopLifeUiSnapshot {
  if (!isRecord(value) || !isRecord(value.create) || !isRecord(value.feedback) || !isRecord(value.design)) {
    return false
  }

  const create = value.create
  const design = value.design

  return (
    (value.libraryScrollTop === undefined ||
      (typeof value.libraryScrollTop === 'number' &&
        Number.isFinite(value.libraryScrollTop) &&
        value.libraryScrollTop >= 0)) &&
    [value.selectedSetId, value.selectedActionId].every(
      id => id === null || (typeof id === 'number' && Number.isSafeInteger(id) && id > 0)
    ) &&
    typeof value.creating === 'boolean' &&
    typeof value.addActionOpen === 'boolean' &&
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
