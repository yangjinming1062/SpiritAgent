import { useStore } from '@nanostores/react'
import { clamp } from '@runtime'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Brain, type IconComponent, Loader2, RefreshCw, Sparkles, Thermometer, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gateway, $gatewayState } from '@/shared/store/gateway'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { SessionRuntimeInfo } from '@/shared/types/spiritagent'

import {
  $chatSessionId,
  $sessionSettings,
  hydrateChatMessages,
  hydrateSessionSettings,
  setSessionContextUsage,
  updateSessionSetting
} from './chat-store'
import {
  type CompressContextResponse,
  formatTokenNumber,
  getReasoningOptions,
  type ReasoningEffort,
  resolveReasoningEffort,
  resolveTemperature,
  temperatureStyleLabel,
  useContextStatus
} from './context-progress-bar'
import { rememberFullHistory } from './session-history-cache'

const DEFAULT_THRESHOLD = 0.7
const THRESHOLD_MIN = 0.3
const THRESHOLD_STEP = 0.05

export type ChatParamsTab = 'context' | 'temperature' | 'reasoning'

const PARAM_TAB_ICONS: Record<ChatParamsTab, IconComponent> = {
  context: Sparkles,
  temperature: Thermometer,
  reasoning: Brain
}

const TEMPERATURE_PRESETS = [{ value: 0.2 }, { value: 0.7 }, { value: 1 }] as const

interface DraggableThresholdBarProps {
  barColor: string
  disabled?: boolean
  isInactive?: boolean
  onChange: (threshold: number) => void
  pct: number
  thresholdPct: number
}

function snapThreshold(value: number): number {
  return clamp(Math.round(value / THRESHOLD_STEP) * THRESHOLD_STEP, THRESHOLD_MIN, 1)
}

function DraggableThresholdBar({
  barColor,
  disabled,
  isInactive,
  onChange,
  pct,
  thresholdPct
}: DraggableThresholdBarProps): React.JSX.Element {
  const params = useStrings().chat.params
  const trackRef = useRef<HTMLDivElement>(null)
  const draggingRef = useRef(false)
  const [isDragging, setIsDragging] = useState(false)
  const [isHoveringThumb, setIsHoveringThumb] = useState(false)

  const calcRatioFromClientX = useCallback((clientX: number): number | null => {
    if (!trackRef.current) {
      return null
    }

    const rect = trackRef.current.getBoundingClientRect()

    if (rect.width <= 0) {
      return null
    }

    return snapThreshold((clientX - rect.left) / rect.width)
  }, [])

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (disabled) {
      return
    }

    e.preventDefault()
    e.stopPropagation()
    e.currentTarget.setPointerCapture(e.pointerId)
    draggingRef.current = true
    setIsDragging(true)

    const ratio = calcRatioFromClientX(e.clientX)

    if (ratio !== null) {
      onChange(ratio)
    }
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (!draggingRef.current) {
      return
    }

    e.preventDefault()
    const ratio = calcRatioFromClientX(e.clientX)

    if (ratio !== null) {
      onChange(ratio)
    }
  }

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }

    if (!draggingRef.current) {
      return
    }

    draggingRef.current = false
    setIsDragging(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent): void => {
    if (disabled) {
      return
    }

    const current = thresholdPct / 100

    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
      e.preventDefault()
      onChange(snapThreshold(current - THRESHOLD_STEP))
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
      e.preventDefault()
      onChange(snapThreshold(current + THRESHOLD_STEP))
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-1.5 font-medium text-body">
          <span>{params.thresholdSliderHeading}</span>
          <span className="text-[10px] text-faint">{params.thresholdSliderSubheading}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-accent font-medium">{params.thresholdSliderTriggerLabel}</span>
          <span className="font-mono text-xs font-semibold text-strong">{Math.round(thresholdPct)}%</span>
        </div>
      </div>

      <div
        className={cn(
          'relative h-5 w-full cursor-pointer flex items-center select-none group touch-none py-1',
          isDragging && 'cursor-grabbing'
        )}
        onPointerCancel={handlePointerUp}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        ref={trackRef}
      >
        <div className="relative h-2 w-full rounded-full bg-fill-hover overflow-hidden pointer-events-none">
          <div
            className={cn('h-full rounded-full transition-all duration-300 ease-out', barColor)}
            style={{ width: `${Math.max(pct, isInactive ? 0 : 1)}%` }}
          />
        </div>

        <div
          aria-label={params.thresholdSliderAria}
          aria-valuemax={100}
          aria-valuemin={30}
          aria-valuenow={Math.round(thresholdPct)}
          className={cn(
            'absolute top-1/2 -translate-y-1/2 -translate-x-1/2 z-20 flex flex-col items-center cursor-ew-resize',
            isDragging && 'cursor-grabbing'
          )}
          onKeyDown={handleKeyDown}
          onMouseEnter={() => setIsHoveringThumb(true)}
          onMouseLeave={() => setIsHoveringThumb(false)}
          role="slider"
          style={{ left: `${thresholdPct}%` }}
          tabIndex={0}
        >
          <div
            className={cn(
              'absolute -top-7 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium shadow-md transition-all pointer-events-none whitespace-nowrap border border-accent/40',
              isDragging || isHoveringThumb
                ? 'opacity-100 scale-100 -translate-y-0.5 bg-neutral-900 text-accent ring-1 ring-accent/30'
                : 'opacity-0 scale-90 translate-y-1'
            )}
          >
            {Math.round(thresholdPct)}%
          </div>

          <div
            className={cn(
              'w-2 h-4 rounded-full border border-line-strong bg-accent shadow-sm flex items-center justify-center transition-all duration-150',
              (isDragging || isHoveringThumb) && 'h-5 scale-110 ring-2 ring-accent/40 bg-accent-hover'
            )}
          >
            <div className="h-2.5 w-[1px] bg-white/80 rounded-full" />
          </div>
        </div>
      </div>

      <div className="flex justify-between items-center text-[9px] text-faint select-none">
        <span>{params.thresholdSliderMin}</span>
        <span className="text-[10px] text-accent/85 font-medium">{params.thresholdSliderHint}</span>
        <span>{params.thresholdSliderMax}</span>
      </div>

      <p className="text-[10px] text-muted leading-relaxed">{params.thresholdSliderDescription}</p>
    </div>
  )
}

