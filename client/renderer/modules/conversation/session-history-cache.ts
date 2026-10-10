import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import type { SessionHistorySnapshot } from '@ipc/contracts'
import type { SessionMessage, SessionResumeResponse, SessionRuntimeInfo } from '@protocol'

const PERSIST_DEBOUNCE_MS = 800

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
const invalidations = new Map<string, symbol>()
const historyRevisions = new Map<string, number>()

export class SessionHistoryChangedError extends Error {
  constructor() {
    super('Session history changed while synchronizing')
    this.name = 'SessionHistoryChangedError'
  }
}

function currentAuthSessionId(): null | string {
  const auth = $auth.get()

  return auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
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

function copyState(state: HistoryCacheState): HistoryCacheState {
  return { ...state, messages: [...state.messages] }
}

function getMemoryHistory(sessionId: string): null | HistoryCacheState {
  const state = memoryBySession.get(sessionId)

  return state ? copyState(state) : null
}

export async function loadLocalSessionHistory(sessionId: string): Promise<null | HistoryCacheState> {
  const authSessionId = currentAuthSessionId()
  const epoch = currentClearEpoch()

  if (!authSessionId) {
    return null
  }

  const memory = getMemoryHistory(sessionId)

  if (memory) {
    return memory
  }

  if (invalidations.has(sessionId)) {
    return null
  }

  try {
    const snap = await window.spiritagent.sessionHistory.get(sessionId, authSessionId)

    if (epoch !== currentClearEpoch() || currentAuthSessionId() !== authSessionId) {
      return null
    }

    const current = getMemoryHistory(sessionId)

    if (current || invalidations.has(sessionId)) {
      return current
    }

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

    return copyState(state)
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
  historyRevisions.set(sessionId, (historyRevisions.get(sessionId) ?? 0) + 1)
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

  const seen = new Set(state.messages.map(m => m.id))

  for (const message of incoming) {
    if (typeof message.id === 'number') {
      if (seen.has(message.id)) {
        continue
      }

      seen.add(message.id)

      if (Number.isFinite(message.id) && (state.lastMessageId === null || message.id > state.lastMessageId)) {
        state.lastMessageId = message.id
      }
    }

    state.messages.push({ ...message })
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

/** 本地秒开后用服务端增量/全量追上：锚点走 after_id；last_seq 只能由调用方以活动聊天列表的水位（gateway.lastReceivedSeq）传入——缓存不追踪实时回合，拿缓存 currentSeq 当 last_seq 会让服务端对陈旧水位重放帧，活列表上重复追加。 */
export async function syncSessionHistory(params: {
  lastSeq?: number
  streamId?: string
  sessionId: string
  request: (body: { after_id?: number; last_seq?: number; stream_id?: string }) => Promise<SessionResumeResponse>
}): Promise<SessionResumeResponse & { current_seq: number; kind: 'full' | 'incremental' | 'noop' }> {
  const epoch = currentClearEpoch()
  const authSessionId = currentAuthSessionId()
  const local = memoryBySession.get(params.sessionId)
  let invalidation = invalidations.get(params.sessionId)
  let historyRevision = historyRevisions.get(params.sessionId)
  const body: { after_id?: number; last_seq?: number; stream_id?: string } = {}

  // 离线期间的视频完成事件可能已过重放窗口，增量锚点无法发现原等待卡片的变化。
  const hasPendingMedia = local?.messages.some(message =>
    message.bubbles?.some(bubble => bubble.type === 'video' && bubble.status === 'pending')
  )

  if (!invalidation && !hasPendingMedia && local?.lastMessageId && local.lastMessageId > 0) {
    body.after_id = local.lastMessageId
  }

  if (!invalidation && params.lastSeq && params.lastSeq > 0) {
    body.last_seq = params.lastSeq
    body.stream_id = params.streamId
  }

  let res = await params.request(body)

  // 每次返回先核对账户；语音或媒体更新撞上在途快照时，有界重取全量。
  for (let retry = 0; ; retry++) {
    if (epoch !== currentClearEpoch() || currentAuthSessionId() !== authSessionId) {
      throw new SessionHistoryChangedError()
    }

    if (
      invalidations.get(params.sessionId) === invalidation &&
      historyRevisions.get(params.sessionId) === historyRevision
    ) {
      break
    }

    if (retry >= 2) {
      throw new SessionHistoryChangedError()
    }

    invalidation = invalidations.get(params.sessionId)
    historyRevision = historyRevisions.get(params.sessionId)
    res = await params.request({})
  }

  invalidations.delete(params.sessionId)

  // noop 与增量以本地缓存兜底，全量只认服务端返回。
  const currentSeq = typeof res.current_seq === 'number' ? res.current_seq : (local?.currentSeq ?? 0)
  const info = res.info ?? local?.info

  let messages = Array.isArray(res.messages) ? res.messages : []
  let kind: 'full' | 'incremental' | 'noop' = 'full'

  if (res.resumed) {
    updateHistorySeq(params.sessionId, currentSeq, res.info)
    messages = local ? [...local.messages] : []
    kind = 'noop'
  } else if (res.incremental) {
    messages = mergeIncrementalHistory(params.sessionId, messages, { currentSeq, info }) ?? messages
    kind = 'incremental'
  } else {
    rememberFullHistory(params.sessionId, messages, {
      currentSeq,
      info,
      nextCursor: res.next_cursor,
      truncated: res.truncated
    })
  }

  return { ...res, current_seq: currentSeq, info, messages, incremental: false, kind }
}

function cancelPersist(sessionId: string): void {
  const timer = persistTimers.get(sessionId)

  if (timer) {
    clearTimeout(timer)
    persistTimers.delete(sessionId)
  }
}

function schedulePersist(sessionId: string): void {
  if (!currentAuthSessionId() || invalidations.has(sessionId)) {
    return
  }

  cancelPersist(sessionId)
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
  const authSessionId = currentAuthSessionId()

  if (!authSessionId || !state || invalidations.has(sessionId)) {
    return
  }

  try {
    await window.spiritagent.sessionHistory.save(sessionId, toSnapshot(state), authSessionId)
  } catch (err) {
    log.warn('session-history', 'persist failed:', err)
  }
}

function clearSessionHistoryMemory(): void {
  memoryBySession.clear()
  invalidations.clear()
  historyRevisions.clear()

  for (const sessionId of persistTimers.keys()) {
    cancelPersist(sessionId)
  }
}

function removeSnapshot(sessionId: string, reason: string): void {
  const authSessionId = currentAuthSessionId()

  if (authSessionId) {
    void window.spiritagent.sessionHistory.remove(sessionId, authSessionId).catch(err => {
      log.warn('session-history', `remove ${reason} history failed:`, err)
    })
  }
}

/** 旧消息发生原地更新：保留展示与增量基底，下次同步强制取回全量。 */
export function invalidateSessionHistory(sessionId: string): void {
  invalidations.set(sessionId, Symbol())
  cancelPersist(sessionId)
  removeSnapshot(sessionId, 'stale')
}

/** 会话被删除后清掉内存与磁盘快照，避免已删对话内容留盘。 */
export function forgetSessionHistory(sessionId: string): void {
  memoryBySession.delete(sessionId)
  historyRevisions.set(sessionId, (historyRevisions.get(sessionId) ?? 0) + 1)
  cancelPersist(sessionId)
  removeSnapshot(sessionId, 'deleted')
}

registerStorageClearHandler(() => {
  // 换号只释放渲染层状态；磁盘快照由主进程在移除账户时删除。
  clearSessionHistoryMemory()
})
