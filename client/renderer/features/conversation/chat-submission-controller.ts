import type { SessionMessage } from '@protocol'

import type { ChatMessageController } from './chat-message-controller'
import type { ConversationState } from './chat-runtime-state'
import { createSubmissionCancellation, installCancel } from './submission/cancel'
import type { SubmissionContext } from './submission/context'
import { installPrompt } from './submission/prompt'
import { installQueue } from './submission/queue'
import { installRecovery } from './submission/recovery'

/** 提交控制器：装配取消、落库对齐、恢复与连发队列，对外保持单一命令面。 */
export function createChatSubmissionController(
  state: ConversationState,
  messages: ChatMessageController,
  isCurrent: () => boolean,
  onReplyReplaced: (replacedMessageId: string, message: SessionMessage) => void
) {
  const settledRequests = new Set<string>()

  function rememberSettled(requestId: string): void {
    settledRequests.add(requestId)

    if (settledRequests.size > 500) {
      settledRequests.delete(settledRequests.values().next().value!)
    }
  }

  const ctx = {
    state,
    messages,
    isCurrent,
    onReplyReplaced,
    settledRequests,
    rememberSettled,
    cancellation: null
  } as unknown as SubmissionContext

  ctx.cancellation = createSubmissionCancellation(ctx)

  installPrompt(ctx)
  installCancel(ctx)
  installRecovery(ctx)
  installQueue(ctx)

  return {
    activeRequestId: (): string | null => state.submission.activeRequestId,
    stopActiveTurn: ctx.stopActiveTurn,
    cancellationRequested: ctx.cancellationRequested,
    submissionFailed: ctx.submissionFailed,
    disposeSubmission: () => {
      ctx.cancelPendingFlush()
      ctx.cancellation.close()
      state.stores.$stoppingRequestId.set(null)
    },
    retryAssistantReply: ctx.retryAssistantReply,
    replaceAssistantReply: ctx.replaceAssistantReply,
    bindTrailingUserMessageIds: ctx.bindTrailingUserMessageIds,
    preparePromptSubmission: ctx.preparePromptSubmission,
    mergePersistedUserMessages: ctx.mergePersistedUserMessages,
    applySessionState: ctx.applySessionState,
    acceptPromptSubmission: ctx.acceptPromptSubmission,
    recoverUnconfirmedSubmission: ctx.recoverUnconfirmedSubmission,
    acceptsTurnEvent: ctx.acceptsTurnEvent,
    isPromptUnconfirmed: ctx.isPromptUnconfirmed,
    pushPendingPrompt: ctx.pushPendingPrompt,
    clearPendingPrompts: ctx.clearPendingPrompts,
    schedulePendingFlush: ctx.schedulePendingFlush,
    cancelPendingFlush: ctx.cancelPendingFlush,
    submitPendingBatch: ctx.submitPendingBatch,
    abandonDisconnectedTurn: ctx.abandonDisconnectedTurn
  }
}

export type ChatSubmissionController = ReturnType<typeof createChatSubmissionController>
