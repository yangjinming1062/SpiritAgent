import {
  $chatSessionId,
  getConversationRuntime,
  loadLocalSessionHistory,
  openMainSession,
  SessionHistoryChangedError,
  setChatSession,
  syncSessionHistory
} from '@/modules/conversation'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'

/** 恢复当前会话；失败只回退仍被选中的会话。 */
export async function resumeHostHistory(isCurrent: () => boolean, replayed = false): Promise<void> {
  const sid = $chatSessionId.get()

  if (sid) {
    try {
      const local = await loadLocalSessionHistory(sid)

      if (!isCurrent() || $chatSessionId.get() !== sid) {
        return
      }

      const runtime = getConversationRuntime(sid)
      const hasMessages = runtime.$chatMessageList.get().length > 0

      // 先呈现缓存，再取得当前完整基线。
      if (local && !hasMessages) {
        runtime.hydrateChatMessages(local.messages, local.info)
      }

      if (replayed && runtime.$historyHydrated.get()) {
        void runtime.recoverUnconfirmedSubmission()

        if (!runtime.$chatTurnInFlight.get()) {
          runtime.submitPendingBatch()
        }

        return
      }

      const snapshot = runtime.captureHistorySync()

      const synced = await syncSessionHistory({
        sessionId: sid,
        request: () => requestResource('sessions.snapshot', { path: { session_id: sid } })
      })

      if (!isCurrent() || $chatSessionId.get() !== sid) {
        return
      }

      runtime.applyRemoteSnapshot(synced, snapshot)

      void runtime.recoverUnconfirmedSubmission()

      if (!runtime.$chatTurnInFlight.get()) {
        runtime.submitPendingBatch()
      }
    } catch (error) {
      if (!isCurrent() || $chatSessionId.get() !== sid) {
        return
      }

      if (error instanceof SessionHistoryChangedError) {
        log.warn('gateway-boot', 'History is changing; keeping current session:', error)

        return
      }

      setChatSession(null)
      void openMainSession()
    }
  } else {
    void openMainSession()
  }
}
