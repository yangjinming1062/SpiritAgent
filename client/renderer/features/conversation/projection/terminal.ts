import { conversationVoiceSink } from '../voice-link'

import type { AssistantProjection, ProjectionShared, RowWriter, TerminalProjection } from './context'

/** 回合终态：把错误、取消与重试标记落到当前回复，并在没有可写气泡时补一行。 */
export function createTerminalProjection(
  shared: ProjectionShared,
  rows: RowWriter,
  assistant: AssistantProjection
): TerminalProjection {
  const { $chatMessageList, $chatMessageBodies, $lastAssistantStreaming, $chatSessionId } = shared.state.stores

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
    shared.inflight.value = null
    conversationVoiceSink().cancel($chatSessionId.get())

    const list = $chatMessageList.get()

    const existing = shared.state.submission.activeRequestId
      ? list.findLast(item => $chatMessageBodies.get()[item.id]?.requestId === shared.state.submission.activeRequestId)
      : undefined

    const last = assistant.lastAssistantMessage()

    const terminal = {
      ...(shared.state.submission.activeRequestId && { requestId: shared.state.submission.activeRequestId }),
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

    const id = rows.appendMessage({ role: 'assistant' }, { text: '', ...terminal, streaming: false, toolName: null })
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
    shared.mediaUpdates.clear()
    shared.videoQueueUpdates.clear()
    shared.inflight.value = null
  }

  const clearInflightReply = (): void => {
    shared.inflight.value = null
  }

  return { markAssistantTerminal, clearReplyState, clearInflightReply }
}
