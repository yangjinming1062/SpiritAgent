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

import type { SubmissionContext } from './context'

/** 回合取消与回复替换：只处理本窗口拥有的回合，迟到回执按会话与请求身份丢弃。 */
export function installCancel(ctx: SubmissionContext): void {
  const {
    $chatSessionId,
    $chatTurnInFlight,
    $stoppingRequestId,
    $replaceableAssistantMessage,
    $replacingReplyMessageId,
    $retryableAssistantMessage,
    $chatMessageBodies
  } = ctx.state.stores

  function ownsTurn(turn: TurnReference): boolean {
    return (
      ctx.isCurrent() &&
      $chatSessionId.get() === turn.sessionId &&
      (!ctx.state.submission.activeRequestId || ctx.state.submission.activeRequestId === turn.requestId)
    )
  }

  async function refreshAfterCancellation(turn: TurnReference): Promise<void> {
    presentationPorts().setSpriteState('idle', { force: true })

    try {
      await requestResource('sessions.snapshot', { path: { session_id: turn.sessionId } })
    } catch (error) {
      if (ctx.isCurrent()) {
        notifyError(error, getStrings().chat.sendFailed)
      }
    }

    if (ctx.isCurrent() && $chatSessionId.get() === turn.sessionId) {
      ctx.submitPendingBatch()
    }
  }

  function cancellationRequested(requestId: string): boolean {
    const sessionId = $chatSessionId.get()

    return Boolean(sessionId && ctx.cancellation.requested({ sessionId, requestId }))
  }

  function submissionFailed(params: ConversationSubmission, error: unknown): void {
    if (error instanceof ApiError && error.status !== null && error.status >= 400 && error.status < 500) {
      ctx.cancellation.rejected({ sessionId: params.session_id, requestId: params.request_id }, error)
    }
  }

  async function stopActiveTurn(): Promise<void> {
    ctx.cancelPendingFlush()
    const sessionId = $chatSessionId.get()
    const requestId = ctx.state.submission.activeRequestId ?? ctx.state.submission.unconfirmedSubmission?.requestId

    if (!sessionId || !requestId || !ctx.isCurrent()) {
      return
    }

    $chatTurnInFlight.set(true)
    $stoppingRequestId.set(requestId)
    await ctx.cancellation.request({ sessionId, requestId })
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

    const params = ctx.preparePromptSubmission({
      session_id: sessionId,
      replace_reply_message_id: message.backendMessageId,
      response_preference: presentationPorts().getResponsePreference()
    })

    try {
      ctx.acceptPromptSubmission(await submitConversationTurn(params))
    } catch (error) {
      submissionFailed(params, error)

      if (
        ctx.isCurrent() &&
        epoch === currentClearEpoch() &&
        $chatSessionId.get() === sessionId &&
        ctx.state.submission.unconfirmedSubmission?.requestId === params.request_id &&
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

    const params = ctx.preparePromptSubmission({
      session_id: sessionId,
      retry_message_id: body.retryMessageId,
      response_preference: presentationPorts().getResponsePreference()
    })

    try {
      ctx.acceptPromptSubmission(await submitConversationTurn(params))
    } catch (error) {
      submissionFailed(params, error)

      // 已收到开始/完成事件时请求已被接受，迟到的请求失败不能覆盖新回复。
      if (
        ctx.isCurrent() &&
        epoch === currentClearEpoch() &&
        $chatSessionId.get() === sessionId &&
        $chatMessageBodies.get()[messageId] === body &&
        ctx.state.submission.unconfirmedSubmission !== null &&
        !cancellationRequested(params.request_id)
      ) {
        $chatTurnInFlight.set(false)
        notifyError(error, getStrings().chat.sendFailed)
      }
    }
  }

  Object.assign(ctx, {
    ownsTurn,
    refreshAfterCancellation,
    cancellationRequested,
    submissionFailed,
    stopActiveTurn,
    replaceAssistantReply,
    retryAssistantReply
  })
}

/** 取消器由控制器创建；回调经上下文读取当前实现，避免固定安装顺序。 */
export function createSubmissionCancellation(ctx: SubmissionContext): ReturnType<typeof createTurnCancellation> {
  const { $stoppingRequestId, $chatTurnInFlight } = ctx.state.stores

  return createTurnCancellation({
    isCurrent: ctx.isCurrent,
    read: turn => readConversationTurn(turn.sessionId, turn.requestId),
    cancel: turn =>
      requestResource('sessions.cancel_turn', {
        path: { session_id: turn.sessionId, request_id: turn.requestId }
      }),
    onSettled: async (receipt, turn) => {
      if ($stoppingRequestId.get() === turn.requestId) {
        $stoppingRequestId.set(null)
      }

      if (!ctx.ownsTurn(turn)) {
        return
      }

      ctx.mergePersistedUserMessages(receipt.messages, receipt.request_id, receipt.message_ids, false)
      ctx.applySessionState({ ...receipt, running: false })
      await ctx.refreshAfterCancellation(turn)
    },
    onRejected: async (error, turn) => {
      if ($stoppingRequestId.get() === turn.requestId) {
        $stoppingRequestId.set(null)
      }

      if (!ctx.ownsTurn(turn)) {
        return
      }

      if (ctx.state.submission.unconfirmedSubmission?.requestId === turn.requestId) {
        ctx.state.submission.unconfirmedSubmission = null
      }

      ctx.state.submission.activeRequestId = null
      ctx.state.submission.settledRequestId = turn.requestId
      ctx.rememberSettled(turn.requestId)
      $chatTurnInFlight.set(false)
      ctx.messages.api.markAssistantTerminal({ error: errorMessage(error, getStrings().chat.sendFailed) })
      await ctx.refreshAfterCancellation(turn)
    },
    onError: error => notifyError(error, getStrings().chat.sendFailed)
  })
}
