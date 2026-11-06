import { useStore } from '@nanostores/react'
import type React from 'react'

import { sampleLine, type VoiceDesignPreview, voiceSelectionId } from '@/features/speech'
import { Check } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $auth } from '@/shared/store/auth'
import { GENDER_FILTER_VALUES } from '@/shared/voice-catalog'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  CreationActionBar,
  CreationFeedback,
  Disclosure,
  EmptyState,
  HINT_TEXT,
  INPUT_CLASS,
  LoadingBlock,
  SettingCard,
  SETTINGS_ROW_DESC,
  SETTINGS_ROW_TITLE,
  Spinner
} from '@/ui'

import { createClonedVoice, updateVoiceCreation } from './voice-creation-store'
import { useVoiceManager, type VoiceManagerProps } from './voice/use-voice-manager'

export function VoiceManager(props: VoiceManagerProps): React.JSX.Element {
  const auth = useStore($auth)
  const identity = auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind

  return <VoiceManagerSession key={identity} {...props} />
}

function VoiceManagerSession({
  compact = false,
  disabled = false,
  intro,
  name,
  onVoiceSelected,
  preference
}: VoiceManagerProps): React.JSX.Element {
  const model = useVoiceManager({ compact, disabled, name, onVoiceSelected, preference })

  const {
    dict,
    t,
    currentVoice,
    active,
    catalog,
    catalogStatus,
    setReload,
    creation,
    tab,
    prompt,
    feedback,
    adjusting,
    operationState,
    outcome,
    startedAt,
    confirmationChecked,
    genderFilter,
    setGenderFilter,
    showAll,
    setShowAll,
    playbackError,
    playing,
    inputRef,
    sample,
    selectedSample,
    busy,
    confirming,
    blocked,
    playbackBlocked,
    stopPlayback,
    previewAudio,
    previewFixed,
    stopOperation,
    previewCreation,
    runDesign,
    adoptVoice,
    checkConfirmation,
    chooseTab,
    voices,
    displayedVoices,
    currentFixed,
    currentCustom,
    customLabels,
    currentVoiceLabel,
    candidate,
    designStep,
    operationTitle,
    status,
    customAvailable
  } = model

  const actions = (
    id: string,
    label: string,
    onPreview: () => void,
    preview?: VoiceDesignPreview
  ): React.JSX.Element => (
    <div className="flex shrink-0 flex-wrap gap-2">
      <button className={BTN_SUBTLE} disabled={playbackBlocked} onClick={onPreview} type="button">
        {t.preview}
      </button>
      <button
        className={cn(BTN_PRIMARY, currentVoice === id && 'opacity-70')}
        disabled={blocked || (!onVoiceSelected && currentVoice === id)}
        onClick={() => adoptVoice(id, label, preview)}
        type="button"
      >
        {!onVoiceSelected && currentVoice === id ? t.inUse : t.useThisVoice}
      </button>
    </div>
  )

  const savedVoice = (kind: 'design' | 'clone'): React.JSX.Element | null => {
    const saved = catalog?.customVoices.find(voice => voice.kind === kind)

    if (!saved) {
      return null
    }

    const referenceAudioUrl = saved.reference_audio_url

    return (
      <SettingCard className="space-y-2 p-4" divided={false}>
        <p className={SETTINGS_ROW_TITLE}>{t.savedVoice}</p>
        {kind === 'design' && <p className={SETTINGS_ROW_DESC}>{saved.prompt}</p>}
        {!saved.available ? (
          <p className={HINT_TEXT}>{t.savedUnavailable}</p>
        ) : (
          actions(
            saved.voice_id,
            kind === 'design' ? t.designHeading : t.cloneHeading,
            () => void previewAudio(saved.preview_audio_url)
          )
        )}
        {kind === 'clone' && referenceAudioUrl && (
          <button
            className={BTN_SUBTLE}
            disabled={blocked}
            onClick={() => void previewAudio(referenceAudioUrl)}
            type="button"
          >
            {t.previewOriginal}
          </button>
        )}
      </SettingCard>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="shrink-0">
        <CapsuleTabs
          ariaLabel={t.methodAria}
          onChange={chooseTab}
          options={[
            { value: 'fixed', label: t.fixedHeading },
            { value: 'design', label: t.designTab },
            { value: 'clone', label: t.cloneHeading }
          ]}
          size="sm"
          value={tab}
        />
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {intro}
        {!compact && catalogStatus === 'ready' && currentVoice && (
          <SettingCard className="flex shrink-0 flex-wrap items-center justify-between gap-3 p-3">
            <div className="min-w-0">
              <p className={SETTINGS_ROW_DESC}>{dict.voice.current}</p>
              <p className={SETTINGS_ROW_TITLE}>{currentVoiceLabel}</p>
            </div>
            <button
              className={BTN_SUBTLE}
              disabled={playbackBlocked || (!currentFixed && !currentCustom?.available)}
              onClick={() => {
                if (currentCustom) {
                  void previewAudio(currentCustom.preview_audio_url)
                } else if (currentFixed) {
                  void previewFixed(currentVoice)
                }
              }}
              type="button"
            >
              {t.preview}
            </button>
          </SettingCard>
        )}
        {playbackError && (
          <CreationFeedback
            description={candidate ? t.trialReadyHint : undefined}
            state="error"
            title={playbackError}
          />
        )}
        {playing && (
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-body" role="status">
            <span>{t.playingPreview}</span>
            <button className={BTN_SUBTLE} onClick={stopPlayback} type="button">
              {t.stopPreview}
            </button>
          </div>
        )}
        {catalogStatus !== 'ready' ? (
          catalogStatus === 'error' ? (
            <EmptyState
              action={
                <button className={BTN_SUBTLE} onClick={() => setReload(value => value + 1)} type="button">
                  {dict.common.retry}
                </button>
              }
              title={t.loadFailed}
            />
          ) : (
            <LoadingBlock label={dict.common.loading} />
          )
        ) : tab === 'fixed' ? (
          <>
            <div>
              <CapsuleTabs
                ariaLabel={t.genderFilterAria}
                onChange={setGenderFilter}
                options={GENDER_FILTER_VALUES.map(value => ({ value, label: t.genderFilters[value] }))}
                size="sm"
                value={genderFilter}
              />
            </div>
            <SettingCard>
              {displayedVoices.map(voice => {
                const id = voiceSelectionId(voice)

                return (
                  <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3" key={id}>
                    <div className="min-w-0">
                      <p className={cn(SETTINGS_ROW_TITLE, 'flex items-center gap-1.5')}>
                        {voice.label}
                        {currentVoice === id && <Check className="size-3.5 text-accent" />}
                      </p>
                      <p className={SETTINGS_ROW_DESC}>{voice.tags.join(' · ')}</p>
                    </div>
                    {actions(id, voice.label, () => void previewFixed(id))}
                  </div>
                )
              })}
              {voices.length === 0 && (
                <p className="px-4 py-6 text-center text-[13px] text-muted">
                  {catalog?.voices.length
                    ? t.noMatch
                    : catalog?.providers.length
                      ? t.noVoicesForLanguage
                      : t.noTtsConfigured}
                </p>
              )}
            </SettingCard>
            {compact && voices.length > 3 && !genderFilter && (
              <button className={BTN_SUBTLE} onClick={() => setShowAll(value => !value)} type="button">
                {showAll ? t.showRecommended : t.showAll}
              </button>
            )}
          </>
        ) : tab === 'design' ? (
          <>
            {savedVoice('design')}
            {catalog?.supportsVoiceDesign ? (
              <div className="space-y-4 rounded-2xl border border-line-hairline p-4">
                <p className="text-xs leading-relaxed text-body">{t.designIntro}</p>
                <ol aria-label={t.designHeading} className="flex flex-wrap gap-x-5 gap-y-2 text-xs">
                  {t.designSteps.map((label, index) => (
                    <li
                      aria-current={designStep === index ? 'step' : undefined}
                      className={cn(
                        'flex items-center gap-2',
                        designStep === index ? 'font-medium text-strong' : 'text-muted'
                      )}
                      key={label}
                    >
                      <span className="flex size-5 items-center justify-center rounded-full bg-fill-faint">
                        {index < designStep ? <Check className="size-3.5" /> : index + 1}
                      </span>
                      {label}
                    </li>
                  ))}
                </ol>
                {!catalog.supportsVoicePromptGeneration && (
                  <p className="text-xs text-body">{t.autoDesignUnavailable}</p>
                )}
                {catalog.voiceDesignGuide && (
                  <Disclosure
                    bodyClassName="whitespace-pre-line text-xs leading-relaxed text-body"
                    summaryClassName="text-xs text-body"
                    title={t.designGuide}
                    variant="inline"
                  >
                    <p className="mt-2 whitespace-pre-line text-xs leading-relaxed text-body">
                      {catalog.voiceDesignGuide}
                    </p>
                  </Disclosure>
                )}
                <label className="block space-y-2 text-xs text-body">
                  <span>{t.promptLabel}</span>
                  <textarea
                    aria-label={t.designHeading}
                    className={cn(INPUT_CLASS, 'resize-none')}
                    maxLength={200}
                    onChange={event => {
                      updateVoiceCreation({
                        prompt: event.target.value,
                        initializedPrompt: true,
                        designPreview: null,
                        outcome: null,
                        error: null
                      })
                    }}
                    placeholder={t.designPlaceholder}
                    readOnly={busy || outcome === 'unknown'}
                    rows={4}
                    value={prompt}
                  />
                </label>
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-body">
                  <span>{busy || outcome === 'unknown' ? dict.creation.inputLocked : t.designHint}</span>
                  <span>{prompt.length}/200</span>
                </div>
                {candidate && (
                  <p className="rounded-xl bg-fill-faint p-3 text-xs leading-relaxed text-body">{sampleLine(name)}</p>
                )}
                {catalog.supportsVoicePromptGeneration && prompt.trim() && (
                  <div className="space-y-2 border-t border-line-hairline pt-3">
                    <button
                      aria-expanded={adjusting}
                      className={BTN_SUBTLE}
                      disabled={blocked}
                      onClick={() => updateVoiceCreation({ adjusting: !adjusting })}
                      type="button"
                    >
                      {t.adjustToggle}
                    </button>
                    {adjusting && (
                      <textarea
                        aria-label={t.feedbackLabel}
                        className={cn(INPUT_CLASS, 'resize-none')}
                        maxLength={2000}
                        onChange={event => updateVoiceCreation({ feedback: event.target.value })}
                        placeholder={t.feedbackPlaceholder}
                        readOnly={busy || outcome === 'unknown'}
                        rows={2}
                        value={feedback}
                      />
                    )}
                  </div>
                )}
              </div>
            ) : (
              <p className={HINT_TEXT}>{t.designUnavailable}</p>
            )}
          </>
        ) : (
          <>
            {savedVoice('clone')}
            {catalog?.supportsVoiceClone ? (
              <div className="space-y-4 rounded-2xl border border-line-hairline p-4">
                <p className="text-xs leading-relaxed text-body">{t.cloneIntro}</p>
                <div className="flex flex-wrap gap-2">
                  <button
                    className={BTN_SUBTLE}
                    disabled={
                      disabled || busy || outcome === 'unknown' || !active || (sample.busy && !sample.recording)
                    }
                    onClick={() => {
                      updateVoiceCreation({ clonePreview: null, outcome: null, error: null })

                      if (sample.recording) {
                        sample.stop()
                      } else {
                        stopPlayback()
                        void sample.start()
                      }
                    }}
                    type="button"
                  >
                    {sample.recording ? t.stopRecording : sample.sample ? t.rerecord : t.startRecording}
                  </button>
                  <button
                    className={BTN_SUBTLE}
                    disabled={blocked}
                    onClick={() => inputRef.current?.click()}
                    type="button"
                  >
                    {t.uploadAudio}
                  </button>
                  <input
                    accept="audio/*,.wav,.mp3,.m4a,.ogg,.flac,.webm,.aac"
                    className="hidden"
                    onChange={event => {
                      const file = event.target.files?.[0]
                      event.target.value = ''

                      if (file) {
                        stopPlayback()
                        updateVoiceCreation({ clonePreview: null, outcome: null, error: null })
                        void sample.upload(file, file.name)
                      }
                    }}
                    ref={inputRef}
                    type="file"
                  />
                </div>
                {selectedSample && (
                  <div className="flex flex-wrap items-center gap-3 rounded-xl bg-fill-faint p-3">
                    <span className="text-xs text-body">{t.sampleDuration(Math.round(selectedSample.duration))}</span>
                    <button
                      className={BTN_SUBTLE}
                      disabled={playbackBlocked}
                      onClick={() => void previewAudio(selectedSample.url)}
                      type="button"
                    >
                      {t.previewOriginal}
                    </button>
                  </div>
                )}
                {candidate && <p className="text-xs leading-relaxed text-body">{sampleLine(name)}</p>}
              </div>
            ) : (
              <p className={HINT_TEXT}>{t.cloneUnavailable}</p>
            )}
          </>
        )}
      </div>
      {(customAvailable || busy || outcome === 'unknown' || status) && (
        <CreationActionBar
          description={
            status ? (
              <CreationFeedback
                description={status.description}
                startedAt={busy ? startedAt : undefined}
                state={status.state}
                title={status.title}
              />
            ) : undefined
          }
        >
          <div aria-atomic="true" aria-live="polite" className="flex flex-wrap items-center gap-2">
            {(customAvailable || busy || outcome === 'unknown') &&
              (busy ? (
                <>
                  <button className={BTN_PRIMARY} disabled type="button">
                    <Spinner />
                    {operationTitle}
                  </button>
                  {!confirming && (
                    <button
                      className={BTN_SUBTLE}
                      disabled={operationState === 'cancelling'}
                      onClick={() => void stopOperation()}
                      type="button"
                    >
                      {dict.common.cancel}
                    </button>
                  )}
                </>
              ) : outcome === 'unknown' ? (
                creation.confirmation ? (
                  <>
                    <button className={BTN_PRIMARY} onClick={() => void checkConfirmation()} type="button">
                      {t.checkCurrent}
                    </button>
                    {confirmationChecked && (
                      <button
                        className={BTN_SUBTLE}
                        onClick={() => {
                          const pending = creation.confirmation

                          if (pending) {
                            adoptVoice(pending.id, pending.label, pending.preview, true)
                          }
                        }}
                        type="button"
                      >
                        {t.retryConfirm}
                      </button>
                    )}
                  </>
                ) : (
                  <button className={BTN_PRIMARY} onClick={() => void stopOperation()} type="button">
                    {creation.errorCode === 'cancel' ? t.retryCancel : t.cancelUnknown}
                  </button>
                )
              ) : (
                <>
                  {candidate && !(adjusting && feedback.trim()) ? (
                    <>
                      <button
                        className={BTN_SUBTLE}
                        disabled={playbackBlocked}
                        onClick={() => void previewAudio(candidate.trialAudioDataUrl)}
                        type="button"
                      >
                        {t.preview}
                      </button>
                      <button
                        className={BTN_PRIMARY}
                        disabled={blocked}
                        onClick={() => adoptVoice(candidate.voiceId, customLabels[candidate.kind], candidate)}
                        type="button"
                      >
                        {t.useThisVoice}
                      </button>
                    </>
                  ) : outcome === 'saved' && !(adjusting && feedback.trim()) ? (
                    <button className={BTN_PRIMARY} disabled type="button">
                      <Check className="size-4" />
                      {t.inUse}
                    </button>
                  ) : tab === 'design' ? (
                    <button
                      className={BTN_PRIMARY}
                      disabled={blocked || (!prompt.trim() && !catalog?.supportsVoicePromptGeneration)}
                      onClick={() =>
                        runDesign(adjusting && feedback.trim() ? 'feedback' : prompt.trim() ? 'preview' : 'auto')
                      }
                      type="button"
                    >
                      {adjusting && feedback.trim()
                        ? t.adjustAndPreview
                        : prompt.trim()
                          ? t.designGenerate
                          : t.autoDesign}
                    </button>
                  ) : (
                    <button
                      className={BTN_PRIMARY}
                      disabled={blocked || !selectedSample}
                      onClick={() => {
                        if (selectedSample) {
                          previewCreation(() => createClonedVoice(name))
                        }
                      }}
                      type="button"
                    >
                      {t.clonePreview}
                    </button>
                  )}
                  {tab === 'design' && prompt.trim() && catalog?.supportsVoicePromptGeneration && (
                    <button className={BTN_SUBTLE} disabled={blocked} onClick={() => runDesign('auto')} type="button">
                      {t.autoDesign}
                    </button>
                  )}
                </>
              ))}
          </div>
        </CreationActionBar>
      )}
    </div>
  )
}
