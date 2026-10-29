import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { pickAvatarImage } from '@/modules/character'
import {
  $sceneMutationIssues,
  adoptSceneImage,
  type SceneAsset,
  type SceneMutationIssue,
  updateSceneMutationIssue
} from '@/modules/scene'
import { visualsApi } from '@/modules/visuals'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import type { DayPhase } from '@/shared/lib/day-phase'
import { ArrowLeft, Pencil, RefreshCw, Sparkles, ZoomIn } from '@/shared/lib/icons'
import { failureText, isClientErrorIpc } from '@/shared/lib/ipc-error'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  CreationActionBar,
  CreationFeedback,
  type CreationFeedbackState,
  CreationWorkspace,
  Disclosure,
  INPUT_CLASS,
  PanelSelect,
  SettingCard,
  Spinner
} from '@/shared/panel'
import { useStrings } from '@/shared/strings'

export interface SceneEditDraft {
  title: string
  description: string
}

interface SceneDetailViewProps {
  busy: boolean
  detail: SceneAsset | null
  draft?: SceneEditDraft
  loadError: string | null
  loading: boolean
  onBack: () => void
  onCancelEdit: () => void
  onCancelTask: (sceneId: string) => Promise<void>
  onChangeDraft: (next: Partial<SceneEditDraft>) => void
  onDelete: (scene: SceneAsset) => void
  onEdit: () => void
  onLoad: () => Promise<SceneAsset | null>
  onRegenerate: (sceneId: string) => Promise<void>
  onRetryAnalysis: (sceneId: string) => Promise<void>
  onRetryImage: () => Promise<SceneAsset | null>
  onSave: (sceneId: string) => Promise<void>
  onSaveAndRegenerate: (sceneId: string) => Promise<void>
  onZoom: (url: string, name: string) => void
}

