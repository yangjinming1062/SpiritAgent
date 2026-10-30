import type {
  CharacterFeatures,
  CharacterOverrides,
  ChatAttachment,
  MemoryListRequest,
  PersonaEditableField
} from '../../../shared/protocol'
import { isResourceId } from '../../../shared/resource-id'
import { isVisualCreationDraft, type VisualCreationDraft } from '../../../shared/visuals'
import { isRecord } from '../is-record'

export interface LifeUiSnapshot {
  status: 'all' | 'active' | 'paused' | 'completed' | 'abandoned'
  pursuitPage: number
  pursuitCursors: (string | null)[]
  eventPage: number
  selectedId: string | null
  scrollTop: number
}

export function isLifeUiSnapshot(value: unknown): value is LifeUiSnapshot {
  return (
    isRecord(value) &&
    (value.status === 'all' ||
      value.status === 'active' ||
      value.status === 'paused' ||
      value.status === 'completed' ||
      value.status === 'abandoned') &&
    [value.pursuitPage, value.eventPage].every(
      page => typeof page === 'number' && Number.isSafeInteger(page) && page >= 0
    ) &&
    Array.isArray(value.pursuitCursors) &&
    typeof value.pursuitPage === 'number' &&
    value.pursuitPage < value.pursuitCursors.length &&
    value.pursuitCursors[0] === null &&
    value.pursuitCursors.every(cursor => cursor === null || typeof cursor === 'string') &&
    (value.selectedId === null || isResourceId(value.selectedId)) &&
    typeof value.scrollTop === 'number' &&
    Number.isFinite(value.scrollTop) &&
    value.scrollTop >= 0
  )
}

export interface CompanionMemoryDraft {
  content: string
  baseContent: string
  baseVersion: number
}

export interface CompanionMemoryBrowser {
  status: NonNullable<MemoryListRequest['status']>
  query: string
  page: number
  cursors: (string | null)[]
  selectedId: string | null
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
        Array.isArray(browser.cursors) &&
        browser.cursors.length > browser.page &&
        browser.cursors[0] === null &&
        browser.cursors.every(cursor => cursor === null || typeof cursor === 'string') &&
        (browser.selectedId === null || isResourceId(browser.selectedId)) &&
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

export interface VisualsUiSnapshot {
  creation?: VisualCreationDraft
  analysisNotes?: Record<string, string>
  analysisSelections?: Record<string, string>
  query: string
  mediaKind: 'all' | 'image' | 'video'
  source: string
  page: number
  cursors: (string | null)[]
  selectedId: string | null
  detailOpen: boolean
}

export function isVisualsUiSnapshot(value: unknown): value is VisualsUiSnapshot {
  return (
    isRecord(value) &&
    typeof value.query === 'string' &&
    (value.mediaKind === 'all' || value.mediaKind === 'image' || value.mediaKind === 'video') &&
    typeof value.source === 'string' &&
    typeof value.page === 'number' &&
    Number.isSafeInteger(value.page) &&
    value.page >= 0 &&
    Array.isArray(value.cursors) &&
    value.cursors.every(cursor => cursor === null || isResourceId(cursor)) &&
    value.page < value.cursors.length &&
    (value.selectedId === null || isResourceId(value.selectedId)) &&
    typeof value.detailOpen === 'boolean' &&
    (value.creation === undefined || isVisualCreationDraft(value.creation)) &&
    (value.analysisNotes === undefined ||
      (isRecord(value.analysisNotes) && Object.values(value.analysisNotes).every(note => typeof note === 'string'))) &&
    (value.analysisSelections === undefined ||
      (isRecord(value.analysisSelections) &&
        Object.values(value.analysisSelections).every(selection => typeof selection === 'string')))
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
      sourceMessageId: string
      text: string
      attachments?: ChatAttachment[]
    } | null
    scrollAnchor: ConversationScrollAnchor | null
  }[]
}
