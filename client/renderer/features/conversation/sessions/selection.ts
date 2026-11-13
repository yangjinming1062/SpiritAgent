import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { SessionSnapshot } from '@protocol'
import { errorMessage } from '@runtime'

import { $callScreenOpen } from '../call-screen'
import type { ConversationRuntime } from '../chat-runtime'
import type { ConversationHistorySync } from '../chat-runtime-types'
import {
  $chatSessionId,
  $companionSessionId,
  conversationRuntimes,
  findConversationRuntime,
  getConversationRuntime,
  retainConversationRuntime,
  setChatSession,
  setCompanionSessionId
} from '../chat-store'
import {
  loadLocalSessionHistory,
  rememberFullHistory,
  requestSessionSnapshot,
  SessionHistoryChangedError,
  syncSessionHistory
} from '../session-history-cache'

import { $sessionNavigationError, sessionTokens } from './state'

let companionLoad: Promise<string | null> | null = null
let companionLoadEpoch = -1
let openMainPromise: Promise<string | null> | null = null
let openMainToken = 0
let openMainEpoch = -1

export async function ensureChatSession(runtime?: ConversationRuntime): Promise<string> {
  const existing = runtime?.$chatSessionId.get() ?? $chatSessionId.get()

  if (existing) {
    return existing
  }

  const sessionId = await openMainSession()

  if (!sessionId) {
    throw new Error(getStrings().chat.openMainSessionFailed)
  }

  return sessionId
}

// 固定轻语只水合陪伴runtime，不参与主对话的navigationToken。
export async function ensureCompanionSession(): Promise<string | null> {
  const authCurrent = captureAuthScope()
  const epoch = currentClearEpoch()

  if (!authCurrent) {
    return null
  }

  if (companionLoad && companionLoadEpoch === epoch) {
    return companionLoad
  }

  const load = (async () => {
    try {
      const knownId = $companionSessionId.get()
      const knownRuntime = knownId ? findConversationRuntime(knownId) : undefined
      const snapshot = knownRuntime?.captureHistorySync()
      const result = await resumeMainSession()

      if (!authCurrent()) {
        return null
      }

      const runtime = getConversationRuntime(result.session_id)
      mountMainSnapshot(result, knownRuntime === runtime ? snapshot : undefined, false)

      return result.session_id
    } catch (error) {
      if (epoch === currentClearEpoch()) {
        log.error('session-list', 'Failed to hydrate companion session:', error)
      }

      return null
    }
  })()

  companionLoad = load
  companionLoadEpoch = epoch

  try {
    return await load
  } finally {
    if (companionLoad === load) {
      companionLoad = null
      companionLoadEpoch = -1
    }
  }
}

// 挂载服务端快照：快照已挂上同一会话时不再重置，保留展示快照期间入列的待发消息；回合状态以服务端 running 为准。
function mountSyncedSession(result: SessionSnapshot, historySync?: ConversationHistorySync): boolean {
  if ($chatSessionId.get() !== result.session_id) {
    setChatSession(result.session_id)
  }

  const runtime = getConversationRuntime(result.session_id)
  const accepted = runtime.applyRemoteSnapshot(result, historySync)

  if (accepted) {
    void runtime.recoverUnconfirmedSubmission()
  }

  return accepted
}

// 主会话快照收尾：登记陪伴归属，select 时选中会话；接受后恢复在途提交并写历史缓存。
function mountMainSnapshot(
  res: SessionSnapshot,
  historySync: ConversationHistorySync | undefined,
  select: boolean
): void {
  setCompanionSessionId(res.session_id)
  const runtime = getConversationRuntime(res.session_id)

  if (select && $chatSessionId.get() !== res.session_id) {
    setChatSession(res.session_id)
  }

  const accepted = runtime.applyRemoteSnapshot(res, historySync)

  if (accepted) {
    void runtime.recoverUnconfirmedSubmission()
    rememberFullHistory(res.session_id, res.messages, { info: res.info })
  }
}

export async function switchSession(sessionId: string): Promise<void> {
  // 通话占用会话期间不切换：UI 入口已收起，这里挡住外部导航与迟到入口。
  if ($callScreenOpen.get() && sessionId !== $chatSessionId.get()) {
    notify({ kind: 'info', message: getStrings().chat.callBusyRetry })

    return
  }

  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return
  }

  const token = ++sessionTokens.navigation
  $sessionNavigationError.set(null)

  const isCurrent = (): boolean => token === sessionTokens.navigation && authCurrent()

  const runtime = getConversationRuntime(sessionId)
  const release = retainConversationRuntime(runtime)
  const localSnapshot = runtime.captureHistorySync()

  try {
    const local = await loadLocalSessionHistory(sessionId)

    if (!isCurrent()) {
      return
    }

    if (runtime.$historyHydrated.get()) {
      setChatSession(sessionId)
    } else if (local?.info) {
      runtime.hydrateSyncedChatMessages(local.messages, local.info, localSnapshot)
      setChatSession(sessionId)
    }

    const snapshot = runtime.captureHistorySync()

    const synced = await syncSessionHistory({
      sessionId,
      request: () => requestSessionSnapshot(sessionId)
    })

    // 快速 A→B 切换时丢弃过期响应，避免旧会话写回覆盖新会话。
    if (!isCurrent()) {
      return
    }

    mountSyncedSession(synced, snapshot)
  } catch (err) {
    if (isCurrent()) {
      log.error('session-list', 'Failed to switch session:', err)
      $sessionNavigationError.set({ sessionId, error: errorMessage(err) })
    }
  } finally {
    release()
  }
}

