import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $outfits,
  $videoGenError,
  $videoGenScope,
  $videoGenStage,
  $videoGenState,
  $videoPacks,
  activateVideoPack,
  generateVideoPack,
  hydrateVideoPack,
  VIDEO_ACTION_KEYS,
  VIDEO_GEN_STAGE_TEXT_KEYS,
  type VideoActionKey,
  videoActionNames,
  type VideoActionWire,
  videoGenScopeMatches
} from '@/modules/character'
import { useResolvedMediaSrc } from '@/modules/media'
import { ArrowLeft } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, ConfirmDialog, HINT_TEXT, INPUT_CLASS } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

const ACTION_IN_PROGRESS = new Set(['queued', 'running', 'processing'])

interface ActionEntry {
  key: string
  label: string
  status: string
  error: string | null
  clipUrl: string | null
  motionPrompt: string
  peekGeometry: VideoActionWire['peek_geometry']
}

function isActionInProgress(status: string): boolean {
  return ACTION_IN_PROGRESS.has(status)
}

function isVideoActionKey(key: string): key is VideoActionKey {
  return (VIDEO_ACTION_KEYS as readonly string[]).includes(key)
}

function ActionPreview({ url }: { url: string }): React.JSX.Element {
  const media = useResolvedMediaSrc({ type: 'video', url })

  return (
    <video
      className="aspect-[2/3] h-full max-h-full w-auto max-w-full object-contain"
      controls
      loop
      muted
      playsInline
      preload="metadata"
      src={media.status === 'ready' ? media.src : undefined}
    />
  )
}

interface VideoSectionProps {
  onBack: () => void
  outfitId: number
}

