import { presentationPorts } from '@/shared/presentation-ports'
import { getStrings } from '@/shared/strings'
import type { PromptSubmissionResult, SessionMessage } from '@protocol'

import type { GatewayEventPayloads } from '../../../../../shared/api.generated'
import { isResourceId } from '../../../../../shared/resource-id'
import { extractMessageContent, splitUserText } from '../chat-message-rules'
import type { ConversationSubmission } from '../chat-requests'
import type { ChatMessageListItem } from '../chat-runtime-types'
import { nextChatMessageId } from '../conversation-state'

import type { PromptSubmissionProjection, SubmissionBase, SubmissionPeers } from './context'

/** 用户消息落库对齐：把待确认气泡绑定到服务端消息 ID，并合并迟到水合。 */
export function createPromptSubmission(base: SubmissionBase, peers: SubmissionPeers): PromptSubmissionProjection {
  const {
    $chatSessionId,
    $chatMessageList,
    $chatMessageBodies,
    $chatTurnInFlight,
    $stoppingRequestId,
    $lastAssistantStreaming,
    $replacingReplyMessageId
  } = base.state.stores

  const { splitUserBubblesEnabled, reconcilePersistedMessages } = base.messages.internal
  const { markAssistantTerminal } = base.messages.api

  function bindTrailingUserMessageIds(ids: string[], requestId?: string): void {
    if (!requestId || requestId !== base.state.submission.submittedRequestId) {
      return
    }

    // 只绑定本批气泡；拆泡超出消息 ID 数时沿用最后一个 ID，与历史水合一致。
    const validIds = ids.filter(isResourceId)

    if (validIds.length === 0) {
      return
    }

    const list = $chatMessageList.get()
    const unboundIndexes: number[] = []

    for (let i = 0; i < list.length; i++) {
      const item = list[i]

      if (
        item.backendMessageId === undefined &&
        item.role === 'user' &&
        base.state.submission.submittedBubbleIds.has(item.id)
      ) {
        unboundIndexes.push(i)
      }
    }

    // 编辑已水合的落库通知不属于本窗口待确认的提交。
    if (unboundIndexes.length === 0) {
      return
    }

    const messageIdsByRow = new Map(
      unboundIndexes.map((rowIndex, index) => [rowIndex, validIds[index] ?? validIds[validIds.length - 1]])
    )

    const boundIds = new Set<string>()
    const bodies = $chatMessageBodies.get()

    const next = list.flatMap((item, index) => {
      const messageId = messageIdsByRow.get(index)

      if (messageId === undefined) {
        return [item]
      }

      const body = bodies[item.id]
      const segments = splitUserBubblesEnabled() ? splitUserText(body.text) : [body.text]

      return (segments.length ? segments : ['']).map((text, segmentIndex) => {
        const id = segmentIndex === 0 ? item.id : nextChatMessageId()
        $chatMessageBodies.setKey(id, {
          ...body,
          text,
          attachments: segmentIndex === 0 ? body.attachments : undefined
        })
        boundIds.add(id)

        return { ...item, id, backendMessageId: messageId }
      })
    })

    reconcilePersistedMessages(next, boundIds)
  }

  function preparePromptSubmission(
    params: Omit<ConversationSubmission, 'request_id'>,
    bubbleIds: string[] = []
  ): ConversationSubmission {
    const fingerprint = JSON.stringify(params)

    const requestId =
      base.state.submission.unconfirmedSubmission?.fingerprint === fingerprint
        ? base.state.submission.unconfirmedSubmission.requestId
        : crypto.randomUUID()

    base.state.submission.submittedRequestId = requestId
    base.state.submission.activeRequestId = requestId
    base.state.submission.submittedBubbleIds = new Set(bubbleIds)
    const prepared = { ...params, request_id: requestId }
    base.state.submission.unconfirmedSubmission = { params: prepared, fingerprint, requestId }

    return prepared
  }

  function mergePersistedUserMessages(
    messages: SessionMessage[],
    requestId?: string,
    ids: string[] = [],
    trackActive = true
  ): void {
    bindTrailingUserMessageIds(ids, requestId)

    if (requestId) {
      if (trackActive && !base.settledRequests.has(requestId) && requestId !== base.state.submission.settledRequestId) {
        base.state.submission.activeRequestId = requestId
      }

      if (base.state.submission.unconfirmedSubmission?.requestId === requestId) {
        base.state.submission.unconfirmedSubmission = null
      }
    }

    const original = $chatMessageList.get()
    const seen = new Set(original.map(item => item.backendMessageId))
    const added: ChatMessageListItem[] = []

    for (const message of messages) {
      if (message.role !== 'user' || !isResourceId(message.id) || seen.has(message.id)) {
        continue
      }

      seen.add(message.id)
      const content = extractMessageContent(message)
      const segments = !message.subtype && splitUserBubblesEnabled() ? splitUserText(content.text) : [content.text]

      for (const [index, text] of (segments.length ? segments : ['']).entries()) {
        const id = nextChatMessageId()
        $chatMessageBodies.setKey(id, {
          text,
          editableText: content.text,
          attachments: index === 0 ? content.attachments : undefined,
          streaming: false,
          toolName: null
        })
        added.push({ id, role: 'user', backendMessageId: message.id, timestamp: message.timestamp })
      }
    }

    if (added.length) {
      const pendingIndex = original.findIndex(item => item.role === 'user' && item.backendMessageId === undefined)
      const index = pendingIndex < 0 ? original.length : pendingIndex
      $chatMessageList.set([...original.slice(0, index), ...added, ...original.slice(index)])
    }
  }

  function applySessionState(event: Partial<GatewayEventPayloads['session.state']>): void {
    if (event.request_id && event.running === false) {
      peers.rememberSettled(event.request_id)

      if ($stoppingRequestId.get() === event.request_id) {
        $stoppingRequestId.set(null)
      }
    }

    if (
      event.request_id &&
      ((base.state.submission.activeRequestId && event.request_id !== base.state.submission.activeRequestId) ||
        (event.running &&
          (event.request_id === base.state.submission.settledRequestId || base.settledRequests.has(event.request_id))))
    ) {
      return
    }

    if (event.status === 'completed' && event.replace_reply_message_id && event.result_message_id) {
      const reply = event.messages?.find(
        message => message.id === event.result_message_id && message.role === 'assistant'
      )

      if (reply) {
        base.onReplyReplaced(event.replace_reply_message_id, reply)
      }
    }

    if (event.running) {
      base.state.submission.activeRequestId = event.request_id ?? base.state.submission.activeRequestId
      $chatTurnInFlight.set(true)

      return
    }

    const unfinished = $chatTurnInFlight.get() || $lastAssistantStreaming.get()
    const replacing = $replacingReplyMessageId.get() !== null || Boolean(event.replace_reply_message_id)

    if (unfinished && !replacing && (event.status === 'cancelled' || event.status === 'interrupted')) {
      markAssistantTerminal({ cancelled: true })
    } else if (unfinished && !replacing && event.status === 'failed') {
      markAssistantTerminal({ error: event.error || getStrings().chat.sendFailed })
    }

    const stopped =
      event.request_id && $chatSessionId.get()
        ? peers.cancellation.settled({ sessionId: $chatSessionId.get()!, requestId: event.request_id })
        : false

    $chatTurnInFlight.set(false)
    $replacingReplyMessageId.set(null)

    if (event.request_id === base.state.submission.activeRequestId) {
      base.state.submission.settledRequestId = event.request_id
      base.state.submission.activeRequestId = null
    }

    if (event.request_id === base.state.submission.unconfirmedSubmission?.requestId) {
      base.state.submission.unconfirmedSubmission = null
    }

    if (stopped) {
      presentationPorts().setSpriteState('idle', { force: true })
      peers.submitPendingBatch()
    }
  }

  function acceptPromptSubmission(result: PromptSubmissionResult): void {
    const sessionId = $chatSessionId.get()

    if (sessionId) {
      peers.cancellation.observe({ sessionId, requestId: result.request_id }, result)
    }

    if (!base.isCurrent() || result.request_id !== base.state.submission.submittedRequestId) {
      return
    }

    if (result.message_ids?.length || result.messages?.length) {
      mergePersistedUserMessages(
        result.messages ?? [],
        result.request_id,
        result.message_ids ?? [],
        (result.status === 'accepted' || result.status === 'running') && !base.settledRequests.has(result.request_id)
      )
    }

    if (result.status !== 'accepted' && result.status !== 'running') {
      applySessionState({ ...result, running: false })
    }
  }

  return {
    bindTrailingUserMessageIds,
    preparePromptSubmission,
    mergePersistedUserMessages,
    applySessionState,
    acceptPromptSubmission
  }
}
