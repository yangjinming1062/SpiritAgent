import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { PortraitLightbox } from '@/shared'
import { BTN_PRIMARY, BTN_SUBTLE, HINT_TEXT, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { pickAvatarImage, type PickedImage } from './avatar-image'
import { $fullbodyReference, hydrateFullbodyReference, regenerateFullbodyReference } from './fullbody-reference-store'
import { SelfSourceImageFlow } from './self-source-image'

interface FullbodyReferencePanelProps {
  avatarId: number
  initialReference?: PickedImage | null
  onContinue?: () => void
  onBack?: () => void
}

export function FullbodyReferencePanel({
  avatarId,
  initialReference = null,
  onContinue,
  onBack
}: FullbodyReferencePanelProps): React.JSX.Element {
  const t = useStrings().settings.persona.fullbodyReference
  const selfSource = useStrings().selfSource
  const state = useStore($fullbodyReference)
  const [feedback, setFeedback] = useState('')
  const [reference, setReference] = useState(initialReference)
  const [referenceError, setReferenceError] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [zoom, setZoom] = useState(false)
  const [selfSourceOpen, setSelfSourceOpen] = useState(false)
  const onboarding = Boolean(onContinue)
  const current = state.avatarId === avatarId
  const preview = current ? state.previewUrl : null
  const busy = !current || state.busy || selecting

  useEffect(() => {
    let live = true
    void hydrateFullbodyReference(avatarId).then((loaded: boolean): void => {
      if (live && loaded && onboarding && !$fullbodyReference.get().rawUrl) {
        void regenerateFullbodyReference(avatarId, '', initialReference)
      }
    })

    return (): void => {
      live = false
    }
  }, [avatarId, initialReference, onboarding])

  const chooseReference = async (): Promise<void> => {
    setSelecting(true)
    setReferenceError(false)
    const result = await pickAvatarImage(t.chooseReference)

    if (result && 'image' in result) {
      setReference(result.image)
    } else if (result && 'error' in result) {
      setReferenceError(true)
    }

    setSelecting(false)
  }

  const regenerate = async (): Promise<void> => {
    if (await regenerateFullbodyReference(avatarId, feedback, reference)) {
      setFeedback('')
    }
  }

  // 微调编辑上一版全身参考：需要已有图与反馈；附参考图时不可用（参考图属重新生成意图）。
  const edit = async (): Promise<void> => {
    if (await regenerateFullbodyReference(avatarId, feedback, null, 'edit')) {
      setFeedback('')
    }
  }

  const fetchSelfSourcePrompt = async (): Promise<string> => {
    const res = await window.spiritagent.api<{ prompt: string }>({
      path: `/api/companion/avatar/${avatarId}/fullbody/reference/prompt`,
      method: 'POST',
      body: { feedback: feedback.trim() || undefined }
    })

    return res.prompt
  }

  const adoptSelfSourceImage = async (image: PickedImage): Promise<void> => {
    await window.spiritagent.api({
      path: `/api/companion/avatar/${avatarId}/fullbody/reference/adopt`,
      method: 'POST',
      body: { image: image.base64, content_type: image.contentType }
    })
    await hydrateFullbodyReference(avatarId)
  }

  return (
    <section className="space-y-3">
      <div>
        <p className={SECTION_TITLE}>{t.title}</p>
        <p className={HINT_TEXT}>{t.hint}</p>
      </div>
      {preview ? (
        <button
          aria-label={t.enlarge}
          className="mx-auto block w-full cursor-zoom-in overflow-hidden rounded-xl border border-line-hairline bg-fill-trough"
          onClick={() => setZoom(true)}
          type="button"
        >
          <img alt={t.title} className="mx-auto max-h-64 max-w-full object-contain" src={preview} />
        </button>
      ) : (
        <p className="rounded-xl border border-line-hairline bg-fill-trough px-4 py-6 text-center text-xs text-muted">
          {busy ? t.loading : t.empty}
        </p>
      )}
      {current && state.busy && (
        <p aria-live="polite" className={HINT_TEXT}>
          {t.loading}
        </p>
      )}
      <div className="space-y-2">
        <p className={HINT_TEXT}>{t.referenceHint}</p>
        <div className="flex items-center gap-2">
          {reference && (
            <img
              alt={t.referenceLabel}
              className="h-16 w-16 rounded-lg border border-line-hairline object-contain"
              src={reference.previewUrl}
            />
          )}
          <button className={BTN_SUBTLE} disabled={busy} onClick={() => void chooseReference()} type="button">
            {reference ? t.replaceReference : t.chooseReference}
          </button>
          {reference && (
            <button
              className={BTN_SUBTLE}
              disabled={busy}
              onClick={() => {
                setReference(null)
                setReferenceError(false)
              }}
              type="button"
            >
              {t.removeReference}
            </button>
          )}
        </div>
        {referenceError && (
          <p className="text-xs text-danger-fg" role="alert">
            {t.pickError}
          </p>
        )}
      </div>
      <textarea
        aria-label={t.feedbackLabel}
        className={INPUT_CLASS}
        disabled={busy}
        maxLength={500}
        onChange={(event): void => setFeedback(event.target.value)}
        placeholder={t.feedbackPlaceholder}
        rows={2}
        value={feedback}
      />
      {current && state.error && (
        <p className="text-xs text-danger-fg" role="alert">
          {state.errorMessage || t.errors[state.error]}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {onBack && (
          <button className={BTN_SUBTLE} disabled={busy} onClick={onBack} type="button">
            {t.back}
          </button>
        )}
        {current && state.error && (
          <button
            className={BTN_SUBTLE}
            disabled={busy}
            onClick={() => void hydrateFullbodyReference(avatarId)}
            type="button"
          >
            {t.reload}
          </button>
        )}
        {current && state.rawUrl && (
          <button
            className={BTN_SUBTLE}
            disabled={busy || !feedback.trim() || !!reference}
            onClick={() => void edit()}
            title={reference ? t.editDisabledByReference : t.editRequiresFeedback}
            type="button"
          >
            {t.edit}
          </button>
        )}
        <button className={BTN_SUBTLE} disabled={busy} onClick={() => void regenerate()} type="button">
          {current && state.rawUrl ? t.regenerate : t.generate}
        </button>
        <button
          className={BTN_SUBTLE}
          disabled={busy}
          onClick={() => setSelfSourceOpen(true)}
          title={selfSource.openTitle}
          type="button"
        >
          {selfSource.open}
        </button>
        {onContinue && (
          <button
            className={BTN_PRIMARY}
            disabled={busy || !preview || !state.rawUrl || state.error !== null}
            onClick={onContinue}
            type="button"
          >
            {t.continue}
          </button>
        )}
      </div>
      <SelfSourceImageFlow
        adopt={adoptSelfSourceImage}
        fetchPrompt={fetchSelfSourcePrompt}
        onClose={() => setSelfSourceOpen(false)}
        onUseAi={() => {
          setSelfSourceOpen(false)
          void regenerate()
        }}
        open={selfSourceOpen}
        title={current && state.rawUrl ? t.regenerate : t.generate}
      />
      {zoom && preview && <PortraitLightbox name={t.title} onClose={() => setZoom(false)} url={preview} />}
    </section>
  )
}
