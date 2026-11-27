import { atom, computed } from 'nanostores'

import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { authedResource } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import type { ApiSchemas } from '../../../../../shared/api.generated'
import { isRecord } from '../../../../../shared/is-record'

export interface DiaryEntry {
  body: string
  createdAt: string | null
  date: string
  id: string
  title: string
  updatedAt: string | null
}

type DiaryWire = ApiSchemas['DiaryEntryResponse']

export const $diaryByDate = atom<Record<string, DiaryEntry>>({})
export const $diaryLoading = atom(false)
export const $diaryUnreadIds = atom<ReadonlySet<string>>(new Set())
export const $diaryHasUnread = computed($diaryUnreadIds, ids => ids.size > 0)
let diaryRevision = 0
let eventRevision = 0
let unreadRevision = 0
let unreadSnapshotRevision = 0
let appliedUnreadSnapshotRevision = 0
let unreadRequest: { isCurrent: () => boolean; promise: Promise<boolean> } | undefined
const entryRevisions = new Map<string, number>()

function toDiary(w: DiaryWire): DiaryEntry {
  return {
    body: w.body,
    createdAt: w.created_at ?? null,
    date: w.entry_date,
    id: w.id,
    title: w.title,
    updatedAt: w.updated_at ?? null
  }
}

function isUnreadIds(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(id => typeof id === 'string')
}

function isUnreadWire(value: unknown): value is ApiSchemas['DiaryUnreadResponse'] {
  if (!isRecord(value) || !isUnreadIds(value.unread_diary_ids)) {
    return false
  }

  const hasUnread = value.unread_diary_ids.length > 0

  return value.has_unread === hasUnread
}

// 查询共用同一顺序；写入与事件使此前发出的两个读取入口都失效。
function invalidateUnreadSnapshots(): number {
  appliedUnreadSnapshotRevision = ++unreadSnapshotRevision

  return appliedUnreadSnapshotRevision
}

function applyUnreadIds(ids: Iterable<string>, version = ++unreadSnapshotRevision): boolean {
  if (version < appliedUnreadSnapshotRevision) {
    return false
  }

  appliedUnreadSnapshotRevision = version
  $diaryUnreadIds.set(new Set(ids))

  return true
}

/** 同轮信号合并查询，查询期间收到的新信号在结束后补查。 */
export function hydrateDiaryUnread(): Promise<boolean> {
  unreadRevision++

  if (unreadRequest?.isCurrent()) {
    return unreadRequest.promise
  }

  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return Promise.resolve(false)
  }

  let version = unreadRevision

  const request = Promise.resolve()
    .then(async () => {
      if (!isCurrent()) {
        return false
      }

      version = unreadRevision
      const snapshotVersion = ++unreadSnapshotRevision

      const result = await authedResource('companion_journal.get_diary_unread')

      if (
        !isCurrent() ||
        version !== unreadRevision ||
        !apiSucceeded(result, 'journal', 'unread failed') ||
        !isUnreadWire(result.value)
      ) {
        return false
      }

      return applyUnreadIds(result.value.unread_diary_ids, snapshotVersion)
    })
    .finally(() => {
      if (unreadRequest?.promise === request) {
        unreadRequest = undefined

        if (isCurrent() && version !== unreadRevision) {
          void hydrateDiaryUnread()
        }
      }
    })

  unreadRequest = { isCurrent, promise: request }

  return request
}

export async function markDiaryRead(ids: string[]): Promise<boolean> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return false
  }

  if (ids.length === 0) {
    return true
  }

  // 作废确认前的查询；发布或其他窗口的事件会再次推进代次。
  const version = ++unreadRevision
  const snapshotVersion = invalidateUnreadSnapshots()

  const result = await authedResource('companion_journal.read_diary', { body: { diary_ids: ids } })

  if (!isCurrent() || !apiSucceeded(result, 'journal', 'read failed') || !isUnreadWire(result.value)) {
    return false
  }

  if (version !== unreadRevision || !applyUnreadIds(result.value.unread_diary_ids, snapshotVersion)) {
    // 已读确认不会逆转，迟到响应只应用本次确认的 ID，其余以补查为准。
    const confirmed = new Set(ids)
    applyUnreadIds([...$diaryUnreadIds.get()].filter(id => !confirmed.has(id)))
    void hydrateDiaryUnread()
  }

  return true
}

export async function hydrateDiary(opts: { from?: string; to?: string } = {}): Promise<boolean> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return false
  }

  const version = ++diaryRevision
  const startedEvents = eventRevision
  const snapshotVersion = ++unreadSnapshotRevision
  $diaryLoading.set(true)

  try {
    const result = await authedResource('companion_journal.get_diary', { query: opts })

    if (
      !isCurrent() ||
      version !== diaryRevision ||
      !apiSucceeded(result, 'journal', 'hydrateDiary failed') ||
      !result.value ||
      !Array.isArray(result.value.entries) ||
      !result.value.entries.every(isDiaryWire)
    ) {
      return false
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
      const current = next[entry.entry_date]

      // 快照只替换读取前的条目；实时事件也可能用新 ID 替换同一天的日记。
      if (
        (entryRevisions.get(entry.id) ?? 0) <= startedEvents &&
        (!current || (entryRevisions.get(current.id) ?? 0) <= startedEvents)
      ) {
        next[entry.entry_date] = toDiary(entry)
      }
    }

    $diaryByDate.set(next)

    // 月份响应也含账户未读状态，专用请求失败时仍可显示日历标记。
    if (isUnreadIds(result.value.unread_diary_ids)) {
      applyUnreadIds(result.value.unread_diary_ids, snapshotVersion)
    }

    return true
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
    (value.created_at === null || typeof value.created_at === 'string') &&
    (value.updated_at === null || typeof value.updated_at === 'string')
  )
}

export function onJournalEvent(event: { payload?: unknown; type: string }): void {
  const payload = event.payload

  if (event.type === 'companion.diary.created' && isDiaryWire(payload)) {
    invalidateUnreadSnapshots()
    entryRevisions.set(payload.id, ++eventRevision)
    $diaryByDate.set({ ...$diaryByDate.get(), [payload.entry_date]: toDiary(payload) })

    void hydrateDiaryUnread()
  } else if (event.type === 'companion.diary.read') {
    invalidateUnreadSnapshots()
    void hydrateDiaryUnread()
  }
}

registerStorageClearHandler(() => {
  unreadRevision++
  unreadRequest = undefined
  applyUnreadIds([])
  diaryRevision++
  eventRevision++
  entryRevisions.clear()
  $diaryByDate.set({})
  $diaryLoading.set(false)
})
