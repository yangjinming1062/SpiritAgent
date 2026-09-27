import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { pickAvatarImage } from '@/modules/character'
import { adoptSceneImage, type SceneAsset } from '@/modules/scene'
import { ArrowLeft, Loader2, Pencil, RefreshCw, Sparkles, ZoomIn } from '@/shared/lib/icons'
import { currentClearEpoch } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, HINT_TEXT, INPUT_CLASS, SettingCard } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

export interface SceneEditDraft {
  editing: boolean
  title: string
  description: string
}

interface SceneDetailViewProps {
  activeScene: SceneAsset | null
  busy: boolean
  detail: SceneAsset | null
  draft?: SceneEditDraft
  loading: boolean
  onActivate: (sceneId: string) => void
  onBack: () => void
  onCancelTask: (sceneId: string) => Promise<void>
  onChangeDraft: (next: Partial<SceneEditDraft>) => void
  onDelete: (scene: SceneAsset) => void
  onEdit: () => void
  onLoad: () => Promise<void>
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
  onActivate,
  onBack,
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
  const t = useStrings().living.scene
  const tToasts = useStrings().living.toasts
  const [actionBusy, setActionBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const mounted = useRef(true)
  const isCurrent = detail?.id === activeScene?.id
  const regenerating = detail?.regeneration?.status === 'pending'
  const generating = detail?.status === 'pending' || regenerating
  const canChangeInfo = Boolean(detail?.url) && detail?.status !== 'pending' && !busy && !regenerating

  const hasDraft = Boolean(
    detail && draft && (draft.title.trim() !== detail.title || draft.description.trim() !== detail.description)
  )

  const canRegenerate =
    canChangeInfo &&
    Boolean(
      draft?.editing && hasDraft
        ? draft.title.trim() && draft.description.trim()
        : detail?.status === 'ready' && detail.title.trim() && detail.description.trim()
    )

  const canActivate = detail?.status === 'ready' && Boolean(detail.url) && Boolean(detail.description.trim())

  useEffect(() => {
    mounted.current = true

    return () => {
      mounted.current = false
    }
  }, [])

  const runAction = async (action: () => Promise<void>): Promise<void> => {
    if (actionBusy) {
      return
    }

    setActionBusy(true)
    setActionError(null)
    const epoch = currentClearEpoch()

    try {
      await action()
    } catch (error) {
      if (mounted.current && epoch === currentClearEpoch()) {
        setActionError(error instanceof Error ? error.message : tToasts.sceneRegenerateFailed)
      }
    } finally {
      if (mounted.current && epoch === currentClearEpoch()) {
        setActionBusy(false)
      }
    }
  }

  if (!detail) {
    return (
      <section className="space-y-4">
        <button className={BTN_SUBTLE} onClick={onBack} type="button">
          <ArrowLeft className="size-3.5" />
          <span>{t.backToLibrary}</span>
        </button>
        <SettingCard>
          <div className="space-y-3 p-5 text-center">
            {loading ? <Loader2 className="mx-auto size-5 animate-spin text-accent" /> : null}
            <p className="text-xs text-faint">{loading ? t.loading : t.detailLoadFailed}</p>
            {!loading ? (
              <button className={BTN_SUBTLE} onClick={onLoad} type="button">
                <RefreshCw className="size-3.5" />
                {t.refresh}
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
      {actionError ? (
        <SettingCard className="border border-danger-line/50">
          <p className="p-3 text-xs text-danger-fg">{actionError}</p>
        </SettingCard>
      ) : null}

      <SettingCard className="min-w-0" divided={false}>
        <div className="border-b border-line-hairline">
          {detail.url ? (
            <button
              aria-label={t.viewOriginal}
              className="group relative block aspect-video w-full cursor-zoom-in bg-fill-trough"
              onClick={() => onZoom(detail.url, detail.title || t.historyAltFallback)}
              type="button"
            >
              <img alt={detail.title || t.historyAltFallback} className="h-full w-full object-cover" src={detail.url} />
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
            <div className="grid aspect-video place-items-center bg-fill-trough text-xs text-faint">
              <div className="flex items-center gap-2">
                {generating ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                {detail.stage === 'waiting_upload' ? t.waitingUploadOverlay : t.noScene}
              </div>
            </div>
          )}
        </div>

        <div className="min-w-0">
          {draft?.editing ? (
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
          {!isCurrent && canActivate ? (
            <button className={BTN_SUBTLE} disabled={actionBusy} onClick={() => onActivate(detail.id)} type="button">
              {t.historyRollbackLabel}
            </button>
          ) : null}
          {!isCurrent ? (
            <button
              className={BTN_SUBTLE}
              disabled={actionBusy || regenerating || detail.status === 'pending'}
              onClick={() => onDelete(detail)}
              type="button"
            >
              {t.historyDeleteLabel}
            </button>
          ) : null}
          {(detail.status === 'description_failed' || detail.status === 'cancelled') && detail.url ? (
            <button
              className={BTN_SUBTLE}
              disabled={actionBusy || busy}
              onClick={() => void runAction(() => onRetryAnalysis(detail.id))}
              type="button"
            >
              {t.retryAnalysis}
            </button>
          ) : null}
          {detail.status === 'pending' ? (
            <>
              <button className={BTN_SUBTLE} disabled={actionBusy} onClick={() => void runAction(onLoad)} type="button">
                {t.refresh}
              </button>
              <button
                className={BTN_SUBTLE}
                disabled={actionBusy}
                onClick={() => void runAction(() => onCancelTask(detail.id))}
                type="button"
              >
                {t.cancelTask}
              </button>
              {detail.stage === 'waiting_upload' ? (
                <button
                  className={BTN_PRIMARY}
                  disabled={actionBusy}
                  onClick={() =>
                    void runAction(async () => {
                      const epoch = currentClearEpoch()
                      const result = await pickAvatarImage(t.chooseReference)

                      if (!mounted.current || epoch !== currentClearEpoch()) {
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
          {regenerating ? (
            <>
              <button className={BTN_SUBTLE} disabled={actionBusy} onClick={() => void runAction(onLoad)} type="button">
                {t.refresh}
              </button>
              <button
                className={BTN_SUBTLE}
                disabled={actionBusy}
                onClick={() => void runAction(() => onCancelTask(detail.id))}
                type="button"
              >
                {t.cancelTask}
              </button>
            </>
          ) : null}
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {draft?.editing ? (
              <>
                <button
                  className={BTN_SUBTLE}
                  disabled={actionBusy}
                  onClick={() =>
                    onChangeDraft({ editing: false, title: detail.title, description: detail.description })
                  }
                  type="button"
                >
                  {t.cancel}
                </button>
                <button
                  className={BTN_SUBTLE}
                  disabled={actionBusy || !canChangeInfo || !draft.title.trim() || !draft.description.trim()}
                  onClick={() => void runAction(() => onSave(detail.id))}
                  type="button"
                >
                  {t.save}
                </button>
              </>
            ) : detail.url ? (
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
            {detail.url && (detail.status === 'ready' || detail.status === 'description_failed') ? (
              <button
                className={cn(BTN_PRIMARY, 'whitespace-nowrap')}
                disabled={!canRegenerate || actionBusy}
                onClick={() => {
                  if (draft?.editing && hasDraft) {
                    void runAction(() => onSaveAndRegenerate(detail.id))
                  } else {
                    void runAction(() => onRegenerate(detail.id))
                  }
                }}
                type="button"
              >
                {actionBusy ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
                {draft?.editing && hasDraft ? t.saveAndRegenerate : t.imageRegenerate}
              </button>
            ) : null}
          </div>
        </div>
        {detail.status === 'description_failed' ? (
          <p className={cn(HINT_TEXT, 'px-4 pb-4 sm:px-5')}>{t.needsDescription}</p>
        ) : null}
      </SettingCard>
    </section>
  )
}
