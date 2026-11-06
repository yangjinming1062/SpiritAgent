import type { SessionMessage } from '@protocol'

import type { ChatMessageController } from './chat-message-controller'
import type { ConversationState } from './chat-runtime-state'
import { createTurnControl } from './submission/cancel'
import type {
  PendingQueueProjection,
  PromptSubmissionProjection,
  SubmissionBase,
  SubmissionPeers,
  TurnControlProjection,
  TurnRecoveryProjection
} from './submission/context'
import { createPromptSubmission } from './submission/prompt'
import { createPendingQueue } from './submission/queue'
import { createTurnRecovery } from './submission/recovery'

/** 提交控制器：按能力装配取消、落库对齐、恢复与连发队列，对外保持单一命令面。 */
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

  const base: SubmissionBase = { state, messages, isCurrent, onReplyReplaced, settledRequests }

  let prompts: PromptSubmissionProjection
  let queue: PendingQueueProjection
  let control: TurnControlProjection
  let recovery: TurnRecoveryProjection

  // 协作面只做转发，模块之间的调用都发生在装配完成之后。
  const peers: SubmissionPeers = {
    get cancellation() {
      return control.cancellation
    },
    rememberSettled,
    submitPendingBatch: () => queue.submitPendingBatch(),
    cancelPendingFlush: () => queue.cancelPendingFlush(),
    preparePromptSubmission: (params, bubbleIds) => prompts.preparePromptSubmission(params, bubbleIds),
    mergePersistedUserMessages: (list, requestId, ids, trackActive) =>
      prompts.mergePersistedUserMessages(list, requestId, ids, trackActive),
    applySessionState: event => prompts.applySessionState(event),
    acceptPromptSubmission: result => prompts.acceptPromptSubmission(result),
    cancellationRequested: requestId => control.cancellationRequested(requestId),
    submissionFailed: (params, error) => control.submissionFailed(params, error),
    isPromptUnconfirmed: requestId => recovery.isPromptUnconfirmed(requestId)
  }

  prompts = createPromptSubmission(base, peers)
  queue = createPendingQueue(base, peers)
  control = createTurnControl(base, peers)
  recovery = createTurnRecovery(base, peers)

  return {
    activeRequestId: (): string | null => state.submission.activeRequestId,
    stopActiveTurn: control.stopActiveTurn,
    cancellationRequested: control.cancellationRequested,
    submissionFailed: control.submissionFailed,
    disposeSubmission: () => {
      queue.cancelPendingFlush()
      control.cancellation.close()
      state.stores.$stoppingRequestId.set(null)
    },
    retryAssistantReply: control.retryAssistantReply,
    replaceAssistantReply: control.replaceAssistantReply,
    bindTrailingUserMessageIds: prompts.bindTrailingUserMessageIds,
    preparePromptSubmission: prompts.preparePromptSubmission,
    mergePersistedUserMessages: prompts.mergePersistedUserMessages,
    applySessionState: prompts.applySessionState,
    acceptPromptSubmission: prompts.acceptPromptSubmission,
    recoverUnconfirmedSubmission: recovery.recoverUnconfirmedSubmission,
    acceptsTurnEvent: recovery.acceptsTurnEvent,
    isPromptUnconfirmed: recovery.isPromptUnconfirmed,
    pushPendingPrompt: queue.pushPendingPrompt,
    clearPendingPrompts: queue.clearPendingPrompts,
    schedulePendingFlush: queue.schedulePendingFlush,
    cancelPendingFlush: queue.cancelPendingFlush,
    submitPendingBatch: queue.submitPendingBatch,
    abandonDisconnectedTurn: recovery.abandonDisconnectedTurn
  }
}

export type ChatSubmissionController = ReturnType<typeof createChatSubmissionController>
