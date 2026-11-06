import { requestResource } from '@/shared/lib/resource-request'
import { currentClearEpoch } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

import { ApiError } from '../../../../../shared/errors'
import { createTurnCancellation, type TurnReference } from '../../../../../shared/turn-cancellation'
import { type ConversationSubmission, readConversationTurn, submitConversationTurn } from '../chat-requests'
import { conversationVoiceSink } from '../voice-link'

import type { SubmissionBase, SubmissionPeers, TurnControlProjection } from './context'

/** 回合取消与回复替换：只处理本窗口拥有的回合，迟到回执按会话与请求身份丢弃。 */
export function createTurnControl(base: SubmissionBase, peers: SubmissionPeers): TurnControlProjection {
  const {
    $chatSessionId,
    $chatTurnInFlight,
    $stoppingRequestId,
    $replaceableAssistantMessage,
    $replacingReplyMessageId,
    $retryableAssistantMessage,
    $chatMessageBodies
  } = base.state.stores

  // 取消器只依赖本模块函数与协作面能力；回调在建立后才可能触发，声明顺序不构成依赖。
  const cancellation = createTurnCancellation({
    isCurrent: base.isCurrent,
    read: turn => readConversationTurn(turn.sessionId, turn.requestId),
    cancel: turn =>
      requestResource('sessions.cancel_turn', {
        path: { session_id: turn.sessionId, request_id: turn.requestId }
      }),
    onSettled: async (receipt, turn) => {
      if ($stoppingRequestId.get() === turn.requestId) {
        $stoppingRequestId.set(null)
      }

      if (!ownsTurn(turn)) {
        return
      }

      peers.mergePersistedUserMessages(receipt.messages, receipt.request_id, receipt.message_ids, false)
      peers.applySessionState({ ...receipt, running: false })
      await refreshAfterCancellation(turn)
    },
    onRejected: async (error, turn) => {
      if ($stoppingRequestId.get() === turn.requestId) {
        $stoppingRequestId.set(null)
      }

      if (!ownsTurn(turn)) {
        return
      }

      if (base.state.submission.unconfirmedSubmission?.requestId === turn.requestId) {
        base.state.submission.unconfirmedSubmission = null
      }

      base.state.submission.activeRequestId = null
      base.state.submission.settledRequestId = turn.requestId
      peers.rememberSettled(turn.requestId)
      $chatTurnInFlight.set(false)
      base.messages.api.markAssistantTerminal({ error: errorMessage(error, getStrings().chat.sendFailed) })
      await refreshAfterCancellation(turn)
    },
    onError: error => notifyError(error, getStrings().chat.sendFailed)
  })

  function ownsTurn(turn: TurnReference): boolean {
    return (
      base.isCurrent() &&
      $chatSessionId.get() === turn.sessionId &&
      (!base.state.submission.activeRequestId || base.state.submission.activeRequestId === turn.requestId)
    )
  }

  async function refreshAfterCancellation(turn: TurnReference): Promise<void> {
    presentationPorts().setSpriteState('idle', { force: true })

    try {
      await requestResource('sessions.snapshot', { path: { session_id: turn.sessionId } })
    } catch (error) {
      if (base.isCurrent()) {
        notifyError(error, getStrings().chat.sendFailed)
      }
    }

    if (base.isCurrent() && $chatSessionId.get() === turn.sessionId) {
      peers.submitPendingBatch()
    }
  }

  function cancellationRequested(requestId: string): boolean {
    const sessionId = $chatSessionId.get()

    return Boolean(sessionId && cancellation.requested({ sessionId, requestId }))
  }

  function submissionFailed(params: ConversationSubmission, error: unknown): void {
    if (error instanceof ApiError && error.status !== null && error.status >= 400 && error.status < 500) {
      cancellation.rejected({ sessionId: params.session_id, requestId: params.request_id }, error)
    }
  }

  async function stopActiveTurn(): Promise<void> {
    peers.cancelPendingFlush()
    const sessionId = $chatSessionId.get()

    const requestId = base.state.submission.activeRequestId ?? base.state.submission.unconfirmedSubmission?.requestId

    if (!sessionId || !requestId || !base.isCurrent()) {
      return
    }

    $chatTurnInFlight.set(true)
    $stoppingRequestId.set(requestId)
    await cancellation.request({ sessionId, requestId })
  }

  async function replaceAssistantReply(messageId: string): Promise<void> {
    const sessionId = $chatSessionId.get()
    const message = $replaceableAssistantMessage.get()

    if (!sessionId || message?.id !== messageId || !message.backendMessageId) {
      return
    }

    const epoch = currentClearEpoch()
    $replacingReplyMessageId.set(message.backendMessageId)
    $chatTurnInFlight.set(true)
    conversationVoiceSink().cancel(sessionId)

    const params = peers.preparePromptSubmission({
      session_id: sessionId,
      replace_reply_message_id: message.backendMessageId,
      response_preference: presentationPorts().getResponsePreference()
    })

    try {
      peers.acceptPromptSubmission(await submitConversationTurn(params))
    } catch (error) {
      submissionFailed(params, error)

      if (
        base.isCurrent() &&
        epoch === currentClearEpoch() &&
        $chatSessionId.get() === sessionId &&
        base.state.submission.unconfirmedSubmission?.requestId === params.request_id &&
        !cancellationRequested(params.request_id)
      ) {
        $chatTurnInFlight.set(false)
        $replacingReplyMessageId.set(null)
        notifyError(error, getStrings().chat.sendFailed)
      }
    }
  }

  async function retryAssistantReply(messageId: string): Promise<void> {
    const sessionId = $chatSessionId.get()
    const body = $chatMessageBodies.get()[messageId]

    if (!sessionId || $retryableAssistantMessage.get()?.id !== messageId || !body?.retryMessageId) {
      return
    }

    const epoch = currentClearEpoch()
    $chatTurnInFlight.set(true)
    conversationVoiceSink().cancel(sessionId)

    const params = peers.preparePromptSubmission({
      session_id: sessionId,
      retry_message_id: body.retryMessageId,
      response_preference: presentationPorts().getResponsePreference()
    })

    try {
      peers.acceptPromptSubmission(await submitConversationTurn(params))
    } catch (error) {
      submissionFailed(params, error)

      // 已收到开始/完成事件时请求已被接受，迟到的请求失败不能覆盖新回复。
      if (
        base.isCurrent() &&
        epoch === currentClearEpoch() &&
        $chatSessionId.get() === sessionId &&
        $chatMessageBodies.get()[messageId] === body &&
        base.state.submission.unconfirmedSubmission !== null &&
        !cancellationRequested(params.request_id)
      ) {
        $chatTurnInFlight.set(false)
        notifyError(error, getStrings().chat.sendFailed)
      }
    }
  }

  return {
    cancellation,
    ownsTurn,
    refreshAfterCancellation,
    cancellationRequested,
    submissionFailed,
    stopActiveTurn,
    replaceAssistantReply,
    retryAssistantReply
  }
}
