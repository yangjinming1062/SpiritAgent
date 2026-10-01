// 记忆 / 日记 store：片刻 + 日记页的水合与缓存。后端直连。GET /api/companion/moments（只取最新一页，不跟随 next_cursor）→ $moments；GET /api/companion/diary（带 from/to 区间）→ $diaryByDate；POST/DELETE /api/companion/moments/{id}/comments → 评论与删除本人评论；WS `companion.moment.created` / `companion.moment.comment` / `companion.diary.upserted` 增量 upsert

import { atom } from 'nanostores'

import { apiSucceeded, authedApi } from '@/shared/lib/authed-api'
import { isRecord } from '@/shared/lib/is-record'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'

export interface MomentCommentEntry {
  content: string
  createdAt: string | null
  id: string
  momentId: string
  role: string
}

interface MomentCommentWire {
  content: string
  created_at: string | null
  id: string
  moment_id: string
  role: string
}

export interface MomentEntry {
  audioUrl: string | null
  body: string
  comments: MomentCommentEntry[]
  createdAt: string
  emotion: string | null
  id: string
  kind: string
  mediaUrl: string | null
  mediaMetadata: Record<string, unknown> | null
  mediaType: '' | 'image' | 'video' | 'audio'
  source: string
  title: string
}

interface MomentWire {
  audio_url: string | null
  body: string
  comments?: MomentCommentWire[]
  emotion: string | null
  id: string
  kind: string
  media_url: string | null
  media_metadata: Record<string, unknown> | null
  media_type: '' | 'image' | 'video' | 'audio'
  occurred_at: string
  source: string
  title: string
}

interface MomentListWire {
  moments: MomentWire[]
  next_cursor: string | null
}

export interface DiaryEntry {
  body: string
  createdAt: string | null
  date: string
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

function toComment(w: MomentCommentWire): MomentCommentEntry {
  return {
    content: w.content,
    createdAt: w.created_at,
    id: w.id,
    momentId: w.moment_id,
    role: w.role
  }
}

function toMoment(w: MomentWire): MomentEntry {
  return {
    audioUrl: w.audio_url,
    body: w.body,
    comments: (w.comments ?? []).map(toComment),
    createdAt: w.occurred_at,
    emotion: w.emotion,
    id: w.id,
    kind: w.kind,
    mediaUrl: w.media_url,
    mediaMetadata: w.media_metadata,
    mediaType: w.media_type,
    source: w.source,
    title: w.title
  }
}

function toDiary(w: DiaryWire): DiaryEntry {
  return {
    body: w.body,
    createdAt: w.created_at,
    date: w.entry_date,
    id: w.id,
    memoryIds: w.memory_ids,
    momentIds: w.moment_ids,
    mood: w.mood,
    source: w.source,
    title: w.title,
    updatedAt: w.updated_at
  }
}

// 水合序号：请求期间页面可能切月 / 重挂，或发生登出清空；迟到的旧响应不得覆盖新数据（与 wardrobe-store 同一套 revision + clearEpoch 防护）。
let momentsRevision = 0
let diaryRevision = 0

// 返回本次结果是否已写入；被新请求取代或清空时同样为 false，调用方按自身代次忽略。
export async function hydrateMoments(): Promise<boolean> {
  const version = ++momentsRevision
  const epoch = currentClearEpoch()
  $momentsLoading.set(true)

  try {
    const result = await authedApi<MomentListWire>({ path: '/api/companion/moments' })

    if (version !== momentsRevision || epoch !== currentClearEpoch()) {
      return false
    }

    if (!apiSucceeded(result, 'journal', 'hydrateMoments failed:') || !result.value) {
      return false
    }

    $moments.set(result.value.moments.map(toMoment))

    return true
  } finally {
    if (version === momentsRevision) {
      $momentsLoading.set(false)
    }
  }
}

// 返回值语义同 hydrateMoments。
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

// 就地更新一条片刻的评论列表（不存在对应片刻时忽略）。
function withMomentComments(momentId: string, update: (comments: MomentCommentEntry[]) => MomentCommentEntry[]): void {
  $moments.set($moments.get().map(m => (m.id === momentId ? { ...m, comments: update(m.comments) } : m)))
}

function upsertComment(momentId: string, comment: MomentCommentEntry): void {
  withMomentComments(momentId, comments =>
    comments.some(c => c.id === comment.id) ? comments : [...comments, comment]
  )
}

export async function commentMoment(momentId: string, content: string): Promise<boolean> {
  const epoch = currentClearEpoch()

  const result = await authedApi<MomentCommentWire>({
    body: { content },
    method: 'POST',
    path: `/api/companion/moments/${momentId}/comments`
  })

  if (epoch !== currentClearEpoch()) {
    return false
  }

  if (!apiSucceeded(result, 'journal', 'commentMoment failed:') || !result.value) {
    return false
  }

  upsertComment(momentId, toComment(result.value))

  return true
}

export async function deleteMomentComment(momentId: string, commentId: string): Promise<boolean> {
  const epoch = currentClearEpoch()

  const result = await authedApi<null>({
    method: 'DELETE',
    path: `/api/companion/moments/${momentId}/comments/${commentId}`
  })

  if (epoch !== currentClearEpoch()) {
    return false
  }

  if (!apiSucceeded(result, 'journal', 'deleteMomentComment failed:')) {
    return false
  }

  withMomentComments(momentId, comments => comments.filter(c => c.id !== commentId))

  return true
}

// WS 载荷未经类型校验：只接受去重、索引与映射依赖的字段形态正确的事件。
function isMomentWire(value: unknown): value is MomentWire {
  return (
    isRecord(value) && typeof value.id === 'string' && (value.comments === undefined || Array.isArray(value.comments))
  )
}

function isMomentCommentWire(value: unknown): value is MomentCommentWire {
  return isRecord(value) && typeof value.id === 'string'
}

function isDiaryWire(value: unknown): value is DiaryWire {
  return isRecord(value) && typeof value.id === 'string' && typeof value.entry_date === 'string'
}

// WS 入口：由 app/runtime/gateway-event-router.ts 调用。
export function onJournalEvent(event: { payload?: unknown; type: string }): void {
  if (event.type === 'companion.moment.created') {
    const w = event.payload

    if (!isMomentWire(w)) {
      return
    }

    const list = $moments.get()
    const exists = list.some(m => m.id === w.id)

    if (exists) {
      return
    }

    $moments.set([toMoment(w), ...list])
  }

  if (event.type === 'companion.moment.comment') {
    const w = event.payload as { comment?: unknown; moment_id?: unknown } | undefined

    if (typeof w?.moment_id !== 'string' || !isMomentCommentWire(w.comment)) {
      return
    }

    upsertComment(w.moment_id, toComment(w.comment))
  }

  if (event.type === 'companion.diary.upserted') {
    const w = event.payload

    if (!isDiaryWire(w)) {
      return
    }

    const map = $diaryByDate.get()

    $diaryByDate.set({ ...map, [w.entry_date]: toDiary(w) })
  }
}

function clearJournal(): void {
  momentsRevision++
  diaryRevision++
  $moments.set([])
  $momentsLoading.set(false)
  $diaryByDate.set({})
  $diaryLoading.set(false)
}
