import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { pickAvatarImage, type PickedImage, SelfSourceImageFlow } from '@/modules/character'
import { $pendingScene, $sceneCreationIssue, $sceneTaskSlow, $sceneTaskStatus } from '@/modules/scene'
import { ReferenceImageField } from '@/shared/components/reference-image-field'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { ArrowLeft, FileImage, Plus, Sparkles } from '@/shared/lib/icons'
import { failureText } from '@/shared/lib/ipc-error'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  BusyButton,
  CreationActionBar,
  CreationFeedback,
  CreationWorkspace,
  Disclosure,
  HINT_TEXT,
  INPUT_CLASS,
  SettingCard
} from '@/shared/panel'
import { useStrings } from '@/shared/strings'

export interface SceneCreateDraft {
  notes: string
  reference: PickedImage | null
}

interface SceneCreateViewProps {
  busy: boolean
  draft: SceneCreateDraft
  onAdopt: (image: PickedImage, sceneId?: string) => Promise<void>
  onBack: () => void
  onChange: (next: Partial<SceneCreateDraft>) => void
  onCheckStatus: () => Promise<void>
  onCancelTask: (sceneId: string) => Promise<void>
  onCreate: () => void
  onFetchPrompt: () => Promise<{ id: string; prompt: string }>
  onUseAi: () => void
}

