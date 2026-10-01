import { atom } from 'nanostores'

import { apiSucceeded, authedApi } from '@/shared/lib/authed-api'
import { isRecord } from '@/shared/lib/is-record'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'

export interface DiaryEntry {
  body: string
  createdAt: string | null
  date: string
  id: string
  postIds: string[]
  mood: string | null
  source: string
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
  source: string
  title: string
  updated_at: string | null
}

interface DiaryListWire {
  entries: DiaryWire[]
}

export const $diaryByDate = atom<Record<string, DiaryEntry>>({})
export const $diaryLoading = atom(false)
let diaryRevision = 0
registerStorageClearHandler(() => {
  diaryRevision++
  $diaryByDate.set({})
  $diaryLoading.set(false)
})

function toDiary(w: DiaryWire): DiaryEntry {
  return {
    body: w.body,
    createdAt: w.created_at,
    date: w.entry_date,
    id: w.id,
    postIds: w.post_ids,
    mood: w.mood,
    source: w.source,
    title: w.title,
    updatedAt: w.updated_at
  }
}

export async function hydrateDiary(opts: { from?: string; to?: string } = {}): Promise<boolean> {
  const version = ++diaryRevision
  const epoch = currentClearEpoch()
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

    if (version !== diaryRevision || epoch !== currentClearEpoch()) {
      return false
    }

    if (!apiSucceeded(result, 'journal', 'hydrateDiary failed:') || !result.value) {
      return false
    }

    const incoming: Record<string, DiaryEntry> = {}

    for (const entry of result.value.entries) {
      incoming[entry.entry_date] = toDiary(entry)
    }

    if (!opts.from && !opts.to) {
      $diaryByDate.set(incoming)
    } else {
      $diaryByDate.set({ ...$diaryByDate.get(), ...incoming })
    }

    return true
  } finally {
    if (version === diaryRevision) {
      $diaryLoading.set(false)
    }
  }
}

function isDiaryWire(value: unknown): value is DiaryWire {
  return isRecord(value) && typeof value.id === 'string' && typeof value.entry_date === 'string'
}

export function onJournalEvent(event: { payload?: unknown; type: string }): void {
  if (event.type === 'companion.diary.upserted' && isDiaryWire(event.payload)) {
    const w = event.payload
    $diaryByDate.set({ ...$diaryByDate.get(), [w.entry_date]: toDiary(w) })
  }
}
