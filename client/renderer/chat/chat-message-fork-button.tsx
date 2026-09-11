import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'

import { $chatSessionId } from '@/chat/chat-store'
import { forkConversation } from '@/chat/session-list-store'
import { GitFork, Loader2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

interface ChatMessageForkButtonProps {
  /** 当前气泡的本地 id；同一时刻只允许一个 fork RPC 在飞，并发点击靠此 id 排他。 */
  messageId: string
  /** 后端 Message.id——直接回传给 session.fork 作为 source_message_id。 */
  sourceMessageId: number
  className?: string
}

const $chatForkInFlight = atom<string | null>(null)

export function ChatMessageForkButton({
  messageId,
  sourceMessageId,
  className = ''
}: ChatMessageForkButtonProps): React.JSX.Element {
  const dict = useStrings()
  const sourceSessionId = useStore($chatSessionId)
  const inFlight = useStore($chatForkInFlight)

  const isMineInFlight = inFlight === messageId
  const otherBusy = inFlight !== null && !isMineInFlight

  const onClick = async (e: React.MouseEvent): Promise<void> => {
    e.stopPropagation()

    if (!sourceSessionId || inFlight !== null) {
      return
    }

    $chatForkInFlight.set(messageId)

    try {
      // forkConversation 失败返回 null 时给用户一个具体提示——它内部只 log 不 toast
      const newId = await forkConversation(sourceSessionId, sourceMessageId)

      if (!newId) {
        notifyError(new Error(dict.chat.fork.internalError), dict.chat.fork.failed)
      }
    } catch (err) {
      notifyError(err, dict.chat.fork.failed)
    } finally {
      if ($chatForkInFlight.get() === messageId) {
        $chatForkInFlight.set(null)
      }
    }
  }

  let label: string = dict.chat.fork.label
  let icon = <GitFork className="size-3.5" />
  let stateClass = 'text-muted hover:bg-fill-hover/80 hover:text-strong'

  if (isMineInFlight) {
    label = dict.chat.fork.inFlight
    icon = <Loader2 className="size-3.5 animate-spin text-accent" />
    stateClass = 'text-accent'
  } else if (otherBusy) {
    label = dict.chat.fork.otherBusy
    icon = <GitFork className="size-3.5 opacity-30" />
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
