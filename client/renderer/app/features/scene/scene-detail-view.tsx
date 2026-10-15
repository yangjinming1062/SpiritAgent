import type React from 'react'
import { useState } from 'react'

import { pickAvatarImage } from '@/modules/character'
import { adoptSceneImage, type SceneAsset } from '@/modules/scene'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { ArrowLeft, Loader2, Pencil, RefreshCw, Sparkles, ZoomIn } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, HINT_TEXT, INPUT_CLASS, SettingCard } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

export interface SceneEditDraft {
  title: string
  description: string
}

interface SceneDetailViewProps {
  activeScene: SceneAsset | null
  busy: boolean
  detail: SceneAsset | null
  draft?: SceneEditDraft
  loading: boolean
  selectingMaterial?: boolean
  onActivate: (sceneId: string) => void
  onBack: () => void
  onCancelEdit: () => void
  onCancelTask: (sceneId: string) => Promise<void>
  onChangeDraft: (next: Partial<SceneEditDraft>) => void
  onDelete: (scene: SceneAsset) => void
  onEdit: () => void
  onLoad: () => Promise<SceneAsset | null>
  onRegenerate: (sceneId: string) => Promise<void>
  onRetryAnalysis: (sceneId: string) => Promise<void>
  onSave: (sceneId: string) => Promise<void>
  onSaveAndRegenerate: (sceneId: string) => Promise<void>
  onZoom: (url: string, name: string) => void
}

