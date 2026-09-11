import { useStore } from '@nanostores/react'
import { clamp } from '@runtime'
import type React from 'react'
import { useState } from 'react'

import {
  $chatSessionId,
  $sessionContextUsage,
  $sessionSettings,
  hydrateChatMessages,
  setSessionContextUsage
} from '@/chat/chat-store'
import { Brain, Loader2, Sparkles, Thermometer } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { SessionMessage } from '@/shared/types/spiritagent'

const DEFAULT_THRESHOLD = 0.7
const DEFAULT_LIMIT = 1_000_000
const DEFAULT_TEMPERATURE = 0.7

export const REASONING_EFFORT_VALUES = ['none', 'low', 'medium', 'high'] as const

export type ReasoningEffort = (typeof REASONING_EFFORT_VALUES)[number]

export interface CompressContextResponse {
  compressed: boolean
  messages?: SessionMessage[]
  reason?: string
  replaced_count?: number
  session_id?: string
  summary?: string
  usage?: {
    context_window?: number
    total_tokens?: number
  }
}

export function formatTokenNumber(num: number): string {
  if (num >= 1_000_000) {
    return `${(num / 1_000_000).toFixed(1)}M`
  }

  if (num >= 1_000) {
    return `${(num / 1_000).toFixed(1)}k`
  }

  return num.toLocaleString()
}

function isReasoningEffort(value: string): value is ReasoningEffort {
  return (REASONING_EFFORT_VALUES as readonly string[]).includes(value)
}

export function resolveReasoningEffort(value: unknown): ReasoningEffort {
  return typeof value === 'string' && isReasoningEffort(value) ? value : 'low'
}

export function resolveTemperature(value: unknown): number {
  return typeof value === 'number' ? value : DEFAULT_TEMPERATURE
}

export function temperatureStyleLabel(
  temp: number,
  styles: { balanced: string; divergent: string; precise: string }
): string {
  return temp <= 0.35 ? styles.precise : temp <= 0.75 ? styles.balanced : styles.divergent
}

// 渲染时由调用方传入当前 locale 的 reasoningOptions；保持函数无副作用以利于热切换订阅。
export function getReasoningOptions(
  options: Record<ReasoningEffort, string>
): readonly { label: string; value: ReasoningEffort }[] {
  return REASONING_EFFORT_VALUES.map(value => ({ label: options[value], value }))
}

export function useContextStatus(): {
  barColor: string
  contextLimit: number
  isHealthy: boolean
  isInactive: boolean
  isWarning: boolean
  pct: number
  sessionId: string | null
  threshold: number
  thresholdPct: number
  totalTokens: number
} {
  const sessionId = useStore($chatSessionId)
  const usage = useStore($sessionContextUsage)
  const settings = useStore($sessionSettings)

  const threshold =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const totalTokens = usage.totalTokens
  const contextLimit = usage.contextLimit > 0 ? usage.contextLimit : DEFAULT_LIMIT
  const rawPct = (totalTokens / contextLimit) * 100
  const pct = clamp(rawPct, 0, 100)
  const thresholdPct = clamp(threshold * 100, 1, 100)

  const isInactive = totalTokens <= 0 || pct < 2
  const isHealthy = !isInactive && pct < thresholdPct * 0.5
  const isWarning = !isInactive && !isHealthy && pct < thresholdPct * 0.88

  const barColor = isInactive
    ? 'bg-fill-faint'
    : isHealthy
      ? 'bg-emerald-400'
      : isWarning
        ? 'bg-amber-400'
        : 'bg-rose-500'

  return {
    barColor,
    contextLimit,
    isHealthy,
    isInactive,
    isWarning,
    pct,
    sessionId,
    threshold,
    thresholdPct,
    totalTokens
  }
}

