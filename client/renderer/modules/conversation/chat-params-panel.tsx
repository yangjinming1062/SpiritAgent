import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Brain, type IconComponent, RefreshCw, Sparkles, Thermometer, X } from '@/shared/lib/icons'
import { type ReasoningEffort, resolveReasoningEffort } from '@/shared/lib/reasoning-effort'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import { CapsuleTabs, Spinner } from '@/shared/panel'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import { clamp } from '@runtime'

import {
  DEFAULT_THRESHOLD,
  formatTokenNumber,
  getReasoningOptions,
  resolveTemperature,
  temperatureStyleLabel,
  useContextStatus
} from './context-progress-bar'
import { useConversationView } from './conversation-view'
import { rememberFullHistory } from './session-history-cache'
import { useIsReadOnlySession } from './use-is-read-only-session'

const THRESHOLD_MIN = 0.3
const THRESHOLD_STEP = 0.05

export type ChatParamsTab = 'context' | 'temperature' | 'reasoning'

const PARAM_TAB_ICONS: Record<ChatParamsTab, IconComponent> = {
  context: Sparkles,
  temperature: Thermometer,
  reasoning: Brain
}

const TEMPERATURE_PRESETS = [
  { key: 'precise', value: 0.2 },
  { key: 'balanced', value: 0.7 },
  { key: 'divergent', value: 1 }
] as const

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

  const emit = (clientX: number): void => {
    const rect = trackRef.current?.getBoundingClientRect()

    if (rect && rect.width > 0) {
      onChange(snapThreshold((clientX - rect.left) / rect.width))
    }
  }

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (disabled) {
      return
    }

    e.preventDefault()
    e.stopPropagation()
    e.currentTarget.setPointerCapture(e.pointerId)
    draggingRef.current = true
    setIsDragging(true)
    emit(e.clientX)
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (!draggingRef.current) {
      return
    }

    e.preventDefault()
    emit(e.clientX)
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
          aria-valuemin={THRESHOLD_MIN * 100}
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
  const { controller, eligible } = useConversationView()

  const {
    $chatSessionId,
    $sessionCacheUsage,
    $sessionSettings,
    hydrateChatMessages,
    hydrateSessionInfo,
    setSessionContextUsage,
    updateSessionSetting
  } = controller

  const dict = useStrings()
  const params = dict.chat.params
  const cacheUsage = useStore($sessionCacheUsage)
  const settings = useStore($sessionSettings)
  // 自动化会话的历史由任务维护，手动压缩沿用只读权限。
  const isReadOnlySession = useIsReadOnlySession()
  const canEdit = sessionId !== null && eligible && !isReadOnlySession
  const contextStatus = useContextStatus()
  const [compressing, setCompressing] = useState(false)

  const temp = resolveTemperature(settings.temperature)
  const threshold = settings.context_compression_threshold ?? DEFAULT_THRESHOLD
  const reasoning = resolveReasoningEffort(settings.reasoning_effort)

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

    if (!controller.isCurrent() || Object.keys(patch).length === 0 || !targetId) {
      return
    }

    pendingPatchRef.current = {}
    void requestResource('sessions.patch_session', {
      path: { session_id: targetId },
      body: { settings: patch }
    })
      .then(res => {
        if (!controller.isCurrent()) {
          return
        }

        if (
          $chatSessionId.get() === targetId &&
          revisionRef.current === revision &&
          $sessionSettings.get() === visibleSettings
        ) {
          hydrateSessionInfo(res.info)
        }

        if (Object.values(patch).every(value => value === null)) {
          notify({ durationMs: 2500, kind: 'info', message: params.resetConfirm })
        }
      })
      .catch(error => {
        if (controller.isCurrent()) {
          notifyError(error, params.saveFailed)
        }
      })
  }, [
    params.resetConfirm,
    params.saveFailed,
    sessionId,
    $chatSessionId,
    $sessionSettings,
    hydrateSessionInfo,
    controller
  ])

  const scheduleSync = useCallback(
    (incremental: SessionSettingsPatch): void => {
      revisionRef.current += 1
      Object.assign(pendingPatchRef.current, incremental)

      if (!sessionId) {
        return
      }

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      debounceTimerRef.current = setTimeout(() => {
        flushPending()
      }, 350)
    },
    [sessionId, flushPending]
  )

  useEffect(
    () => () => {
      flushPending()
    },
    [flushPending]
  )

  const handleTempChange = (val: number): void => {
    const rounded = Math.round(val * 100) / 100
    updateSessionSetting('temperature', rounded)
    scheduleSync({ temperature: rounded })
  }

  const handleThresholdChange = (val: number): void => {
    const rounded = snapThreshold(val)
    updateSessionSetting('context_compression_threshold', rounded)
    scheduleSync({ context_compression_threshold: rounded })
  }

  const handleReasoningChange = (val: ReasoningEffort): void => {
    updateSessionSetting('reasoning_effort', val)
    scheduleSync({ reasoning_effort: val })
  }

  const handleResetDefaults = (): void => {
    scheduleSync({ temperature: null, context_compression_threshold: null, reasoning_effort: null })
    flushPending()
  }

  const handleManualCompress = async (): Promise<void> => {
    if (compressing || isReadOnlySession || !sessionId) {
      return
    }

    setCompressing(true)

    try {
      const res = await requestResource('sessions.compress', {
        path: { session_id: sessionId }
      })

      if (!controller.isCurrent()) {
        return
      }

      if (res.compressed) {
        // 已切到其他会话时只更新本会话缓存，不改写当前视图。
        const isCurrentSession = $chatSessionId.get() === sessionId

        if (Array.isArray(res.messages)) {
          rememberFullHistory(sessionId, res.messages)

          if (isCurrentSession) {
            hydrateChatMessages(res.messages)
          }
        }

        if (isCurrentSession && res.usage?.total_tokens !== undefined) {
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
    <div className="w-full flex flex-col gap-3 rounded-2xl border border-line-standard bg-[var(--ui-app-bg)] p-3.5 text-left shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-150 select-none">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-hairline pb-2.5">
        <CapsuleTabs
          ariaLabel={params.openPanel}
          onChange={onTabChange}
          options={(['context', 'temperature', 'reasoning'] as const).map(id => ({
            icon: PARAM_TAB_ICONS[id],
            label: params.tabs[id],
            value: id
          }))}
          size="sm"
          value={activeTab}
          variant="segmented"
        />

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
              className="flex size-6 items-center justify-center rounded-lg text-faint hover:bg-fill-hover hover:text-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent transition cursor-pointer"
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

          <div className="flex flex-col gap-1.5 rounded-xl border border-line-hairline bg-fill-faint p-2.5">
            <div className="flex items-center justify-between gap-2 text-[10px] text-muted">
              <span>{params.cacheUsageTitle}</span>
              {cacheUsage && cacheUsage.reported_request_count > 0 && (
                <span className="font-mono text-xs font-semibold text-strong">
                  {cacheUsage.cache_hit_rate === null ? '—' : `${(cacheUsage.cache_hit_rate * 100).toFixed(1)}%`}
                </span>
              )}
            </div>
            {!cacheUsage || cacheUsage.reported_request_count === 0 ? (
              <span className="text-[11px] text-faint">
                {cacheUsage ? params.cacheUsageUnavailable : params.cacheUsagePending}
              </span>
            ) : (
              <>
                <span className="text-[11px] text-body">
                  {params.cacheUsageTokens(
                    cacheUsage.cached_input_tokens?.toLocaleString() ?? '—',
                    cacheUsage.input_tokens?.toLocaleString() ?? '—'
                  )}
                </span>
                {cacheUsage.reported_request_count < cacheUsage.request_count && (
                  <span className="text-[10px] text-muted">{params.cacheUsagePartial}</span>
                )}
              </>
            )}
            {cacheUsage && (
              <span className="text-[10px] text-faint">
                {params.cacheUsageRequests(cacheUsage.reported_request_count, cacheUsage.request_count)}
              </span>
            )}
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
            disabled={!canEdit || isReadOnlySession || compressing || contextStatus.totalTokens <= 0}
            onClick={() => {
              void handleManualCompress()
            }}
            type="button"
          >
            {compressing ? (
              <>
                <Spinner size="sm" />
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
                    <span className="text-[9px]">{params.temperaturePresets[preset.key]}</span>
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

            <div className="grid grid-cols-4 gap-1 rounded-lg border border-line-hairline bg-fill-hover/50 p-1 lg:grid-cols-8">
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

      <div className="flex items-center justify-between gap-2 border-t border-line-hairline pt-2 text-[10px]">
        <span className="min-w-0 flex-1 text-faint flex items-center gap-1.5 select-none" title={params.scopeNoteTitle}>
          <span className="size-1.5 shrink-0 rounded-full bg-accent" />
          <span>{params.scopeNote}</span>
        </span>
        <button
          className="flex shrink-0 items-center gap-1 text-[11px] text-muted hover:text-strong transition cursor-pointer"
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
