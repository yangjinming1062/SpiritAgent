import { currentClearEpoch } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import { errorMessage, sleep } from '@runtime'

import { ApiError } from '../../../../../shared/errors'
import { isRecord } from '../../../../../shared/gateway'
import { submitConversationTurn } from '../chat-requests'
import type { PendingPromptItem } from '../chat-runtime-types'
import { $chatDraftFromUndo, FLUSH_DEBOUNCE_MS } from '../conversation-state'

import type { PendingQueueProjection, SubmissionBase, SubmissionPeers } from './context'

/** 连发队列：合并等待项后按单批提交，turn_busy 时退回队列或有限指数退避。 */
export function createPendingQueue(base: SubmissionBase, peers: SubmissionPeers): PendingQueueProjection {
  const {
    $chatSessionId,
    $chatMessageList,
    $chatMessageBodies,
    $chatTurnInFlight,
    $stoppingRequestId,
    $pendingPromptBatch
  } = base.state.stores

  const { markAssistantTerminal } = base.messages.api
  const { splitUserBubblesEnabled } = base.messages.internal

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
    base.state.submission.submittedBubbleIds = new Set(pendingRows.map(item => item.id))

    const attachments = pendingBatch.flatMap(p => p.attachments ?? [])
    const promptText = pendingBatch.map(p => p.text).join('\n\n')

    for (const id of base.state.submission.submittedBubbleIds) {
      const body = $chatMessageBodies.get()[id]

      if (body) {
        $chatMessageBodies.setKey(id, { ...body, editableText: promptText })
      }
    }

    const batchPayload = peers.preparePromptSubmission(
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
      [...base.state.submission.submittedBubbleIds]
    )

    const submittedRevision = base.state.historyEditRevision
    const submittedGeneration = base.state.submission.submissionGeneration
    const epoch = currentClearEpoch()

    // 失败只收尾本批所属runtime；断连或账户清理后不再回写。
    const failSubmit = (err?: unknown): void => {
      if (
        !base.isCurrent() ||
        epoch !== currentClearEpoch() ||
        submittedGeneration !== base.state.submission.submissionGeneration ||
        !peers.isPromptUnconfirmed(batchPayload.request_id) ||
        (base.state.submission.activeRequestId !== null &&
          base.state.submission.activeRequestId !== batchPayload.request_id)
      ) {
        return
      }

      markAssistantTerminal({ error: errorMessage(err, getStrings().chat.sendFailed) })
      // thinking（50）> idle（10）：不带 force 会被优先级门控吞掉，精灵卡在思考态。
      presentationPorts().setSpriteState('idle', { force: true })
      $chatTurnInFlight.set(false)
    }

    const submitWithRetry = async (attempt = 0): Promise<void> => {
      if (!base.isCurrent() || submittedGeneration !== base.state.submission.submissionGeneration) {
        return
      }

      try {
        presentationPorts().setSpriteState('thinking')
        peers.acceptPromptSubmission(await submitConversationTurn(batchPayload))
      } catch (err: unknown) {
        peers.submissionFailed(batchPayload, err)

        if (peers.cancellationRequested(batchPayload.request_id)) {
          return
        }

        const turnBusy = err instanceof ApiError && err.code === 'turn_busy'

        if (
          err instanceof ApiError &&
          turnBusy &&
          isRecord(err.business.details) &&
          err.business.details.realtime_call === true &&
          base.isCurrent() &&
          epoch === currentClearEpoch() &&
          submittedGeneration === base.state.submission.submissionGeneration
        ) {
          base.state.submission.unconfirmedSubmission = null

          if (base.state.submission.activeRequestId === batchPayload.request_id) {
            base.state.submission.activeRequestId = null
          }

          $chatTurnInFlight.set(false)
          presentationPorts().setSpriteState('idle', { force: true })

          const bodies = $chatMessageBodies.get()

          const draftText = pendingRows
            .map(row => bodies[row.id]?.text ?? '')
            .filter(Boolean)
            .join('\n\n')

          const draftAttachments = pendingRows
            .flatMap(row => bodies[row.id]?.attachments ?? [])
            .filter(attachment => attachment.type === 'image')

          if (!draftText && draftAttachments.length === 0) {
            // 语音等无法回到输入框的批次仍按发送失败收尾，保留真实错误。
            failSubmit(err)

            return
          }

          // 通话占用的会话不受理提交：撤掉未落库气泡，把内容送回输入框等挂断后重发。
          const bubbleIds = base.state.submission.submittedBubbleIds
          $chatMessageList.set($chatMessageList.get().filter(item => !bubbleIds.has(item.id)))

          for (const id of bubbleIds) {
            $chatMessageBodies.setKey(id, undefined)
          }

          $chatDraftFromUndo.set({
            session_id: sessionId,
            text: draftText,
            attachments: draftAttachments,
            merge: true
          })
          notify({ kind: 'info', message: getStrings().chat.callBusyRetry })

          return
        }

        if (turnBusy && base.state.historyEditRevision !== submittedRevision && $chatSessionId.get() === sessionId) {
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

  return {
    pushPendingPrompt,
    clearPendingPrompts,
    schedulePendingFlush,
    cancelPendingFlush,
    submitPendingBatch
  }
}
