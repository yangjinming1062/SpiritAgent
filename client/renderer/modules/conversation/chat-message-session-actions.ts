import { useStore } from '@nanostores/react'
import { atom, type WritableAtom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { confirmAction, type ConfirmRequest } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { useConversationView } from './conversation-view'
import { forkConversation, undoToMessage } from './session-list-store'

// fork 与 undo 各持独立的在途标记，互不排他；同类操作同一时刻只允许一个 RPC 在飞。
interface SessionActionRequest {
  messageId: string
}

const $chatForkInFlight = atom<SessionActionRequest | null>(null)
const $chatUndoInFlight = atom<SessionActionRequest | null>(null)

registerStorageClearHandler(() => {
  $chatForkInFlight.set(null)
  $chatUndoInFlight.set(null)
})

async function runSessionAction({
  confirm,
  failedLabel,
  $inFlight,
  messageId,
  run,
  sourceMessageId,
  sourceSessionId
}: {
  /** 需二次确认时的弹窗内容（confirmAction），未提供则直接执行。 */
  confirm?: ConfirmRequest
  failedLabel: string
  $inFlight: WritableAtom<SessionActionRequest | null>
  /** 当前气泡的本地 id；同类操作同一时刻只允许一个 RPC 在飞，并发点击靠此 id 排他。 */
  messageId: string
  run: (sessionId: string, sourceMessageId: string) => Promise<unknown>
  /** 后端 Message.id——直接回传给会话 RPC 作为 source_message_id。 */
  sourceMessageId: string
  sourceSessionId: string | null
}): Promise<void> {
  const isCurrent = captureAuthScope()

  if (!isCurrent || !sourceSessionId || $inFlight.get() !== null) {
    return
  }

  if (confirm && !(await confirmAction(confirm))) {
    return
  }

  // 确认弹窗等待期间可能已有同类操作起飞，复核排他标记。
  if (!isCurrent() || $inFlight.get() !== null) {
    return
  }

  const request = { messageId }
  $inFlight.set(request)

  try {
    // 返回 null 表示已换号或换网关，结果作废，不再提示。
    await run(sourceSessionId, sourceMessageId)
  } catch (err) {
    if (isCurrent()) {
      notifyError(err, failedLabel)
    }
  } finally {
    if ($inFlight.get() === request) {
      $inFlight.set(null)
    }
  }
}

interface MessageSessionActionState {
  disabled: boolean
  label: string
}

export interface MessageSessionActions {
  fork: MessageSessionActionState
  undo: MessageSessionActionState
  runFork(sourceMessageId: string): Promise<void>
  runUndo(sourceMessageId: string): Promise<void>
}

/** 消息菜单里的 fork / undo：同类操作同一时刻只允许一个在途 RPC，undo 执行前须二次确认。 */
export function useMessageSessionActions({ messageId }: { messageId: string }): MessageSessionActions {
  const { $chatSessionId } = useConversationView().controller
  const dict = useStrings()

  const sourceSessionId = useStore($chatSessionId)
  const forkInFlight = useStore($chatForkInFlight)?.messageId ?? null
  const undoInFlight = useStore($chatUndoInFlight)?.messageId ?? null

  return {
    fork: {
      disabled: forkInFlight !== null && forkInFlight !== messageId,
      label:
        forkInFlight === messageId
          ? dict.chat.fork.inFlight
          : forkInFlight !== null
            ? dict.chat.fork.otherBusy
            : dict.chat.fork.label
    },
    undo: {
      disabled: undoInFlight !== null && undoInFlight !== messageId,
      label:
        undoInFlight === messageId
          ? dict.chat.undo.inFlight
          : undoInFlight !== null
            ? dict.chat.undo.otherBusy
            : dict.chat.undo.label
    },
    runFork: sourceMessageId =>
      runSessionAction({
        failedLabel: dict.chat.fork.failed,
        $inFlight: $chatForkInFlight,
        messageId,
        run: forkConversation,
        sourceMessageId,
        sourceSessionId
      }),
    runUndo: sourceMessageId =>
      runSessionAction({
        confirm: {
          confirmLabel: dict.chat.undo.confirmLabel,
          description: dict.chat.undo.confirmDescription,
          title: dict.chat.undo.confirmTitle
        },
        failedLabel: dict.chat.undo.failed,
        $inFlight: $chatUndoInFlight,
        messageId,
        run: undoToMessage,
        sourceMessageId,
        sourceSessionId
      })
  }
}