/** 顶栏极简环境感知细线：1.5px 极细微进度条，带阈值标记刻度 */
export function ChatContextAmbientLine(): React.JSX.Element {
  const { pct, thresholdPct, isInactive, isHealthy, isWarning } = useContextStatus()
  const params = useStrings().chat.params

  const lineColor = isInactive
    ? 'bg-fill-faint'
    : isHealthy
      ? 'bg-gradient-to-r from-emerald-500 to-emerald-400'
      : isWarning
        ? 'bg-gradient-to-r from-amber-500 to-amber-400'
        : 'bg-gradient-to-r from-rose-500 to-rose-400'

  return (
    <div className="relative h-[1.5px] w-full bg-line-hairline overflow-hidden select-none">
      <div
        className={cn('h-full transition-all duration-300 ease-out', lineColor)}
        style={{ width: `${Math.max(pct, isInactive ? 0 : 1)}%` }}
      />
      <div
        className="absolute top-0 bottom-0 w-[1px] bg-line-strong z-10 opacity-70"
        style={{ left: `${thresholdPct}%` }}
        title={params.thresholdMarker(Math.round(thresholdPct))}
      />
    </div>
  )
}

export interface ChatCapsuleProps {
  active?: boolean
  onClick?: () => void
  variant?: 'workbench' | 'living'
}

function capsuleClassName(active: boolean | undefined, variant: ChatCapsuleProps['variant']): string {
  return cn(
    'inline-flex h-6 items-center gap-1.5 rounded-full transition cursor-pointer select-none',
    variant === 'living'
      ? 'border border-line-standard bg-surface-card px-2.5 text-[10.5px] text-strong shadow-xs backdrop-blur-md hover:border-line-strong hover:bg-surface-card/90'
      : 'border border-line-hairline bg-fill-faint px-2 text-[10px] text-body hover:border-line-standard hover:bg-fill-hover hover:text-strong',
    active && 'border-accent/40 bg-accent/15 text-accent shadow-xs'
  )
}

/** 顶栏上下文胶囊微徽标：展示健康指示点、实时 Token 数及百分比 */
export function ChatContextCapsule({ active, onClick, variant }: ChatCapsuleProps): React.JSX.Element {
  const { totalTokens, contextLimit, pct, isInactive, isHealthy, isWarning, thresholdPct } = useContextStatus()
  const params = useStrings().chat.params

  const dotColor = isInactive
    ? 'bg-muted-foreground/40'
    : isHealthy
      ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]'
      : isWarning
        ? 'bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)]'
        : 'bg-rose-500 shadow-[0_0_6px_rgba(244,63,94,0.7)]'

  const titleText = params.contextCapsuleTitle(
    totalTokens.toLocaleString(),
    contextLimit.toLocaleString(),
    pct.toFixed(1),
    Math.round(thresholdPct)
  )

  return (
    <button
      aria-label={params.contextCapsuleAria}
      className={capsuleClassName(active, variant)}
      onClick={onClick}
      title={titleText}
      type="button"
    >
      <span className={cn('size-1.5 rounded-full shrink-0 transition-colors', dotColor)} />
      <span className="font-mono text-[10px] tracking-tight">
        {formatTokenNumber(totalTokens)}
        <span className="text-faint font-sans"> / </span>
        <span className="text-muted">{formatTokenNumber(contextLimit)}</span>
      </span>
      <span className="text-[9px] text-faint">({pct.toFixed(0)}%)</span>
    </button>
  )
}

export function ChatTemperatureCapsule({ active, onClick, variant }: ChatCapsuleProps): React.JSX.Element {
  const settings = useStore($sessionSettings)
  const temp = resolveTemperature(settings.temperature)
  const params = useStrings().chat.params
  const tempLabel = temperatureStyleLabel(temp, params.temperatureStyles)
  const titleText = params.temperatureCapsuleTitle(temp.toFixed(2), tempLabel)

  return (
    <button
      aria-label={params.temperatureCapsuleAria}
      className={capsuleClassName(active, variant)}
      onClick={onClick}
      title={titleText}
      type="button"
    >
      <Thermometer className="size-3 text-accent shrink-0" />
      <span className="font-mono text-[10px] tracking-tight">{temp.toFixed(2)}</span>
      {variant !== 'living' && <span className="text-[9px] text-faint">({tempLabel})</span>}
    </button>
  )
}

