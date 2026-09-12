import type { SessionHistorySnapshot } from '@ipc/contracts'
import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'
import {
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedString
} from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import type { SessionMessage, SessionRuntimeInfo } from '@/shared/types/spiritagent'

const COMPANION_SESSION_ID_KEY = registerCompanionStorageKey('da.companion.companionSessionId')
const PERSIST_DEBOUNCE_MS = 800

export const $persistedCompanionSessionId = atom<null | string>(storedString(COMPANION_SESSION_ID_KEY))

interface HistoryCacheState {
  currentSeq: number
  info?: SessionRuntimeInfo
  lastMessageId: null | number
  messages: SessionMessage[]
  nextCursor: null | string
  truncated: boolean
}

const memoryBySession = new Map<string, HistoryCacheState>()
const persistTimers = new Map<string, ReturnType<typeof setTimeout>>()

function canPersist(): boolean {
  return $auth.get().kind === 'authenticated'
}

function lastIdFrom(messages: SessionMessage[]): null | number {
  let last: null | number = null

  for (const m of messages) {
    if (typeof m.id === 'number' && Number.isFinite(m.id)) {
      last = last === null || m.id > last ? m.id : last
    }
  }

  return last
}

function toSnapshot(state: HistoryCacheState): SessionHistorySnapshot {
  return {
    currentSeq: state.currentSeq,
    info: state.info as Record<string, unknown> | undefined,
    lastMessageId: state.lastMessageId,
    messages: state.messages,
    nextCursor: state.nextCursor,
    truncated: state.truncated,
    writtenAt: Date.now()
  }
}

function cloneMessages(messages: unknown[]): SessionMessage[] {
  return messages.map(m => ({ ...(m as SessionMessage) }))
}

export function setPersistedCompanionSessionId(id: null | string): void {
  $persistedCompanionSessionId.set(id)
  persistString(COMPANION_SESSION_ID_KEY, id)
}

function getMemoryHistory(sessionId: string): null | HistoryCacheState {
  const state = memoryBySession.get(sessionId)

  if (!state) {
    return null
  }

  return {
    currentSeq: state.currentSeq,
    info: state.info,
    lastMessageId: state.lastMessageId,
    messages: [...state.messages],
    nextCursor: state.nextCursor,
    truncated: state.truncated
  }
}

export async function loadLocalSessionHistory(sessionId: string): Promise<null | HistoryCacheState> {
  const memory = getMemoryHistory(sessionId)

  if (memory) {
    return memory
  }

  if (!canPersist()) {
    return null
  }

  try {
    const snap = await window.spiritagent.sessionHistory.get(sessionId)

    if (!snap || !Array.isArray(snap.messages)) {
      return null
    }

    const state: HistoryCacheState = {
      currentSeq: snap.currentSeq,
      info: snap.info as SessionRuntimeInfo | undefined,
      lastMessageId: snap.lastMessageId,
      messages: cloneMessages(snap.messages),
      nextCursor: snap.nextCursor,
      truncated: snap.truncated
    }

    memoryBySession.set(sessionId, state)

    return {
      ...state,
      messages: [...state.messages]
    }
  } catch (err) {
    log.warn('session-history', 'load local history failed:', err)

    return null
  }
}

export function rememberFullHistory(
  sessionId: string,
  messages: SessionMessage[],
  opts?: {
    currentSeq?: number
    info?: SessionRuntimeInfo
    nextCursor?: null | string
    truncated?: boolean
  }
): void {
  const prev = memoryBySession.get(sessionId)

  const next: HistoryCacheState = {
    currentSeq: typeof opts?.currentSeq === 'number' ? opts.currentSeq : (prev?.currentSeq ?? 0),
    info: opts?.info ?? prev?.info,
    lastMessageId: lastIdFrom(messages),
    messages: cloneMessages(messages),
    nextCursor: opts?.nextCursor ?? null,
    truncated: opts?.truncated ?? false
  }

  memoryBySession.set(sessionId, next)
  schedulePersist(sessionId)
}

function updateHistorySeq(sessionId: string, currentSeq: number, info?: SessionRuntimeInfo): void {
  const state = memoryBySession.get(sessionId)

  if (!state) {
    return
  }

  state.currentSeq = currentSeq

  if (info) {
    state.info = info
  }

  schedulePersist(sessionId)
}

