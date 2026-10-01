import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import {
  $avatarSeeds,
  hydrateAvatarSeeds,
  pickAvatarImage,
  type PickedImage,
  SelfSourceImageFlow
} from '@/modules/character'
import { $pendingScene, $sceneTaskSlow, $sceneTaskStatus, hydrateScene } from '@/modules/scene'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { ArrowLeft, FileImage, Loader2, Plus, Sparkles } from '@/shared/lib/icons'
import { errorMessage } from '@/shared/lib/ipc-error'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, HINT_TEXT, INPUT_CLASS, SettingCard } from '@/shared/panel'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

export interface SceneCreateDraft {
  notes: string
  outfitDescription: string
  reference: PickedImage | null
}

interface SceneCreateViewProps {
  busy: boolean
  draft: SceneCreateDraft
  onAdopt: (image: PickedImage) => Promise<void>
  onBack: () => void
  onChange: (next: Partial<SceneCreateDraft>) => void
  onCancelTask: (sceneId: string) => Promise<void>
  onCreate: () => void
  onFetchPrompt: () => Promise<string>
  onUseAi: () => void
}

export function SceneCreateView({
  busy,
  draft,
  onAdopt,
  onBack,
  onChange,
  onCancelTask,
  onCreate,
  onFetchPrompt,
  onUseAi
}: SceneCreateViewProps): React.JSX.Element {
  const seeds = useStore($avatarSeeds)
  const pending = useStore($pendingScene)
  const taskStatus = useStore($sceneTaskStatus)
  const slow = useStore($sceneTaskSlow)
  const strings = useStrings()
  const t = strings.living.scene
  const tToasts = strings.living.toasts
  const [selfSourceOpen, setSelfSourceOpen] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [referenceError, setReferenceError] = useState(false)
  const begin = useAsyncGuard()
  const generating = taskStatus === 'pending'
  const waitingUpload = taskStatus === 'waiting_upload'
  const formBusy = busy || generating || waitingUpload || selecting || saving

  const chooseReference = async (): Promise<void> => {
    if (formBusy) {
      return
    }

    const isLive = begin()
    setSelecting(true)
    setReferenceError(false)
    const result = await pickAvatarImage(t.chooseReference)

    if (!isLive()) {
      return
    }

    if (result && 'image' in result) {
      onChange({ reference: result.image })
    } else if (result && 'error' in result) {
      setReferenceError(true)
    }

    setSelecting(false)
  }

  const runSaving = async (action: () => Promise<void>): Promise<void> => {
    const isLive = begin()
    setSaving(true)

    try {
      await action()
    } catch (error) {
      if (isLive()) {
        notify({ kind: 'warning', message: errorMessage(error, tToasts.sceneRegenerateFailed) })
      }
    } finally {
      if (isLive()) {
        setSaving(false)
      }
    }
  }

  const uploadWaitingImage = async (): Promise<void> => {
    if (!pending || selecting || saving) {
      return
    }

    const isLive = begin()
    setSelecting(true)
    const result = await pickAvatarImage(t.chooseReference)
    setSelecting(false)

    if (!isLive()) {
      return
    }

    if (!result || !('image' in result)) {
      if (result && 'error' in result) {
        notify({ kind: 'warning', message: result.error })
      }

      return
    }

    await runSaving(() => onAdopt(result.image))
  }

  const openSelfSource = async (): Promise<void> => {
    const isLive = begin()
    await hydrateAvatarSeeds()

    if (isLive()) {
      setSelfSourceOpen(true)
    }
  }

  const cancelWaitingTask = async (): Promise<void> => {
    if (!pending || saving || selecting) {
      return
    }

    await runSaving(() => onCancelTask(pending.id))
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-3">
        <button className={cn(BTN_SUBTLE, 'shrink-0')} onClick={onBack} type="button">
          <ArrowLeft className="size-3.5" />
          <span>{t.backToLibrary}</span>
        </button>
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-strong">{t.newSceneTitle}</h2>
      </div>

      <p className="text-[11px] leading-relaxed text-faint">{t.intro}</p>

      {pending ? (
        <SettingCard>
          <div className="space-y-2 p-3.5">
            <p className="flex items-center gap-2 text-xs text-body">
              {generating || waitingUpload ? <Loader2 className="size-4 animate-spin" /> : null}
              {waitingUpload ? t.waitingUploadOverlay : slow ? t.slow : t.pendingOverlay}
            </p>
            <p className={HINT_TEXT}>{waitingUpload ? t.waitingUploadHint : t.pendingOverlayHint}</p>
            <div className="flex flex-wrap gap-2">
              <button className={BTN_SUBTLE} onClick={() => void hydrateScene()} type="button">
                {t.refresh}
              </button>
              {waitingUpload ? (
                <button
                  className={BTN_PRIMARY}
                  disabled={saving || selecting}
                  onClick={() => void uploadWaitingImage()}
                  type="button"
                >
                  {selecting || saving ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
                  {t.waitingUploadAction}
                </button>
              ) : null}
              <button
                className={BTN_SUBTLE}
                disabled={selecting || saving}
                onClick={() => void cancelWaitingTask()}
                type="button"
              >
                {t.cancelTask}
              </button>
            </div>
          </div>
        </SettingCard>
      ) : null}

      <SettingCard>
        <div className="space-y-4 p-4">
          <div className="space-y-2">
            <label className="block space-y-1.5 text-xs text-body">
              <span>{t.notesLabel}</span>
              <textarea
                aria-label={t.notesLabel}
                className={INPUT_CLASS}
                disabled={formBusy}
                maxLength={500}
                onChange={event => onChange({ notes: event.target.value })}
                placeholder={t.notesPlaceholder}
                rows={4}
                value={draft.notes}
              />
            </label>
          </div>
          <label className="block space-y-1.5 text-xs text-body">
            <span>{t.outfitLabel}</span>
            <textarea
              className={INPUT_CLASS}
              disabled={formBusy}
              maxLength={500}
              onChange={event => onChange({ outfitDescription: event.target.value })}
              placeholder={t.outfitPlaceholder}
              rows={3}
              value={draft.outfitDescription}
            />
            <span className={HINT_TEXT}>{t.outfitHint}</span>
          </label>

          <div className="space-y-2 border-t border-line-hairline pt-3">
            <p className={HINT_TEXT}>{t.referenceHint}</p>
            <div className="flex flex-wrap items-center gap-2">
              {draft.reference ? (
                <div className="relative h-20 w-28 overflow-hidden rounded-lg border border-line-hairline">
                  <img alt={t.referenceLabel} className="h-full w-full object-cover" src={draft.reference.previewUrl} />
                </div>
              ) : null}
              <button className={BTN_SUBTLE} disabled={formBusy} onClick={() => void chooseReference()} type="button">
                {draft.reference ? t.replaceReference : t.chooseReference}
              </button>
              {draft.reference ? (
                <button
                  className={BTN_SUBTLE}
                  disabled={formBusy}
                  onClick={() => onChange({ reference: null })}
                  type="button"
                >
                  {t.removeReference}
                </button>
              ) : null}
            </div>
            {referenceError ? <p className="text-xs text-danger-fg">{t.referenceError}</p> : null}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-hairline pt-3">
            <p className={HINT_TEXT}>{t.savedHint}</p>
            <div className="flex shrink-0 items-center gap-2">
              <button className={BTN_SUBTLE} disabled={formBusy} onClick={() => void openSelfSource()} type="button">
                <FileImage className="size-3.5" />
                <span>{strings.selfSource.open}</span>
              </button>
              <button
                className={BTN_PRIMARY}
                disabled={formBusy || (!draft.notes.trim() && !draft.reference)}
                onClick={onCreate}
                type="button"
              >
                {saving || generating ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                <span>{generating ? t.generatingButton : t.generateButton}</span>
              </button>
            </div>
          </div>
        </div>
      </SettingCard>

      <SelfSourceImageFlow
        adopt={onAdopt}
        fetchPrompt={onFetchPrompt}
        onClose={() => setSelfSourceOpen(false)}
        onUseAi={onUseAi}
        open={selfSourceOpen}
        referenceImages={
          seeds.fullbodySeedUrl
            ? [{ label: strings.selfSource.refs.fullbodySeed, url: seeds.fullbodySeedUrl }]
            : undefined
        }
        title={`${t.generateButton} · ${strings.selfSource.open}`}
      />
    </section>
  )
}