interface ChatParamsPanelProps {
  activeTab: ChatParamsTab
  onClose?: () => void
  onTabChange: (tab: ChatParamsTab) => void
  sessionId: string | null
}

export function ChatParamsPanel({
  activeTab,
  onClose,
  onTabChange,
  sessionId
}: ChatParamsPanelProps): React.JSX.Element {
  const dict = useStrings()
  const params = dict.chat.params
  const settings = useStore($sessionSettings)
  const gateway = useStore($gateway)
  const gatewayState = useStore($gatewayState)
  const canEdit = gatewayState === 'open' && sessionId !== null
  const contextStatus = useContextStatus()
  const [compressing, setCompressing] = useState(false)

  const tempValue = resolveTemperature(settings.temperature)

  const thresholdValue =
    typeof settings.context_compression_threshold === 'number'
      ? settings.context_compression_threshold
      : DEFAULT_THRESHOLD

  const reasoningValue = resolveReasoningEffort(settings.reasoning_effort)

  const [temp, setTemp] = useState(tempValue)
  const [threshold, setThreshold] = useState(thresholdValue)
  const [reasoning, setReasoning] = useState<ReasoningEffort>(reasoningValue)

  useEffect(() => {
    setTemp(tempValue)
  }, [tempValue])

  useEffect(() => {
    setThreshold(thresholdValue)
  }, [thresholdValue])

  useEffect(() => {
    setReasoning(reasoningValue)
  }, [reasoningValue])

  type SessionSettingsPatch = {
    context_compression_threshold?: number | null
    reasoning_effort?: ReasoningEffort | null
    temperature?: number | null
  }

  const pendingPatchRef = useRef<SessionSettingsPatch>({})
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const revisionRef = useRef(0)

  const flushPending = useCallback((): void => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }

    const patch = pendingPatchRef.current
    const targetId = sessionId
    const revision = revisionRef.current
    const visibleSettings = $sessionSettings.get()

    if (Object.keys(patch).length === 0 || !targetId || !gateway || gateway.connectionState !== 'open') {
      return
    }

    pendingPatchRef.current = {}
    void gateway
      .request<{ info: SessionRuntimeInfo }>('session.set_settings', {
        session_id: targetId,
        settings: patch
      })
      .then(res => {
        if (
          $chatSessionId.get() === targetId &&
          revisionRef.current === revision &&
          $sessionSettings.get() === visibleSettings
        ) {
          hydrateSessionSettings(res.info)
        }

        if (Object.values(patch).every(value => value === null)) {
          notify({ durationMs: 2500, kind: 'info', message: params.resetConfirm })
        }
      })
      .catch(error => notifyError(error, params.saveFailed))
  }, [gateway, params.resetConfirm, params.saveFailed, sessionId])

  const scheduleSync = useCallback(
    (incremental: SessionSettingsPatch): void => {
      revisionRef.current += 1
      Object.assign(pendingPatchRef.current, incremental)

      if (!sessionId || !gateway || gateway.connectionState !== 'open') {
        return
      }

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      debounceTimerRef.current = setTimeout(() => {
        flushPending()
      }, 350)
    },
    [sessionId, gateway, flushPending]
  )

  useEffect(
    () => () => {
      flushPending()
    },
    [flushPending]
  )

  const handleTempChange = (val: number): void => {
    const rounded = Math.round(val * 100) / 100
    setTemp(rounded)
    updateSessionSetting('temperature', rounded)
    scheduleSync({ temperature: rounded })
  }

  const handleThresholdChange = (val: number): void => {
    const rounded = snapThreshold(val)
    setThreshold(rounded)
    updateSessionSetting('context_compression_threshold', rounded)
    scheduleSync({ context_compression_threshold: rounded })
  }

  const handleReasoningChange = (val: ReasoningEffort): void => {
    setReasoning(val)
    updateSessionSetting('reasoning_effort', val)
    scheduleSync({ reasoning_effort: val })
  }

  const handleResetDefaults = (): void => {
    scheduleSync({ temperature: null, context_compression_threshold: null, reasoning_effort: null })
    flushPending()
  }

  const handleManualCompress = async (): Promise<void> => {
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

          if (sessionId) {
            rememberFullHistory(sessionId, res.messages)
          }
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
          message: params.compressSuccessMore(res.replaced_count ?? 0)
        })
      } else {
        notify({
          durationMs: 3500,
          kind: 'info',
          message: res.reason || params.compressNotNeeded
        })
      }
    } catch (err) {
      notifyError(err, params.manualCompressFailed)
    } finally {
      setCompressing(false)
    }
  }

  const tempPct = Math.round(temp * 100)
  const thresholdPct = Math.round(threshold * 100)
  const tempSemantics = temperatureStyleLabel(temp, params.temperatureStyles)

  return (
    <div className="w-[350px] flex flex-col gap-3 rounded-2xl border border-line-standard bg-surface-card/95 p-3.5 text-left shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-150 select-none">
      <div className="flex items-center justify-between border-b border-line-hairline pb-2.5">
        <div className="flex items-center gap-1 bg-fill-faint p-0.5 rounded-lg border border-line-hairline">
          {(['context', 'temperature', 'reasoning'] as const).map(id => {
            const Icon = PARAM_TAB_ICONS[id]
            const active = activeTab === id
            const label = params.tabs[id]

            return (
              <button
                className={cn(
                  'flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition cursor-pointer select-none',
                  active ? 'bg-surface-panel text-strong shadow-xs' : 'text-muted hover:text-strong hover:bg-fill-hover'
                )}
                key={id}
                onClick={() => onTabChange(id)}
                type="button"
              >
                <Icon className="size-3.5 text-accent" />
                <span>{label}</span>
              </button>
            )
          })}
        </div>

        <div className="flex items-center gap-1.5">
          <span
            className="text-[10px] font-medium text-accent bg-accent/10 border border-accent/20 px-1.5 py-0.5 rounded-md select-none cursor-default"
            title={params.sessionScopeBadgeTitle}
          >
            {params.sessionScopeBadge}
          </span>
          {onClose && (
            <button
              aria-label={params.closePanel}
              className="flex size-6 items-center justify-center rounded-lg text-faint hover:bg-fill-hover hover:text-strong transition cursor-pointer"
              onClick={onClose}
              type="button"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      {activeTab === 'context' && (
        <div className="flex flex-col gap-3 animate-in fade-in duration-150">
          <div className="grid grid-cols-3 gap-2">
            <div className="flex flex-col gap-0.5 rounded-xl border border-line-hairline bg-fill-faint p-2">
              <span className="text-[10px] text-muted">{params.statsUsed}</span>
              <span className="font-mono text-xs font-semibold text-strong truncate">
                {contextStatus.totalTokens.toLocaleString()}
              </span>
              <span className="text-[9px] text-faint">{params.statsTokens}</span>
            </div>

            <div className="flex flex-col gap-0.5 rounded-xl border border-line-hairline bg-fill-faint p-2">
              <span className="text-[10px] text-muted">{params.statsMax}</span>
              <span className="font-mono text-xs font-semibold text-strong truncate">
                {formatTokenNumber(contextStatus.contextLimit)}
              </span>
              <span className="text-[9px] text-faint">{params.statsTokens}</span>
            </div>

            <div className="flex flex-col gap-0.5 rounded-xl border border-line-hairline bg-fill-faint p-2">
              <span className="text-[10px] text-muted">{params.statsPercent}</span>
              <span className="font-mono text-xs font-semibold text-strong">{contextStatus.pct.toFixed(1)}%</span>
              <span className="text-[9px] text-faint">{params.statsNodeAt(Math.round(thresholdPct))}</span>
            </div>
          </div>

          <DraggableThresholdBar
            barColor={contextStatus.barColor}
            disabled={!canEdit}
            isInactive={contextStatus.isInactive}
            onChange={handleThresholdChange}
            pct={contextStatus.pct}
            thresholdPct={thresholdPct}
          />

          <div
            className={cn(
              'flex items-center gap-2 rounded-xl p-2.5 text-xs leading-relaxed border',
              contextStatus.isInactive || contextStatus.isHealthy
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300'
                : contextStatus.isWarning
                  ? 'bg-amber-500/10 border-amber-500/20 text-amber-300'
                  : 'bg-rose-500/10 border-rose-500/20 text-rose-300'
            )}
          >
            <Sparkles className="size-4 shrink-0" />
            <span className="text-[11px]">
              {contextStatus.isInactive || contextStatus.isHealthy
                ? params.statusHealthy
                : contextStatus.isWarning
                  ? params.statusWarning
                  : params.statusCritical}
            </span>
          </div>

          <button
            className={cn(
              'flex items-center justify-center gap-2 w-full rounded-xl py-2 px-3 text-xs font-medium transition cursor-pointer border',
              compressing
                ? 'bg-fill-hover text-muted cursor-wait border-line-standard'
                : 'bg-accent/15 hover:bg-accent/25 text-accent border-accent/30 hover:border-accent/50 shadow-xs'
            )}
            disabled={!canEdit || compressing || contextStatus.totalTokens <= 0}
            onClick={() => {
              void handleManualCompress()
            }}
            type="button"
          >
            {compressing ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                <span>{params.compressing}</span>
              </>
            ) : (
              <>
                <Sparkles className="size-3.5 text-accent" />
                <span>{params.compressAction}</span>
              </>
            )}
          </button>
        </div>
      )}

      {activeTab === 'temperature' && (
        <div className="flex flex-col gap-3 animate-in fade-in duration-150">
          <div className="flex flex-col gap-2 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-1.5 font-medium text-body">
                <Thermometer className="size-3.5 text-accent" />
                <span>{params.temperatureLabel}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-accent font-medium px-1.5 py-0.5 rounded bg-accent/10 border border-accent/20">
                  {tempSemantics}
                </span>
                <span className="font-mono text-xs font-semibold text-strong">{temp.toFixed(2)}</span>
              </div>
            </div>

            <input
              aria-label={params.temperatureSliderAria}
              className="sa-slider h-1.5 w-full cursor-pointer accent-accent"
              disabled={!canEdit}
              max={1}
              min={0}
              onChange={e => handleTempChange(Number(e.target.value))}
              step={0.05}
              style={{ '--sa-slider-fill': `${tempPct}%` } as React.CSSProperties}
              type="range"
              value={temp}
            />

            <div className="flex justify-between text-[9px] text-faint select-none">
              <span>{params.temperatureScaleMin}</span>
              <span>{params.temperatureScaleMid}</span>
              <span>{params.temperatureScaleMax}</span>
            </div>

            <div className="grid grid-cols-3 gap-1.5 pt-1">
              {TEMPERATURE_PRESETS.map(preset => {
                const active = Math.abs(temp - preset.value) < 0.05

                const presetLabel =
                  preset.value === 0.2
                    ? params.temperaturePresets.precise
                    : preset.value === 0.7
                      ? params.temperaturePresets.balanced
                      : params.temperaturePresets.divergent

                return (
                  <button
                    className={cn(
                      'flex flex-col items-center py-1.5 px-1 rounded-lg border text-center transition cursor-pointer select-none',
                      active
                        ? 'border-accent bg-accent/15 text-accent shadow-xs'
                        : 'border-line-hairline bg-surface-panel/40 text-muted hover:bg-fill-hover hover:text-strong'
                    )}
                    disabled={!canEdit}
                    key={preset.value}
                    onClick={() => handleTempChange(preset.value)}
                    type="button"
                  >
                    <span className="font-mono text-[11px] font-semibold">{preset.value.toFixed(2)}</span>
                    <span className="text-[9px]">{presetLabel}</span>
                  </button>
                )
              })}
            </div>
          </div>

          <p className="text-[10px] text-faint px-0.5">{params.temperatureHint}</p>
        </div>
      )}

      {activeTab === 'reasoning' && (
        <div className="flex flex-col gap-3 animate-in fade-in duration-150">
          <div className="flex flex-col gap-2 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-1.5 font-medium text-body">
                <Brain className="size-3.5 text-accent" />
                <span>{params.reasoningLabel}</span>
              </div>
              <span className="font-semibold text-xs text-accent px-1.5 py-0.5 rounded bg-accent/10 border border-accent/20">
                {params.reasoningOptions[reasoning] ?? params.reasoningFallbackLabel}
              </span>
            </div>

            <div className="grid grid-cols-4 gap-1 rounded-lg border border-line-hairline bg-fill-hover/50 p-1">
              {getReasoningOptions(params.reasoningOptions).map(opt => {
                const active = reasoning === opt.value

                return (
                  <button
                    className={cn(
                      'rounded-md py-1.5 text-xs font-medium transition cursor-pointer text-center select-none',
                      active ? 'bg-accent text-on-accent shadow-xs' : 'text-muted hover:bg-fill-hover hover:text-strong'
                    )}
                    disabled={!canEdit}
                    key={opt.value}
                    onClick={() => handleReasoningChange(opt.value)}
                    type="button"
                  >
                    {opt.label}
                  </button>
                )
              })}
            </div>
          </div>

          <p className="text-[10px] text-muted leading-relaxed px-0.5">{params.reasoningHints[reasoning]}</p>
        </div>
      )}

      <div className="flex items-center justify-between border-t border-line-hairline pt-2 text-[10px]">
        <span className="text-faint flex items-center gap-1.5 select-none" title={params.scopeNoteTitle}>
          <span className="size-1.5 rounded-full bg-accent" />
          <span>{params.scopeNote}</span>
        </span>
        <button
          className="flex items-center gap-1 text-[11px] text-muted hover:text-strong transition cursor-pointer"
          disabled={!canEdit}
          onClick={handleResetDefaults}
          title={params.resetButtonTitle}
          type="button"
        >
          <RefreshCw className="size-3" />
          <span>{params.resetButton}</span>
        </button>
      </div>
    </div>
  )
}
