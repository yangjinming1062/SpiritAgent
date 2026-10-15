import type { ReactNode } from 'react'
import { useState } from 'react'

import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { ChevronDown } from '@/shared/lib/icons'

export function Disclosure({
  title,
  hint,
  children,
  defaultOpen = false,
  open: controlledOpen,
  onOpenChange
}: {
  title: string
  hint?: string
  children: ReactNode
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
}): React.JSX.Element {
  const [expanded, setExpanded] = useState(defaultOpen)
  const [visited, setVisited] = useState(defaultOpen)
  const open = controlledOpen ?? expanded

  return (
    <details
      className="group rounded-xl border border-line-hairline bg-surface-card"
      onToggle={event => {
        setExpanded(event.currentTarget.open)
        onOpenChange?.(event.currentTarget.open)

        if (event.currentTarget.open) {
          setVisited(true)
        }
      }}
      open={open}
    >
      <summary className="flex cursor-pointer list-none items-center gap-3 rounded-xl px-4 py-4 text-sm font-medium text-strong focus-visible:outline-2 focus-visible:outline-accent">
        <span className="min-w-0 flex-1">
          <span className="block">{title}</span>
          {hint && <span className="mt-1 block text-xs font-normal leading-relaxed text-muted">{hint}</span>}
        </span>
        <ChevronDown aria-hidden="true" className="size-4 shrink-0 transition-transform group-open:rotate-180" />
      </summary>
      {(visited || open) && (
        <PanelActivityProvider active={open}>
          <div className="border-t border-line-hairline px-4 py-5">{children}</div>
        </PanelActivityProvider>
      )}
    </details>
  )
}
