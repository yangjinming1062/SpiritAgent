import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import type { SessionHistorySnapshot } from '@ipc/contracts'
import type { SessionMessage, SessionRuntimeInfo, SessionSnapshot } from '@protocol'

const PERSIST_DEBOUNCE_MS = 800

interface HistoryCacheState {
  info?: SessionRuntimeInfo
  messages: SessionMessage[]
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

function toSnapshot(state: HistoryCacheState): SessionHistorySnapshot {
  return {
    info: state.info as Record<string, unknown> | undefined,
    messages: state.messages,
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
    const snap = await backendApi().sessionHistory.get(sessionId, authSessionId)

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
      info: snap.info as SessionRuntimeInfo | undefined,
      messages: cloneMessages(snap.messages)
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
    info?: SessionRuntimeInfo
  }
): void {
  const prev = memoryBySession.get(sessionId)

  const next: HistoryCacheState = {
    info: opts?.info ?? prev?.info,
    messages: cloneMessages(messages)
  }

  memoryBySession.set(sessionId, next)
  historyRevisions.set(sessionId, (historyRevisions.get(sessionId) ?? 0) + 1)
  schedulePersist(sessionId)
}

export async function syncSessionHistory(params: {
  sessionId: string
  request: () => Promise<SessionSnapshot>
}): Promise<SessionSnapshot> {
  const epoch = currentClearEpoch()
  const authSessionId = currentAuthSessionId()
  let invalidation = invalidations.get(params.sessionId)
  let historyRevision = historyRevisions.get(params.sessionId)
  let res = await params.request()

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
    res = await params.request()
  }

  invalidations.delete(params.sessionId)

  rememberFullHistory(params.sessionId, res.messages, { info: res.info })

  return res
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
    await backendApi().sessionHistory.save(sessionId, toSnapshot(state), authSessionId)
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
    void backendApi()
      .sessionHistory.remove(sessionId, authSessionId)
      .catch(err => {
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
