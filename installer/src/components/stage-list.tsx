import type React from 'react'
import { useEffect, useRef } from 'react'
import { Check, X, ChevronRight, Loader2 } from 'lucide-react'
import clsx from 'clsx'
import type { BootstrapStateModel, StageState } from '../store'

interface StageListProps {
  bootstrap: BootstrapStateModel
}

export function StageList({ bootstrap }: StageListProps): React.JSX.Element {
  const logEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [bootstrap.logs.length])

  return (
    <div className="flex h-full overflow-hidden rounded-lg border border-line-strong bg-glass shadow-md backdrop-blur-xs">
      <div className="flex-1 overflow-y-auto px-4 py-3">
        <ol className="space-y-1">
          {bootstrap.stageOrder.map((name) => {
            const rec = bootstrap.stages[name]
            if (!rec) return null
            return (
              <li
                key={name}
                className={clsx(
                  'flex items-center gap-3 rounded-md px-3 py-1.5 text-xs transition-colors',
                  rec.state === 'running' && 'bg-accent-soft font-medium text-text-strong',
                  rec.state === 'succeeded' && 'text-text-strong',
                  rec.state === 'skipped' && 'text-text-muted',
                  rec.state === 'failed' && 'bg-destructive/15 text-destructive',
                  !rec.state && 'text-text-faint'
                )}
              >
                <StateIcon state={rec.state ?? null} />
                <span className="flex-1 truncate">{rec.info.title}</span>
                {rec.durationMs != null && (
                  <span className="text-[11px] font-mono text-text-muted">
                    {formatDuration(rec.durationMs)}
                  </span>
                )}
              </li>
            )
          })}
        </ol>
      </div>

      <div className="flex w-1/2 flex-col border-l border-line-standard bg-surface-card/80 backdrop-blur-xs">
        <div className="flex shrink-0 items-center justify-between border-b border-line-standard px-3 py-1.5">
          <div className="text-[11px] font-medium text-text-body">实时输出</div>
          <div className="text-[11px] text-text-muted">{bootstrap.logs.length} 行</div>
        </div>
        <div className="flex-1 overflow-y-auto px-3 py-2 font-mono text-[10px] leading-relaxed">
          {bootstrap.logs.map((entry, idx) => (
            <div
              key={idx}
              className={clsx(
                'whitespace-pre-wrap',
                entry.stream === 'stderr' ? 'text-destructive/80' : 'text-text-body'
              )}
            >
              {entry.line}
            </div>
          ))}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  )
}

function StateIcon({ state }: { state: StageState | null }) {
  if (state === 'running') {
    return <Loader2 size={13} className="animate-spin text-accent" />
  }
  if (state === 'succeeded') {
    return <Check size={13} className="text-success" />
  }
  if (state === 'skipped') {
    return <ChevronRight size={13} className="text-text-muted" />
  }
  if (state === 'failed') {
    return <X size={13} className="text-destructive" />
  }
  return (
    <div
      className="h-[5px] w-[5px] rounded-full bg-text-faint"
      aria-hidden
    />
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60000)
  const s = Math.round((ms % 60000) / 1000)
  return `${m}m ${s}s`
}
