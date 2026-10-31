import { useId, useLayoutEffect, useRef, useState } from 'react'

import { ChevronDown } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { BTN_COMPACT } from './palette'

const CLAMP: Record<2 | 3 | 4 | 6, string> = {
  2: 'line-clamp-2',
  3: 'line-clamp-3',
  4: 'line-clamp-4',
  6: 'line-clamp-6'
}

const TEXT_ACTION =
  '-ml-1 mt-1.5 inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-faint transition hover:bg-fill-hover hover:text-strong'

/**
 * 长段落先给短预览，只有真的被截断才出现展开入口。
 * action 决定入口档位：正文主体用描边按钮，附录类文字用轻量文字动作。
 */
export function ExpandableText({
  action = 'text',
  className,
  collapseLabel,
  expandLabel,
  lines = 3,
  text
}: {
  action?: 'button' | 'text'
  className?: string
  collapseLabel: string
  expandLabel: string
  lines?: 2 | 3 | 4 | 6
  text: string
}): React.JSX.Element {
  const textId = useId()
  const textRef = useRef<HTMLParagraphElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [clipped, setClipped] = useState(false)

  useLayoutEffect(() => {
    const element = textRef.current

    if (!element || expanded) {
      return
    }

    const update = (): void => setClipped(element.scrollHeight > element.clientHeight + 1)
    const observer = new ResizeObserver(update)
    observer.observe(element)
    update()

    return () => observer.disconnect()
  }, [expanded, text])

  return (
    <>
      <p
        className={cn('whitespace-pre-wrap break-words', !expanded && CLAMP[lines], className)}
        id={textId}
        ref={textRef}
      >
        {text}
      </p>
      {expanded || clipped ? (
        <button
          aria-controls={textId}
          aria-expanded={expanded}
          className={cn(
            'focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2',
            action === 'button' ? `${BTN_COMPACT} mt-2` : TEXT_ACTION
          )}
          onClick={() => setExpanded(value => !value)}
          type="button"
        >
          {expanded ? collapseLabel : expandLabel}
          <ChevronDown
            aria-hidden="true"
            className={cn(
              'shrink-0 transition-transform',
              action === 'button' ? 'size-3.5' : 'size-3',
              expanded && 'rotate-180'
            )}
          />
        </button>
      ) : null}
    </>
  )
}
