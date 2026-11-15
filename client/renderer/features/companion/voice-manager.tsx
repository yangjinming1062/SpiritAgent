import { useStore } from '@nanostores/react'
import type React from 'react'

import { sampleLine, voiceSelectionId } from '@/features/speech'
import { Check, SquareFilled, Volume2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $auth } from '@/shared/store/auth'
import { GENDER_FILTER_VALUES } from '@/shared/voice-catalog'
import {
  BTN_ICON,
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  CreationActionBar,
  CreationFeedback,
  CreationSteps,
  CreationWorkspace,
  Disclosure,
  EmptyState,
  HINT_TEXT,
  INPUT_CLASS,
  LoadingBlock,
  PanelSelect,
  SearchField,
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
    voiceQuery,
    setVoiceQuery,
    showAll,
    setShowAll,
    playbackError,
    playing,
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

  // 试听键：固定音色用选择 id，自定义声音与候选用音频 URL。
  const isPlayingAudio = (url?: string | null): boolean => Boolean(url) && playing === url

  const voiceActions = (id: string, label: string, onPreview: () => void, url?: string | null): React.JSX.Element => {
    // url 为 null 表示这条声音没有可播放的成品；undefined 表示按选择 id 播放固定音色。
    const hasAudio = url !== null
    const active = url ? isPlayingAudio(url) : url === undefined && playing === id

    return (
      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
        <button
          aria-pressed={active}
          className={BTN_SUBTLE}
          disabled={playbackBlocked || !hasAudio}
          onClick={() => (active ? stopPlayback() : onPreview())}
          type="button"
        >
          {active ? t.stopPreview : t.preview}
        </button>
        <button
          className={cn(BTN_PRIMARY, currentVoice === id && 'opacity-70')}
          disabled={blocked || (!onVoiceSelected && currentVoice === id)}
          onClick={() => adoptVoice(id, label)}
          type="button"
        >
          {!onVoiceSelected && currentVoice === id ? t.inUse : t.useThisVoice}
        </button>
      </div>
    )
  }

  const savedVoice = (kind: 'design' | 'clone'): React.JSX.Element | null => {
    const saved = catalog?.customVoices.find(voice => voice.kind === kind)

    if (!saved) {
      return null
    }

    const audioUrl = saved.preview_audio_url
    const referenceUrl = saved.reference_audio_url

    return (
      <SettingCard className="space-y-2 p-4" divided={false}>
        <p className={SETTINGS_ROW_TITLE}>{t.savedVoice}</p>
        {kind === 'design' ? <p className={SETTINGS_ROW_DESC}>{saved.prompt}</p> : null}
        {!saved.available ? (
          <p className={HINT_TEXT}>{t.savedUnavailable}</p>
        ) : (
          voiceActions(
            saved.voice_id,
            kind === 'design' ? t.designHeading : t.cloneHeading,
            () => {
              if (audioUrl) {
                void previewAudio(audioUrl)
              }
            },
            audioUrl
          )
        )}
        {kind === 'clone' && referenceUrl ? (
          <button
            className={BTN_SUBTLE}
            disabled={blocked}
            onClick={() => void previewAudio(referenceUrl)}
            type="button"
          >
            {t.previewOriginal}
          </button>
        ) : null}
      </SettingCard>
    )
  }

  const showBar = customAvailable || busy || outcome === 'unknown' || Boolean(status)

  // 设计/克隆共用同一条操作条：生成、采用、取消与未知结果核对都在这里，固定音色页不再出现制作按钮。
  const creationActions = (): React.JSX.Element => (
    <div aria-atomic="true" aria-live="polite" className="flex flex-wrap items-center gap-2">
      {customAvailable || busy || outcome === 'unknown' ? (
        busy ? (
          <>
            <button className={BTN_PRIMARY} disabled type="button">
              <Spinner />
              {operationTitle}
            </button>
            {!confirming ? (
              <button
                className={BTN_SUBTLE}
                disabled={operationState === 'cancelling'}
                onClick={() => void stopOperation()}
                type="button"
              >
                {dict.common.cancel}
              </button>
            ) : null}
          </>
        ) : outcome === 'unknown' ? (
          creation.confirmation ? (
            <>
              <button className={BTN_PRIMARY} onClick={() => void checkConfirmation()} type="button">
                {t.checkCurrent}
              </button>
              {confirmationChecked ? (
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
              ) : null}
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
                  {isPlayingAudio(candidate.trialAudioDataUrl) ? t.stopPreview : t.preview}
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
                {adjusting && feedback.trim() ? t.adjustAndPreview : prompt.trim() ? t.designGenerate : t.autoDesign}
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
            {tab === 'design' && prompt.trim() && catalog?.supportsVoicePromptGeneration ? (
              <button className={BTN_SUBTLE} disabled={blocked} onClick={() => runDesign('auto')} type="button">
                {t.autoDesign}
              </button>
            ) : null}
          </>
        )
      ) : null}
    </div>
  )

  const statusBar = status ? (
    <CreationFeedback
      description={status.description}
      startedAt={busy ? startedAt : undefined}
      state={status.state}
      title={status.title}
    />
  ) : undefined

  const currentAudioUrl = currentCustom?.preview_audio_url ?? null
  const currentPlaying = currentAudioUrl ? playing === currentAudioUrl : playing === currentVoice
  const cloneStep = candidate ? 3 : selectedSample ? 2 : 1

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {intro ? <div className="shrink-0">{intro}</div> : null}
      {!compact && catalogStatus === 'ready' && currentVoice ? (
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-xl bg-fill-faint px-3 py-2">
          <div className="flex min-w-0 items-baseline gap-1.5">
            <span className="shrink-0 text-[10px] text-muted">{dict.voice.current}</span>
            <span className="min-w-0 truncate text-[13px] font-medium text-strong">{currentVoiceLabel}</span>
          </div>
          <button
            aria-label={currentPlaying ? t.stopPreview : t.preview}
            aria-pressed={currentPlaying}
            className={BTN_ICON}
            disabled={playbackBlocked || (!currentFixed && !currentCustom?.available)}
            onClick={() => {
              if (currentPlaying) {
                stopPlayback()
              } else if (currentAudioUrl) {
                void previewAudio(currentAudioUrl)
              } else if (currentFixed) {
                void previewFixed(currentVoice)
              }
            }}
            title={t.preview}
            type="button"
          >
            {currentPlaying ? <SquareFilled aria-hidden="true" /> : <Volume2 aria-hidden="true" />}
          </button>
        </div>
      ) : null}
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
          variant="segmented"
        />
      </div>
      {playbackError ? <CreationFeedback description={t.trialReadyHint} state="error" title={playbackError} /> : null}
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
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <SearchField
              ariaLabel={t.searchAria}
              className="min-w-40 max-w-none flex-1"
              onChange={setVoiceQuery}
              placeholder={t.searchPlaceholder}
              type="search"
              value={voiceQuery}
            />
            <PanelSelect
              ariaLabel={t.genderFilterAria}
              onChange={setGenderFilter}
              options={GENDER_FILTER_VALUES.map(value => ({ label: t.genderFilters[value], value }))}
              value={genderFilter}
              widthClass="w-32"
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain pr-1">
            <SettingCard>
              {displayedVoices.map(voice => {
                const id = voiceSelectionId(voice)
                const current = currentVoice === id
                const active = playing === id

                return (
                  <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5" key={id}>
                    <div className="min-w-0 flex-1">
                      <p className={cn(SETTINGS_ROW_TITLE, 'flex items-center gap-1.5')}>
                        <span className="min-w-0 truncate">{voice.label}</span>
                        {current ? (
                          <>
                            <Check aria-hidden="true" className="size-3.5 shrink-0 text-accent" />
                            <span className="shrink-0 text-[10px] font-normal text-accent">{t.inUse}</span>
                          </>
                        ) : null}
                      </p>
                      {voice.tags.length > 0 ? (
                        <p className={cn(SETTINGS_ROW_DESC, 'truncate')}>{voice.tags.join(' · ')}</p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <button
                        aria-label={t.previewAria(voice.label)}
                        aria-pressed={active}
                        className={BTN_ICON}
                        disabled={playbackBlocked}
                        onClick={() => (active ? stopPlayback() : void previewFixed(id))}
                        title={t.preview}
                        type="button"
                      >
                        {active ? <SquareFilled aria-hidden="true" /> : <Volume2 aria-hidden="true" />}
                      </button>
                      {current ? null : (
                        <button
                          className={BTN_SUBTLE}
                          disabled={blocked}
                          onClick={() => adoptVoice(id, voice.label)}
                          type="button"
                        >
                          {t.useThisVoice}
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
              {voices.length === 0 ? (
                <p className="px-4 py-6 text-center text-[13px] text-muted">
                  {catalog?.voices.length
                    ? t.noMatch
                    : catalog?.providers.length
                      ? t.noVoicesForLanguage
                      : t.noTtsConfigured}
                </p>
              ) : null}
            </SettingCard>
            {compact && voices.length > 3 && !genderFilter && !voiceQuery ? (
              <button
                className={cn(BTN_SUBTLE, 'mx-auto mt-3 flex')}
                onClick={() => setShowAll(value => !value)}
                type="button"
              >
                {showAll ? t.showRecommended : t.showAll}
              </button>
            ) : null}
          </div>
        </div>
      ) : tab === 'design' ? (
        <CreationWorkspace
          footer={
            showBar ? <CreationActionBar description={statusBar}>{creationActions()}</CreationActionBar> : undefined
          }
          header={
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CreationSteps labels={t.designSteps} step={designStep + 1} />
              {catalog?.supportsVoicePromptGeneration ? (
                <span className={HINT_TEXT}>{dict.creation.canLeave}</span>
              ) : null}
            </div>
          }
        >
          <div className="space-y-4">
            {savedVoice('design')}
            {catalog?.supportsVoiceDesign ? (
              <div className="space-y-4 rounded-2xl border border-line-hairline p-4">
                <p className="text-xs leading-relaxed text-body">{t.designIntro}</p>
                {!catalog.supportsVoicePromptGeneration ? (
                  <p className="text-xs text-body">{t.autoDesignUnavailable}</p>
                ) : null}
                {catalog.voiceDesignGuide ? (
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
                ) : null}
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
                {candidate ? (
                  <p className="rounded-xl bg-fill-faint p-3 text-xs leading-relaxed text-body">{sampleLine(name)}</p>
                ) : null}
                {catalog.supportsVoicePromptGeneration && prompt.trim() ? (
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
                    {adjusting ? (
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
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : (
              <p className={HINT_TEXT}>{t.designUnavailable}</p>
            )}
          </div>
        </CreationWorkspace>
      ) : (
        <CreationWorkspace
          footer={
            showBar ? <CreationActionBar description={statusBar}>{creationActions()}</CreationActionBar> : undefined
          }
          header={
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CreationSteps labels={t.cloneSteps} step={cloneStep} />
              <span className={HINT_TEXT}>{dict.creation.canLeave}</span>
            </div>
          }
        >
          <div className="space-y-4">
            {savedVoice('clone')}
            {catalog?.supportsVoiceClone ? (
              <div className="space-y-4 rounded-2xl border border-line-hairline p-4">
                <p className="text-xs leading-relaxed text-body">{t.cloneIntro}</p>
                <div className="flex flex-wrap gap-2">
                  <button
                    className={BTN_SUBTLE}
                    disabled={
                      disabled || busy || outcome === 'unknown' || !model.active || (sample.busy && !sample.recording)
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
                    onClick={() => model.inputRef.current?.click()}
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
                    ref={model.inputRef}
                    type="file"
                  />
                </div>
                {selectedSample ? (
                  <div className="flex flex-wrap items-center gap-3 rounded-xl bg-fill-faint p-3">
                    <span className="text-xs text-body">{t.sampleDuration(Math.round(selectedSample.duration))}</span>
                    <button
                      aria-pressed={isPlayingAudio(selectedSample.url)}
                      className={BTN_SUBTLE}
                      disabled={playbackBlocked}
                      onClick={() =>
                        isPlayingAudio(selectedSample.url) ? stopPlayback() : void previewAudio(selectedSample.url)
                      }
                      type="button"
                    >
                      {isPlayingAudio(selectedSample.url) ? t.stopPreview : t.previewOriginal}
                    </button>
                  </div>
                ) : null}
                {candidate ? <p className="text-xs leading-relaxed text-body">{sampleLine(name)}</p> : null}
              </div>
            ) : (
              <p className={HINT_TEXT}>{t.cloneUnavailable}</p>
            )}
          </div>
        </CreationWorkspace>
      )}
    </div>
  )
}
