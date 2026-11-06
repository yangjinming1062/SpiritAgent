import { conversationVoiceSink } from '../voice-link'

import type { ProjectionContext } from './context'

/** 回合终态：把错误、取消与重试标记落到当前回复，并在没有可写气泡时补一行。 */
export function installTerminal(ctx: ProjectionContext): void {
  const { $chatMessageList, $chatMessageBodies, $lastAssistantStreaming, $chatSessionId } = ctx.state.stores

  function markAssistantTerminal({
    error,
    cancelled,
    retryMessageId,
    beforePending = false
  }: {
    error?: string
    cancelled?: boolean
    retryMessageId?: string
    beforePending?: boolean
  } = {}): void {
    ctx.inflight.value = null
    conversationVoiceSink().cancel($chatSessionId.get())

    const list = $chatMessageList.get()

    const existing = ctx.state.submission.activeRequestId
      ? list.findLast(item => $chatMessageBodies.get()[item.id]?.requestId === ctx.state.submission.activeRequestId)
      : undefined

    const last = ctx.lastAssistantMessage()

    const terminal = {
      ...(ctx.state.submission.activeRequestId && { requestId: ctx.state.submission.activeRequestId }),
      ...(error !== undefined && { error }),
      ...(cancelled && { cancelled: true }),
      ...(retryMessageId !== undefined && { retryMessageId })
    }

    if (existing && !$chatMessageBodies.get()[existing.id]?.streaming) {
      $chatMessageBodies.setKey(existing.id, { ...$chatMessageBodies.get()[existing.id], ...terminal })

      return
    }

    if (
      last &&
      !last.body.streaming &&
      ((error !== undefined && last.body.error === error && last.body.retryMessageId === retryMessageId) ||
        (cancelled && last.body.cancelled))
    ) {
      return
    }

    if (last?.body.streaming) {
      const { body, item } = last
      $chatMessageBodies.setKey(item.id, {
        ...body,
        text: (body.streamingText ?? body.text).trim(),
        streamingText: undefined,
        streaming: false,
        ...terminal
      })
      $lastAssistantStreaming.set(false)

      return
    }

    const id = ctx.appendMessage({ role: 'assistant' }, { text: '', ...terminal, streaming: false, toolName: null })
    const updated = $chatMessageList.get()
    const pendingIndex = updated.findIndex(item => item.role === 'user' && item.backendMessageId === undefined)

    if (beforePending && pendingIndex >= 0) {
      const terminalRow = updated.find(item => item.id === id)

      if (terminalRow) {
        $chatMessageList.set([...updated.slice(0, pendingIndex), terminalRow, ...updated.slice(pendingIndex, -1)])
      }
    }

    $lastAssistantStreaming.set(false)
  }

  const clearReplyState = (): void => {
    ctx.mediaUpdates.clear()
    ctx.videoQueueUpdates.clear()
    ctx.inflight.value = null
  }

  const clearInflightReply = (): void => {
    ctx.inflight.value = null
  }

  Object.assign(ctx, { markAssistantTerminal, clearReplyState, clearInflightReply })
}
