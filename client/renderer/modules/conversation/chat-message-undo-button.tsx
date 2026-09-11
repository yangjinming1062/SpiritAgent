import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'

import { ArrowBackUp, Loader2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { $chatSessionId } from './chat-store'
import { undoToMessage } from './session-list-store'

interface ChatMessageUndoButtonProps {
  /** 当前气泡的本地 id；同一时刻只允许一个 undo RPC 在飞，靠此 id 排他。 */
  messageId: string
  /** 后端 Message.id——直接回传给 session.undo_to_message 作为 source_message_id。 */
  sourceMessageId: number
  className?: string
}

const $chatUndoInFlight = atom<string | null>(null)

export function ChatMessageUndoButton({
  messageId,
  sourceMessageId,
  className = ''
}: ChatMessageUndoButtonProps): React.JSX.Element {
  const dict = useStrings()
  const sourceSessionId = useStore($chatSessionId)
  const inFlight = useStore($chatUndoInFlight)

  const isMineInFlight = inFlight === messageId
  const otherBusy = inFlight !== null && !isMineInFlight

  const onClick = async (e: React.MouseEvent): Promise<void> => {
    e.stopPropagation()

    if (!sourceSessionId || inFlight !== null) {
      return
    }

    if (!window.confirm(dict.chat.undo.confirm)) {
      return
    }

    $chatUndoInFlight.set(messageId)

    try {
      const ok = await undoToMessage(sourceSessionId, sourceMessageId)

      if (!ok) {
        notifyError(new Error(dict.chat.undo.internalError), dict.chat.undo.failed)
      }
    } catch (err) {
      notifyError(err, dict.chat.undo.failed)
    } finally {
      if ($chatUndoInFlight.get() === messageId) {
        $chatUndoInFlight.set(null)
      }
    }
  }

  let label: string = dict.chat.undo.label
  let icon = <ArrowBackUp className="size-3.5" />
  let stateClass = 'text-muted hover:bg-fill-hover/80 hover:text-strong'

  if (isMineInFlight) {
    label = dict.chat.undo.inFlight
    icon = <Loader2 className="size-3.5 animate-spin text-accent" />
    stateClass = 'text-accent'
  } else if (otherBusy) {
    label = dict.chat.undo.otherBusy
    icon = <ArrowBackUp className="size-3.5 opacity-30" />
    stateClass = 'cursor-not-allowed text-faint/40 opacity-50'
  }

  return (
    <button
      aria-label={label}
      className={cn(
        'inline-flex size-6 shrink-0 items-center justify-center rounded-md transition select-none',
        stateClass,
        className
      )}
      data-busy={isMineInFlight || undefined}
      disabled={otherBusy || isMineInFlight}
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
