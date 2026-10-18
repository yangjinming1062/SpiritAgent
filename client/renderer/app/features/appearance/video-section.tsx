import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $outfits,
  $videoGenErrors,
  $videoGenRequests,
  $videoGenStages,
  $videoPacks,
  type ActionWire,
  activateVideoPack,
  generateVideoPack,
  hydrateVideoPack,
  scopedGenError,
  SYSTEM_ACTION_KEYS,
  type SystemActionKey,
  systemActionNames,
  VIDEO_GEN_STAGE_TEXT_KEYS,
  videoActionIsBusy,
  videoPackIsGenerating
} from '@/modules/character'
import { ChatMediaCard } from '@/modules/conversation'
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
  mediaUrl: string | null
  pendingReview: ActionWire['pending_review']
  hasReadyAsset: boolean
  canRetry: boolean
  mediaType: 'image' | 'video' | null
  motionPrompt: string
  feedback: string
  peekGeometry: ActionWire['peek_geometry']
  videoQueue: ActionWire['video_queue']
}

function isActionInProgress(status: string): boolean {
  return ACTION_IN_PROGRESS.has(status)
}

function isSystemActionKey(key: string): key is SystemActionKey {
  return (SYSTEM_ACTION_KEYS as readonly string[]).includes(key)
}

function ActionPreview({ type, url }: { type: 'image' | 'video'; url: string }): React.JSX.Element {
  const media = useResolvedMediaSrc({ type, url })
  const className = 'aspect-[2/3] h-full max-h-full w-auto max-w-full object-contain'
  const src = media.status === 'ready' ? media.src : undefined

  if (type === 'image') {
    return <img alt="" className={className} draggable={false} src={src} />
  }

  return <video className={className} controls loop muted playsInline preload="metadata" src={src} />
}

interface VideoSectionProps {
  onBack: () => void
  outfitId: number
  onMaterialReady?: (outfitId: number) => void
}

