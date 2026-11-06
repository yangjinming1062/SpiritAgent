import { currentClearEpoch } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { getStrings } from '@/shared/strings'
import { errorMessage, sleep } from '@runtime'

import { ApiError } from '../../../../../shared/errors'
import { submitConversationTurn } from '../chat-requests'
import type { PendingPromptItem } from '../chat-runtime-types'
import { FLUSH_DEBOUNCE_MS } from '../conversation-state'

import type { SubmissionContext } from './context'

/** 连发队列：合并等待项后按单批提交，turn_busy 时退回队列或有限指数退避。 */
export function installQueue(ctx: SubmissionContext): void {
  const {
    $chatSessionId,
    $chatMessageList,
    $chatMessageBodies,
    $chatTurnInFlight,
    $stoppingRequestId,
    $pendingPromptBatch
  } = ctx.state.stores

  const { markAssistantTerminal } = ctx.messages.api
  const { splitUserBubblesEnabled } = ctx.messages.internal

  let flushTimer: ReturnType<typeof setTimeout> | null = null

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
    if ($chatTurnInFlight.get() || $stoppingRequestId.get() !== null || flushTimer !== null) {
      return
    }

    const sessionId = $chatSessionId.get()

    if (!sessionId) {
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
    ctx.state.submission.submittedBubbleIds = new Set(pendingRows.map(item => item.id))

    const attachments = pendingBatch.flatMap(p => p.attachments ?? [])
    const promptText = pendingBatch.map(p => p.text).join('\n\n')

    for (const id of ctx.state.submission.submittedBubbleIds) {
      const body = $chatMessageBodies.get()[id]

      if (body) {
        $chatMessageBodies.setKey(id, { ...body, editableText: promptText })
      }
    }

    const batchPayload = ctx.preparePromptSubmission(
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
      [...ctx.state.submission.submittedBubbleIds]
    )

    const submittedRevision = ctx.state.historyEditRevision
    const submittedGeneration = ctx.state.submission.submissionGeneration
    const epoch = currentClearEpoch()

    // 失败只收尾本批所属runtime；断连或账户清理后不再回写。
    const failSubmit = (err?: unknown): void => {
      if (
        !ctx.isCurrent() ||
        epoch !== currentClearEpoch() ||
        submittedGeneration !== ctx.state.submission.submissionGeneration ||
        !ctx.isPromptUnconfirmed(batchPayload.request_id) ||
        (ctx.state.submission.activeRequestId !== null &&
          ctx.state.submission.activeRequestId !== batchPayload.request_id)
      ) {
        return
      }

      markAssistantTerminal({ error: errorMessage(err, getStrings().chat.sendFailed) })
      // thinking（50）> idle（10）：不带 force 会被优先级门控吞掉，精灵卡在思考态。
      presentationPorts().setSpriteState('idle', { force: true })
      $chatTurnInFlight.set(false)
    }

    const submitWithRetry = async (attempt = 0): Promise<void> => {
      if (!ctx.isCurrent() || submittedGeneration !== ctx.state.submission.submissionGeneration) {
        return
      }

      try {
        presentationPorts().setSpriteState('thinking')
        ctx.acceptPromptSubmission(await submitConversationTurn(batchPayload))
      } catch (err: unknown) {
        ctx.submissionFailed(batchPayload, err)

        if (ctx.cancellationRequested(batchPayload.request_id)) {
          return
        }

        const turnBusy = err instanceof ApiError && err.code === 'turn_busy'

        if (turnBusy && ctx.state.historyEditRevision !== submittedRevision && $chatSessionId.get() === sessionId) {
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

  Object.assign(ctx, {
    pushPendingPrompt,
    clearPendingPrompts,
    schedulePendingFlush,
    cancelPendingFlush,
    submitPendingBatch
  })

  return
}
