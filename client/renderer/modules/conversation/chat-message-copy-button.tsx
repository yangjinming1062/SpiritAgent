import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { Check, Copy } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

interface ChatMessageCopyButtonProps {
  className?: string
  text: string
}

export function ChatMessageCopyButton({ className = '', text }: ChatMessageCopyButtonProps): React.JSX.Element {
  const dict = useStrings()
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
    }
  }, [])

  const onClick = async (e: React.MouseEvent): Promise<void> => {
    e.stopPropagation()

    if (!text.trim()) {
      return
    }

    try {
      if (window.spiritagent?.writeClipboard) {
        await window.spiritagent.writeClipboard(text)
      } else {
        await navigator.clipboard.writeText(text)
      }

      setCopied(true)

      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }

      timerRef.current = setTimeout(() => {
        setCopied(false)
        timerRef.current = null
      }, 1500)
    } catch (err) {
      notifyError(err, dict.chat.copy.failed)
    }
  }

  const label = copied ? dict.chat.copy.copied : dict.chat.copy.label

  return (
    <button
      aria-label={label}
      className={cn(
        'inline-flex size-6 shrink-0 items-center justify-center rounded-md transition select-none',
        copied ? 'text-success hover:bg-fill-hover/80' : 'text-muted hover:bg-fill-hover/80 hover:text-strong',
        className
      )}
      onClick={e => {
        void onClick(e)
      }}
      title={label}
      type="button"
    >
      {copied ? <Check className="size-3.5 text-emerald-400" /> : <Copy className="size-3.5" />}
    </button>
  )
}