export function SceneDetailView({
  activeScene,
  busy,
  detail,
  draft,
  loading,
  selectingMaterial = false,
  onActivate,
  onBack,
  onCancelEdit,
  onCancelTask,
  onChangeDraft,
  onDelete,
  onEdit,
  onLoad,
  onRegenerate,
  onRetryAnalysis,
  onSave,
  onSaveAndRegenerate,
  onZoom
}: SceneDetailViewProps): React.JSX.Element {
  const strings = useStrings()
  const t = strings.living.scene
  const tToasts = strings.living.toasts
  const [actionKind, setActionKind] = useState<'refresh' | 'other' | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [loadedImage, setLoadedImage] = useState<string | null>(null)
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const begin = useAsyncGuard()
  const actionBusy = actionKind !== null
  const refreshing = actionKind === 'refresh'
  const isCurrent = detail?.id === activeScene?.id
  const regenerating = detail?.regeneration?.status === 'pending'
  const generating = detail?.status === 'pending' || regenerating
  const hasImage = Boolean(detail?.assetUrl)
  const canChangeInfo = hasImage && detail?.status !== 'pending' && !busy && !regenerating

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

  const canActivate =
    detail?.status === 'ready' &&
    Boolean(detail.url) &&
    loadedImage === detail.url &&
    Boolean(detail.title.trim() && detail.description.trim())

  const canRetryAnalysis = Boolean(
    detail &&
    (((detail.status === 'description_failed' || detail.status === 'cancelled') && hasImage) ||
      (detail.status === 'failed' && detail.stage === 'review_failed') ||
      (detail.regeneration?.status === 'failed' &&
        (detail.regeneration.stage === 'analyze' || detail.regeneration.stage === 'review_failed')))
  )

  const runAction = async (
    action: (isLive: () => boolean) => Promise<void>,
    kind: 'refresh' | 'other' = 'other'
  ): Promise<void> => {
    if (actionBusy) {
      return
    }

    setActionKind(kind)
    setActionError(null)
    const isLive = begin()

    try {
      await action(isLive)
    } catch (error) {
      if (isLive()) {
        setActionError(
          errorMessage(error, kind === 'refresh' ? tToasts.sceneStatusRefreshFailed : tToasts.sceneRegenerateFailed)
        )
      }
    } finally {
      if (isLive()) {
        setActionKind(null)
      }
    }
  }

  const refreshDetail = (): Promise<void> =>
    runAction(async () => {
      await onLoad()
    }, 'refresh')

  if (!detail) {
    return (
      <section className="space-y-4">
        <button className={BTN_SUBTLE} onClick={onBack} type="button">
          <ArrowLeft className="size-3.5" />
          <span>{t.backToLibrary}</span>
        </button>
        <SettingCard>
          <div className="space-y-3 p-5 text-center">
            {loading || refreshing ? <Loader2 className="mx-auto size-5 animate-spin text-accent" /> : null}
            <p className="text-xs text-faint">{loading ? t.loading : t.detailLoadFailed}</p>
            {actionError ? (
              <p className="text-xs text-danger-fg" role="alert">
                {actionError}
              </p>
            ) : null}
            {!loading ? (
              <button
                aria-busy={refreshing}
                className={BTN_SUBTLE}
                disabled={actionBusy}
                onClick={() => void refreshDetail()}
                type="button"
              >
                <RefreshCw className="size-3.5" />
                {refreshing ? t.refreshing : t.refresh}
              </button>
            ) : null}
          </div>
        </SettingCard>
      </section>
    )
  }

  return (
    <section className="space-y-4">
      <div className="flex min-w-0 items-center gap-3">
        <button className={cn(BTN_SUBTLE, 'shrink-0')} onClick={onBack} type="button">
          <ArrowLeft className="size-3.5" />
          <span>{t.backToLibrary}</span>
        </button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-strong">{detail.title || t.untitled}</h2>
          <p className={HINT_TEXT}>{t.statuses[detail.status]}</p>
        </div>
        {isCurrent ? (
          <span className="shrink-0 rounded-full bg-accent/15 px-2.5 py-1 text-[10px] text-accent">
            {t.historyCurrentLabel}
          </span>
        ) : null}
      </div>

      {detail.regeneration?.status === 'failed' ? (
        <SettingCard className="border border-danger-line/50">
          <p className="p-3 text-xs text-danger-fg">{detail.regeneration.error || t.imageRegenerationFailed}</p>
        </SettingCard>
      ) : null}
      {detail.error && detail.status !== 'ready' ? (
        <SettingCard className="border border-danger-line/50">
          <p className="p-3 text-xs text-danger-fg">{detail.error}</p>
        </SettingCard>
      ) : null}
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
                onError={() => setLoadedImage(null)}
                onLoad={() => setLoadedImage(detail.url)}
                src={detail.url}
              />
              <span className="absolute bottom-2 right-2 rounded-full bg-black/50 p-1.5 text-white opacity-80 transition group-hover:opacity-100">
                <ZoomIn className="size-4" />
              </span>
              {regenerating ? (
                <span className="absolute inset-0 flex items-center justify-center gap-2 bg-black/35 text-xs text-white">
                  <Loader2 className="size-4 animate-spin" />
                  {t.imageRegenerating}
                </span>
              ) : null}
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
                {generating ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                {detail.stage === 'waiting_upload' ? t.waitingUploadOverlay : t.noScene}
              </div>
            </div>
          )}
        </div>

        <div className="min-w-0">
          {detail.image_size || detail.source_size || detail.target_size ? (
            <details className="px-4 pt-3 text-xs text-muted sm:px-5">
              <summary className="cursor-pointer">{strings.common.creationDetails}</summary>
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
            </details>
          ) : null}
          {draft ? (
            <div className="space-y-4 p-4 sm:p-5">
              <label className="block space-y-1.5 text-xs text-body">
                <span>{t.titleLabel}</span>
                <input
                  className={INPUT_CLASS}
                  maxLength={80}
                  onChange={event => onChangeDraft({ title: event.target.value })}
                  value={draft.title}
                />
              </label>
              <label className="block space-y-1.5 text-xs text-body">
                <span>{t.descriptionLabel}</span>
                <textarea
                  className={INPUT_CLASS}
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
              {detail.regeneration?.status === 'pending' ? (
                <p className="flex items-center gap-2 border-t border-line-hairline pt-2 text-[11px] text-accent">
                  <Loader2 className="size-3.5 animate-spin" />
                  {t.imageRegenerating}
                </p>
              ) : null}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-line-hairline p-4 sm:px-5">
          {(!isCurrent || selectingMaterial) && hasImage && !draft ? (
            <button
              className={BTN_PRIMARY}
              disabled={actionBusy || !canActivate}
              onClick={() => onActivate(detail.id)}
              type="button"
            >
              {t.historyRollbackLabel}
            </button>
          ) : null}
          {canRetryAnalysis ? (
            <button
              className={BTN_SUBTLE}
              disabled={actionBusy || busy}
              onClick={() => void runAction(() => onRetryAnalysis(detail.id))}
              type="button"
            >
              {t.retryAnalysis}
            </button>
          ) : null}
          {generating ? (
            <>
              <button
                className={BTN_SUBTLE}
                disabled={actionBusy}
                onClick={() => void runAction(() => onCancelTask(detail.id))}
                type="button"
              >
                {t.cancelTask}
              </button>
              {detail.status === 'pending' && detail.stage === 'waiting_upload' ? (
                <button
                  className={BTN_PRIMARY}
                  disabled={actionBusy}
                  onClick={() =>
                    void runAction(async isLive => {
                      const result = await pickAvatarImage(t.chooseReference)

                      if (!isLive()) {
                        return
                      }

                      if (result && 'image' in result) {
                        await adoptSceneImage(result.image, detail.id)
                      } else if (result && 'error' in result) {
                        throw new Error(result.error)
                      }
                    })
                  }
                  type="button"
                >
                  {t.waitingUploadAction}
                </button>
              ) : null}
            </>
          ) : null}
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
            <details>
              <summary className="cursor-pointer rounded-lg px-3 py-2 text-xs text-muted hover:bg-fill-hover">
                {strings.common.moreActions}
              </summary>
              <div className="mt-1 flex min-w-48 flex-col gap-2 rounded-xl border border-line-hairline bg-fill-trough p-3">
                {hasImage && (detail.status === 'ready' || detail.status === 'description_failed') ? (
                  <button
                    className={cn(BTN_SUBTLE, 'whitespace-nowrap')}
                    disabled={!canRegenerate || actionBusy}
                    onClick={() => {
                      if (hasDraft) {
                        void runAction(() => onSaveAndRegenerate(detail.id))
                      } else {
                        void runAction(() => onRegenerate(detail.id))
                      }
                    }}
                    type="button"
                  >
                    {actionKind === 'other' ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="size-3.5" />
                    )}
                    {hasDraft ? t.saveAndRegenerate : t.imageRegenerate}
                  </button>
                ) : null}
                <button
                  className={BTN_SUBTLE}
                  disabled={actionBusy}
                  onClick={() => {
                    setLoadedImage(null)
                    setPreviewAttempt(attempt => attempt + 1)
                    void refreshDetail()
                  }}
                  type="button"
                >
                  {t.refresh}
                </button>
                {!isCurrent ? (
                  <button
                    className={cn(BTN_SUBTLE, 'text-danger-fg')}
                    disabled={actionBusy || generating}
                    onClick={() => onDelete(detail)}
                    type="button"
                  >
                    {t.historyDeleteLabel}
                  </button>
                ) : null}
              </div>
            </details>
          </div>
        </div>
        {actionError ? (
          <p className="px-4 pb-4 text-xs text-danger-fg sm:px-5" role="alert">
            {actionError}
          </p>
        ) : null}
        {detail.status === 'description_failed' ? (
          <p className={cn(HINT_TEXT, 'px-4 pb-4 sm:px-5')}>{t.needsDescription}</p>
        ) : null}
      </SettingCard>
    </section>
  )
}
