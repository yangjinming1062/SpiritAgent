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
import type { SpiritAgentGateway } from '@/shared/spiritagent'
import type { SessionResumeResponse } from '@protocol'

export function createHostHistoryRecovery(gateway: SpiritAgentGateway, isCurrent: () => boolean) {
  // 恢复服务端会话并同步历史；失败仅回退仍被选中的会话。
  async function resumeSessionHistory(): Promise<void> {
    const sid = $chatSessionId.get()

    const syncMountSeq = (res: { current_seq?: number; stream_id?: string }) => {
      if (typeof res.current_seq === 'number') {
        gateway.resetSeq(res.current_seq, res.stream_id)
      }
    }

    if (sid) {
      try {
        const local = await loadLocalSessionHistory(sid)

        if (!isCurrent() || $chatSessionId.get() !== sid) {
          return
        }

        const runtime = getConversationRuntime(sid)
        const hasMessages = runtime.$chatMessageList.get().length > 0

        // 本地秒开：先渲染缓存再后台增量追上；网关 seq 不重置到缓存值，否则后端重启后的低 seq 会被陈旧高水位当重复丢弃。
        if (local && !hasMessages) {
          runtime.hydrateChatMessages(local.messages, local.info)
        }

        // last_seq 只在聊天列表是活数据（重连）时发；缓存不追踪实时回合，冷启动一律走 after_id 增量，否则服务端按陈旧水位重放会重复追加。
        const snapshot = runtime.captureHistorySync()

        const synced = await syncSessionHistory({
          streamId: gateway.streamId,
          lastSeq: hasMessages && gateway.lastReceivedSeq > 0 ? gateway.lastReceivedSeq : undefined,
          sessionId: sid,
          request: body => gateway.request<SessionResumeResponse>('session.resume', { session_id: sid, ...body })
        })

        if (!isCurrent() || $chatSessionId.get() !== sid) {
          return
        }

        gateway.resetSeq(synced.current_seq, synced.stream_id)

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
        void openMainSession(syncMountSeq)
      }
    } else {
      void openMainSession(syncMountSeq)
    }
  }

  return { resumeSessionHistory }
}
