import type React from 'react'

import { Pencil } from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'

import { startEditingMessage } from './chat-store'

export function ChatMessageEditButton({ messageId }: { messageId: string }): React.JSX.Element {
  const dict = useStrings()

  return (
    <button
      aria-label={dict.chat.edit.label}
      className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-fill-hover/80 hover:text-strong select-none"
      onClick={e => {
        e.stopPropagation()
        startEditingMessage(messageId)
      }}
      title={dict.chat.edit.label}
      type="button"
    >
      <Pencil className="size-3.5" />
    </button>
  )
}