export function SceneCreateView({
  busy,
  draft,
  onAdopt,
  onBack,
  onChange,
  onCheckStatus,
  onCancelTask,
  onCreate,
  onFetchPrompt,
  onUseAi
}: SceneCreateViewProps): React.JSX.Element {
  const pending = useStore($pendingScene)
  const taskStatus = useStore($sceneTaskStatus)
  const creationIssue = useStore($sceneCreationIssue)
  const slow = useStore($sceneTaskSlow)
  const strings = useStrings()
  const t = strings.living.scene
  const tToasts = strings.living.toasts
  const [selfSourceOpen, setSelfSourceOpen] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [referenceError, setReferenceError] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [cancelled, setCancelled] = useState(false)
  const begin = useAsyncGuard()
  const externalSceneId = useRef<string | undefined>(undefined)
  const externalRevision = useRef(0)
  const generating = taskStatus === 'pending'
  const waitingUpload = taskStatus === 'waiting_upload'
  const formBusy = busy || generating || waitingUpload || selecting || saving || creationIssue?.outcome === 'unknown'

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
    setActionError(null)

    try {
      await action()
    } catch (error) {
      if (isLive()) {
        setActionError(failureText(error, tToasts.sceneRegenerateFailed))
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

    if (!isLive()) {
      return
    }

    setSelecting(false)

    if (!result || !('image' in result)) {
      if (result && 'error' in result) {
        setActionError(result.error)
      }

      return
    }

    await runSaving(() => onAdopt(result.image, pending.id))
  }

  const cancelWaitingTask = async (): Promise<void> => {
    if (!pending || saving || selecting) {
      return
    }

    const isLive = begin()
    await runSaving(async () => {
      await onCancelTask(pending.id)

      if (isLive()) {
        setCancelled(true)
      }
    })
  }

  const feedback = (
    <>
      {creationIssue ? (
        <CreationFeedback
          actions={
            creationIssue.outcome === 'unknown' ? (
              <button
                className={BTN_SUBTLE}
                disabled={saving}
                onClick={() => void runSaving(onCheckStatus)}
                type="button"
              >
                {saving ? strings.common.loading : t.inspectLibrary}
              </button>
            ) : undefined
          }
          description={
            creationIssue.outcome === 'unknown'
              ? `${t.submissionUnknown} ${creationIssue.message}`
              : creationIssue.message
          }
          state={creationIssue.outcome === 'unknown' ? 'unknown' : 'error'}
          title={creationIssue.outcome === 'unknown' ? strings.creation.unknown : t.statuses.failed}
        />
      ) : pending || busy || saving || actionError || cancelled ? (
        <CreationFeedback
          description={
            actionError ||
            (pending
              ? `${waitingUpload ? t.waitingUploadHint : t.pendingOverlayHint} ${strings.creation.canLeave}`
              : cancelled
                ? t.savedHint
                : strings.creation.inputLocked)
          }
          state={
            actionError
              ? 'error'
              : pending && waitingUpload
                ? 'waiting'
                : busy || saving || pending
                  ? 'working'
                  : 'cancelled'
          }
          title={
            actionError
              ? tToasts.sceneRegenerateFailed
              : waitingUpload
                ? t.waitingUploadOverlay
                : pending
                  ? pending.stage === 'analyze'
                    ? t.analyzing
                    : pending.stage === 'evaluating' || pending.stage === 'submitting'
                      ? t.checking
                      : slow
                        ? t.slow
                        : t.pendingOverlay
                  : saving
                    ? strings.common.saving
                    : busy
                      ? strings.common.loading
                      : strings.creation.cancelled
          }
        />
      ) : null}
    </>
  )

  const header = (
    <div className="flex items-center gap-3">
      <button className={cn(BTN_SUBTLE, 'shrink-0')} onClick={onBack} type="button">
        <ArrowLeft className="size-3.5" />
        <span>{t.backToLibrary}</span>
      </button>
      <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-strong">{t.newSceneTitle}</h2>
    </div>
  )

  const footer = (
    <div className="space-y-2">
      {feedback}
      <CreationActionBar description={pending ? undefined : t.savedHint}>
        {pending ? (
          <>
            {waitingUpload ? (
              <BusyButton
                busy={selecting || saving}
                className={BTN_PRIMARY}
                disabled={saving || selecting}
                icon={<Plus className="size-3.5" />}
                onClick={() => void uploadWaitingImage()}
              >
                {t.waitingUploadAction}
              </BusyButton>
            ) : null}
            <button
              className={BTN_SUBTLE}
              disabled={selecting || saving}
              onClick={() => void cancelWaitingTask()}
              type="button"
            >
              {t.cancelTask}
            </button>
          </>
        ) : (
          <>
            <BusyButton
              busy={saving || generating}
              className={BTN_PRIMARY}
              disabled={formBusy || (!draft.notes.trim() && !draft.reference)}
              icon={<Sparkles className="size-3.5" />}
              onClick={() => {
                setCancelled(false)
                setActionError(null)
                onCreate()
              }}
            >
              <span>{generating ? t.generatingButton : t.generateButton}</span>
            </BusyButton>
            <button
              className={BTN_SUBTLE}
              disabled={formBusy}
              onClick={() => {
                externalRevision.current += 1
                externalSceneId.current = undefined
                setSelfSourceOpen(true)
              }}
              type="button"
            >
              <FileImage className="size-3.5" />
              <span>{strings.selfSource.open}</span>
            </button>
          </>
        )}
      </CreationActionBar>
    </div>
  )

  return (
    <>
      <CreationWorkspace className="mx-auto w-full max-w-4xl" footer={footer} header={header}>
        <div className="space-y-4">
          <p className="text-sm leading-relaxed text-muted">{t.intro}</p>

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
              <Disclosure
                bodyClassName="space-y-2"
                className="space-y-2 border-t border-line-hairline pt-3"
                open={draft.reference ? true : undefined}
                summaryClassName="text-xs text-body"
                title={t.referenceLabel}
                variant="inline"
              >
                <p className={HINT_TEXT}>{t.referenceHint}</p>
                <ReferenceImageField
                  alt={t.referenceLabel}
                  busy={formBusy}
                  error={referenceError ? t.referenceError : null}
                  imageUrl={draft.reference?.previewUrl ?? null}
                  onPick={() => void chooseReference()}
                  onRemove={() => onChange({ reference: null })}
                  pickLabel={t.chooseReference}
                  removeLabel={t.removeReference}
                  replaceLabel={t.replaceReference}
                  thumbClassName="relative h-20 w-28 overflow-hidden rounded-lg border border-line-hairline"
                />
              </Disclosure>
            </div>
          </SettingCard>
        </div>
      </CreationWorkspace>
      <SelfSourceImageFlow
        adopt={image => onAdopt(image, externalSceneId.current)}
        fetchPrompt={async () => {
          const revision = externalRevision.current
          const isLive = begin()
          const prepared = await onFetchPrompt()

          if (isLive() && revision === externalRevision.current) {
            externalSceneId.current = prepared.id
          }

          return prepared.prompt
        }}
        hint={t.uploadHint}
        onClose={() => {
          externalRevision.current += 1
          setSelfSourceOpen(false)
        }}
        onUseAi={() => {
          externalRevision.current += 1
          setSelfSourceOpen(false)
          onUseAi()
        }}
        open={selfSourceOpen}
        referenceImages={draft.reference ? [{ label: t.referenceLabel, url: draft.reference.previewUrl }] : undefined}
        referenceRequired={false}
        title={strings.selfSource.open}
      />
    </>
  )
}
