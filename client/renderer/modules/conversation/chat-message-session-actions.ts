import { useStore } from '@nanostores/react'
import { atom, type WritableAtom } from 'nanostores'

import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { useConversationView } from './conversation-view'
import { forkConversation, undoToMessage } from './session-list-store'

// fork 与 undo 各持独立的在途标记，互不排他；同类操作同一时刻只允许一个 RPC 在飞。
const $chatForkInFlight = atom<string | null>(null)
const $chatUndoInFlight = atom<string | null>(null)

async function runSessionAction({
  confirmText,
  failedLabel,
  $inFlight,
  messageId,
  run,
  sourceMessageId,
  sourceSessionId
}: {
  confirmText?: string
  failedLabel: string
  $inFlight: WritableAtom<string | null>
  /** 当前气泡的本地 id；同类操作同一时刻只允许一个 RPC 在飞，并发点击靠此 id 排他。 */
  messageId: string
  run: (sessionId: string, sourceMessageId: number) => Promise<unknown>
  /** 后端 Message.id——直接回传给会话 RPC 作为 source_message_id。 */
  sourceMessageId: number
  sourceSessionId: string | null
}): Promise<void> {
  if (!sourceSessionId || $inFlight.get() !== null) {
    return
  }

  if (confirmText !== undefined && !window.confirm(confirmText)) {
    return
  }

  $inFlight.set(messageId)

  try {
    // 返回 null 表示已换号或换网关，结果作废，不再提示。
    await run(sourceSessionId, sourceMessageId)
  } catch (err) {
    notifyError(err, failedLabel)
  } finally {
    if ($inFlight.get() === messageId) {
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
  runFork(sourceMessageId: number): Promise<void>
  runUndo(sourceMessageId: number): Promise<void>
}

/** 消息菜单里的 fork / undo：同类操作同一时刻只允许一个在途 RPC，undo 执行前须系统确认。 */
export function useMessageSessionActions({ messageId }: { messageId: string }): MessageSessionActions {
  const { $chatSessionId } = useConversationView().controller
  const dict = useStrings()

  const sourceSessionId = useStore($chatSessionId)
  const forkInFlight = useStore($chatForkInFlight)
  const undoInFlight = useStore($chatUndoInFlight)

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
        confirmText: dict.chat.undo.confirm,
        failedLabel: dict.chat.undo.failed,
        $inFlight: $chatUndoInFlight,
        messageId,
        run: undoToMessage,
        sourceMessageId,
        sourceSessionId
      })
  }
}