export function SceneDetailView({
  busy,
  detail,
  draft,
  loadError,
  loading,
  onBack,
  onCancelEdit,
  onCancelTask,
  onChangeDraft,
  onDelete,
  onEdit,
  onLoad,
  onRegenerate,
  onRetryAnalysis,
  onRetryImage,
  onSave,
  onSaveAndRegenerate,
  onZoom
}: SceneDetailViewProps): React.JSX.Element {
  const strings = useStrings()
  const t = strings.living.scene
  const tToasts = strings.living.toasts

  const [actionKind, setActionKind] = useState<
    'refresh' | 'save' | 'cancel' | 'generate' | 'analyze' | 'upload' | null
  >(null)

  const [actionError, setActionError] = useState<string | null>(null)
  const issues = useStore($sceneMutationIssues)
  const uncertain = detail ? (issues[detail.id] ?? null) : null

  const setUncertain = (update: React.SetStateAction<SceneMutationIssue | null>): void => {
    if (detail) {
      updateSceneMutationIssue(detail.id, current => (typeof update === 'function' ? update(current) : update))
    }
  }

  const [failedImage, setFailedImage] = useState<string | null>(null)
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const [motionBusy, setMotionBusy] = useState(false)
  const [motionError, setMotionError] = useState<string | null>(null)
  const [motionNotice, setMotionNotice] = useState<string | null>(null)
  const [motionPhase, setMotionPhase] = useState<'' | DayPhase>('')
  const begin = useAsyncGuard()
  const actionBusy = actionKind !== null || motionBusy
  const canMakeMotion = Boolean(detail?.status === 'ready' && detail.assetUrl && detail.id)

  const makeMotion = (): void => {
    const dayPhase = motionPhase

    if (!detail || actionBusy || !canMakeMotion) {
      return
    }

    const scope = captureAuthScope()
    const live = begin()

    setMotionBusy(true)
    setActionError(null)
    setMotionError(null)
    setMotionNotice(null)
    void visualsApi
      .createSceneMedia({ scene_id: detail.id, day_phase: dayPhase || null })
      .then(() => {
        if (scope?.() && live()) {
          setActionError(null)
          setMotionNotice(dayPhase ? t.makeMotionPhaseDone : t.makeMotionDone)
        }
      })
      .catch(error => {
        if (scope?.() && live()) {
          setMotionError(failureText(error, t.makeMotionFailed))
        }
      })
      .finally(() => {
        if (scope?.() && live()) {
          setMotionBusy(false)
        }
      })
  }

  const refreshing = actionKind === 'refresh'
  const regenerating = detail?.regeneration?.status === 'pending'
  const generating = detail?.status === 'pending' || regenerating
  const hasImage = Boolean(detail?.assetUrl)
  const stage = regenerating ? detail?.regeneration?.stage : detail?.stage
  const canChangeInfo = hasImage && detail?.status !== 'pending' && !busy && !regenerating && !uncertain

  const hasDraft = Boolean(
    detail && draft && (draft.title.trim() !== detail.title || draft.description.trim() !== detail.description)
  )

  const canRegenerate =
    canChangeInfo &&
    Boolean(
      draft && hasDraft
        ? draft.title.trim() && draft.description.trim()
        : detail?.status === 'ready' && detail.title.trim() && detail.description.trim()
    )

  const canRetryAnalysis = Boolean(
    detail &&
    (((detail.status === 'description_failed' || detail.status === 'cancelled') && hasImage) ||
      (detail.status === 'failed' && detail.stage === 'review_failed') ||
      (detail.regeneration?.status === 'failed' &&
        (detail.regeneration.stage === 'analyze' || detail.regeneration.stage === 'review_failed')))
  )

  const canRetryGeneration = detail?.can_retry_generation

  const runAction = async (
    action: (isLive: () => boolean) => Promise<void>,
    kind: NonNullable<typeof actionKind> = 'save'
  ): Promise<void> => {
    const currentIssue = detail ? $sceneMutationIssues.get()[detail.id] : null
    const scope = captureAuthScope()

    if (!scope || actionBusy || currentIssue?.pending || (currentIssue && kind !== 'refresh')) {
      return
    }

    const isLive = begin()

    const mutation: SceneMutationIssue | null =
      kind !== 'refresh' && detail ? { baseline: detail, message: '', inspected: false, pending: true } : null

    const settle = (next: SceneMutationIssue | null): void => {
      if (scope() && mutation) {
        updateSceneMutationIssue(mutation.baseline.id, current => (current === mutation ? next : current))
      }
    }

    if (mutation) {
      setUncertain(mutation)
    }

    setActionKind(kind)
    setActionError(null)

    try {
      await action(isLive)
      settle(null)
    } catch (error) {
      const message = failureText(
        error,
        kind === 'refresh' ? tToasts.sceneStatusRefreshFailed : tToasts.sceneRegenerateFailed
      )

      if (mutation && !isClientErrorIpc(error)) {
        settle({ ...mutation, message, pending: false })
      } else {
        settle(null)

        if (isLive()) {
          setActionError(message)

          if (kind === 'refresh') {
            setUncertain(current => (current ? { ...current, inspected: false } : null))
          }
        }
      }
    } finally {
      if (isLive()) {
        setActionKind(null)
      }
    }
  }

  const refreshDetail = (): Promise<void> =>
    runAction(async isLive => {
      const loaded = await onLoad()
      const baseline = uncertain?.baseline

      if (isLive() && loaded && baseline) {
        const changed =
          loaded.status !== baseline.status ||
          loaded.stage !== baseline.stage ||
          loaded.regeneration?.task_id !== baseline.regeneration?.task_id ||
          loaded.regeneration?.status !== baseline.regeneration?.status ||
          loaded.assetUrl.split(/[?#]/)[0] !== baseline.assetUrl.split(/[?#]/)[0] ||
          loaded.title !== baseline.title ||
          loaded.description !== baseline.description

        setUncertain(current => (current === uncertain ? (changed ? null : { ...current, inspected: true }) : current))
      } else if (isLive() && uncertain) {
        setUncertain(current => (current === uncertain ? { ...current, inspected: false } : current))
      }
    }, 'refresh')

  if (!detail) {
    return (
      <CreationWorkspace
        className="mx-auto w-full max-w-6xl"
        header={
          <button className={BTN_SUBTLE} onClick={onBack} type="button">
            <ArrowLeft className="size-3.5" />
            <span>{t.backToLibrary}</span>
          </button>
        }
      >
        <CreationFeedback
          actions={
            !loading ? (
              <button
                aria-atomic="true"
                aria-live="polite"
                className={BTN_SUBTLE}
                disabled={actionBusy || uncertain?.pending}
                onClick={() => void refreshDetail()}
                type="button"
              >
                <RefreshCw className="size-3.5" />
                {refreshing ? strings.common.loading : strings.common.retry}
              </button>
            ) : undefined
          }
          description={actionError || loadError || undefined}
          state={loading || refreshing ? 'working' : 'error'}
          title={loading ? strings.common.loading : refreshing ? undefined : t.detailLoadFailed}
        />
      </CreationWorkspace>
    )
  }

  const showFeedback = Boolean(
    actionError ||
    motionError ||
    loadError ||
    uncertain ||
    actionBusy ||
    generating ||
    detail.error ||
    detail.status !== 'ready' ||
    detail.regeneration?.status === 'failed' ||
    detail.regeneration?.status === 'cancelled'
  )

  const uncertaintyDescription = uncertain?.pending
    ? strings.creation.canLeave
    : uncertain
      ? `${uncertain.message} ${uncertain.inspected ? strings.creation.inspectionHint : ''}`
      : null

  const generationTitle =
    !regenerating && detail.stage === 'waiting_upload'
      ? t.waitingUploadOverlay
      : stage === 'analyze'
        ? t.analyzing
        : stage === 'evaluating' || stage === 'submitting'
          ? t.checking
          : regenerating
            ? t.imageRegenerating
            : t.pendingOverlay

  const feedbackStatus: { state: CreationFeedbackState; title?: string } = actionError
    ? { state: 'error', title: tToasts.sceneRegenerateFailed }
    : motionError
      ? { state: 'error', title: t.makeMotionFailed }
      : motionBusy
        ? { state: 'working', title: t.makeMotionBusy }
        : loadError
          ? { state: 'unknown', title: tToasts.sceneStatusRefreshFailed }
          : actionKind
            ? {
                state: 'working',
                title: {
                  refresh:
                    uncertain || detail.imageLoadFailed || (detail.url && failedImage === detail.url)
                      ? undefined
                      : strings.common.loading,
                  save: strings.common.saving,
                  cancel: t.cancelTask,
                  generate: t.imageRegenerate,
                  analyze: t.analyzing,
                  upload: strings.common.processing
                }[actionKind]
              }
            : uncertain
              ? {
                  state: uncertain.pending ? 'working' : 'unknown',
                  title: uncertain.pending ? strings.common.processing : strings.creation.unknown
                }
              : detail.regeneration?.status === 'failed'
                ? { state: 'error', title: t.imageRegenerationFailed }
                : generating
                  ? { state: detail.stage === 'waiting_upload' ? 'waiting' : 'working', title: generationTitle }
                  : {
                      state:
                        detail.regeneration?.status === 'cancelled' || detail.status === 'cancelled'
                          ? 'cancelled'
                          : 'error',
                      title:
                        detail.regeneration?.status === 'cancelled'
                          ? strings.creation.cancelled
                          : t.statuses[detail.status]
                    }

  const feedback = showFeedback && (
    <CreationFeedback
      {...feedbackStatus}
      actions={
        loadError || uncertain ? (
          <>
            <button
              aria-atomic="true"
              aria-live="polite"
              className={BTN_SUBTLE}
              disabled={actionBusy || uncertain?.pending}
              onClick={() => void refreshDetail()}
              type="button"
            >
              {refreshing ? strings.common.loading : t.checkStatus}
            </button>
            {uncertain?.inspected && !uncertain.pending ? (
              <button
                className={BTN_PRIMARY}
                disabled={actionBusy}
                onClick={() => setUncertain(current => (current === uncertain && current.inspected ? null : current))}
                type="button"
              >
                {strings.creation.finishWaiting}
              </button>
            ) : null}
          </>
        ) : undefined
      }
      description={
        actionError ||
        motionError ||
        loadError ||
        uncertaintyDescription ||
        (actionBusy
          ? strings.creation.inputLocked
          : detail.regeneration?.status === 'failed'
            ? detail.regeneration.error || t.imageRegenerationFailed
            : detail.error) ||
        (generating
          ? `${detail.stage === 'waiting_upload' ? t.waitingUploadHint : regenerating ? t.imageRegenerating : t.pendingOverlayHint} ${strings.creation.canLeave}`
          : hasImage && detail.status !== 'ready'
            ? t.needsDescription
            : undefined)
      }
    />
  )

  const header = (
    <div className="flex min-w-0 items-center gap-3">
      <button className={cn(BTN_SUBTLE, 'shrink-0')} onClick={onBack} type="button">
        <ArrowLeft className="size-3.5" />
        <span>{t.backToLibrary}</span>
      </button>
      <div className="min-w-0 flex-1">
        <h2 className="truncate text-sm font-semibold text-strong">{detail.title || t.untitled}</h2>
      </div>
    </div>
  )

  const footer = (
    <div className="space-y-2">
      {feedback}
      <CreationActionBar>
        {canRetryGeneration ? (
          <button
            className={BTN_PRIMARY}
            disabled={actionBusy || busy || Boolean(uncertain)}
            onClick={() => void runAction(() => onRegenerate(detail.id), 'generate')}
            type="button"
          >
            <RefreshCw className="size-3.5" />
            {t.retryGeneration}
          </button>
        ) : null}
        {canRetryAnalysis ? (
          <button
            className={BTN_SUBTLE}
            disabled={actionBusy || busy || Boolean(uncertain)}
            onClick={() => void runAction(() => onRetryAnalysis(detail.id), 'analyze')}
            type="button"
          >
            {t.retryAnalysis}
          </button>
        ) : null}
        {generating ? (
          <>
            <button
              className={BTN_SUBTLE}
              disabled={actionBusy || Boolean(uncertain)}
              onClick={() => void runAction(() => onCancelTask(detail.id), 'cancel')}
              type="button"
            >
              {t.cancelTask}
            </button>
            {detail.status === 'pending' && detail.stage === 'waiting_upload' ? (
              <button
                className={BTN_PRIMARY}
                disabled={actionBusy || Boolean(uncertain)}
                onClick={() =>
                  void runAction(async isLive => {
                    const result = await pickAvatarImage(t.chooseReference)

                    if (!isLive()) {
                      return
                    }

                    if (result && 'image' in result) {
                      await adoptSceneImage(result.image, detail.id)
                    } else if (result && 'error' in result) {
                      setActionError(result.error)
                    }
                  }, 'upload')
                }
                type="button"
              >
                {t.waitingUploadAction}
              </button>
            ) : null}
          </>
        ) : null}
        {canMakeMotion ? (
          <div className="flex flex-wrap items-center gap-2">
            <button
              className={BTN_SUBTLE}
              disabled={actionBusy || Boolean(uncertain)}
              onClick={makeMotion}
              type="button"
            >
              {motionBusy ? t.makeMotionBusy : t.makeMotion}
            </button>
            <PanelSelect
              ariaLabel={t.makeMotionPhaseLabel}
              disabled={actionBusy || Boolean(uncertain)}
              onChange={setMotionPhase}
              options={[
                { value: '', label: t.makeMotionPhaseAny },
                { value: 'dawn', label: t.dayPhases.dawn },
                { value: 'day', label: t.dayPhases.day },
                { value: 'dusk', label: t.dayPhases.dusk },
                { value: 'night', label: t.dayPhases.night }
              ]}
              value={motionPhase}
            />
          </div>
        ) : null}
        {motionNotice && !motionError ? <p className="text-[11px] text-faint">{motionNotice}</p> : null}
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {draft ? (
            <>
              <button className={BTN_SUBTLE} disabled={actionBusy} onClick={onCancelEdit} type="button">
                {t.cancel}
              </button>
              <button
                className={BTN_PRIMARY}
                disabled={actionBusy || !canChangeInfo || !draft.title.trim() || !draft.description.trim()}
                onClick={() => void runAction(() => onSave(detail.id))}
                type="button"
              >
                {t.save}
              </button>
            </>
          ) : hasImage ? (
            <button
              className={cn(BTN_SUBTLE, 'whitespace-nowrap')}
              disabled={!canChangeInfo || actionBusy}
              onClick={onEdit}
              type="button"
            >
              <Pencil className="size-3.5" />
              {t.edit}
            </button>
          ) : null}
          {hasImage && (detail.status === 'ready' || detail.status === 'description_failed') ? (
            <button
              className={cn(BTN_SUBTLE, 'whitespace-nowrap')}
              disabled={!canRegenerate || actionBusy}
              onClick={() => {
                if (hasDraft) {
                  void runAction(() => onSaveAndRegenerate(detail.id), 'generate')
                } else {
                  void runAction(() => onRegenerate(detail.id), 'generate')
                }
              }}
              type="button"
            >
              {actionKind === 'generate' ? <Spinner size="sm" /> : <RefreshCw className="size-3.5" />}
              {hasDraft ? t.saveAndRegenerate : t.imageRegenerate}
            </button>
          ) : null}
          <button
            className={cn(BTN_SUBTLE, 'text-danger-fg')}
            disabled={actionBusy || generating || Boolean(uncertain)}
            onClick={() => onDelete(detail)}
            type="button"
          >
            {t.historyDeleteLabel}
          </button>
        </div>
      </CreationActionBar>
    </div>
  )

  return (
    <CreationWorkspace className="mx-auto w-full max-w-6xl" footer={footer} header={header}>
      <SettingCard className="min-w-0" divided={false}>
        <div className="border-b border-line-hairline">
          {detail.url ? (
            <button
              aria-label={t.viewOriginal}
              className="group relative block w-full cursor-zoom-in bg-fill-trough"
              onClick={() => onZoom(detail.url, detail.title || t.historyAltFallback)}
              type="button"
            >
              <img
                alt={detail.title || t.historyAltFallback}
                className="block h-auto max-h-[65vh] w-full object-contain"
                key={`${detail.url}:${previewAttempt}`}
                onError={() => {
                  setFailedImage(detail.url)
                }}
                onLoad={() => {
                  setFailedImage(null)
                }}
                src={detail.url}
              />
              <span className="absolute bottom-2 right-2 rounded-full bg-black/50 p-1.5 text-white opacity-80 transition group-hover:opacity-100">
                <ZoomIn className="size-4" />
              </span>
            </button>
          ) : (
            <div
              className="grid max-h-[65vh] place-items-center bg-fill-trough text-xs text-faint"
              style={{
                aspectRatio: detail.target_size
                  ? `${detail.target_size.width} / ${detail.target_size.height}`
                  : '16 / 9'
              }}
            >
              <div className="flex items-center gap-2">
                {generating ? <Spinner /> : <Sparkles className="size-4" />}
                {detail.stage === 'waiting_upload'
                  ? t.waitingUploadOverlay
                  : generating
                    ? t.statuses.pending
                    : hasImage && !detail.imageLoadFailed
                      ? strings.common.loading
                      : t.noScene}
              </div>
            </div>
          )}
          {detail.imageLoadFailed || (detail.url && failedImage === detail.url) ? (
            <CreationFeedback
              actions={
                <button
                  aria-atomic="true"
                  aria-live="polite"
                  className={BTN_SUBTLE}
                  disabled={actionBusy || uncertain?.pending}
                  onClick={() =>
                    void runAction(async isLive => {
                      await onRetryImage()

                      if (isLive()) {
                        setFailedImage(null)
                        setPreviewAttempt(attempt => attempt + 1)
                      }
                    }, 'refresh')
                  }
                  type="button"
                >
                  {refreshing ? strings.common.loading : t.retryImage}
                </button>
              }
              state="error"
              title={t.imageLoadFailed}
            />
          ) : null}
        </div>

        <div className="min-w-0">
          {detail.image_size || detail.source_size || detail.target_size ? (
            <Disclosure
              bodyClassName="flex flex-wrap gap-x-4 gap-y-1 text-[11px]"
              className="px-4 pt-3 text-xs text-muted sm:px-5"
              title={strings.common.creationDetails}
              variant="inline"
            >
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                {detail.image_size ? (
                  <span>{t.imageSize(detail.image_size.width, detail.image_size.height)}</span>
                ) : null}
                {detail.source_size ? (
                  <span>{t.sourceSize(detail.source_size.width, detail.source_size.height)}</span>
                ) : null}
                {detail.target_size ? (
                  <span>{t.generationTargetSize(detail.target_size.width, detail.target_size.height)}</span>
                ) : null}
              </div>
            </Disclosure>
          ) : null}
          {draft ? (
            <div className="space-y-4 p-4 sm:p-5">
              <label className="block space-y-1.5 text-xs text-body">
                <span>{t.titleLabel}</span>
                <input
                  className={INPUT_CLASS}
                  disabled={actionBusy || !canChangeInfo}
                  maxLength={80}
                  onChange={event => onChangeDraft({ title: event.target.value })}
                  value={draft.title}
                />
              </label>
              <label className="block space-y-1.5 text-xs text-body">
                <span>{t.descriptionLabel}</span>
                <textarea
                  className={INPUT_CLASS}
                  disabled={actionBusy || !canChangeInfo}
                  maxLength={2000}
                  onChange={event => onChangeDraft({ description: event.target.value })}
                  rows={7}
                  value={draft.description}
                />
              </label>
            </div>
          ) : (
            <div className="space-y-3 p-4 sm:p-5">
              <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted">
                {detail.description || t.needsDescription}
              </p>
            </div>
          )}
        </div>
      </SettingCard>
    </CreationWorkspace>
  )
}
