import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

import { isResourceId } from '../../../../../shared/resource-id'
import { recoverConversationTurn } from '../chat-requests'

import type { SubmissionBase, SubmissionPeers, TurnRecoveryProjection } from './context'

/** 未确认提交的恢复与回合事件接纳：断线后先查回执，迟到的旧回合不再改写视图。 */
export function createTurnRecovery(base: SubmissionBase, peers: SubmissionPeers): TurnRecoveryProjection {
  const { $chatTurnInFlight, $replacingReplyMessageId } = base.state.stores

  async function recoverUnconfirmedSubmission(): Promise<void> {
    if (base.state.submission.submissionRecovery) {
      return base.state.submission.submissionRecovery
    }

    const pending = base.state.submission.unconfirmedSubmission

    if (pending && peers.cancellationRequested(pending.requestId)) {
      return peers.cancellation.request({ sessionId: pending.params.session_id, requestId: pending.requestId })
    }

    if (
      !pending ||
      !base.isCurrent() ||
      ($chatTurnInFlight.get() && base.state.submission.activeRequestId !== pending.requestId)
    ) {
      return
    }

    base.state.submission.activeRequestId = pending.requestId
    const replacedId = pending.params.replace_reply_message_id
    $replacingReplyMessageId.set(isResourceId(replacedId) ? replacedId : null)
    $chatTurnInFlight.set(true)

    const recovery = (async (): Promise<void> => {
      try {
        const result = await recoverConversationTurn(pending.params)

        if (base.isCurrent() && base.state.submission.unconfirmedSubmission === pending) {
          peers.acceptPromptSubmission(result)
        }
      } catch (error) {
        if (
          base.isCurrent() &&
          base.state.submission.unconfirmedSubmission === pending &&
          base.state.submission.activeRequestId === pending.requestId
        ) {
          $chatTurnInFlight.set(false)
          notifyError(error, getStrings().chat.sendFailed)
        }
      }
    })()

    base.state.submission.submissionRecovery = recovery

    try {
      await recovery
    } finally {
      if (base.state.submission.submissionRecovery === recovery) {
        base.state.submission.submissionRecovery = null
      }
    }
  }

  function acceptsTurnEvent(requestId?: string): boolean {
    return (
      !requestId ||
      (!base.settledRequests.has(requestId) &&
        requestId !== base.state.submission.settledRequestId &&
        (!base.state.submission.activeRequestId || base.state.submission.activeRequestId === requestId))
    )
  }

  function isPromptUnconfirmed(requestId: unknown): boolean {
    return typeof requestId === 'string' && base.state.submission.unconfirmedSubmission?.requestId === requestId
  }

  const abandonDisconnectedTurn = (): void => {
    base.state.submission.submissionGeneration++
    peers.cancelPendingFlush()

    // 已受理回合继续执行，重连后由恢复快照确认终态。
    if (
      !base.state.submission.activeRequestId ||
      !peers.cancellationRequested(base.state.submission.activeRequestId)
    ) {
      $chatTurnInFlight.set(false)
    }
  }

  return { recoverUnconfirmedSubmission, acceptsTurnEvent, isPromptUnconfirmed, abandonDisconnectedTurn }
}
