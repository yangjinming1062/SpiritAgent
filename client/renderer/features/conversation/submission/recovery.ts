import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

import { isResourceId } from '../../../../../shared/resource-id'
import { recoverConversationTurn } from '../chat-requests'

import type { SubmissionContext } from './context'

/** 未确认提交的恢复与回合事件接纳：断线后先查回执，迟到的旧回合不再改写视图。 */
export function installRecovery(ctx: SubmissionContext): void {
  const { $chatTurnInFlight, $replacingReplyMessageId } = ctx.state.stores

  async function recoverUnconfirmedSubmission(): Promise<void> {
    if (ctx.state.submission.submissionRecovery) {
      return ctx.state.submission.submissionRecovery
    }

    const pending = ctx.state.submission.unconfirmedSubmission

    if (pending && ctx.cancellationRequested(pending.requestId)) {
      return ctx.cancellation.request({ sessionId: pending.params.session_id, requestId: pending.requestId })
    }

    if (
      !pending ||
      !ctx.isCurrent() ||
      ($chatTurnInFlight.get() && ctx.state.submission.activeRequestId !== pending.requestId)
    ) {
      return
    }

    ctx.state.submission.activeRequestId = pending.requestId
    const replacedId = pending.params.replace_reply_message_id
    $replacingReplyMessageId.set(isResourceId(replacedId) ? replacedId : null)
    $chatTurnInFlight.set(true)

    const recovery = (async (): Promise<void> => {
      try {
        const result = await recoverConversationTurn(pending.params)

        if (ctx.isCurrent() && ctx.state.submission.unconfirmedSubmission === pending) {
          ctx.acceptPromptSubmission(result)
        }
      } catch (error) {
        if (
          ctx.isCurrent() &&
          ctx.state.submission.unconfirmedSubmission === pending &&
          ctx.state.submission.activeRequestId === pending.requestId
        ) {
          $chatTurnInFlight.set(false)
          notifyError(error, getStrings().chat.sendFailed)
        }
      }
    })()

    ctx.state.submission.submissionRecovery = recovery

    try {
      await recovery
    } finally {
      if (ctx.state.submission.submissionRecovery === recovery) {
        ctx.state.submission.submissionRecovery = null
      }
    }
  }

  function acceptsTurnEvent(requestId?: string): boolean {
    return (
      !requestId ||
      (!ctx.settledRequests.has(requestId) &&
        requestId !== ctx.state.submission.settledRequestId &&
        (!ctx.state.submission.activeRequestId || ctx.state.submission.activeRequestId === requestId))
    )
  }

  function isPromptUnconfirmed(requestId: unknown): boolean {
    return typeof requestId === 'string' && ctx.state.submission.unconfirmedSubmission?.requestId === requestId
  }

  const abandonDisconnectedTurn = (): void => {
    ctx.state.submission.submissionGeneration++
    ctx.cancelPendingFlush()

    // 已受理回合继续执行，重连后由恢复快照确认终态。
    if (!ctx.state.submission.activeRequestId || !ctx.cancellationRequested(ctx.state.submission.activeRequestId)) {
      $chatTurnInFlight.set(false)
    }
  }

  Object.assign(ctx, {
    recoverUnconfirmedSubmission,
    acceptsTurnEvent,
    isPromptUnconfirmed,
    abandonDisconnectedTurn
  })
}
