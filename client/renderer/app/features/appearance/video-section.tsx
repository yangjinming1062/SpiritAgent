import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { ActionSyncStatus } from '@/app/components/action-sync-status'
import { useAssetLibrarySearch } from '@/app/components/asset-library-header'
import {
  $outfits,
  $videoGenErrors,
  $videoGenRequests,
  $videoGenStages,
  $videoPacks,
  $videoPacksStatus,
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
import { MediaPreview } from '@/modules/media'
import { ArrowLeft } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  confirmAction,
  CreationActionBar,
  CreationFeedback,
  type CreationFeedbackState,
  Disclosure,
  HINT_TEXT,
  INPUT_CLASS,
  Pill
} from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

const ACTION_IN_PROGRESS = new Set(['queued', 'running', 'processing'])

interface ActionEntry {
  key: string
  label: string
  status: string
  stage: string
  error: string | null
  mediaUrl: string | null
  hasReadyAsset: boolean
  canRetry: boolean
  canGenerate: boolean
  canRegenerate: boolean
  syncTargets: ActionWire['sync_targets']
  mediaType: 'image' | 'video' | null
  motionPrompt: string
  feedback: string
  peekGeometry: ActionWire['peek_geometry']
  videoQueue: ActionWire['video_queue']
}

interface ActionPresentation {
  label: string
  hint?: string
  className: string
  state: CreationFeedbackState | 'success'
  isGenerating: boolean
}

function isActionInProgress(status: string): boolean {
  return ACTION_IN_PROGRESS.has(status)
}

function isSystemActionKey(key: string): key is SystemActionKey {
  return (SYSTEM_ACTION_KEYS as readonly string[]).includes(key)
}