export function VideoSection({ onBack, outfitId, onMaterialReady }: VideoSectionProps): React.JSX.Element {
  const authKind = useStore($auth).kind
  const packs = useStore($videoPacks)
  const outfits = useStore($outfits)
  const genStages = useStore($videoGenStages)
  const genErrors = useStore($videoGenErrors)
  const genRequests = useStore($videoGenRequests)
  const dict = useStrings()
  const t = dict.living.appearance
  const [regenConfirmOpen, setRegenConfirmOpen] = useState(false)
  const [packsStatus, setPacksStatus] = useState<'loading' | 'loaded' | 'failed'>('loading')
  const [packsReloadKey, setPacksReloadKey] = useState(0)
  const [selectedActionKey, setSelectedActionKey] = useState<string | null>(null)
  const [actionFilter, setActionFilter] = useState('')
  const [editing, setEditing] = useState<{ key: string; feedback: string } | null>(null)

  const selectedOutfit = outfits.find(outfit => outfit.id === outfitId) ?? null
  const selectedPack = selectedOutfit ? (packs.find(pack => pack.outfit_id === selectedOutfit.id) ?? null) : null

  // 整包操作等待本着装任务收敛；单动作操作只等待同一动作。
  const scopedBusy =
    (!!selectedPack && videoPackIsGenerating(selectedPack)) ||
    Object.values(genRequests).some(scope =>
      scope.outfitId != null ? scope.outfitId === outfitId : scope.packId === selectedPack?.id
    )

  const initialVideoError = selectedOutfit?.initialVideoError ?? null
  // 列表加载失败不能按“尚未生成”展示，避免网络错误引导用户重复发起付费生成。
  const packsLoadFailed = packsStatus === 'failed' && !!selectedOutfit && !selectedPack

  const actionNames = systemActionNames(t)

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
        (isSystemActionKey(entry.action) ? actionNames[entry.action] : '') ||
        entry.name ||
        entry.action.replace(/_/g, ' '),
      status: entry.status,
      error: entry.error,
      mediaUrl: entry.current_media_url,
      pendingReview: entry.pending_review,
      hasReadyAsset: entry.has_ready_asset,
      canRetry: entry.can_retry,
      mediaType: entry.media_type,
      motionPrompt: entry.media_type === 'video' ? entry.motion_prompt : '',
      feedback: entry.feedback ?? '',
      peekGeometry: entry.peek_geometry,
      videoQueue: entry.video_queue
    })
  }

  for (const slot of SYSTEM_ACTION_KEYS) {
    if (!seenKeys.has(slot)) {
      actions.push({
        key: slot,
        label: actionNames[slot],
        status: 'missing',
        videoQueue: null,
        feedback: '',
        error: null,
        mediaUrl: null,
        pendingReview: null,
        hasReadyAsset: false,
        canRetry: false,
        mediaType: null,
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

  const actionIsBusy =
    !!selectedAction && videoActionIsBusy(packs, genRequests, outfitId, selectedPack?.id ?? null, selectedAction.key)

  const scopedError =
    (selectedAction
      ? scopedGenError(genErrors, outfitId, selectedPack?.id ?? null, selectedAction.key)?.message
      : null) ??
    scopedGenError(genErrors, outfitId, selectedPack?.id ?? null, null)?.message ??
    null

  const canRegeneratePack =
    !!selectedPack?.can_regenerate_pack && !selectedPack.actions.some(action => action.has_ready_asset) && !scopedBusy

  const stageText =
    scopedBusy && selectedPack?.status === 'processing' && selectedPack.id in genStages
      ? t[VIDEO_GEN_STAGE_TEXT_KEYS[genStages[selectedPack.id]]]
      : null

  const statusLine = ((): string => {
    if (selectedAction?.videoQueue) {
      return dict.common.videoQueue[selectedAction.videoQueue.state]
    }

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
      return t.videoReady(selectedPack.actions.filter(action => action.has_ready_asset).length)
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

  // 包、单动作或先于列表到达的进度仍在途时，轮询收敛失败及遗漏的终态事件。
  const anyPackGenerating = packs.some(videoPackIsGenerating) || Object.keys(genStages).length > 0

  useEffect(() => {
    if (!anyPackGenerating) {
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
  }, [anyPackGenerating])

  const requestActionGeneration = (feedback: string): void => {
    if (!selectedPack || !selectedAction || !selectedPack.can_regenerate || actionIsBusy) {
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

      setEditing(current => (current?.key === selectedAction.key ? null : current))
    })
  }

  const requestActionResume = (): void => {
    if (!selectedPack || selectedAction?.status !== 'failed' || !selectedAction.canRetry || actionIsBusy) {
      return
    }

    void generateVideoPack({ retryPackId: selectedPack.id, action: selectedAction.key })
  }

  const actionStatus = (action: ActionEntry): { label: string; hint?: string; className: string } => {
    if (action.videoQueue) {
      return { label: dict.common.videoQueue[action.videoQueue.state], className: 'bg-amber-400' }
    }

    if (
      action.status === 'result_unknown' ||
      selectedPack?.actions.some(
        candidate =>
          candidate.action === action.key &&
          (candidate.status === 'result_unknown' ||
            (candidate.can_regenerate === false && !isActionInProgress(candidate.status)))
      )
    ) {
      return { label: t.videoActionResultUnknown, hint: t.videoActionResultUnknownHint, className: 'bg-amber-400' }
    }

    if (
      isActionInProgress(action.status) ||
      Object.values(genRequests).some(
        scope =>
          (scope.outfitId != null ? scope.outfitId === outfitId : scope.packId === selectedPack?.id) &&
          scope.action === action.key
      )
    ) {
      return { label: t.videoActionGenerating, className: 'bg-amber-400' }
    }

    const requestError = scopedGenError(genErrors, outfitId, selectedPack?.id ?? null, action.key)?.message

    if (requestError) {
      return { label: t.videoActionFailed, hint: requestError, className: 'bg-danger-fg' }
    }

    if (action.status === 'review') {
      return { label: t.videoActionReview, hint: t.videoActionReviewHint, className: 'bg-amber-400' }
    }

    if ((action.key === 'peek_left' || action.key === 'peek_right') && action.mediaUrl && !action.peekGeometry) {
      return { label: t.videoPeekCalibrationFailed, hint: t.videoPeekCalibrationFailedHint, className: 'bg-amber-400' }
    }

    if (action.status === 'failed') {
      return { label: t.videoActionFailed, hint: action.error ?? undefined, className: 'bg-danger-fg' }
    }

    if (action.mediaUrl) {
      return { label: t.videoActionReady, className: 'bg-success' }
    }

    if (action.error) {
      return { label: t.videoActionFailed, className: 'bg-danger-fg' }
    }

    return { label: t.videoActionOnDemandShort, hint: t.videoActionOnDemand, className: 'bg-line-strong' }
  }

  const selectedStatus = selectedAction ? actionStatus(selectedAction) : null

  const canGenerateAction = !!selectedPack?.can_regenerate && !!selectedAction && !actionIsBusy
  const canResumeAction = selectedAction?.status === 'failed' && selectedAction.canRetry && !actionIsBusy
  const actionGenerationLabel = selectedAction?.status === 'missing' ? t.videoGenMissingAction : t.videoRedoAction

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
            {onMaterialReady && selectedOutfit?.status === 'ready' ? (
              <button className={BTN_PRIMARY} onClick={() => onMaterialReady(selectedOutfit.id)} type="button">
                {dict.common.useForDesktopLife}
              </button>
            ) : null}
            {selectedPack?.can_retry ? (
              <button
                className={BTN_SUBTLE}
                disabled={scopedBusy}
                onClick={() => void generateVideoPack({ retryPackId: selectedPack.id })}
                type="button"
              >
                {t.videoResume}
              </button>
            ) : null}
            {!onMaterialReady && !selectedPack && packsStatus === 'loaded' && selectedOutfit?.status === 'ready' ? (
              <button
                className={BTN_PRIMARY}
                disabled={scopedBusy || authKind !== 'authenticated'}
                onClick={() => void generateVideoPack({ outfitId: selectedOutfit.id })}
                type="button"
              >
                {t.videoGenAction}
              </button>
            ) : null}
            {!onMaterialReady && selectedPack?.status === 'ready' && !selectedPack.active ? (
              <button className={BTN_PRIMARY} onClick={() => void activateVideoPack(selectedPack.id)} type="button">
                {selectedPack.identity_review === 'review' ? t.videoIdentityReviewActivate : t.videoActivate}
              </button>
            ) : null}
            {canRegeneratePack ? (
              <details>
                <summary className="cursor-pointer rounded-lg px-3 py-2 text-xs text-muted">
                  {dict.common.moreActions}
                </summary>
                <button
                  className={BTN_SUBTLE}
                  disabled={scopedBusy}
                  onClick={() => setRegenConfirmOpen(true)}
                  type="button"
                >
                  {t.videoRegenAction}
                </button>
              </details>
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

                <div
                  className={cn(
                    'mt-3 grid min-h-0 flex-1 gap-3 overflow-y-auto',
                    selectedAction.pendingReview && selectedAction.mediaUrl ? 'xl:grid-cols-2' : 'grid-cols-1'
                  )}
                >
                  {selectedAction.mediaUrl && selectedAction.mediaType ? (
                    <div className="flex min-h-48 min-w-0 flex-col rounded-lg bg-fill-trough p-2">
                      <p className="mb-2 text-[11px] text-muted">{t.videoActionCurrent}</p>
                      <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden">
                        <ActionPreview type={selectedAction.mediaType} url={selectedAction.mediaUrl} />
                      </div>
                    </div>
                  ) : !selectedAction.pendingReview ? (
                    <div className="flex items-center justify-center rounded-lg bg-fill-trough px-5 text-center">
                      <div className="max-w-md">
                        <p className="text-xs text-body">{selectedStatus.hint ?? selectedStatus.label}</p>
                        {selectedAction.error ? (
                          <p className="mt-1 text-[11px] text-danger-fg">{selectedAction.error}</p>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                  {selectedAction.pendingReview ? (
                    <div className="min-w-0 space-y-2 overflow-y-auto [&_img]:max-h-64 [&_video]:max-h-64">
                      <p className="text-[11px] text-muted">{t.videoActionPending}</p>
                      <ChatMediaCard
                        item={{
                          type: selectedAction.pendingReview.media_type,
                          url: selectedAction.pendingReview.media_url,
                          review_id: String(selectedAction.pendingReview.id)
                        }}
                        key={selectedAction.pendingReview.id}
                        onReviewed={() => void hydrateVideoPack(true)}
                      />
                      <p className="text-[11px] text-muted">{t.videoActionPendingHint}</p>
                    </div>
                  ) : null}
                </div>

                {selectedAction.motionPrompt ? (
                  <details className="mt-2 text-xs text-muted">
                    <summary className="cursor-pointer">{dict.common.creationDetails}</summary>
                    <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-muted">
                      {selectedAction.motionPrompt}
                    </p>
                  </details>
                ) : null}

                {editing?.key === selectedAction.key ? (
                  <div className="mt-3 space-y-2">
                    <p className={HINT_TEXT}>{t.videoRedoActionHint}</p>
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
                        {actionGenerationLabel}
                      </button>
                    </div>
                  </div>
                ) : canGenerateAction || canResumeAction ? (
                  <div className="mt-3 space-y-2">
                    {canResumeAction ? <p className={HINT_TEXT}>{t.videoResumeActionHint}</p> : null}
                    <div className="flex justify-end gap-2">
                      {canResumeAction ? (
                        <button className={BTN_PRIMARY} onClick={requestActionResume} type="button">
                          {t.videoResumeAction}
                        </button>
                      ) : null}
                      {canGenerateAction ? (
                        <button
                          className={BTN_SUBTLE}
                          onClick={() => setEditing({ key: selectedAction.key, feedback: selectedAction.feedback })}
                          type="button"
                        >
                          {actionGenerationLabel}
                        </button>
                      ) : null}
                    </div>
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
            {selectedOutfit?.fullbodyUrl ? (
              <img
                alt={selectedOutfit.name}
                className="mx-auto mb-4 max-h-64 max-w-full object-contain"
                src={selectedOutfit.fullbodyUrl}
              />
            ) : null}
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
          if (!canRegeneratePack) {
            return
          }

          void generateVideoPack({ force: true, outfitId: selectedOutfit?.id })
        }}
        onOpenChange={setRegenConfirmOpen}
        open={regenConfirmOpen}
        title={t.videoRegenTitle}
      />
    </section>
  )
}