// 模式交接须确认历史已恢复；普通列表切换仍保留原位错误提示的语义。
export async function restoreConversationSelection(sessionId: string): Promise<void> {
  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    throw new Error('Conversation restore requires an authenticated account')
  }

  const token = ++sessionTokens.navigation
  const runtime = getConversationRuntime(sessionId)
  const release = retainConversationRuntime(runtime)

  const ownerCurrent = (): boolean => authCurrent() && token === sessionTokens.navigation && runtime.isCurrent()

  const assertCurrent = (): void => {
    if (!ownerCurrent()) {
      throw new Error('Conversation restore owner changed')
    }
  }

  $sessionNavigationError.set(null)

  try {
    const localSnapshot = runtime.captureHistorySync()
    const local = await loadLocalSessionHistory(sessionId)
    assertCurrent()

    if (!runtime.$historyHydrated.get() && local?.info) {
      runtime.hydrateSyncedChatMessages(local.messages, local.info, localSnapshot)
    }

    setChatSession(sessionId)

    if (authCurrent()) {
      const snapshot = runtime.captureHistorySync()

      try {
        const synced = await syncSessionHistory({
          sessionId,
          request: () => requestSessionSnapshot(sessionId)
        })

        assertCurrent()

        if (synced.session_id !== sessionId) {
          throw new Error('Conversation restore returned a different session')
        }

        if (runtime.applyRemoteSnapshot(synced, snapshot)) {
          void runtime.recoverUnconfirmedSubmission()
        }
      } catch (error) {
        assertCurrent()
        $sessionNavigationError.set({ sessionId, error: errorMessage(error) })

        if (!runtime.$historyHydrated.get()) {
          throw error
        }
      }
    }

    assertCurrent()

    if ($chatSessionId.get() !== sessionId || !runtime.$historyHydrated.get()) {
      throw new Error('Conversation history could not be restored')
    }
  } finally {
    release()
  }
}

// 挂载主会话并加载其对话流。被更晚的会话跳转取代后仍完成挂载与缓存，但不再改写当前视图。
export async function openMainSession(onMounted?: (res: SessionSnapshot) => void): Promise<string | null> {
  const authCurrent = captureAuthScope()
  const epoch = currentClearEpoch()

  if (!authCurrent) {
    return null
  }

  if (openMainPromise && openMainToken === sessionTokens.navigation && openMainEpoch === epoch) {
    return openMainPromise
  }

  const token = ++sessionTokens.navigation
  const isCurrent = authCurrent
  const isLatest = (): boolean => token === sessionTokens.navigation
  $sessionNavigationError.set(null)

  const load = (async () => {
    try {
      // 已知陪伴会话 id 时走本地秒开 + 增量；未知（首装/清缓存）才 get_main 全量。
      const knownCompanionId = $companionSessionId.get()

      if (knownCompanionId) {
        const local = await loadLocalSessionHistory(knownCompanionId)

        if (!isCurrent()) {
          return null
        }

        if (local) {
          if (isLatest()) {
            const runtime = getConversationRuntime(knownCompanionId)

            if (!runtime.$historyHydrated.get()) {
              runtime.hydrateSyncedChatMessages(local.messages, local.info)
            }

            setChatSession(knownCompanionId)
          }

          try {
            const runtime = getConversationRuntime(knownCompanionId)
            const snapshot = runtime.captureHistorySync()

            const synced = await syncSessionHistory({
              sessionId: knownCompanionId,
              request: () => requestSessionSnapshot(knownCompanionId)
            })

            if (!isCurrent()) {
              return null
            }

            // 同步期间已切到其他会话时不覆盖其视图。
            if (isLatest() && $chatSessionId.get() === knownCompanionId) {
              mountSyncedSession(synced, snapshot)
            }

            onMounted?.(synced)

            return knownCompanionId
          } catch (error) {
            if (!isCurrent()) {
              return null
            }

            if (error instanceof SessionHistoryChangedError) {
              log.warn('session-list', 'History is changing; keeping current session:', error)

              return knownCompanionId
            }

            // 增量失败回源读取主会话并完整挂载。
          }
        }
      }

      const snapshots = new Map(
        conversationRuntimes().map(runtime => [runtime.$chatSessionId.get(), runtime.captureHistorySync()])
      )

      const res = await resumeMainSession()

      if (!isCurrent()) {
        return null
      }

      mountMainSnapshot(res, snapshots.get(res.session_id), isLatest())

      onMounted?.(res)

      return res.session_id
    } catch (err) {
      if (isCurrent()) {
        log.error('session-list', 'Failed to open main session:', err)

        if (isLatest()) {
          $sessionNavigationError.set({ sessionId: $companionSessionId.get(), error: errorMessage(err) })
        }
      }

      return null
    }
  })()

  openMainPromise = load
  openMainEpoch = epoch
  openMainToken = token

  try {
    return await load
  } finally {
    if (openMainPromise === load) {
      openMainPromise = null
      openMainEpoch = -1
    }
  }
}

export async function resumeMainSession(): Promise<SessionSnapshot> {
  const authCurrent = captureAuthScope()
  const resource = await requestResource('sessions.get_main_session')

  if (!authCurrent?.()) {
    throw new Error('Account changed')
  }

  return requestSessionSnapshot(resource.session_id)
}

registerStorageClearHandler(() => {
  openMainPromise = null
  openMainEpoch = -1
  companionLoad = null
  companionLoadEpoch = -1
})
