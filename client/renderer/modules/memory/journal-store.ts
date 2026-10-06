import { atom } from 'nanostores'

import { apiSucceeded, authedApi, captureAuthScope } from '@/shared/lib/authed-api'
import { isRecord } from '@/shared/lib/is-record'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { createUnreadMirror } from '@/shared/lib/unread-mirror'

export interface DiaryEntry {
  body: string
  createdAt: string | null
  date: string
  id: string
  postIds: string[]
  mood: string | null
  title: string
  updatedAt: string | null
}

interface DiaryWire {
  body: string
  created_at: string | null
  entry_date: string
  id: string
  post_ids: string[]
  mood: string | null
  title: string
  updated_at: string | null
}

interface DiaryListWire {
  entries: DiaryWire[]
  unread_diary_ids: string[]
}

export const $diaryByDate = atom<Record<string, DiaryEntry>>({})
export const $diaryLoading = atom(false)
export const $diaryHasUnread = atom(false)
let diaryRevision = 0
let eventRevision = 0
const entryRevisions = new Map<string, number>()
const deletedIds = new Set<string>()

function toDiary(w: DiaryWire): DiaryEntry {
  return {
    body: w.body,
    createdAt: w.created_at,
    date: w.entry_date,
    id: w.id,
    postIds: w.post_ids,
    mood: w.mood,
    title: w.title,
    updatedAt: w.updated_at
  }
}

const diaryUnread = createUnreadMirror({
  scope: 'journal',
  unreadPath: '/api/companion/diary/unread',
  readPath: '/api/companion/diary/read',
  idField: 'diary_ids',
  $hasUnread: $diaryHasUnread
})

export const hydrateDiaryUnread = diaryUnread.hydrate
export const markDiaryRead = diaryUnread.markRead

export async function hydrateDiary(opts: { from?: string; to?: string } = {}): Promise<string[] | null> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return null
  }

  const version = ++diaryRevision
  const startedEvents = eventRevision
  $diaryLoading.set(true)

  try {
    const params = new URLSearchParams()

    if (opts.from) {
      params.set('from', opts.from)
    }

    if (opts.to) {
      params.set('to', opts.to)
    }

    const query = params.toString()
    const result = await authedApi<DiaryListWire>({ path: `/api/companion/diary${query ? `?${query}` : ''}` })

    if (
      !isCurrent() ||
      version !== diaryRevision ||
      !apiSucceeded(result, 'journal', 'hydrateDiary failed') ||
      !result.value ||
      !Array.isArray(result.value.entries) ||
      !result.value.entries.every(isDiaryWire) ||
      !Array.isArray(result.value.unread_diary_ids) ||
      !result.value.unread_diary_ids.every(id => typeof id === 'string')
    ) {
      return null
    }

    const next = { ...$diaryByDate.get() }

    for (const [date, entry] of Object.entries(next)) {
      if (
        (!opts.from || date >= opts.from) &&
        (!opts.to || date <= opts.to) &&
        (entryRevisions.get(entry.id) ?? 0) <= startedEvents
      ) {
        delete next[date]
      }
    }

    for (const entry of result.value.entries) {
      if (!deletedIds.has(entry.id)) {
        next[entry.entry_date] = toDiary(entry)
      }
    }

    $diaryByDate.set(next)

    return result.value.unread_diary_ids.filter(id => !deletedIds.has(id))
  } finally {
    if (isCurrent() && version === diaryRevision) {
      $diaryLoading.set(false)
    }
  }
}

function isDiaryWire(value: unknown): value is DiaryWire {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.entry_date === 'string' &&
    typeof value.body === 'string' &&
    typeof value.title === 'string' &&
    (value.mood === null || typeof value.mood === 'string') &&
    (value.created_at === null || typeof value.created_at === 'string') &&
    (value.updated_at === null || typeof value.updated_at === 'string') &&
    Array.isArray(value.post_ids) &&
    value.post_ids.every(id => typeof id === 'string')
  )
}

export function onJournalEvent(event: { payload?: unknown; type: string }): void {
  const payload = event.payload

  if (event.type === 'companion.diary.created' && isDiaryWire(payload)) {
    if (!deletedIds.has(payload.id)) {
      entryRevisions.set(payload.id, ++eventRevision)
      $diaryByDate.set({ ...$diaryByDate.get(), [payload.entry_date]: toDiary(payload) })
    }

    void hydrateDiaryUnread()
  } else if (event.type === 'companion.diary.read') {
    void hydrateDiaryUnread()
  } else if (
    event.type === 'companion.diary.deleted' &&
    isRecord(payload) &&
    typeof payload.diary_id === 'string' &&
    typeof payload.entry_date === 'string'
  ) {
    deletedIds.add(payload.diary_id)
    entryRevisions.delete(payload.diary_id)
    eventRevision++
    const next = { ...$diaryByDate.get() }

    if (next[payload.entry_date]?.id === payload.diary_id) {
      delete next[payload.entry_date]
      $diaryByDate.set(next)
    }

    void hydrateDiaryUnread()
  }
}

registerStorageClearHandler(() => {
  diaryRevision++
  eventRevision++
  entryRevisions.clear()
  deletedIds.clear()
  $diaryByDate.set({})
  $diaryLoading.set(false)
})
