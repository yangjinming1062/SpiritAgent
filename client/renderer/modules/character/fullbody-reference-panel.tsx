import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { PortraitLightbox } from '@/shared'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { authedResource, requestResource } from '@/shared/lib/resource-request'
import { BTN_PRIMARY, BTN_SUBTLE, FIELD_LABEL, HINT_TEXT, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import type { ImageReviseMode } from '@protocol'

import { pickAvatarImage, type PickedImage } from './avatar-image'
import { $avatarSeeds, hydrateAvatarSeeds } from './avatar-seeds-store'
import {
  $fullbodyReference,
  acceptFullbodyCandidate,
  clearFullbodyReferenceHistory,
  hydrateFullbodyReference,
  regenerateFullbodyReference,
  restoreFullbodyReferenceVersion,
  retryFullbodyCandidateAnalysis
} from './fullbody-reference-store'
import { GenerationActionsGroup } from './generation-actions'
import { MAX_IMAGE_DESCRIPTION } from './persona'
import { $portraitUrl } from './portrait-store'
import { SelfSourceImageFlow, type SelfSourceReferenceImage } from './self-source-image'

interface FullbodyReferencePanelProps {
  avatarId: number
  onContinue?: (expectedUrl: string) => Promise<void>
  onBack?: () => void
}

export function FullbodyReferencePanel({
  avatarId,
  onContinue,
  onBack
}: FullbodyReferencePanelProps): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.persona.fullbodyReference
  const genActions = dict.generationActions
  const selfSource = dict.selfSource
  const state = useStore($fullbodyReference)
  const begin = useAsyncGuard()
  const portraitUrl = useStore($portraitUrl)
  const avatarSeeds = useStore($avatarSeeds)
  const [feedback, setFeedback] = useState('')
  const [reference, setReference] = useState<PickedImage | null>(null)
  const [referenceError, setReferenceError] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [zoom, setZoom] = useState(false)
  const [selfSourceOpen, setSelfSourceOpen] = useState(false)
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null)
  const onboarding = Boolean(onContinue)
  const current = state.avatarId === avatarId
  const currentRawUrl = current ? state.rawUrl : null
  const initialGeneration = onboarding && !currentRawUrl
  const preview = current && state.error !== 'load' && state.error !== 'preview' ? state.previewUrl : null
  const busy = !current || state.busy || selecting || confirming
  const history = current ? state.history : []
  const selectedHistory = history.find(entry => entry.id === selectedHistoryId) ?? null
  const displayedPreview = selectedHistory?.previewUrl ?? preview
  const currentImageUnavailable = state.error === 'load' || state.error === 'preview'

  // 历史版本与当前版本同构展示；当前版本的 id 为 null。
  const thumbnails = [
    ...history.map((entry, index) => ({
      id: entry.id,
      label: `${t.historyVersion} ${index + 1}`,
      url: entry.previewUrl
    })),
    ...(preview ? [{ id: null, label: t.historyCurrent, url: preview }] : [])
  ]

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

    if (latest.avatarId !== avatarId || (!latest.rawUrl && !selectedHistory) || latest.busy) {
      return
    }

    setConfirming(true)
    setConfirmError(null)

    try {
      if (selectedHistory && !(await restoreFullbodyReferenceVersion(avatarId, selectedHistory.id))) {
        setConfirmError(t.restoreFailed)

        return
      }

      const confirmed = $fullbodyReference.get()

      if (
        confirmed.avatarId !== avatarId ||
        !confirmed.rawUrl ||
        !confirmed.previewUrl ||
        confirmed.error ||
        confirmed.busy
      ) {
        setConfirmError(t.restoreFailed)

        return
      }

      await onContinue(confirmed.rawUrl)
      clearFullbodyReferenceHistory(avatarId)
      setSelectedHistoryId(null)
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

  // 重新生成与微调共用提交；微调需要反馈且不能附参考图，由 editDisabled 禁用入口，store 对 edit 也不发参考图。
  const submit = async (mode: ImageReviseMode): Promise<void> => {
    if (await regenerateFullbodyReference(avatarId, feedback, reference, mode)) {
      setFeedback('')
      setSelectedHistoryId(null)
    }
  }

  const restoreSelectedHistory = async (): Promise<void> => {
    if (!selectedHistory || busy) {
      return
    }

    setConfirmError(null)
    setConfirming(true)

    try {
      if (await restoreFullbodyReferenceVersion(avatarId, selectedHistory.id)) {
        setSelectedHistoryId(null)
      } else {
        setConfirmError(t.restoreFailed)
      }
    } catch {
      setConfirmError(t.restoreFailed)
    } finally {
      setConfirming(false)
    }
  }

  const fetchSelfSourcePrompt = async (): Promise<string> => {
    const res = await requestResource('companion.post_fullbody_prompt', {
      path: { avatar_id: avatarId },
      body: { feedback: feedback.trim() || undefined }
    })

    return res.prompt
  }

  const adoptSelfSourceImage = async (image: PickedImage): Promise<void> => {
    const isLive = begin()

    const result = await authedResource('companion.post_fullbody_adopt', {
      path: { avatar_id: avatarId },
      body: { image: image.base64, content_type: image.contentType }
    })

    if (!isLive() || (!result.ok && result.reason === 'unauth')) {
      return
    }

    if (!result.ok) {
      throw result.error
    }

    const expectedUrl =
      result.value && ('image_url' in result.value ? result.value.image_url : result.value.seed_fullbody_url)

    const hydrated = await hydrateFullbodyReference(avatarId)

    if (!isLive()) {
      return
    }

    const latest = $fullbodyReference.get()

    if (
      !expectedUrl ||
      !hydrated ||
      latest.avatarId !== avatarId ||
      latest.rawUrl?.split('?')[0] !== expectedUrl.split('?')[0]
    ) {
      throw new Error(t.adoptPreviewFailed)
    }

    setFeedback('')
    // 引导内采纳即确认并继续，失败留在本面板可手动重试；设置页无后续步骤，不自动确认。
    await confirm()
  }

  // 独立全身参考以头像为身份锚点（与 AI 生图同源）；优先读种子缓存，回落到当前头像。
  const selfSourceAvatarUrl = avatarSeeds.avatarUrl || portraitUrl

  const selfSourceReferences: SelfSourceReferenceImage[] | undefined = selfSourceAvatarUrl
    ? [{ label: selfSource.refs.avatarSeed, url: selfSourceAvatarUrl }]
    : undefined

  const openSelfSource = (): void => {
    // 头像为独立全身参考身份锚点：打开前确保种子缓存已水合。
    void hydrateAvatarSeeds().finally(() => setSelfSourceOpen(true))
  }

  return (
    <section className="space-y-3">
      <>
        <div>
          <p className={SECTION_TITLE}>{t.title}</p>
          <p className={HINT_TEXT}>{t.hint}</p>
        </div>
        {displayedPreview ? (
          <button
            aria-label={t.enlarge}
            className="mx-auto block w-full cursor-zoom-in overflow-hidden rounded-xl border border-line-hairline bg-fill-trough"
            onClick={() => setZoom(true)}
            type="button"
          >
            <img alt={t.title} className="mx-auto max-h-64 max-w-full object-contain" src={displayedPreview} />
          </button>
        ) : (
          <p className="rounded-xl border border-line-hairline bg-fill-trough px-4 py-6 text-center text-xs text-muted">
            {busy ? t.loading : history.length > 0 ? t.historyCurrentUnavailable : t.empty}
          </p>
        )}
        {current && state.busy && (
          <p aria-live="polite" className={HINT_TEXT}>
            {t.loading}
          </p>
        )}
        {onboarding && current && history.length > 0 && (
          <div className="space-y-2">
            <p className={FIELD_LABEL}>{t.historyTitle}</p>
            <div className="flex flex-wrap gap-2">
              {thumbnails.map(item => {
                const selected = item.id === (selectedHistory?.id ?? null)

                return (
                  <button
                    aria-label={item.label}
                    aria-pressed={selected}
                    className={`overflow-hidden rounded-lg border bg-fill-trough text-left transition ${
                      selected ? 'border-accent ring-1 ring-accent' : 'border-line-hairline hover:border-line-strong'
                    }`}
                    disabled={busy}
                    key={item.id ?? 'current'}
                    onClick={() => setSelectedHistoryId(item.id)}
                    type="button"
                  >
                    <img alt="" className="h-16 w-16 object-contain" src={item.url} />
                    <span className="block px-1 pb-1 text-center text-[10px] text-muted">{item.label}</span>
                  </button>
                )
              })}
            </div>
            {selectedHistory && (
              <div className="space-y-2">
                <p className={HINT_TEXT}>{t.historySelectedHint}</p>
                <button
                  className={BTN_SUBTLE}
                  disabled={busy}
                  onClick={() => void restoreSelectedHistory()}
                  type="button"
                >
                  {t.restoreVersion}
                </button>
              </div>
            )}
          </div>
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
        <details
          className="space-y-3 rounded-xl border border-line-hairline p-3"
          open={!displayedPreview ? true : undefined}
        >
          <summary className="cursor-pointer text-xs text-body">
            {displayedPreview ? genActions.aiTitle : t.descriptionLabel}
          </summary>
          <div className="space-y-1">
            <label className={FIELD_LABEL} htmlFor="fullbody-reference-feedback">
              {initialGeneration ? t.descriptionLabel : t.feedbackLabel}
            </label>
            <p className={HINT_TEXT}>{t.descriptionHint}</p>
            <textarea
              className={INPUT_CLASS}
              disabled={busy}
              id="fullbody-reference-feedback"
              maxLength={MAX_IMAGE_DESCRIPTION}
              onChange={(event): void => setFeedback(event.target.value)}
              placeholder={initialGeneration ? t.descriptionPlaceholder : t.feedbackPlaceholder}
              rows={2}
              value={feedback}
            />
          </div>
          <details className="space-y-1 rounded-xl border border-line-hairline p-3" open={reference ? true : undefined}>
            <summary className="cursor-pointer text-xs text-body">{t.refLabel}</summary>
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
          </details>
          <GenerationActionsGroup
            editDisabled={
              busy || Boolean(selectedHistory) || currentImageUnavailable || !preview || !feedback.trim() || !!reference
            }
            editReason={
              reference ? t.editDisabledByReference : !feedback.trim() ? genActions.editRequiresFeedback : undefined
            }
            onEdit={current && state.rawUrl ? () => void submit('edit') : undefined}
            onRegenerate={() => void submit('regenerate')}
            onSelfSource={openSelfSource}
            regenerateDisabled={
              busy || Boolean(selectedHistory) || currentImageUnavailable || (history.length > 0 && !currentRawUrl)
            }
            regenerateLabel={current && state.rawUrl ? undefined : genActions.generate}
            selfSourceDisabled={
              busy || Boolean(selectedHistory) || currentImageUnavailable || (history.length > 0 && !currentRawUrl)
            }
          />
        </details>
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
        {onboarding && preview && <p className={HINT_TEXT}>{t.confirmHint}</p>}
      </>
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
              disabled={busy || !displayedPreview || (!selectedHistory && (!state.rawUrl || state.error !== null))}
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
        }}
        open={selfSourceOpen}
        referenceImages={selfSourceReferences}
        title={selfSource.open}
      />
      {zoom && displayedPreview && (
        <PortraitLightbox name={t.title} onClose={() => setZoom(false)} url={displayedPreview} />
      )}
    </section>
  )
}
