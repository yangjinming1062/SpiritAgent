import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { ImageLightboxButton } from '@/shared/components/image-lightbox-button'
import { ReferenceImageField } from '@/shared/components/reference-image-field'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { FolderOpen } from '@/shared/lib/icons'
import { failureText, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { authedResource, requestResource } from '@/shared/lib/resource-request'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  CreationActionBar,
  CreationFeedback,
  CreationWorkspace,
  Disclosure,
  FIELD_LABEL,
  HINT_TEXT,
  INPUT_CLASS,
  Placeholder,
  SECTION_TITLE
} from '@/shared/panel'
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
  const [selfSourceOpen, setSelfSourceOpen] = useState(false)
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null)
  const [failedPreview, setFailedPreview] = useState<string | null>(null)
  const [previewRevision, setPreviewRevision] = useState(0)
  const [loadedPreviewKey, setLoadedPreviewKey] = useState<string | null>(null)

  const adoptedImageRef = useRef<{
    avatarId: number
    image: PickedImage
    url: string
    isCurrent: () => boolean
  } | null>(null)

  const onboarding = Boolean(onContinue)
  const current = state.avatarId === avatarId
  const currentRawUrl = current ? state.rawUrl : null
  const initialGeneration = onboarding && !currentRawUrl
  const preview = current ? state.previewUrl : null
  const busy = !current || state.busy || selecting || confirming
  const history = current ? state.history : []
  const selectedHistory = history.find(entry => entry.id === selectedHistoryId) ?? null
  const displayedPreview = selectedHistory?.previewUrl ?? preview
  const previewKey = `${displayedPreview}:${previewRevision}`
  const previewReady = Boolean(displayedPreview && loadedPreviewKey === previewKey)
  const previewFailed = Boolean(displayedPreview && failedPreview === displayedPreview)
  const currentImageUnavailable = Boolean(state.error) || previewFailed

  const generationBlocked =
    busy || Boolean(selectedHistory) || currentImageUnavailable || (history.length > 0 && !currentRawUrl)

  const errorMessage =
    confirmError ||
    (previewFailed ? t.errors.preview : null) ||
    (current && state.error ? state.errorMessage || t.errors[state.error] : null) ||
    (current ? state.candidateError : null)

  const reloadPreview = (): void => {
    setFailedPreview(null)
    setPreviewRevision(value => value + 1)
    void hydrateFullbodyReference(avatarId)
  }

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

  const confirm = async (acceptedUrl?: string): Promise<void> => {
    if (!onContinue || selecting || confirming) {
      return
    }

    // 手动确认读取当前版本；自备图直接使用服务端返回的采纳路径。
    const latest = $fullbodyReference.get()

    if (!acceptedUrl && (latest.avatarId !== avatarId || (!latest.rawUrl && !selectedHistory) || latest.busy)) {
      return
    }

    setConfirming(true)
    setConfirmError(null)

    try {
      let confirmedUrl = acceptedUrl

      if (!confirmedUrl) {
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

        confirmedUrl = confirmed.rawUrl
      }

      await onContinue(confirmedUrl)
      clearFullbodyReferenceHistory(avatarId)
      setSelectedHistoryId(null)
    } catch (error) {
      if (acceptedUrl) {
        throw error
      }

      setConfirmError(failureText(error, t.errors.load))
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
    const adopted = adoptedImageRef.current

    let expectedUrl =
      adopted?.avatarId === avatarId && adopted.image === image && adopted.isCurrent() ? adopted.url : undefined

    if (!onContinue || !expectedUrl) {
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

      expectedUrl = result.value
        ? 'image_url' in result.value
          ? result.value.image_url
          : result.value.seed_fullbody_url
        : undefined

      if (onContinue && expectedUrl) {
        adoptedImageRef.current = { avatarId, image, url: expectedUrl, isCurrent: isLive }
      }
    }

    if (onContinue) {
      if (!expectedUrl) {
        throw new Error(t.adoptPreviewFailed)
      }

      try {
        await confirm(expectedUrl)
        adoptedImageRef.current = null
      } catch (error) {
        // 回执未知时保留上传结果，只重试确认；明确拒绝源图后允许重新上传。
        const status = ipcErrorStatus(error)

        if (status !== null && status >= 400 && status < 500 && status !== 429) {
          adoptedImageRef.current = null
        }

        throw error
      }

      return
    }

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

  const generationActions = (
    <GenerationActionsGroup
      dense={onboarding}
      editDisabled={
        busy || Boolean(selectedHistory) || currentImageUnavailable || !preview || !feedback.trim() || !!reference
      }
      editReason={
        reference ? t.editDisabledByReference : !feedback.trim() ? genActions.editRequiresFeedback : undefined
      }
      onEdit={current && state.rawUrl ? () => void submit('edit') : undefined}
      onRegenerate={() => void submit('regenerate')}
      onSelfSource={onboarding ? undefined : openSelfSource}
      regenerateDisabled={generationBlocked}
      regenerateLabel={current && state.rawUrl ? undefined : genActions.generate}
      selfSourceDisabled={generationBlocked}
    />
  )

  const adjustmentFields = (
    <>
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
      <Disclosure
        bodyClassName="space-y-2"
        className="onboarding ? 'space-y-2' : 'space-y-1 rounded-xl border border-line-hairline p-3'"
        open={reference ? true : undefined}
        summaryClassName="text-xs text-body"
        title={t.refLabel}
        variant="inline"
      >
        <p className={HINT_TEXT}>{t.refHint}</p>
        <ReferenceImageField
          alt={t.referenceLabel}
          busy={busy}
          error={referenceError ? t.pickError : null}
          imageUrl={reference?.previewUrl ?? null}
          onPick={() => void chooseReference()}
          onRemove={() => {
            setReference(null)
            setReferenceError(false)
          }}
          pickLabel={t.chooseReference}
          removeLabel={t.removeReference}
          replaceLabel={t.replaceReference}
        />
      </Disclosure>
    </>
  )

  const creationFeedback = (busy || errorMessage || (onboarding && displayedPreview) || state.candidateId) && (
    <CreationFeedback
      actions={
        currentImageUnavailable || state.candidateError ? (
          <button className={BTN_SUBTLE} disabled={busy} onClick={reloadPreview} type="button">
            {dict.creation.retryLoad}
          </button>
        ) : undefined
      }
      description={
        busy
          ? `${dict.creation.inputLocked} ${displayedPreview ? dict.creation.preserveCurrent : ''} ${!onboarding ? dict.creation.canLeave : ''}`
          : errorMessage || (state.candidateId ? t.candidateHint : onboarding ? t.confirmHint : undefined)
      }
      state={busy ? 'working' : errorMessage ? (state.error === 'generate' ? 'unknown' : 'error') : 'review'}
      title={
        busy
          ? onboarding && !displayedPreview
            ? undefined
            : confirming
              ? dict.common.saving
              : t.loading
          : errorMessage
            ? currentImageUnavailable
              ? state.error === 'generate'
                ? dict.creation.unknown
                : dict.creation.imageLoadFailed
              : confirmError
                ? t.restoreFailed
                : t.retryCandidateAnalysisFailed
            : dict.creation.review
      }
    />
  )

  return (
    <>
      <CreationWorkspace
        className="gap-3"
        footer={
          <CreationActionBar description={creationFeedback}>
            {!onboarding && generationActions}
            {!onboarding &&
              current &&
              state.candidateId &&
              (state.candidateStatus === 'ready' ? (
                <button
                  className={BTN_PRIMARY}
                  disabled={busy || currentImageUnavailable || !previewReady}
                  onClick={() => void acceptFullbodyCandidate(avatarId)}
                  type="button"
                >
                  {t.acceptCandidate}
                </button>
              ) : (
                <button
                  className={BTN_SUBTLE}
                  disabled={busy || currentImageUnavailable}
                  onClick={() => void retryFullbodyCandidateAnalysis(avatarId)}
                  type="button"
                >
                  {t.retryCandidateAnalysis}
                </button>
              ))}
            {(onBack || onContinue) && (
              <div
                className={
                  onboarding
                    ? 'flex w-full flex-wrap items-center justify-between gap-3'
                    : 'flex flex-wrap items-center gap-2'
                }
              >
                {onBack && (
                  <button className={BTN_SUBTLE} disabled={busy} onClick={onBack} type="button">
                    {t.back}
                  </button>
                )}
                {onContinue &&
                  (displayedPreview ? (
                    <button
                      className={`${BTN_PRIMARY} ml-auto`}
                      disabled={
                        busy ||
                        previewFailed ||
                        !previewReady ||
                        (!selectedHistory && (!state.rawUrl || state.error !== null))
                      }
                      onClick={() => void confirm()}
                      type="button"
                    >
                      {t.continue}
                    </button>
                  ) : (
                    <button
                      aria-atomic="true"
                      aria-live="polite"
                      className={`${BTN_PRIMARY} ml-auto`}
                      disabled={generationBlocked}
                      onClick={() => void submit('regenerate')}
                      type="button"
                    >
                      {busy ? dict.creation.working : currentRawUrl ? t.regenerate : t.generate}
                    </button>
                  ))}
              </div>
            )}
          </CreationActionBar>
        }
        header={
          <div>
            <h2 className={onboarding ? 'text-lg font-medium leading-relaxed text-strong' : SECTION_TITLE}>
              {t.title}
            </h2>
            <p className={HINT_TEXT}>{t.hint}</p>
          </div>
        }
      >
        <div className="space-y-3">
          {displayedPreview ? (
            <ImageLightboxButton
              ariaLabel={t.enlarge}
              className="mx-auto block w-full cursor-zoom-in overflow-hidden rounded-xl border border-line-hairline bg-fill-trough"
              name={t.title}
              url={displayedPreview}
            >
              <img
                alt={t.title}
                className="mx-auto max-h-64 max-w-full object-contain"
                key={previewKey}
                onError={() => setFailedPreview(displayedPreview)}
                onLoad={() => setLoadedPreviewKey(previewKey)}
                src={displayedPreview}
              />
            </ImageLightboxButton>
          ) : (
            (!onboarding || history.length > 0) && (
              <Placeholder>{busy ? t.loading : history.length > 0 ? t.historyCurrentUnavailable : t.empty}</Placeholder>
            )
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
          {onboarding && !displayedPreview ? (
            <div className="space-y-4">{adjustmentFields}</div>
          ) : (
            <Disclosure
              bodyClassName="space-y-3"
              className="space-y-3 rounded-xl border border-line-hairline p-3"
              open={!displayedPreview ? true : undefined}
              summaryClassName="text-xs text-body"
              title={displayedPreview ? genActions.aiTitle : t.descriptionLabel}
              variant="bordered"
            >
              {adjustmentFields}
              {onboarding && generationActions}
            </Disclosure>
          )}
          {onboarding && (
            <button
              className="inline-flex items-center gap-1.5 text-xs text-muted transition hover:text-strong disabled:opacity-40"
              disabled={generationBlocked}
              onClick={openSelfSource}
              title={selfSource.openTitle}
              type="button"
            >
              <FolderOpen className="size-3.5" />
              {selfSource.open}
            </button>
          )}
        </div>
      </CreationWorkspace>
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
        waitForPromptBeforeAdopt={false}
      />
    </>
  )
}