function ActionPreview({ type, url }: { type: 'image' | 'video'; url: string }): React.JSX.Element {
  const dict = useStrings()
  const className = 'aspect-[2/3] h-full max-h-full w-auto max-w-full object-contain'

  return (
    <MediaPreview type={type} url={url}>
      {({ failed, onError, onRetry, src, status }) => {
        if (failed || status === 'failed') {
          return (
            <CreationFeedback
              actions={
                <button className={BTN_SUBTLE} onClick={onRetry} type="button">
                  {dict.creation.retryLoad}
                </button>
              }
              state="error"
              title={dict.creation.previewFailed}
            />
          )
        }

        if (type === 'image') {
          return <img alt="" className={className} draggable={false} onError={onError} src={src} />
        }

        return (
          <video className={className} controls loop muted onError={onError} playsInline preload="metadata" src={src} />
        )
      }}
    </MediaPreview>
  )
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
  const [packsReloadKey, setPacksReloadKey] = useState(0)
  const [selectedActionKey, setSelectedActionKey] = useState<string | null>(null)
  const actionSearch = useAssetLibrarySearch()
  const handledReload = useRef(0)
  const packsStoreStatus = useStore($videoPacksStatus)

  // 列表状态由 store 维护，组件不再为拿状态重发一次列表读。
  const packsStatus: 'failed' | 'loaded' | 'loading' =
    authKind !== 'authenticated'
      ? 'loaded'
      : packsStoreStatus === 'error'
        ? 'failed'
        : packsStoreStatus === 'loaded'
          ? 'loaded'
          : 'loading'

  const [editing, setEditing] = useState<{ key: string; feedback: string } | null>(null)

  const selectedOutfit = outfits.find(outfit => outfit.id === outfitId) ?? null
  const selectedPack = selectedOutfit ? (packs.find(pack => pack.outfit_id === selectedOutfit.id) ?? null) : null

  const scopedRequests = Object.values(genRequests).filter(scope =>
    scope.outfitId != null ? scope.outfitId === outfitId : scope.packId === selectedPack?.id
  )

  const requestedActions = new Set(scopedRequests.map(scope => scope.action))

  // 整包操作等待本着装任务收敛；单动作操作只等待同一动作。
  const scopedBusy = (!!selectedPack && videoPackIsGenerating(selectedPack)) || scopedRequests.length > 0

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
      stage: entry.stage,
      error: entry.error,
      mediaUrl: entry.current_media_url,
      hasReadyAsset: entry.has_ready_asset,
      canRetry: entry.can_retry,
      canGenerate: entry.can_generate,
      canRegenerate: entry.can_regenerate,
      syncTargets: entry.sync_targets,
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
        stage: '',
        videoQueue: null,
        feedback: '',
        error: null,
        mediaUrl: null,
        hasReadyAsset: false,
        canRetry: false,
        canGenerate: false,
        canRegenerate: true,
        syncTargets: [],
        mediaType: null,
        motionPrompt: '',
        peekGeometry: null
      })
    }
  }

  const visibleActions = actions.filter(action => actionSearch.matches(action.label))

  // 搜索只过滤列表；已选动作仍保留在详情区，避免输入时预览被抢走。
  const selectedAction = actions.find(action => action.key === selectedActionKey) ?? visibleActions[0] ?? null

  const actionIsBusy =
    !!selectedAction && videoActionIsBusy(packs, genRequests, outfitId, selectedPack?.id ?? null, selectedAction.key)

  const packError = scopedGenError(genErrors, outfitId, selectedPack?.id ?? null, null)?.message ?? null

  const scopedError =
    (selectedAction
      ? scopedGenError(genErrors, outfitId, selectedPack?.id ?? null, selectedAction.key)?.message
      : null) ?? packError

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

  // 列表由衣柜页统一拉取；这里只在用户手动刷新时补一次。
  useEffect(() => {
    if (authKind !== 'authenticated' || handledReload.current === packsReloadKey) {
      return
    }

    handledReload.current = packsReloadKey
    void hydrateVideoPack(true)
  }, [authKind, packsReloadKey])

  // 包、单动作或先于列表到达的进度仍在途时，轮询收敛失败及遗漏的终态事件。
  const anyPackGenerating =
    packs.some(
      pack =>
        videoPackIsGenerating(pack) ||
        pack.actions.some(action =>
          action.sync_targets.some(target => ['queued', 'processing'].includes(target.status))
        )
    ) || Object.keys(genStages).length > 0

  useEffect(() => {
    if (!anyPackGenerating) {
      return
    }

    const timer = window.setInterval(() => {
      void hydrateVideoPack(true)
    }, 5000)

    return () => {
      window.clearInterval(timer)
    }
  }, [anyPackGenerating])

  const requestActionGeneration = (feedback: string): void => {
    if (
      !selectedPack ||
      !selectedAction ||
      (!selectedAction.canGenerate && (!selectedPack.can_regenerate || !selectedAction.canRegenerate)) ||
      actionIsBusy
    ) {
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

  const actionStatus = (action: ActionEntry): ActionPresentation => {
    const ownVariant = action.syncTargets.find(
      target => target.target_kind === 'window' && target.target_id === selectedPack?.id
    )

    const variantUnavailable =
      !!ownVariant && ['not_generated', 'blocked', 'disabled', 'excluded'].includes(ownVariant.status)

    const actionGenerating = isActionInProgress(action.status) || requestedActions.has(action.key)
    const isGenerating = actionGenerating || requestedActions.has(null)

    const resultUnknown =
      (action.status === 'result_unknown' || !variantUnavailable) &&
      !action.videoQueue &&
      (action.status === 'result_unknown' ||
        (!action.canRegenerate && !action.canGenerate && !isActionInProgress(action.status)))

    const requestError = scopedGenError(genErrors, outfitId, selectedPack?.id ?? null, action.key)?.message

    const state: ActionPresentation['state'] =
      resultUnknown || action.videoQueue?.state === 'blocked'
        ? 'unknown'
        : action.videoQueue
          ? 'waiting'
          : isGenerating
            ? 'working'
            : variantUnavailable
              ? 'waiting'
              : action.status === 'failed' || (requestError ?? packError)
                ? 'error'
                : action.hasReadyAsset
                  ? 'success'
                  : 'waiting'

    const presentation = ((): Pick<ActionPresentation, 'label' | 'hint' | 'className'> => {
      if (action.status !== 'result_unknown' && variantUnavailable && ownVariant) {
        return {
          label: dict.common.actionVariants.statuses[ownVariant.status],
          hint: ownVariant.reason ?? undefined,
          className: ownVariant.status === 'blocked' ? 'bg-amber-400' : 'bg-line-strong'
        }
      }

      if (action.videoQueue) {
        return { label: dict.common.videoQueue[action.videoQueue.state], className: 'bg-amber-400' }
      }

      if (resultUnknown) {
        return { label: t.videoActionResultUnknown, hint: t.videoActionResultUnknownHint, className: 'bg-amber-400' }
      }

      if (actionGenerating) {
        return { label: t.videoActionGenerating, className: 'bg-amber-400' }
      }

      if (requestError) {
        return { label: t.videoActionFailed, hint: requestError, className: 'bg-danger-fg' }
      }

      if ((action.key === 'peek_left' || action.key === 'peek_right') && action.mediaUrl && !action.peekGeometry) {
        return {
          label: t.videoPeekCalibrationFailed,
          hint: t.videoPeekCalibrationFailedHint,
          className: 'bg-amber-400'
        }
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
    })()

    return { ...presentation, state, isGenerating }
  }

  const actionStatuses = Object.fromEntries(actions.map(action => [action.key, actionStatus(action)]))
  const selectedStatus = selectedAction ? actionStatuses[selectedAction.key] : null

  const selectedStageKey = Object.entries(VIDEO_GEN_STAGE_TEXT_KEYS).find(
    ([stage]) => stage === selectedAction?.stage
  )?.[1]

  const canGenerateAction =
    !!selectedAction &&
    (selectedAction.canGenerate || (!!selectedPack?.can_regenerate && selectedAction.canRegenerate)) &&
    !actionIsBusy

  const canResumeAction = selectedAction?.status === 'failed' && selectedAction.canRetry && !actionIsBusy
  const firstGeneration = !!selectedAction && !selectedAction.hasReadyAsset && selectedAction.canGenerate

  const actionGenerationLabel =
    firstGeneration || selectedAction?.status === 'missing' ? t.videoGenMissingAction : t.videoRedoAction

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
                {selectedPack?.active ? <Pill tone="success">{t.videoActive}</Pill> : null}
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
            {!onMaterialReady &&
            selectedPack?.status === 'ready' &&
            selectedPack.identity_review !== 'review' &&
            !selectedPack.active ? (
              <button className={BTN_PRIMARY} onClick={() => void activateVideoPack(selectedPack.id)} type="button">
                {t.videoActivate}
              </button>
            ) : null}
            {canRegeneratePack ? (
              <Disclosure
                summaryClassName="rounded-lg px-3 py-2 text-xs text-muted"
                title={dict.common.moreActions}
                variant="inline"
              >
                <button
                  className={BTN_SUBTLE}
                  disabled={scopedBusy}
                  onClick={() => {
                    void confirmAction({
                      confirmLabel: t.videoRegenAction,
                      description: t.videoRegenBody,
                      title: t.videoRegenTitle
                    }).then(confirmed => {
                      if (confirmed && canRegeneratePack) {
                        void generateVideoPack({ force: true, outfitId: selectedOutfit?.id })
                      }
                    })
                  }}
                  type="button"
                >
                  {t.videoRegenAction}
                </button>
              </Disclosure>
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
                onChange={event => actionSearch.onQueryChange(event.target.value)}
                placeholder={t.videoActionSearch}
                type="search"
                value={actionSearch.query}
              />
            </div>
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-1.5">
              {visibleActions.map(action => {
                const status = actionStatuses[action.key]
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
                  </div>
                </div>

                <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
                  <div className="grid min-h-48 grid-cols-1 gap-3">
                    {selectedAction.mediaUrl && selectedAction.mediaType ? (
                      <div className="flex min-h-48 min-w-0 flex-col rounded-lg bg-fill-trough p-2">
                        <p className="mb-2 text-[11px] text-muted">{t.videoActionCurrent}</p>
                        <div className="flex h-64 items-center justify-center overflow-hidden">
                          <ActionPreview
                            key={selectedAction.mediaUrl}
                            type={selectedAction.mediaType}
                            url={selectedAction.mediaUrl}
                          />
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center justify-center rounded-lg bg-fill-trough px-5 text-center">
                        <div className="max-w-md">
                          <p className="text-xs text-body">{selectedStatus.hint ?? selectedStatus.label}</p>
                          {selectedAction.error ? (
                            <p className="mt-1 text-[11px] text-danger-fg">{selectedAction.error}</p>
                          ) : null}
                        </div>
                      </div>
                    )}
                  </div>

                  {selectedAction.syncTargets.length > 0 ? (
                    <div className="mt-3 shrink-0">
                      <ActionSyncStatus targets={selectedAction.syncTargets} />
                    </div>
                  ) : null}

                  {selectedAction.motionPrompt ? (
                    <Disclosure
                      bodyClassName="whitespace-pre-wrap text-xs leading-relaxed text-muted"
                      className="mt-2 text-xs text-muted"
                      title={dict.common.creationDetails}
                      variant="inline"
                    >
                      <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-muted">
                        {selectedAction.motionPrompt}
                      </p>
                    </Disclosure>
                  ) : null}

                  {editing?.key === selectedAction.key ? (
                    <div className="mt-3 space-y-2">
                      <p className={HINT_TEXT}>{t.videoRedoActionHint}</p>
                      <label className={HINT_TEXT} htmlFor="video-action-feedback">
                        {t.videoFeedback}
                      </label>
                      <textarea
                        className={cn(INPUT_CLASS, 'min-h-16 resize-y text-xs')}
                        disabled={actionIsBusy}
                        id="video-action-feedback"
                        maxLength={1000}
                        onChange={event => setEditing({ ...editing, feedback: event.target.value })}
                        value={editing.feedback}
                      />
                    </div>
                  ) : null}
                </div>

                {selectedStatus.state !== 'success' && (
                  <CreationFeedback
                    actions={
                      selectedStatus.state === 'unknown' ? (
                        <button
                          className={BTN_SUBTLE}
                          disabled={packsStatus === 'loading'}
                          onClick={() => setPacksReloadKey(key => key + 1)}
                          type="button"
                        >
                          {dict.common.retry}
                        </button>
                      ) : undefined
                    }
                    className="mt-3 shrink-0"
                    description={
                      selectedStatus.hint ?? (selectedStatus.isGenerating ? dict.creation.canLeave : undefined)
                    }
                    key={`${selectedPack.id}:${selectedAction.key}:${selectedStatus.state}`}
                    state={selectedStatus.state}
                    title={
                      selectedStatus.isGenerating && !selectedAction.videoQueue && selectedStageKey
                        ? t[selectedStageKey]
                        : selectedStatus.label
                    }
                  />
                )}

                {editing?.key === selectedAction.key ? (
                  <CreationActionBar>
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
                  </CreationActionBar>
                ) : canGenerateAction || canResumeAction ? (
                  <CreationActionBar description={canResumeAction ? t.videoResumeActionHint : undefined}>
                    {canResumeAction ? (
                      <button className={BTN_PRIMARY} onClick={requestActionResume} type="button">
                        {t.videoResumeAction}
                      </button>
                    ) : null}
                    {canGenerateAction ? (
                      <button
                        className={BTN_SUBTLE}
                        onClick={() => {
                          if (firstGeneration) {
                            requestActionGeneration('')
                          } else {
                            setEditing({ key: selectedAction.key, feedback: selectedAction.feedback })
                          }
                        }}
                        type="button"
                      >
                        {actionGenerationLabel}
                      </button>
                    ) : null}
                  </CreationActionBar>
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
    </section>
  )
}
