import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { PortraitLightbox } from '@/shared'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { BTN_PRIMARY, BTN_SUBTLE, FIELD_LABEL, HINT_TEXT, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { pickAvatarImage, type PickedImage } from './avatar-image'
import { $avatarSeeds, hydrateAvatarSeeds } from './avatar-seeds-store'
import {
  $fullbodyReference,
  acceptFullbodyCandidate,
  hydrateFullbodyReference,
  regenerateFullbodyReference,
  retryFullbodyCandidateAnalysis
} from './fullbody-reference-store'
import { GenerationActionsGroup } from './generation-actions'
import { $portraitUrl } from './portrait-store'
import { SelfSourceImageFlow, type SelfSourceReferenceImage } from './self-source-image'

interface FullbodyReferencePanelProps {
  avatarId: number
  initialReference?: PickedImage | null
  onContinue?: (expectedUrl: string) => Promise<void>
  onBack?: () => void
}

export function FullbodyReferencePanel({
  avatarId,
  initialReference = null,
  onContinue,
  onBack
}: FullbodyReferencePanelProps): React.JSX.Element {
  const t = useStrings().settings.persona.fullbodyReference
  const genActions = useStrings().generationActions
  const selfSource = useStrings().selfSource
  const state = useStore($fullbodyReference)
  const portraitUrl = useStore($portraitUrl)
  const avatarSeeds = useStore($avatarSeeds)
  const [feedback, setFeedback] = useState('')
  const [reference, setReference] = useState(initialReference)
  const [referenceError, setReferenceError] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [zoom, setZoom] = useState(false)
  const [selfSourceOpen, setSelfSourceOpen] = useState(false)
  const onboarding = Boolean(onContinue)
  const current = state.avatarId === avatarId
  const preview = current ? state.previewUrl : null
  const busy = !current || state.busy || selecting || confirming

  useEffect(() => {
    void hydrateAvatarSeeds()
    void hydrateFullbodyReference(avatarId)
  }, [avatarId])

  const confirm = async (): Promise<void> => {
    if (!onContinue || selecting || confirming) {
      return
    }

    // 读 store 而非渲染闭包：自备图采纳后要立即确认刚水合的结果。
    const latest = $fullbodyReference.get()

    if (latest.avatarId !== avatarId || !latest.rawUrl || latest.busy) {
      return
    }

    setConfirming(true)
    setConfirmError(null)

    try {
      await onContinue(latest.rawUrl)
    } catch (error) {
      setConfirmError(backendDetailMessage(error, t.errors.load))
    } finally {
      setConfirming(false)
    }
  }

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
    // 引导内采纳即确认并继续，失败留在本面板可手动重试；设置页无后续步骤，不自动确认。
    await confirm()
  }

  // 独立全身参考以头像为身份锚点（与 AI 生图同源）；优先读种子缓存，回落到当前头像。
  const selfSourceAvatarUrl = avatarSeeds.avatarUrl || portraitUrl

  const selfSourceReferences: SelfSourceReferenceImage[] | undefined = selfSourceAvatarUrl
    ? [{ label: selfSource.refs.avatarSeed, url: selfSourceAvatarUrl }]
    : undefined

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
      {!onboarding && current && state.candidateId && (
        <div className="space-y-2 rounded-xl border border-line-hairline bg-fill-trough p-3">
          <p className={HINT_TEXT}>{t.candidateHint}</p>
          {state.candidateError && (
            <p className="text-xs text-danger-fg" role="alert">
              {state.candidateError}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {state.candidateStatus === 'ready' ? (
              <button
                className={BTN_PRIMARY}
                disabled={busy}
                onClick={() => void acceptFullbodyCandidate(avatarId)}
                type="button"
              >
                {t.acceptCandidate}
              </button>
            ) : (
              <button
                className={BTN_SUBTLE}
                disabled={busy}
                onClick={() => void retryFullbodyCandidateAnalysis(avatarId)}
                type="button"
              >
                {t.retryCandidateAnalysis}
              </button>
            )}
          </div>
        </div>
      )}
      <div className="space-y-1">
        <label className={FIELD_LABEL} htmlFor="fullbody-reference-feedback">
          {t.feedbackLabel}
        </label>
        <textarea
          className={INPUT_CLASS}
          disabled={busy}
          id="fullbody-reference-feedback"
          maxLength={500}
          onChange={(event): void => setFeedback(event.target.value)}
          placeholder={t.feedbackPlaceholder}
          rows={2}
          value={feedback}
        />
      </div>
      <div className="space-y-1">
        <p className={FIELD_LABEL}>{t.refLabel}</p>
        <p className={HINT_TEXT}>{t.refHint}</p>
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
      <GenerationActionsGroup
        editDisabled={busy || !feedback.trim() || !!reference}
        editReason={
          reference ? t.editDisabledByReference : !feedback.trim() ? genActions.editRequiresFeedback : undefined
        }
        onEdit={current && state.rawUrl ? () => void edit() : undefined}
        onRegenerate={() => void regenerate()}
        onSelfSource={() => {
          // 头像为独立全身参考身份锚点：打开前确保种子缓存已水合。
          void hydrateAvatarSeeds().finally(() => setSelfSourceOpen(true))
        }}
        regenerateDisabled={busy}
        regenerateLabel={current && state.rawUrl ? undefined : genActions.generate}
        selfSourceDisabled={busy}
      />
      {current && state.error && (
        <div className="space-y-2">
          <p className="text-xs text-danger-fg" role="alert">
            {state.errorMessage || t.errors[state.error]}
          </p>
          <button
            className={BTN_SUBTLE}
            disabled={busy}
            onClick={() => void hydrateFullbodyReference(avatarId)}
            type="button"
          >
            {t.reload}
          </button>
        </div>
      )}
      {confirmError && (
        <p className="text-xs text-danger-fg" role="alert">
          {confirmError}
        </p>
      )}
      {onboarding && <p className={HINT_TEXT}>{t.confirmHint}</p>}
      {(onBack || onContinue) && (
        <div className="flex flex-wrap items-center gap-2">
          {onBack && (
            <button className={BTN_SUBTLE} disabled={busy} onClick={onBack} type="button">
              {t.back}
            </button>
          )}
          {onContinue && (
            <button
              className={BTN_PRIMARY}
              disabled={busy || !preview || !state.rawUrl || state.error !== null}
              onClick={() => void confirm()}
              type="button"
            >
              {t.continue}
            </button>
          )}
        </div>
      )}
      <SelfSourceImageFlow
        adopt={adoptSelfSourceImage}
        fetchPrompt={fetchSelfSourcePrompt}
        hint={onboarding ? t.adoptHint : undefined}
        onClose={() => setSelfSourceOpen(false)}
        onUseAi={() => {
          setSelfSourceOpen(false)
          void regenerate()
        }}
        open={selfSourceOpen}
        referenceImages={selfSourceReferences}
        title={current && state.rawUrl ? t.regenerate : t.generate}
      />
      {zoom && preview && <PortraitLightbox name={t.title} onClose={() => setZoom(false)} url={preview} />}
    </section>
  )
}
