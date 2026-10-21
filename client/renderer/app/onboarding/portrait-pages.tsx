import { useState } from 'react'

import type { OnboardingAnswers } from '@/modules/character'
import { FullbodyReferencePanel, SelfSourceImageFlow } from '@/modules/character'
import { FolderOpen } from '@/shared/lib/icons'
import { BTN_SUBTLE, CreationActionBar, CreationFeedback, CreationWorkspace, Disclosure } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import type { Phase } from './back-transition'
import { EditAvatarButton, ONBOARDING_PRIMARY_BUTTON, PortraitPanel, RegenFeedbackInput } from './onboarding-components'
import styles from './onboarding.module.css'
import type { OnboardingPortrait } from './use-onboarding-portrait'

interface PortraitPagesProps {
  phase: Phase
  answers: OnboardingAnswers
  hint: string | null
  setHint: (hint: string | null) => void
  setPhase: (phase: Phase) => void
  onBack: () => void
  portrait: OnboardingPortrait
}

export function PortraitPages({
  phase,
  answers,
  hint,
  setHint,
  setPhase,
  onBack,
  portrait
}: PortraitPagesProps): React.JSX.Element | null {
  const dict = useStrings()
  const t = dict.onboarding
  const [readyPreviewUrl, setReadyPreviewUrl] = useState<string | null>(null)

  const {
    activeAvatarId,
    sealingPortrait,
    portraitUrl,
    portraitPreviewId,
    portraitPanelHint,
    setPortraitPanelHint,
    avatarSelfSourceOpen,
    setAvatarSelfSourceOpen,
    refImage,
    presentationRef,
    setPresentationRef,
    updateRefImage,
    avatarBusy,
    portraitResultUnknown,
    portraitPreviewUnavailable,
    startAiHatching,
    currentHistoryItems,
    onSelectHistoryEntry,
    pickReferenceImage,
    pickPresentationImage,
    fetchAvatarPrompt,
    adoptAvatarSeed,
    sealPortrait,
    continueCurrentPortrait,
    confirmFullbody,
    regenerateAvatarPortrait,
    editAvatarPortrait,
    reloadAvatarPortrait,
    portraitSelectedIdx
  } = portrait

  if (phase === 'fullbody-reference' && activeAvatarId != null) {
    return (
      <FullbodyReferencePanel
        avatarId={activeAvatarId}
        key={activeAvatarId}
        onBack={onBack}
        onContinue={confirmFullbody}
      />
    )
  }

  if (phase !== 'portrait-generate' && phase !== 'portrait-avatar' && phase !== 'hatching') {
    return null
  }

  const generationBlocked = avatarBusy || sealingPortrait || portraitResultUnknown || portraitPreviewUnavailable

  const creationFeedback = (phase === 'hatching' || phase === 'portrait-avatar' || sealingPortrait) && (
    <CreationFeedback
      actions={
        portraitPanelHint ? (
          <button
            className={BTN_SUBTLE}
            disabled={avatarBusy || sealingPortrait}
            onClick={() => void reloadAvatarPortrait()}
            type="button"
          >
            {dict.creation.retryLoad}
          </button>
        ) : undefined
      }
      description={
        avatarBusy || sealingPortrait
          ? `${dict.creation.inputLocked} ${portraitUrl ? dict.creation.preserveCurrent : ''}`
          : portraitPanelHint || t.portraitPreviewHint
      }
      state={
        phase === 'hatching' || avatarBusy || sealingPortrait
          ? 'working'
          : portraitPanelHint
            ? portraitResultUnknown
              ? 'unknown'
              : 'error'
            : 'review'
      }
      title={
        sealingPortrait
          ? dict.common.saving
          : phase === 'hatching'
            ? hint || t.portraitGenerating
            : avatarBusy
              ? dict.creation.working
              : portraitPanelHint
                ? portraitResultUnknown
                  ? dict.creation.unknown
                  : t.portraitFailed
                : dict.creation.review
      }
    />
  )

  return (
    <>
      <CreationWorkspace
        footer={
          phase === 'portrait-generate' ? (
            <CreationActionBar description={creationFeedback}>
              <div className={styles.navigation}>
                <button
                  className="text-body transition hover:text-strong disabled:opacity-40"
                  disabled={sealingPortrait}
                  onClick={onBack}
                  type="button"
                >
                  {t.portraitBack}
                </button>
                <button
                  className={`ml-auto ${ONBOARDING_PRIMARY_BUTTON}`}
                  disabled={sealingPortrait}
                  onClick={() => void startAiHatching()}
                  type="button"
                >
                  {t.portraitTitle}
                </button>
              </div>
            </CreationActionBar>
          ) : phase === 'portrait-avatar' ? (
            <CreationActionBar description={creationFeedback}>
              <div className={styles.navigation}>
                <button
                  className="text-body transition hover:text-strong disabled:opacity-40"
                  disabled={avatarBusy || sealingPortrait}
                  onClick={onBack}
                  type="button"
                >
                  {t.portraitBack}
                </button>
                <button
                  className={`ml-auto ${ONBOARDING_PRIMARY_BUTTON}`}
                  disabled={
                    !portraitUrl ||
                    readyPreviewUrl !== portraitUrl ||
                    portraitPreviewId == null ||
                    portraitPreviewId !== activeAvatarId ||
                    avatarBusy ||
                    sealingPortrait ||
                    Boolean(portraitPanelHint)
                  }
                  onClick={() => void sealPortrait()}
                  type="button"
                >
                  {t.portraitConfirm}
                </button>
              </div>
            </CreationActionBar>
          ) : (
            <CreationActionBar description={creationFeedback}>{null}</CreationActionBar>
          )
        }
      >
        <div className="space-y-4">
          {phase === 'portrait-generate' && (
            <div>
              <h2 className={styles.questionTitle}>{t.portraitIdeaTitle}</h2>
              <p className="mt-1 text-xs text-body">{t.portraitIdeaHint}</p>

              <div className="mt-3 space-y-1">
                <label className="text-xs text-body" htmlFor="onboarding-portrait-description">
                  {t.portraitDescriptionLabel}
                </label>
                <RegenFeedbackInput id="onboarding-portrait-description" initial />
              </div>

              <Disclosure
                bodyClassName="space-y-1"
                className="mt-3 rounded-xl border border-line-hairline bg-fill-trough p-3"
                open={refImage ? true : undefined}
                summaryClassName="text-xs text-body"
                title={t.referenceSummary}
                variant="bordered"
              >
                <p className="mt-1 text-[11px] text-muted">{t.referenceHint}</p>
                <div className="mt-2 flex items-center gap-2">
                  {refImage && (
                    <img alt={t.referenceAlt} className="size-12 rounded-md object-contain" src={refImage.previewUrl} />
                  )}
                  <button className="text-xs text-strong" onClick={() => void pickReferenceImage()} type="button">
                    {refImage ? t.replaceReference : t.addReference}
                  </button>
                  {refImage && (
                    <button className="text-xs text-muted" onClick={() => updateRefImage(null)} type="button">
                      {t.removeReference}
                    </button>
                  )}
                </div>
                {hint && <p className="mt-2 text-xs text-strong">{hint}</p>}
              </Disclosure>

              {activeAvatarId != null && portraitUrl && (
                <div className="mt-3 flex items-center gap-3 rounded-xl border border-line-hairline p-3">
                  <img alt={t.previousAvatarAlt} className="size-12 rounded-lg object-contain" src={portraitUrl} />
                  <button
                    className="text-xs text-body transition hover:text-strong disabled:opacity-40"
                    disabled={avatarBusy || sealingPortrait}
                    onClick={() => void continueCurrentPortrait()}
                    type="button"
                  >
                    {t.keepAvatar}
                  </button>
                </div>
              )}
              {portraitPanelHint && (
                <p className="mt-3 text-xs text-danger-fg" role="alert">
                  {portraitPanelHint}
                </p>
              )}

              <button
                className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted transition hover:text-strong disabled:opacity-40"
                disabled={avatarBusy || sealingPortrait}
                onClick={() => setAvatarSelfSourceOpen(true)}
                type="button"
              >
                <FolderOpen className="size-3.5" /> {t.useOwnImage}
              </button>
            </div>
          )}

          {(phase === 'portrait-avatar' || (phase === 'hatching' && portraitUrl)) && (
            <div className="space-y-4">
              <h2 className={styles.questionTitle}>{t.confirmTitle}</h2>
              <PortraitPanel
                avatarUrl={portraitUrl}
                hint={null}
                history={currentHistoryItems}
                name={answers.name?.trim() || t.defaultName}
                onPreviewReady={setReadyPreviewUrl}
                onSelectEntry={avatarBusy || sealingPortrait ? undefined : onSelectHistoryEntry}
                selectedIdx={portraitSelectedIdx}
              />
            </div>
          )}

          {phase === 'portrait-avatar' && (
            <div className="mt-4">
              <>
                <Disclosure
                  bodyClassName="space-y-3"
                  className="rounded-xl border border-line-hairline p-3"
                  open={!portraitUrl ? true : undefined}
                  summaryClassName="mb-3text-xs text-body"
                  title={t.adjustSummary}
                  variant="bordered"
                >
                  <RegenFeedbackInput disabled={avatarBusy || sealingPortrait} />
                  <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
                    <button
                      className="text-body transition hover:text-strong disabled:opacity-40"
                      disabled={generationBlocked}
                      onClick={() => {
                        setPortraitPanelHint(null)
                        void regenerateAvatarPortrait()
                      }}
                      type="button"
                    >
                      {t.regenerate}
                    </button>
                    <EditAvatarButton
                      busy={generationBlocked}
                      disabledByReference={Boolean(refImage || presentationRef)}
                      onEdit={() => {
                        setPortraitPanelHint(null)
                        void editAvatarPortrait()
                      }}
                    />
                    <button
                      className="text-body transition hover:text-strong disabled:opacity-40"
                      disabled={avatarBusy || sealingPortrait}
                      onClick={() => void reloadAvatarPortrait()}
                      type="button"
                    >
                      {t.reload}
                    </button>
                  </div>
                  <div className="mt-2 space-y-2 text-xs">
                    {refImage && (
                      <div className="flex items-center gap-2">
                        <img
                          alt={t.shapeReferenceAlt}
                          className="h-9 w-9 rounded-md object-cover"
                          src={refImage.previewUrl}
                        />
                        <span className="text-[11px] text-muted">{t.shapeReferenceHint}</span>
                        <button
                          className="ml-auto text-muted transition hover:text-strong"
                          disabled={avatarBusy || sealingPortrait}
                          onClick={() => updateRefImage(null)}
                          type="button"
                        >
                          {t.removeReference}
                        </button>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <button
                        className="rounded-full border border-dashed border-line-standard px-3 py-1 text-body transition hover:bg-fill-hover"
                        disabled={generationBlocked}
                        onClick={() => void (refImage ? pickPresentationImage() : pickReferenceImage())}
                        type="button"
                      >
                        {!refImage
                          ? t.shapeReference
                          : presentationRef
                            ? t.replaceCompositionReference
                            : t.compositionReference}
                      </button>
                      {presentationRef && (
                        <>
                          <img
                            alt={t.compositionReferenceAlt}
                            className="h-9 w-9 rounded-md object-cover"
                            src={presentationRef.previewUrl}
                          />
                          <span className="text-[10px] text-faint">{t.compositionReferenceHint}</span>
                          <button
                            className="ml-auto text-muted transition hover:text-strong"
                            disabled={avatarBusy || sealingPortrait}
                            onClick={() => setPresentationRef(null)}
                            type="button"
                          >
                            {t.removeReference}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </Disclosure>
              </>
            </div>
          )}
        </div>
      </CreationWorkspace>
      <SelfSourceImageFlow
        adopt={adoptAvatarSeed}
        fetchPrompt={fetchAvatarPrompt}
        hint="{t.ownImageHint}"
        onClose={() => setAvatarSelfSourceOpen(false)}
        onUseAi={() => {
          setAvatarSelfSourceOpen(false)
          setHint(null)
          setPhase('portrait-generate')
        }}
        open={avatarSelfSourceOpen}
        referenceImages={refImage ? [{ label: t.shapeReferenceAlt, url: refImage.previewUrl }] : undefined}
        referenceRequired={Boolean(refImage)}
        title={t.ownImageTitle}
      />
    </>
  )
}
