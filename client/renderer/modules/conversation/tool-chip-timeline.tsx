// 工具芯片时间轴：工作台消息气泡内的可折叠工具调用列表。

import type React from 'react'

import { ChevronDown } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

interface ToolChipTimelineProps {
  active: boolean
  tools: string[]
}

export function ToolChipTimeline({ active, tools }: ToolChipTimelineProps): React.JSX.Element {
  const dict = useStrings()
  const current = tools[tools.length - 1]
  const count = tools.length

  return (
    <details className="mx-auto my-1.5 w-fit">
      <summary className="inline-flex cursor-pointer list-none items-center gap-2 rounded-full border border-line-standard bg-surface-card/70 px-3 py-1 text-xs text-muted shadow-xs backdrop-blur-glass transition hover:bg-fill-hover">
        <span className={cn('size-1.5 rounded-full bg-accent', active && 'animate-pulse')} />
        <span className="text-muted">{active ? dict.chat.tools.busy(current) : dict.chat.tools.completed(count)}</span>
        <ChevronDown className="size-3 text-faint transition-transform duration-200" />
      </summary>
      <div className="mt-1 flex flex-col items-center gap-1">
        {tools.map((name, index) => (
          <div
            className="inline-flex items-center gap-1.5 rounded-full border border-line-hairline bg-surface-panel/80 px-2.5 py-0.5 font-mono text-[11px] text-muted shadow-xs"
            key={`${name}-${index}`}
          >
            {name}
          </div>
        ))}
      </div>
    </details>
  )
}
