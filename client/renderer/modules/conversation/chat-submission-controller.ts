import { SpiritAgentRpcError, SpiritAgentRpcErrorCode } from '@/shared/lib/gateway-protocol'
import { currentClearEpoch } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { $gateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { PromptSubmissionResult, SessionMessage } from '@protocol'
import { errorMessage, sleep } from '@runtime'

import type { ChatMessageController } from './chat-message-controller'
import { extractMessageContent, isPositiveInt, splitUserText } from './chat-message-rules'
import type { ConversationState } from './chat-runtime-state'
import type { ChatMessageListItem, PendingPromptItem } from './chat-runtime-types'
import { FLUSH_DEBOUNCE_MS, nextChatMessageId } from './conversation-state'
import { conversationVoiceSink } from './voice-link'

export function createChatSubmissionController(
  state: ConversationState,
  messages: ChatMessageController,
  isCurrent: () => boolean
) {
  const {
    $chatSessionId,
    $chatMessageList,
    $chatMessageBodies,
    $chatTurnInFlight,
    $lastAssistantStreaming,
    $retryableAssistantMessage,
    $pendingPromptBatch
  } = state.stores

  const { splitUserBubblesEnabled, reconcilePersistedMessages } = messages.internal
  const { markAssistantTerminal } = messages.api

  let flushTimer: ReturnType<typeof setTimeout> | null = null

  async function retryAssistantReply(messageId: string): Promise<void> {
    const sessionId = $chatSessionId.get()
    const gateway = $gateway.get()
    const body = $chatMessageBodies.get()[messageId]

    if (
      !sessionId ||
      !gateway ||
      gateway.connectionState !== 'open' ||
      $retryableAssistantMessage.get()?.id !== messageId ||
      !body?.retryMessageId
    ) {
      return
    }

    const epoch = currentClearEpoch()
    $chatTurnInFlight.set(true)
    conversationVoiceSink().cancel($chatSessionId.get())

    try {
      acceptPromptSubmission(
        await gateway.request<PromptSubmissionResult>(
          'prompt.submit',
          preparePromptSubmission({
            session_id: sessionId,
            retry_message_id: body.retryMessageId,
            response_preference: presentationPorts().getResponsePreference()
          })
        )
      )
    } catch (error) {
      // 已收到开始/完成事件时请求已被接受，迟到的 RPC 失败不能覆盖新回复。
      if (
        isCurrent() &&
        epoch === currentClearEpoch() &&
        $chatSessionId.get() === sessionId &&
        $chatMessageBodies.get()[messageId] === body &&
        state.submission.unconfirmedSubmission !== null
      ) {
        $chatTurnInFlight.set(false)
        notifyError(error, getStrings().chat.sendFailed)
      }
    }
  }

  function bindTrailingUserMessageIds(ids: number[], requestId?: string): void {
    if (!requestId || requestId !== state.submission.submittedRequestId) {
      return
    }

    // 只绑定本批气泡；拆泡超出消息 ID 数时沿用最后一个 ID，与历史水合一致。
    const validIds = ids.filter(isPositiveInt)

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
        state.submission.submittedBubbleIds.has(item.id)
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

  function preparePromptSubmission(params: Record<string, unknown>, bubbleIds: string[] = []): Record<string, unknown> {
    const fingerprint = JSON.stringify(params)

    const requestId =
      state.submission.unconfirmedSubmission?.fingerprint === fingerprint
        ? state.submission.unconfirmedSubmission.requestId
        : crypto.randomUUID()

    state.submission.submittedRequestId = requestId
    state.submission.activeRequestId = requestId
    state.submission.submittedBubbleIds = new Set(bubbleIds)
    const prepared = { ...params, request_id: requestId }
    state.submission.unconfirmedSubmission = { params: prepared, fingerprint, requestId }

    return prepared
  }

  function mergePersistedUserMessages(
    messages: SessionMessage[],
    requestId?: string,
    ids: number[] = [],
    trackActive = true
  ): void {
    bindTrailingUserMessageIds(ids, requestId)

    if (requestId) {
      if (trackActive) {
        state.submission.activeRequestId = requestId
      }

      if (state.submission.unconfirmedSubmission?.requestId === requestId) {
        state.submission.unconfirmedSubmission = null
      }
    }

    const original = $chatMessageList.get()
    const seen = new Set(original.map(item => item.backendMessageId))
    const added: ChatMessageListItem[] = []

    for (const message of messages) {
      if (message.role !== 'user' || !isPositiveInt(message.id) || seen.has(message.id)) {
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

  function applySessionState(event: {
    request_id?: string
    running?: boolean
    status?: string
    error?: string | null
  }): void {
    if (
      event.request_id &&
      ((state.submission.activeRequestId && event.request_id !== state.submission.activeRequestId) ||
        (event.running && event.request_id === state.submission.settledRequestId))
    ) {
      return
    }

    if (event.running) {
      state.submission.activeRequestId = event.request_id ?? state.submission.activeRequestId
      $chatTurnInFlight.set(true)

      return
    }

    const unfinished = $chatTurnInFlight.get() || $lastAssistantStreaming.get()

    if (unfinished && (event.status === 'cancelled' || event.status === 'interrupted')) {
      markAssistantTerminal({ cancelled: true })
    } else if (unfinished && event.status === 'failed') {
      markAssistantTerminal({ error: event.error || getStrings().chat.sendFailed })
    }

    $chatTurnInFlight.set(false)

    if (event.request_id === state.submission.activeRequestId) {
      state.submission.settledRequestId = event.request_id
      state.submission.activeRequestId = null
    }

    if (event.request_id === state.submission.unconfirmedSubmission?.requestId) {
      state.submission.unconfirmedSubmission = null
    }
  }

  function acceptPromptSubmission(result: PromptSubmissionResult): void {
    if (!isCurrent() || result.request_id !== state.submission.submittedRequestId) {
      return
    }

    if (result.message_ids?.length || result.messages?.length) {
      mergePersistedUserMessages(
        result.messages ?? [],
        result.request_id,
        result.message_ids ?? [],
        result.status === 'accepted' || result.status === 'running'
      )
    }

    if (result.status !== 'accepted' && result.status !== 'running') {
      applySessionState({ ...result, running: false })
    }
  }

  async function recoverUnconfirmedSubmission(): Promise<void> {
    if (state.submission.submissionRecovery) {
      return state.submission.submissionRecovery
    }

    const pending = state.submission.unconfirmedSubmission
    const gateway = $gateway.get()

    if (
      !pending ||
      !gateway ||
      !isCurrent() ||
      gateway.connectionState !== 'open' ||
      ($chatTurnInFlight.get() && state.submission.activeRequestId !== pending.requestId)
    ) {
      return
    }

    state.submission.activeRequestId = pending.requestId
    $chatTurnInFlight.set(true)

    const recovery = (async (): Promise<void> => {
      try {
        const result = await gateway.request<PromptSubmissionResult>('prompt.submit', pending.params)

        if (isCurrent() && state.submission.unconfirmedSubmission === pending) {
          acceptPromptSubmission(result)
        }
      } catch (error) {
        if (
          isCurrent() &&
          state.submission.unconfirmedSubmission === pending &&
          state.submission.activeRequestId === pending.requestId
        ) {
          $chatTurnInFlight.set(false)
          notifyError(error, getStrings().chat.sendFailed)
        }
      }
    })()

    state.submission.submissionRecovery = recovery

    try {
      await recovery
    } finally {
      if (state.submission.submissionRecovery === recovery) {
        state.submission.submissionRecovery = null
      }
    }
  }

  function acceptsTurnEvent(requestId?: string): boolean {
    return (
      !requestId ||
      (requestId !== state.submission.settledRequestId &&
        (!state.submission.activeRequestId || state.submission.activeRequestId === requestId))
    )
  }

  function isPromptUnconfirmed(requestId: unknown): boolean {
    return typeof requestId === 'string' && state.submission.unconfirmedSubmission?.requestId === requestId
  }

  function pushPendingPrompt(item: PendingPromptItem): void {
    const last = $chatMessageList.get().at(-1)
    $pendingPromptBatch.set([
      ...$pendingPromptBatch.get(),
      { ...item, messageId: last?.role === 'user' ? last.id : undefined }
    ])
  }

  function drainPendingPrompts(): PendingPromptItem[] {
    const items = $pendingPromptBatch.get()
    $pendingPromptBatch.set([])

    return items
  }

  function clearPendingPrompts(): void {
    $pendingPromptBatch.set([])
  }

  function schedulePendingFlush(): void {
    if ($pendingPromptBatch.get().length === 0) {
      return
    }

    if (flushTimer) {
      clearTimeout(flushTimer)
    }

    flushTimer = setTimeout(() => {
      flushTimer = null
      submitPendingBatch()
    }, FLUSH_DEBOUNCE_MS)
  }

  function cancelPendingFlush(): void {
    if (flushTimer) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
  }

  function submitPendingBatch(): void {
    if ($chatTurnInFlight.get() || flushTimer !== null) {
      return
    }

    const sessionId = $chatSessionId.get()
    const gateway = $gateway.get()

    if (!sessionId || !gateway || gateway.connectionState !== 'open') {
      return
    }

    const pendingBatch = drainPendingPrompts()

    if (pendingBatch.length === 0) {
      return
    }

    $chatTurnInFlight.set(true)

    const pendingIds = new Set(pendingBatch.map(p => p.messageId).filter((id): id is string => Boolean(id)))
    const list = $chatMessageList.get()
    const pendingRows = list.filter(item => pendingIds.has(item.id))
    const first = pendingRows[0]

    // 专业对话连发合回首条再提交；陪伴会话每条连发保留独立气泡（DB 仍合并为一行）。
    if (first && !splitUserBubblesEnabled()) {
      const bodies = $chatMessageBodies.get()
      const displayAttachments = pendingRows.flatMap(item => bodies[item.id]?.attachments ?? [])
      $chatMessageBodies.setKey(first.id, {
        ...bodies[first.id],
        text: pendingRows
          .map(item => bodies[item.id]?.text ?? '')
          .filter(Boolean)
          .join('\n\n'),
        attachments: displayAttachments.length ? displayAttachments : undefined
      })
      $chatMessageList.set([...list.filter(item => !pendingIds.has(item.id)), first])

      for (const item of pendingRows.slice(1)) {
        $chatMessageBodies.setKey(item.id, undefined)
      }
    }

    // 记录本批对应的存活气泡 id（合并路径只剩首条），persisted 据此精确绑定。
    state.submission.submittedBubbleIds = new Set(pendingRows.map(item => item.id))

    const attachments = pendingBatch.flatMap(p => p.attachments ?? [])
    const promptText = pendingBatch.map(p => p.text).join('\n\n')

    for (const id of state.submission.submittedBubbleIds) {
      const body = $chatMessageBodies.get()[id]

      if (body) {
        $chatMessageBodies.setKey(id, { ...body, editableText: promptText })
      }
    }

    const batchPayload = preparePromptSubmission(
      {
        session_id: sessionId,
        response_preference: presentationPorts().getResponsePreference(),
        batch: [
          {
            text: promptText,
            ...(attachments.length ? { attachments: attachments.map(a => ({ file_url: a.url, type: a.type })) } : {})
          }
        ]
      },
      [...state.submission.submittedBubbleIds]
    )

    const submittedRevision = state.historyEditRevision
    const submittedGeneration = state.submission.submissionGeneration
    const epoch = currentClearEpoch()

    // 失败只收尾本批所属runtime；断连或账户清理后不再回写。
    const failSubmit = (err?: unknown): void => {
      if (
        !isCurrent() ||
        epoch !== currentClearEpoch() ||
        submittedGeneration !== state.submission.submissionGeneration ||
        !isPromptUnconfirmed(batchPayload.request_id) ||
        (state.submission.activeRequestId !== null && state.submission.activeRequestId !== batchPayload.request_id)
      ) {
        return
      }

      const sendFailed = getStrings().chat.sendFailed

      markAssistantTerminal({ error: errorMessage(err, sendFailed) })
      // thinking（50）> idle（10）：不带 force 会被优先级门控吞掉，精灵卡在思考态。
      presentationPorts().setSpriteState('idle', { force: true })
      $chatTurnInFlight.set(false)
    }

    const submitWithRetry = async (attempt = 0): Promise<void> => {
      if (!isCurrent() || submittedGeneration !== state.submission.submissionGeneration) {
        return
      }

      const g = $gateway.get()

      // 首次提交前已确认连接；只有退避重试期间断连会走到这里。
      if (!g || g.connectionState !== 'open') {
        failSubmit()

        return
      }

      try {
        presentationPorts().setSpriteState('thinking')
        acceptPromptSubmission(await g.request<PromptSubmissionResult>('prompt.submit', batchPayload))
      } catch (err: unknown) {
        const turnBusy = err instanceof SpiritAgentRpcError && err.code === SpiritAgentRpcErrorCode.TurnBusy

        if (turnBusy && state.historyEditRevision !== submittedRevision && $chatSessionId.get() === sessionId) {
          // 另一个窗口的编辑先被接受；本批尚未落库，回到队列等该回合结束。
          $pendingPromptBatch.set([...pendingBatch, ...$pendingPromptBatch.get()])
          submitPendingBatch()

          return
        }

        if (turnBusy && attempt < 3) {
          await sleep(50 * Math.pow(2, attempt))

          return submitWithRetry(attempt + 1)
        }

        failSubmit(err)
      }
    }

    void submitWithRetry()
  }

  const abandonDisconnectedTurn = (): void => {
    state.submission.submissionGeneration++
    cancelPendingFlush()
    // 已受理回合继续执行，重连后由恢复快照确认终态。
    $chatTurnInFlight.set(false)
  }

  return {
    retryAssistantReply,
    bindTrailingUserMessageIds,
    preparePromptSubmission,
    mergePersistedUserMessages,
    applySessionState,
    acceptPromptSubmission,
    recoverUnconfirmedSubmission,
    acceptsTurnEvent,
    isPromptUnconfirmed,
    pushPendingPrompt,
    clearPendingPrompts,
    schedulePendingFlush,
    cancelPendingFlush,
    submitPendingBatch,
    abandonDisconnectedTurn
  }
}

export type ChatSubmissionController = ReturnType<typeof createChatSubmissionController>
