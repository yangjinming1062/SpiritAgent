import type { ReactNode } from 'react'
import { useState } from 'react'

import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { ChevronDown } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

/**
 * 折叠区。三种形态对应三档视觉密度：
 * - `panel`：带卡片边框与箭头，用于设置页与主操作区。
 * - `bordered`：紧凑边框卡片，用于制作区里的当前成品等内容块。
 * - `inline`：无边框轻量折叠，用于详情补充信息。
 */
export type DisclosureVariant = 'panel' | 'bordered' | 'inline'

const DETAIL_CLASS: Record<DisclosureVariant, string> = {
  panel: 'group rounded-xl border border-line-hairline bg-surface-card',
  bordered: 'rounded-xl border border-line-hairline p-3',
  inline: ''
}

const SUMMARY_CLASS: Record<DisclosureVariant, string> = {
  panel:
    'flex cursor-pointer list-none items-center gap-3 rounded-xl px-4 py-4 text-sm font-medium text-strong focus-visible:outline-2 focus-visible:outline-accent',
  bordered: 'cursor-pointer text-sm font-medium text-strong',
  inline: 'cursor-pointer'
}

const BODY_CLASS: Record<DisclosureVariant, string> = {
  panel: 'border-t border-line-hairline px-4 py-5',
  bordered: 'mt-3',
  inline: 'mt-2'
}

export function Disclosure({
  title,
  hint,
  children,
  defaultOpen = false,
  open: controlledOpen,
  onOpenChange,
  variant = 'panel',
  className,
  bodyClassName,
  summaryClassName
}: {
  title: string
  hint?: string
  children: ReactNode
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
  variant?: DisclosureVariant
  className?: string
  bodyClassName?: string
  summaryClassName?: string
}): React.JSX.Element {
  const [expanded, setExpanded] = useState(defaultOpen)
  const [visited, setVisited] = useState(defaultOpen)
  const open = controlledOpen ?? expanded
  const panel = variant === 'panel'

  return (
    <details
      className={cn(DETAIL_CLASS[variant], className)}
      onToggle={event => {
        setExpanded(event.currentTarget.open)
        onOpenChange?.(event.currentTarget.open)

        if (event.currentTarget.open) {
          setVisited(true)
        }
      }}
      open={open}
    >
      <summary className={cn(SUMMARY_CLASS[variant], summaryClassName)}>
        {panel ? (
          <span className="min-w-0 flex-1">
            <span className="block">{title}</span>
            {hint && <span className="mt-1 block text-xs font-normal leading-relaxed text-muted">{hint}</span>}
          </span>
        ) : (
          title
        )}
        {panel && (
          <ChevronDown aria-hidden="true" className="size-4 shrink-0 transition-transform group-open:rotate-180" />
        )}
      </summary>
      {(visited || open) && (
        <PanelActivityProvider active={open}>
          <div className={cn(BODY_CLASS[variant], bodyClassName)}>{children}</div>
        </PanelActivityProvider>
      )}
    </details>
  )
}
