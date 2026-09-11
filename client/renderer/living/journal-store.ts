// 记忆 / 日记 store：片刻 + 日记页的水合与缓存。后端直连。
//
// - GET /api/companion/moments（带 cursor 分页）→ $moments
// - GET /api/companion/diary（带 from/to 区间）→ $diaryByDate
// - WS `companion.moment.created` / `companion.diary.upserted` 增量 upsert

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'

export interface MomentEntry {
  audioUrl: string | null
  body: string
  createdAt: string
  emotion: string | null
  id: string
  kind: string
  mediaUrl: string | null
  mediaMetadata: Record<string, unknown> | null
  mediaType: '' | 'image' | 'video' | 'audio'
  source: string
  title: string
  visibility?: string
}

interface MomentWire {
  audio_url: string | null
  body: string
  emotion: string | null
  id: string
  kind: string
  media_url: string | null
  media_metadata: Record<string, unknown> | null
  media_type: '' | 'image' | 'video' | 'audio'
  occurred_at: string
  source: string
  title: string
  visibility?: string
}

interface MomentListWire {
  moments: MomentWire[]
  next_cursor: string | null
}

export interface DiaryEntry {
  body: string
  createdAt: string | null
  date: string
  editedAt: string | null
  id: string
  memoryIds: string[]
  momentIds: string[]
  mood: string | null
  source: string
  title: string
  updatedAt: string | null
}

interface DiaryWire {
  body: string
  created_at: string | null
  edited_at: string | null
  entry_date: string
  id: string
  memory_ids: string[]
  moment_ids: string[]
  mood: string | null
  source: string
  title: string
  updated_at: string | null
}

interface DiaryListWire {
  entries: DiaryWire[]
}

export const $moments = atom<MomentEntry[]>([])
export const $momentsLoading = atom<boolean>(false)
export const $diaryByDate = atom<Record<string, DiaryEntry>>({})
export const $diaryLoading = atom<boolean>(false)

registerStorageClearHandler(clearJournal)

function toMoment(w: MomentWire): MomentEntry {
  return {
    audioUrl: w.audio_url,
    body: w.body,
    createdAt: w.occurred_at,
    emotion: w.emotion,
    id: w.id,
    kind: w.kind,
    mediaUrl: w.media_url,
    mediaMetadata: w.media_metadata,
    mediaType: w.media_type,
    source: w.source,
    title: w.title,
    visibility: w.visibility
  }
}

function toDiary(w: DiaryWire): DiaryEntry {
  return {
    body: w.body,
    createdAt: w.created_at,
    date: w.entry_date,
    editedAt: w.edited_at,
    id: w.id,
    memoryIds: w.memory_ids,
    momentIds: w.moment_ids,
    mood: w.mood,
    source: w.source,
    title: w.title,
    updatedAt: w.updated_at
  }
}

export async function hydrateMoments(): Promise<void> {
  $momentsLoading.set(true)

  try {
    const result = await authedApi<MomentListWire>({ path: '/api/companion/moments' })

    if (!result.ok) {
      if (result.reason === 'err') {
        log.warn('journal', 'hydrateMoments failed:', result.error)
      }

      return
    }

    if (!result.value) {
      return
    }

    $moments.set(result.value.moments.map(toMoment))
  } finally {
    $momentsLoading.set(false)
  }
}

export async function hydrateDiary(opts: { from?: string; to?: string; reset?: boolean } = {}): Promise<void> {
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

    const result = await authedApi<DiaryListWire>({
      path: `/api/companion/diary${query ? `?${query}` : ''}`
    })

    if (!result.ok) {
      if (result.reason === 'err') {
        log.warn('journal', 'hydrateDiary failed:', result.error)
      }

      return
    }

    if (!result.value) {
      return
    }

    const incoming: Record<string, DiaryEntry> = {}

    for (const entry of result.value.entries) {
      incoming[entry.entry_date] = toDiary(entry)
    }

    if (opts.reset || (!opts.from && !opts.to)) {
      $diaryByDate.set(incoming)
    } else {
      $diaryByDate.set({ ...$diaryByDate.get(), ...incoming })
    }
  } finally {
    $diaryLoading.set(false)
  }
}

// WS 入口：handleCompanionEvent 调用。
export function onJournalEvent(event: { payload?: unknown; type: string }): void {
  if (event.type === 'companion.moment.created') {
    const w = event.payload as MomentWire | undefined

    if (!w) {
      return
    }

    const list = $moments.get()
    const exists = list.some(m => m.id === w.id)

    if (exists) {
      return
    }

    $moments.set([toMoment(w), ...list])
  }

  if (event.type === 'companion.diary.upserted') {
    const w = event.payload as DiaryWire | undefined

    if (!w) {
      return
    }

    const map = $diaryByDate.get()

    $diaryByDate.set({ ...map, [w.entry_date]: toDiary(w) })
  }
}

export function clearJournal(): void {
  $moments.set([])
  $diaryByDate.set({})
}