export function ChatReasoningCapsule({ active, onClick, variant }: ChatCapsuleProps): React.JSX.Element {
  const settings = useStore($sessionSettings)
  const reasoning = resolveReasoningEffort(settings.reasoning_effort)
  const params = useStrings().chat.params
  const label = params.reasoningOptions[reasoning]
  const isOff = reasoning === 'none'
  const titleText = params.reasoningCapsuleTitle(isOff ? params.reasoningOff : label)

  return (
    <button
      aria-label={params.reasoningCapsuleAria}
      className={capsuleClassName(active, variant)}
      onClick={onClick}
      title={titleText}
      type="button"
    >
      <Brain className={cn('size-3 shrink-0', isOff ? 'text-faint' : 'text-accent')} />
      <span className="text-[10px] tracking-tight">
        {isOff ? params.reasoningCapsuleOff : params.reasoningCapsuleOn(label)}
      </span>
    </button>
  )
}

/** 兼容保留的完整进度条与手动压缩组件 */
export function ContextProgressBar(): React.JSX.Element {
  const { sessionId, totalTokens, contextLimit, pct, thresholdPct, isInactive, barColor } = useContextStatus()
  const gateway = useStore($gateway)
  const params = useStrings().chat.params
  const [hovered, setHovered] = useState(false)
  const [compressing, setCompressing] = useState(false)

  const handleManualCompress = async (e: React.MouseEvent) => {
    e.stopPropagation()

    if (compressing || !sessionId || !gateway || gateway.connectionState !== 'open') {
      return
    }

    setCompressing(true)

    try {
      const res = await gateway.request<CompressContextResponse>('session.compress_context', {
        session_id: sessionId
      })

      if (res.compressed) {
        if (Array.isArray(res.messages)) {
          hydrateChatMessages(res.messages)
        }

        if (res.usage?.total_tokens !== undefined) {
          setSessionContextUsage({
            contextLimit: res.usage.context_window,
            totalTokens: res.usage.total_tokens
          })
        }

        notify({
          durationMs: 4000,
          kind: 'success',
          message: params.manualCompressSuccess(res.replaced_count ?? 0)
        })
      } else {
        notify({
          durationMs: 3500,
          kind: 'info',
          message: res.reason || params.manualCompressNotNeeded
        })
      }
    } catch (err) {
      notifyError(err, params.manualCompressFailed)
    } finally {
      setCompressing(false)
    }
  }

  return (
    <div
      className="relative w-full pt-1.5 pb-0.5"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {hovered && (
        <div className="absolute -top-8 left-1/2 -translate-x-1/2 z-50 flex items-center gap-1.5 rounded-md border border-line-standard bg-neutral-900/95 px-2.5 py-1 text-[10px] text-strong shadow-lg backdrop-blur-sm whitespace-nowrap pointer-events-none animate-in fade-in zoom-in-95 duration-150">
          {compressing ? (
            <div className="flex items-center gap-1.5 text-accent">
              <Loader2 className="size-3 animate-spin" />
              <span>{params.manualCompressRunning}</span>
            </div>
          ) : (
            <>
              <span>
                {params.manualCompressTooltipInline(
                  totalTokens.toLocaleString(),
                  contextLimit.toLocaleString(),
                  pct.toFixed(1)
                )}
              </span>
              <span className="text-faint">·</span>
              <span className="text-accent font-medium">
                {params.manualCompressThresholdInline(Math.round(thresholdPct))}
              </span>
              <span className="text-faint">·</span>
              <span className="text-muted font-sans flex items-center gap-0.5">
                <Sparkles className="size-2.5 text-amber-300" />
                {params.manualCompressClickInline}
              </span>
            </>
          )}
        </div>
      )}

      <button
        aria-label={params.manualCompressButtonAria}
        className={cn(
          'relative h-1.5 w-full overflow-visible rounded-full bg-fill-hover transition group cursor-pointer block border-0 p-0',
          compressing && 'cursor-wait animate-pulse'
        )}
        disabled={compressing}
        onClick={handleManualCompress}
        title={params.manualCompressButtonTitle}
        type="button"
      >
        <div
          className={cn('h-full rounded-full transition-all duration-300 ease-out', barColor)}
          style={{ width: `${Math.max(pct, isInactive ? 0 : 1)}%` }}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-2.5 w-[2px] rounded-full bg-line-strong shadow-xs transition group-hover:h-3.5"
          style={{ left: `${thresholdPct}%` }}
          title={params.manualCompressThresholdMarker(Math.round(thresholdPct))}
        >
          <div className="absolute -top-1 left-1/2 -translate-x-1/2 size-1 rounded-full bg-line-strong" />
        </div>
      </button>
    </div>
  )
}
