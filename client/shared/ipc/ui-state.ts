import { type ArchiveSearchState, isArchiveSearchState } from '../../../shared/archive-search'
import type { ChatAttachment, MemoryListRequest, PersonaEditableField } from '../../../shared/protocol'
import { isResourceId } from '../../../shared/resource-id'
import { isRecord } from '../is-record'

export interface LifeUiSnapshot {
  search: ArchiveSearchState
  status: 'all' | 'active' | 'paused' | 'completed' | 'abandoned'
  pursuitPage: number
  pursuitCursors: (string | null)[]
  eventPage: number
  scrollTop: number
}

export function isLifeUiSnapshot(value: unknown): value is LifeUiSnapshot {
  return (
    isRecord(value) &&
    isArchiveSearchState(value.search) &&
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
    typeof value.scrollTop === 'number' &&
    Number.isFinite(value.scrollTop) &&
    value.scrollTop >= 0
  )
}

export interface DiaryUiSnapshot {
  search: ArchiveSearchState
  selectedDate: string
  month: string
  scrollTop: number
}

export function isDiaryUiSnapshot(value: unknown): value is DiaryUiSnapshot {
  return (
    isRecord(value) &&
    isArchiveSearchState(value.search) &&
    typeof value.selectedDate === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(value.selectedDate) &&
    typeof value.month === 'string' &&
    /^\d{4}-\d{2}-01$/.test(value.month) &&
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
  scrollTop: number
}

export interface CompanionEditorUiSnapshot {
  persona: {
    values: Record<PersonaEditableField, string>
    base: Record<PersonaEditableField, string | null>
  } | null
  memory: {
    presetId: string
    section: 'recall' | 'profile'
    browsers: Record<string, CompanionMemoryBrowser>
    drafts: Record<string, CompanionMemoryDraft>
    profileDrafts: Record<string, CompanionMemoryDraft>
  }
}

const PERSONA_FIELDS = ['name', 'personality', 'relationship', 'speaking_style', 'age', 'occupation', 'background']

export function isCompanionEditorUiSnapshot(value: unknown): value is CompanionEditorUiSnapshot {
  if (!isRecord(value) || !isRecord(value.memory)) {
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

  const validFields = (fields: unknown, keys: string[], nullable = false): boolean =>
    isRecord(fields) && keys.every(key => typeof fields[key] === 'string' || (nullable && fields[key] === null))

  return (
    (persona === null ||
      (isRecord(persona) &&
        validFields(persona.values, PERSONA_FIELDS) &&
        validFields(persona.base, PERSONA_FIELDS, true))) &&
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

/** 创作草稿：描述、整理结果与待确认计划；只在本机内存与模式交接中保留。 */
export interface StudioUiSnapshot {
  mode: 'idle' | 'describe' | 'upload'
  intent: 'photo' | 'video' | 'motion'
  description: string
  preview: Record<string, unknown> | null
  refinement: string
}

export interface VisualsUiSnapshot {
  studio?: StudioUiSnapshot
  analysisNotes?: Record<string, string>
  analysisSelections?: Record<string, string>
  query: string
  mediaKind: 'all' | 'image' | 'video'
  source: string
  page: number
  cursors: (string | null)[]
}

export function isStudioUiSnapshot(value: unknown): value is StudioUiSnapshot {
  return (
    isRecord(value) &&
    (value.mode === 'idle' || value.mode === 'describe' || value.mode === 'upload') &&
    (value.intent === 'photo' || value.intent === 'video' || value.intent === 'motion') &&
    typeof value.description === 'string' &&
    typeof value.refinement === 'string' &&
    (value.preview === null || isRecord(value.preview))
  )
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
    (value.studio === undefined || isStudioUiSnapshot(value.studio)) &&
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
