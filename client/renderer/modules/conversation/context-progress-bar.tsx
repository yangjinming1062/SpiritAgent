import { useStore } from '@nanostores/react'
import type React from 'react'

import { REASONING_EFFORT_VALUES, type ReasoningEffort } from '@/shared/lib/reasoning-effort'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import type { SessionMessage } from '@protocol'
import { clamp } from '@runtime'

import { useConversationView } from './conversation-view'

export const DEFAULT_THRESHOLD = 0.7
const DEFAULT_TEMPERATURE = 0.7

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
  thresholdPct: number
  totalTokens: number
} {
  const { $sessionContextUsage, $sessionSettings } = useConversationView().controller

  const usage = useStore($sessionContextUsage)
  const settings = useStore($sessionSettings)

  const threshold = settings.context_compression_threshold ?? DEFAULT_THRESHOLD
  const totalTokens = usage.totalTokens
  const contextLimit = usage.contextLimit
  const rawPct = contextLimit > 0 ? (totalTokens / contextLimit) * 100 : 0
  const pct = clamp(rawPct, 0, 100)
  const thresholdPct = clamp(threshold * 100, 1, 100)

  const isInactive = contextLimit <= 0 || totalTokens <= 0 || pct < 2
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