/** 增量合并：按后端 Message.id 去重追加，返回合并后的完整列表。 */
function mergeIncrementalHistory(
  sessionId: string,
  incoming: SessionMessage[],
  opts?: {
    currentSeq?: number
    info?: SessionRuntimeInfo
  }
): null | SessionMessage[] {
  const state = memoryBySession.get(sessionId)

  if (!state) {
    return null
  }

  const seen = new Set(state.messages.map(m => m.id).filter((id): id is number => typeof id === 'number'))
  const appended = incoming.filter(m => typeof m.id !== 'number' || !seen.has(m.id))

  if (appended.length > 0) {
    state.messages = [...state.messages, ...cloneMessages(appended)]
    state.lastMessageId = lastIdFrom(state.messages)
  }

  if (typeof opts?.currentSeq === 'number') {
    state.currentSeq = opts.currentSeq
  }

  if (opts?.info) {
    state.info = opts.info
  }

  schedulePersist(sessionId)

  return [...state.messages]
}

/** 本地秒开后用服务端增量/全量追上：锚点走 after_id；last_seq 只能由调用方以
 * 活动聊天列表的水位（gateway.lastReceivedSeq）传入——缓存不追踪实时回合，
 * 拿缓存 currentSeq 当 last_seq 会让服务端对陈旧水位重放帧，活列表上重复追加。 */
export async function syncSessionHistory(params: {
  lastSeq?: number
  sessionId: string
  request: (body: { after_id?: number; last_seq?: number }) => Promise<{
    current_seq?: number
    incremental?: boolean
    info?: SessionRuntimeInfo
    messages?: SessionMessage[]
    next_cursor?: null | string
    resumed?: boolean
    truncated?: boolean
  }>
}): Promise<{
  currentSeq: number
  info?: SessionRuntimeInfo
  kind: 'full' | 'incremental' | 'noop'
  messages: SessionMessage[]
}> {
  const local = memoryBySession.get(params.sessionId)
  const body: { after_id?: number; last_seq?: number } = {}

  if (local?.lastMessageId && local.lastMessageId > 0) {
    body.after_id = local.lastMessageId
  }

  if (params.lastSeq && params.lastSeq > 0) {
    body.last_seq = params.lastSeq
  }

  const res = await params.request(body)

  if (res.resumed) {
    if (typeof res.current_seq === 'number') {
      updateHistorySeq(params.sessionId, res.current_seq, res.info)
    }

    return {
      currentSeq: typeof res.current_seq === 'number' ? res.current_seq : (local?.currentSeq ?? 0),
      info: res.info ?? local?.info,
      kind: 'noop',
      messages: local ? [...local.messages] : []
    }
  }

  const incoming = Array.isArray(res.messages) ? res.messages : []

  if (res.incremental) {
    const merged =
      mergeIncrementalHistory(params.sessionId, incoming, {
        currentSeq: res.current_seq,
        info: res.info
      }) ?? incoming

    return {
      currentSeq: typeof res.current_seq === 'number' ? res.current_seq : (local?.currentSeq ?? 0),
      info: res.info ?? local?.info,
      kind: 'incremental',
      messages: merged
    }
  }

  rememberFullHistory(params.sessionId, incoming, {
    currentSeq: res.current_seq,
    info: res.info,
    nextCursor: res.next_cursor,
    truncated: res.truncated
  })

  return {
    currentSeq: typeof res.current_seq === 'number' ? res.current_seq : 0,
    info: res.info,
    kind: 'full',
    messages: incoming
  }
}

function schedulePersist(sessionId: string): void {
  if (!canPersist()) {
    return
  }

  const existing = persistTimers.get(sessionId)

  if (existing) {
    clearTimeout(existing)
  }

  persistTimers.set(
    sessionId,
    setTimeout(() => {
      persistTimers.delete(sessionId)
      void persistNow(sessionId)
    }, PERSIST_DEBOUNCE_MS)
  )
}

async function persistNow(sessionId: string): Promise<void> {
  const state = memoryBySession.get(sessionId)

  if (!canPersist() || !state) {
    return
  }

  try {
    await window.spiritagent.sessionHistory.save(sessionId, toSnapshot(state))
  } catch (err) {
    log.warn('session-history', 'persist failed:', err)
  }
}

function clearSessionHistoryMemory(): void {
  memoryBySession.clear()

  for (const timer of persistTimers.values()) {
    clearTimeout(timer)
  }

  persistTimers.clear()
  setPersistedCompanionSessionId(null)
}

/** 会话被删除后清掉内存与磁盘快照，避免已删对话内容留盘。 */
export function forgetSessionHistory(sessionId: string): void {
  memoryBySession.delete(sessionId)

  const timer = persistTimers.get(sessionId)

  if (timer) {
    clearTimeout(timer)
    persistTimers.delete(sessionId)
  }

  void window.spiritagent.sessionHistory.remove(sessionId).catch(() => {})
}

registerStorageClearHandler(() => {
  // 磁盘清理由主进程登出/换号的 clearLocalAssetCaches 统一负责，这里只清渲染层状态。
  clearSessionHistoryMemory()
})