export function VideoSection({ onBack, outfitId }: VideoSectionProps): React.JSX.Element {
  const authKind = useStore($auth).kind
  const packs = useStore($videoPacks)
  const outfits = useStore($outfits)
  const genState = useStore($videoGenState)
  const genStage = useStore($videoGenStage)
  const genError = useStore($videoGenError)
  const genScope = useStore($videoGenScope)
  const dict = useStrings()
  const t = dict.living.appearance
  const [regenConfirmOpen, setRegenConfirmOpen] = useState(false)
  const [packsStatus, setPacksStatus] = useState<'loading' | 'loaded' | 'failed'>('loading')
  const [packsReloadKey, setPacksReloadKey] = useState(0)
  const [selectedPackId, setSelectedPackId] = useState<number | null>(null)
  const [selectedActionKey, setSelectedActionKey] = useState<string | null>(null)
  const [actionFilter, setActionFilter] = useState('')
  const [editing, setEditing] = useState<{ key: string; feedback: string } | null>(null)

  const selectedOutfit = outfits.find(outfit => outfit.id === outfitId) ?? null
  const outfitPacks = selectedOutfit ? packs.filter(pack => pack.outfit_id === selectedOutfit.id) : []
  // 接口按新→旧返回；默认选最新包，旧版本从版本选择器切换。
  const selectedPack = outfitPacks.find(pack => pack.id === selectedPackId) ?? outfitPacks[0] ?? null
  const globalBusy = genState === 'generating'
  // 生成/失败按着装与包归属过滤：A 的进度或错误不落到 B 的标题下。
  const scopedBusy = globalBusy && videoGenScopeMatches(genScope, outfitId, selectedPack?.id ?? null)

  const scopedError =
    genError && videoGenScopeMatches(genError, outfitId, selectedPack?.id ?? null) ? genError.message : null

  const initialVideoError = selectedOutfit?.initialVideoError ?? null
  // 列表加载失败不能按“尚未生成”展示，避免网络错误引导用户重复发起付费生成。
  const packsLoadFailed = packsStatus === 'failed' && !!selectedOutfit && !selectedPack

  const actionNames = videoActionNames(t)

  const actions: ActionEntry[] = []
  const seenKeys = new Set<string>()

  for (const entry of selectedPack?.actions ?? []) {
    if (seenKeys.has(entry.action)) {
      continue
    }

    seenKeys.add(entry.action)
    actions.push({
      key: entry.action,
      label:
        (isVideoActionKey(entry.action) ? actionNames[entry.action] : '') ||
        entry.name ||
        entry.action.replace(/_/g, ' '),
      status: entry.status,
      error: entry.error,
      clipUrl: entry.clip_url,
      motionPrompt: entry.motion_prompt,
      peekGeometry: entry.peek_geometry
    })
  }

  for (const slot of VIDEO_ACTION_KEYS) {
    if (!seenKeys.has(slot)) {
      actions.push({
        key: slot,
        label: actionNames[slot],
        status: 'missing',
        error: null,
        clipUrl: null,
        motionPrompt: '',
        peekGeometry: null
      })
    }
  }

  const visibleActions = actions.filter(action =>
    action.label.toLocaleLowerCase().includes(actionFilter.trim().toLocaleLowerCase())
  )

  // 搜索只过滤列表；已选动作仍保留在详情区，避免输入时预览被抢走。
  const selectedAction = actions.find(action => action.key === selectedActionKey) ?? visibleActions[0] ?? null
  const actionIsGenerating = !!selectedAction && isActionInProgress(selectedAction.status)
  const selectedActionFailed = !!selectedAction && (selectedAction.status === 'failed' || !!selectedAction.error)
  const actionRetryWaiting = selectedActionFailed && selectedPack?.status === 'processing'

  const stageText =
    scopedBusy && selectedPack?.status === 'processing' && genStage ? t[VIDEO_GEN_STAGE_TEXT_KEYS[genStage]] : null

  const statusLine = ((): string => {
    // 包自身 processing 时优先展示进度；阶段文案仅在归属命中时展开。
    if (selectedPack?.status === 'processing') {
      return stageText ?? t.videoGenStageDefault
    }

    // 请求级错误（生成/穿着失败、并发拒绝）：已有动作包时也必须露出，不能落到「已就绪」。
    if (scopedError) {
      return scopedError
    }

    if (selectedPack?.status === 'failed') {
      return selectedPack.error || t.videoActionFailed
    }

    if (initialVideoError) {
      return initialVideoError
    }

    if (packsLoadFailed) {
      return t.videoPacksLoadFailed
    }

    // 单动作重做时整包仍是 ready，只提示动作生成，不误报为整包状态。
    if (scopedBusy) {
      return t.videoActionGenerating
    }

    if (selectedPack?.status === 'ready') {
      return t.videoReady(selectedPack.pack_version, selectedPack.actions.filter(action => !!action.clip_url).length)
    }

    if (selectedOutfit) {
      return packsStatus === 'loading' ? t.videoLoading : t.videoOutfitNotReady
    }

    return t.videoSelectOutfit
  })()

  const statusIsError = !!scopedError || selectedPack?.status === 'failed' || !!initialVideoError || packsLoadFailed

  useEffect(() => {
    if (authKind !== 'authenticated') {
      setPacksStatus('loaded')

      return
    }

    let cancelled = false
    setPacksStatus('loading')
    void hydrateVideoPack().then(ok => {
      if (!cancelled) {
        setPacksStatus(ok ? 'loaded' : 'failed')
      }
    })

    return () => {
      cancelled = true
    }
  }, [authKind, packsReloadKey])

  useEffect(() => {
    if (!globalBusy) {
      return
    }

    let cancelled = false

    const timer = window.setInterval(() => {
      void hydrateVideoPack(true).then(ok => {
        if (!cancelled) {
          setPacksStatus(ok ? 'loaded' : 'failed')
        }
      })
    }, 5000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [globalBusy])

  const requestActionGeneration = (feedback: string): void => {
    if (!selectedPack || !selectedAction || !selectedPack.can_regenerate || globalBusy) {
      return
    }

    void generateVideoPack({
      sourcePackId: selectedPack.id,
      action: selectedAction.key,
      feedback
    }).then(accepted => {
      if (!accepted) {
        return
      }

      setSelectedPackId(null)
      setEditing(null)
    })
  }

  const actionStatus = (action: ActionEntry): { label: string; hint?: string; className: string } => {
    if (isActionInProgress(action.status)) {
      return { label: t.videoActionGenerating, className: 'bg-amber-400' }
    }

    if (action.status === 'result_unknown') {
      return { label: t.videoActionResultUnknown, hint: t.videoActionResultUnknownHint, className: 'bg-amber-400' }
    }

    if (action.status === 'review') {
      return { label: t.videoActionReview, hint: t.videoActionReviewHint, className: 'bg-amber-400' }
    }

    if ((action.key === 'peek_left' || action.key === 'peek_right') && action.clipUrl && !action.peekGeometry) {
      return { label: t.videoPeekCalibrationFailed, hint: t.videoPeekCalibrationFailedHint, className: 'bg-amber-400' }
    }

    if (action.clipUrl) {
      return { label: t.videoActionReady, className: 'bg-success' }
    }

    if (action.status === 'failed' || action.error) {
      return { label: t.videoActionFailed, className: 'bg-danger-fg' }
    }

    return { label: t.videoActionOnDemandShort, className: 'bg-line-strong' }
  }

  const selectedStatus = selectedAction ? actionStatus(selectedAction) : null
  const canGenerateAction = !!selectedPack?.can_regenerate && !globalBusy && !actionIsGenerating

  return (
    <section
      aria-labelledby="appearance-actions-heading"
      className="flex min-h-[360px] flex-1 flex-col overflow-hidden"
    >
      <div className="shrink-0 border-b border-line-hairline px-4 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex min-w-0 items-center gap-3">
            <button
              aria-label={t.videoBackToOutfits}
              className={cn(BTN_SUBTLE, 'h-8 shrink-0 px-2')}
              onClick={onBack}
              type="button"
            >
              <ArrowLeft className="size-4" />
              {t.videoBackToOutfits}
            </button>
            <div className="min-w-0">
              <div className="flex min-w-0 items-baseline gap-2">
                <h2 className="shrink-0 text-sm font-semibold text-strong" id="appearance-actions-heading">
                  {t.actionsHeading}
                </h2>
                {selectedOutfit ? <span className="truncate text-xs text-body">{selectedOutfit.name}</span> : null}
                {selectedPack?.active ? (
                  <span className="shrink-0 rounded-full bg-success/15 px-2 py-0.5 text-[10px] text-success">
                    {t.videoActive}
                  </span>
                ) : null}
              </div>
              <p className={cn('mt-0.5 truncate text-[11px]', statusIsError ? 'text-danger-fg' : 'text-muted')}>
                {statusLine}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2">
            {outfitPacks.length > 1 ? (
              <select
                aria-label={t.videoVersions}
                className={cn(INPUT_CLASS, 'h-8 w-44 py-1 text-[11px]')}
                onChange={event => {
                  setSelectedPackId(Number(event.target.value))
                  setSelectedActionKey(null)
                  setEditing(null)
                }}
                value={selectedPack?.id ?? ''}
              >
                {outfitPacks.map(pack => (
                  <option key={pack.id} value={pack.id}>
                    {t.videoVersion(pack.pack_version)}
                    {pack.active ? ` · ${t.videoActive}` : ''}
                    {pack.status === 'processing' ? ` · ${t.videoActionGenerating}` : ''}
                    {pack.status === 'failed' ? ` · ${t.videoActionFailed}` : ''}
                  </option>
                ))}
              </select>
            ) : selectedPack ? (
              <span className="rounded-md border border-line-hairline px-2 py-1 text-[10px] text-muted">
                {t.videoVersion(selectedPack.pack_version)}
              </span>
            ) : null}

            {selectedPack?.can_retry ? (
              <button
                className={BTN_SUBTLE}
                disabled={globalBusy}
                onClick={() => void generateVideoPack({ retryPackId: selectedPack.id })}
                type="button"
              >
                {t.videoResume}
              </button>
            ) : null}
            {!selectedPack && packsStatus === 'loaded' && selectedOutfit?.status === 'ready' ? (
              <button
                className={BTN_PRIMARY}
                disabled={globalBusy || authKind !== 'authenticated'}
                onClick={() => void generateVideoPack({ outfitId: selectedOutfit.id })}
                type="button"
              >
                {t.videoGenAction}
              </button>
            ) : null}
            {selectedPack?.status === 'ready' && !selectedPack.active ? (
              <button
                className={BTN_PRIMARY}
                disabled={globalBusy}
                onClick={() => void activateVideoPack(selectedPack.id)}
                type="button"
              >
                {selectedPack.identity_review === 'review' ? t.videoIdentityReviewActivate : t.videoActivate}
              </button>
            ) : null}
            {(selectedPack?.status === 'ready' || selectedPack?.status === 'failed') && selectedPack.can_regenerate ? (
              <button
                className={BTN_SUBTLE}
                disabled={globalBusy}
                onClick={() => setRegenConfirmOpen(true)}
                type="button"
              >
                {t.videoRegenAction}
              </button>
            ) : null}
          </div>
        </div>
        {selectedPack?.identity_review === 'review' ? (
          <p className="mt-1 text-[11px] text-amber-300" role="status">
            {t.videoIdentityReviewHint}
          </p>
        ) : null}
      </div>

      {selectedPack ? (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-hidden px-4 pb-4 pt-3 md:grid-cols-[minmax(220px,0.36fr)_minmax(0,1fr)]">
          <div className="flex max-h-48 min-h-0 flex-col overflow-hidden rounded-xl border border-line-hairline bg-surface-card md:max-h-none">
            <div className="shrink-0 space-y-2 border-b border-line-hairline px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-strong">{t.actionCount(actions.length)}</span>
                <span className="text-[10px] text-muted">{t.videoActionCompactHint}</span>
              </div>
              <input
                aria-label={t.videoActionSearch}
                className={cn(INPUT_CLASS, 'h-8 py-1 text-xs')}
                onChange={event => setActionFilter(event.target.value)}
                placeholder={t.videoActionSearch}
                type="search"
                value={actionFilter}
              />
            </div>
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-1.5">
              {visibleActions.map(action => {
                const status = actionStatus(action)
                const isSelected = selectedAction?.key === action.key

                return (
                  <button
                    aria-pressed={isSelected}
                    className={cn(
                      'flex min-h-10 w-full items-center gap-2 rounded-lg px-2.5 text-left transition',
                      isSelected ? 'bg-accent-soft text-strong' : 'text-body hover:bg-fill-hover'
                    )}
                    key={action.key}
                    onClick={() => {
                      setSelectedActionKey(action.key)
                      setEditing(null)
                    }}
                    title={status.hint ?? status.label}
                    type="button"
                  >
                    <span className={cn('size-1.5 shrink-0 rounded-full', status.className)} />
                    <span className="min-w-0 flex-1 truncate text-xs">{action.label}</span>
                    <span className="max-w-[50%] shrink-0 truncate text-[10px] text-muted">{status.label}</span>
                  </button>
                )
              })}
              {visibleActions.length === 0 ? (
                <p className="px-2 py-3 text-[11px] text-muted">{t.videoActionNoMatch}</p>
              ) : null}
            </div>
          </div>

          <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-line-hairline bg-surface-card p-3">
            {selectedAction && selectedStatus ? (
              <>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-strong">{selectedAction.label}</p>
                    <p className="mt-0.5 text-[11px] text-muted">{selectedStatus.hint ?? selectedStatus.label}</p>
                  </div>
                </div>

                <div className="mt-3 flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg bg-fill-trough">
                  {selectedAction.clipUrl ? (
                    <ActionPreview url={selectedAction.clipUrl} />
                  ) : (
                    <div className="max-w-md px-5 text-center">
                      <p className="text-xs text-body">
                        {selectedAction.status === 'missing' ? t.videoActionOnDemand : selectedStatus.label}
                      </p>
                      {selectedAction.error ? (
                        <p className="mt-1 text-[11px] text-danger-fg">{selectedAction.error}</p>
                      ) : null}
                    </div>
                  )}
                </div>

                {selectedAction.motionPrompt ? (
                  <p
                    className="mt-2 line-clamp-3 text-[11px] leading-relaxed text-muted"
                    title={selectedAction.motionPrompt}
                  >
                    {selectedAction.motionPrompt}
                  </p>
                ) : null}

                {editing?.key === selectedAction.key ? (
                  <div className="mt-3 space-y-2">
                    <label className={HINT_TEXT} htmlFor="video-action-feedback">
                      {t.videoFeedback}
                    </label>
                    <textarea
                      className={cn(INPUT_CLASS, 'min-h-16 resize-y text-xs')}
                      id="video-action-feedback"
                      maxLength={1000}
                      onChange={event => setEditing({ ...editing, feedback: event.target.value })}
                      value={editing.feedback}
                    />
                    <div className="flex justify-end gap-2">
                      <button className={BTN_SUBTLE} onClick={() => setEditing(null)} type="button">
                        {t.videoActionCancel}
                      </button>
                      <button
                        className={BTN_PRIMARY}
                        disabled={!canGenerateAction}
                        onClick={() => requestActionGeneration(editing.feedback)}
                        type="button"
                      >
                        {selectedAction.clipUrl ? t.videoRedoAction : t.videoGenMissingAction}
                      </button>
                    </div>
                  </div>
                ) : actionRetryWaiting ? (
                  <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                    <span className="text-[10px] text-muted" id="video-action-retry-wait-hint" role="status">
                      {t.videoActionRetryWaitHint}
                    </span>
                    <button
                      aria-describedby="video-action-retry-wait-hint"
                      className={BTN_SUBTLE}
                      disabled
                      type="button"
                    >
                      {t.videoRedoAction}
                    </button>
                  </div>
                ) : canGenerateAction ? (
                  <div className="mt-3 flex justify-end">
                    <button
                      className={BTN_SUBTLE}
                      onClick={() => setEditing({ key: selectedAction.key, feedback: '' })}
                      type="button"
                    >
                      {selectedAction.clipUrl ? t.videoRedoAction : t.videoGenMissingAction}
                    </button>
                  </div>
                ) : null}
              </>
            ) : (
              <div className="grid min-h-40 flex-1 place-items-center text-center text-xs text-muted">
                {t.videoActionEmpty}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="m-4 grid min-h-0 flex-1 place-items-center rounded-xl border border-dashed border-line-strong bg-surface-card px-6 text-center">
          <div className="max-w-md">
            <p className="text-sm font-medium text-strong">
              {!selectedOutfit
                ? t.videoSelectOutfit
                : packsLoadFailed
                  ? t.videoPacksLoadFailed
                  : packsStatus === 'loaded'
                    ? t.videoOutfitNotReady
                    : t.videoLoading}
            </p>
            {packsLoadFailed ? (
              <button
                className={cn(BTN_SUBTLE, 'mt-3')}
                onClick={() => setPacksReloadKey(key => key + 1)}
                type="button"
              >
                {dict.common.retry}
              </button>
            ) : selectedOutfit && packsStatus === 'loaded' ? (
              <p className="mt-1 text-xs leading-relaxed text-muted">{t.videoGenHint}</p>
            ) : null}
            {initialVideoError ? <p className="mt-2 text-xs text-danger-fg">{initialVideoError}</p> : null}
          </div>
        </div>
      )}

      <ConfirmDialog
        confirmLabel={t.videoRegenAction}
        description={t.videoRegenBody}
        onConfirm={() => {
          setSelectedPackId(null)
          void generateVideoPack({ force: true, outfitId: selectedOutfit?.id })
        }}
        onOpenChange={setRegenConfirmOpen}
        open={regenConfirmOpen}
        title={t.videoRegenTitle}
      />
    </section>
  )
}
