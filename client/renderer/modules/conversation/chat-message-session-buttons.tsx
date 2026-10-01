import { useStore } from '@nanostores/react'
import { atom, type WritableAtom } from 'nanostores'
import type React from 'react'

import { ArrowBackUp, GitFork, type IconComponent, Loader2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { $chatSessionId } from './chat-store'
import { forkConversation, undoToMessage } from './session-list-store'

interface ChatMessageSessionButtonProps {
  /** 当前气泡的本地 id；同类操作同一时刻只允许一个 RPC 在飞，并发点击靠此 id 排他。 */
  messageId: string
  /** 后端 Message.id——直接回传给会话 RPC 作为 source_message_id。 */
  sourceMessageId: number
}

interface SessionActionButtonProps extends ChatMessageSessionButtonProps {
  $inFlight: WritableAtom<string | null>
  /** 存在时先弹系统确认框，取消则不执行。 */
  confirmText?: string
  Icon: IconComponent
  labels: { failed: string; inFlight: string; label: string; otherBusy: string }
  run: (sessionId: string, sourceMessageId: number) => Promise<unknown>
}

// fork 与 undo 各持独立的在途标记，互不排他。
const $chatForkInFlight = atom<string | null>(null)
const $chatUndoInFlight = atom<string | null>(null)

function SessionActionButton({
  $inFlight,
  confirmText,
  Icon,
  labels,
  messageId,
  run,
  sourceMessageId
}: SessionActionButtonProps): React.JSX.Element {
  const sourceSessionId = useStore($chatSessionId)
  const inFlight = useStore($inFlight)

  const isMineInFlight = inFlight === messageId
  const otherBusy = inFlight !== null && !isMineInFlight

  const onClick = async (e: React.MouseEvent): Promise<void> => {
    e.stopPropagation()

    if (!sourceSessionId || inFlight !== null) {
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
      notifyError(err, labels.failed)
    } finally {
      if ($inFlight.get() === messageId) {
        $inFlight.set(null)
      }
    }
  }

  let label = labels.label
  let icon = <Icon className="size-3.5" />
  let stateClass = 'text-muted hover:bg-fill-hover/80 hover:text-strong'

  if (isMineInFlight) {
    label = labels.inFlight
    icon = <Loader2 className="size-3.5 animate-spin text-accent" />
    stateClass = 'text-accent'
  } else if (otherBusy) {
    label = labels.otherBusy
    icon = <Icon className="size-3.5 opacity-30" />
    stateClass = 'cursor-not-allowed text-faint/40 opacity-50'
  }

  return (
    <button
      aria-label={label}
      className={cn(
        'inline-flex size-6 shrink-0 items-center justify-center rounded-md transition select-none',
        stateClass
      )}
      data-busy={isMineInFlight || undefined}
      disabled={inFlight !== null}
      onClick={e => {
        void onClick(e)
      }}
      title={label}
      type="button"
    >
      {icon}
    </button>
  )
}

export function ChatMessageForkButton(props: ChatMessageSessionButtonProps): React.JSX.Element {
  const dict = useStrings()

  return (
    <SessionActionButton
      {...props}
      $inFlight={$chatForkInFlight}
      Icon={GitFork}
      labels={dict.chat.fork}
      run={forkConversation}
    />
  )
}

export function ChatMessageUndoButton(props: ChatMessageSessionButtonProps): React.JSX.Element {
  const dict = useStrings()

  return (
    <SessionActionButton
      {...props}
      $inFlight={$chatUndoInFlight}
      confirmText={dict.chat.undo.confirm}
      Icon={ArrowBackUp}
      labels={dict.chat.undo}
      run={undoToMessage}
    />
  )
}
