import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $companionVoiceId, applyServerCompanionVoiceId, setCompanionVoiceId } from '@/modules/character'
import {
  $voiceCatalogRevision,
  cloneVoice,
  designVoice,
  fetchVoiceCatalogRaw,
  matchVoicePreference,
  playDataUrl,
  sampleLine,
  speakScripted,
  type VoiceCatalog,
  type VoiceDesignPreview,
  voiceSelectionId
} from '@/modules/speech'
import { requestGateway } from '@/shared'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { Check } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  HINT_TEXT,
  INPUT_CLASS,
  SettingCard,
  SETTINGS_ROW_DESC,
  SETTINGS_ROW_TITLE
} from '@/shared/panel'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { $surfaceRole, $surfaceScreenLocked, $surfaceSpriteVisible } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'
import { GENDER_FILTER_VALUES } from '@/shared/voice-catalog'
import { errorMessage } from '@runtime'

import { useVoiceSample } from './use-voice-sample'

type VoiceTab = 'fixed' | 'design' | 'clone'
type VoiceOperation = 'idle' | 'generating' | 'confirming'
interface VoiceManagerProps {
  name: string
  compact?: boolean
  preference?: string
  disabled?: boolean
  onVoiceSelected?: (voiceId: string, label: string, serverConfirmed: boolean) => Promise<void>
}

export function VoiceManager(props: VoiceManagerProps): React.JSX.Element {
  const auth = useStore($auth)
  const identity = auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind

  return <VoiceManagerSession key={identity} {...props} />
}

function VoiceManagerSession({
  name,
  compact = false,
  preference,
  disabled = false,
  onVoiceSelected
}: VoiceManagerProps): React.JSX.Element {
  const dict = useStrings()
  const t = dict.voice
  const locale = useStore($locale)
  const auth = useStore($auth)
  const gateway = useStore($gatewayState)
  const catalogRevision = useStore($voiceCatalogRevision)
  const currentVoice = useStore($companionVoiceId)
  const panelActive = usePanelActivity()
  const screenLocked = useStore($surfaceScreenLocked)
  const surfaceRole = useStore($surfaceRole)
  const spriteVisible = useStore($surfaceSpriteVisible)
  const [documentVisible, setDocumentVisible] = useState(document.visibilityState === 'visible')
  const active = panelActive && documentVisible && !screenLocked && (surfaceRole !== 'sprite' || spriteVisible)
  const activeRef = useLatestRef(active)
  const begin = useAsyncGuard()
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null)
  const [catalogStatus, setCatalogStatus] = useState<'loading' | 'error' | 'ready'>('loading')
  const [reload, setReload] = useState(0)
  const [tab, setTab] = useState<VoiceTab>('fixed')
  const [genderFilter, setGenderFilter] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [provider, setProvider] = useState<string | undefined>()
  const [feedback, setFeedback] = useState('')
  const [designPreview, setDesignPreview] = useState<VoiceDesignPreview | null>(null)
  const [clonePreview, setClonePreview] = useState<VoiceDesignPreview | null>(null)
  const [operationState, setOperationState] = useState<VoiceOperation>('idle')
  const [error, setError] = useState<string | null>(null)
  const previewAbort = useRef<AbortController | null>(null)
  const operation = useRef<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const sample = useVoiceSample(active && tab === 'clone')
  const selectedSample = sample.sample
  const busy = operationState !== 'idle'
  const confirming = operationState === 'confirming'
  const blocked = disabled || busy || sample.busy || !active
  const initialize = useRef({ prompt: true, tab: true })

  useEffect(() => {
    const onVisibility = (): void => setDocumentVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', onVisibility)

    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [])

  const cancelOperation = useCallback(() => {
    const id = operation.current
    operation.current = null
    previewAbort.current?.abort()
    previewAbort.current = null

    if (id) {
      void requestGateway('tts.cancel_voice_operation', { operation_id: id }).catch(failure => {
        log.warn('voice-manager', 'Could not cancel voice operation', failure)
      })
    }
  }, [])

  useEffect(() => {
    if (!active) {
      cancelOperation()
      setOperationState('idle')
    }

    return cancelOperation
  }, [active, auth, cancelOperation])

  useEffect(() => {
    if (!active || gateway !== 'open') {
      return
    }

    let cancelled = false
    const mounted = begin()
    setCatalogStatus(status => (status === 'ready' ? status : 'loading'))

    void (async () => {
      const [result, matched] = await Promise.all([
        fetchVoiceCatalogRaw(requestGateway, locale),
        preference?.trim() ? matchVoicePreference(requestGateway, preference, locale) : Promise.resolve(null)
      ])

      if (cancelled || !mounted()) {
        return
      }

      if (!result.ok) {
        setCatalogStatus(status => (status === 'ready' ? status : 'error'))
        setError(t.loadFailed)

        return
      }

      const next = result.catalog

      if (matched?.ok && matched.voice) {
        const priority = [matched.voice, ...matched.alternatives]
        const ids = new Set(priority.map(voiceSelectionId))
        next.voices = [...priority, ...next.voices.filter(voice => !ids.has(voiceSelectionId(voice)))]
      }

      setCatalog(next)
      setCatalogStatus('ready')

      if (initialize.current.tab) {
        const selected = next.customVoices.find(voice => voice.voice_id === $companionVoiceId.get())

        if (selected) {
          setTab(selected.kind)
        }

        initialize.current.tab = false
      }

      if (initialize.current.prompt) {
        const saved = next.customVoices.find(voice => voice.kind === 'design')

        if (saved) {
          setPrompt(saved.prompt)
          setProvider(saved.provider)
        }

        initialize.current.prompt = false
      }
    })()

    return () => {
      cancelled = true
    }
  }, [active, gateway, locale, reload, preference, catalogRevision, t.loadFailed, begin])

  const beginPlayback = (): AbortSignal | null => {
    if (!activeRef.current) {
      return null
    }

    previewAbort.current?.abort()
    const controller = new AbortController()
    previewAbort.current = controller

    return controller.signal
  }

  const previewAudio = async (url: string): Promise<void> => {
    const signal = beginPlayback()

    if (!signal) {
      return
    }

    const mounted = begin()
    setError(null)

    try {
      const dataUrl = url.startsWith('data:') ? url : await backendApi().apiAsset({ url, preferCache: true })

      if (!dataUrl) {
        throw new Error(t.previewFailed)
      }

      if (!signal.aborted && mounted() && activeRef.current) {
        const result = await playDataUrl(dataUrl, { signal })

        if (result === 'failed' && !signal.aborted && mounted()) {
          setError(t.previewFailed)
        }
      }
    } catch (failure) {
      if (!signal.aborted && mounted()) {
        setError(errorMessage(failure, t.previewFailed))
      }
    }
  }

  const runOperation = async (
    action: (id: string, live: () => boolean) => Promise<void>,
    state: Exclude<VoiceOperation, 'idle'> = 'generating'
  ): Promise<void> => {
    if (blocked || operation.current || !activeRef.current) {
      return
    }

    cancelOperation()
    const id = crypto.randomUUID()
    operation.current = id
    const mounted = begin()
    const live = (): boolean => mounted() && activeRef.current && operation.current === id
    setOperationState(state)
    setError(null)

    try {
      await action(id, live)
    } catch (failure) {
      log.warn('voice-manager', 'Voice operation failed', failure)

      if (live()) {
        setError(errorMessage(failure, t.designFailed))
      }
    } finally {
      if (live()) {
        operation.current = null
        setOperationState('idle')
      }
    }
  }

  const runDesign = (mode: 'auto' | 'feedback' | 'preview'): void => {
    void runOperation(async (id, live) => {
      let description = prompt.trim()
      let designProvider = provider

      if (mode !== 'preview') {
        const generated = await requestGateway<{ prompt: string; provider: string }>('tts.generate_voice_prompt', {
          operation_id: id,
          preference: preference?.trim() || undefined,
          current_prompt: mode === 'feedback' ? description : undefined,
          feedback: mode === 'feedback' ? feedback.trim() : undefined
        })

        if (!live()) {
          return
        }

        description = generated.prompt
        designProvider = generated.provider
        setPrompt(description)
        setProvider(designProvider)
        setDesignPreview(null)
      }

      const result = await designVoice(requestGateway, description, sampleLine(name), id, designProvider)

      if (live()) {
        setDesignPreview(result)
        void previewAudio(result.trialAudioDataUrl)
      }
    })
  }

  const applyVoice = async (id: string, label: string, serverConfirmed: boolean): Promise<void> => {
    if (onVoiceSelected) {
      await onVoiceSelected(id, label, serverConfirmed)
    } else if (serverConfirmed) {
      await applyServerCompanionVoiceId(id)
    } else {
      setCompanionVoiceId(id)
    }
  }

  const adoptVoice = (id: string, label: string, preview?: VoiceDesignPreview): void => {
    void runOperation(async (_operationId, live) => {
      let voiceId = id

      if (preview) {
        const result = await requestGateway<{ voice_id: string; version: number }>('tts.confirm_voice', {
          candidate_id: preview.candidateId,
          expected_version: preview.expectedVersion
        })

        if (!live()) {
          return
        }

        voiceId = result.voice_id
      }

      await applyVoice(voiceId, label, Boolean(preview))

      if (live()) {
        if (preview?.kind === 'design') {
          setDesignPreview(null)
        } else if (preview?.kind === 'clone') {
          setClonePreview(null)
        }

        setReload(value => value + 1)
      }
    }, 'confirming')
  }

  const actions = (
    id: string,
    label: string,
    onPreview: () => void,
    preview?: VoiceDesignPreview
  ): React.JSX.Element => (
    <div className="flex shrink-0 flex-wrap gap-2">
      <button className={BTN_SUBTLE} disabled={blocked} onClick={onPreview} type="button">
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

  const chooseTab = (value: VoiceTab): void => {
    if (confirming) {
      return
    }

    cancelOperation()
    initialize.current.tab = false
    setOperationState('idle')
    setError(null)
    setTab(value)
  }

  const voices = catalog?.voices.filter(voice => !genderFilter || voice.gender === genderFilter) ?? []

  const displayedVoices =
    !compact || showAll || genderFilter
      ? voices
      : voices.filter((voice, index) => index < 3 || voiceSelectionId(voice) === currentVoice)

  const currentFixed = catalog?.voices.find(voice => voiceSelectionId(voice) === currentVoice)
  const currentCustom = catalog?.customVoices.find(voice => voice.voice_id === currentVoice)
  const customLabels = { design: t.designHeading, clone: t.cloneHeading }

  const currentVoiceLabel =
    currentFixed?.label || (currentCustom ? customLabels[currentCustom.kind] : t.savedUnavailable)

  return (
    <div className="space-y-4">
      {!compact && catalogStatus === 'ready' && currentVoice && (
        <SettingCard className="flex flex-wrap items-center justify-between gap-3 p-4">
          <div className="min-w-0">
            <p className={SETTINGS_ROW_DESC}>{locale === 'en' ? 'Current voice' : '当前声音'}</p>
            <p className={SETTINGS_ROW_TITLE}>{currentVoiceLabel}</p>
          </div>
          <button
            className={BTN_SUBTLE}
            disabled={blocked || (!currentFixed && !currentCustom?.available)}
            onClick={() => {
              if (currentCustom) {
                void previewAudio(currentCustom.preview_audio_url)
              } else if (currentFixed) {
                const signal = beginPlayback()

                if (signal) {
                  void speakScripted(sampleLine(name), currentVoice, 'voice.preview', signal)
                }
              }
            }}
            type="button"
          >
            {t.preview}
          </button>
        </SettingCard>
      )}
      <div>
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
      {catalogStatus !== 'ready' ? (
        <div className="space-y-2">
          <p className={HINT_TEXT}>{catalogStatus === 'error' ? t.loadFailed : dict.common.loading}</p>
          {catalogStatus === 'error' && (
            <button className={BTN_SUBTLE} onClick={() => setReload(value => value + 1)} type="button">
              {dict.common.retry}
            </button>
          )}
        </div>
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
                  {actions(id, voice.label, () => {
                    const signal = beginPlayback()

                    if (signal) {
                      void speakScripted(sampleLine(name), id, 'voice.preview', signal)
                    }
                  })}
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
            <SettingCard className="space-y-3 p-4" divided={false}>
              <p className={HINT_TEXT}>{t.designIntro}</p>
              {catalog.supportsVoicePromptGeneration ? (
                <button className={BTN_PRIMARY} disabled={blocked} onClick={() => runDesign('auto')} type="button">
                  {t.autoDesign}
                </button>
              ) : (
                <p className={HINT_TEXT}>{t.autoDesignUnavailable}</p>
              )}
              {catalog.voiceDesignGuide && (
                <details>
                  <summary className="cursor-pointer text-xs text-muted">{t.designGuide}</summary>
                  <p className={cn(HINT_TEXT, 'mt-2 whitespace-pre-line')}>{catalog.voiceDesignGuide}</p>
                </details>
              )}
              <textarea
                aria-label={t.designHeading}
                className={cn(INPUT_CLASS, 'resize-none')}
                disabled={busy}
                maxLength={200}
                onChange={event => {
                  setPrompt(event.target.value)
                  setDesignPreview(null)
                }}
                placeholder={t.designPlaceholder}
                rows={4}
                value={prompt}
              />
              <p className="text-right text-[10px] text-muted">{prompt.length}/200</p>
              <button
                className={BTN_SUBTLE}
                disabled={blocked || !prompt.trim()}
                onClick={() => runDesign('preview')}
                type="button"
              >
                {t.designGenerate}
              </button>
              {catalog.supportsVoicePromptGeneration && (
                <div className="space-y-2 border-t border-line-hairline pt-3">
                  <textarea
                    aria-label={t.feedbackLabel}
                    className={cn(INPUT_CLASS, 'resize-none')}
                    disabled={busy}
                    maxLength={2000}
                    onChange={event => setFeedback(event.target.value)}
                    placeholder={t.feedbackPlaceholder}
                    rows={2}
                    value={feedback}
                  />
                  <button
                    className={BTN_SUBTLE}
                    disabled={blocked || !prompt.trim() || !feedback.trim()}
                    onClick={() => runDesign('feedback')}
                    type="button"
                  >
                    {t.adjustDesign}
                  </button>
                </div>
              )}
              {designPreview &&
                designPreview.prompt === prompt.trim() &&
                actions(
                  designPreview.voiceId,
                  t.designHeading,
                  () => void previewAudio(designPreview.trialAudioDataUrl),
                  designPreview
                )}
            </SettingCard>
          ) : (
            <p className={HINT_TEXT}>{t.designUnavailable}</p>
          )}
        </>
      ) : (
        <>
          {savedVoice('clone')}
          {catalog?.supportsVoiceClone ? (
            <SettingCard className="space-y-3 p-4" divided={false}>
              <p className={HINT_TEXT}>{t.cloneIntro}</p>
              <div className="flex flex-wrap gap-2">
                <button
                  className={BTN_PRIMARY}
                  disabled={disabled || busy || !active || (sample.busy && !sample.recording)}
                  onClick={() => {
                    setClonePreview(null)

                    if (sample.recording) {
                      sample.stop()
                    } else {
                      cancelOperation()
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
                      cancelOperation()
                      setClonePreview(null)
                      void sample.upload(file, file.name)
                    }
                  }}
                  ref={inputRef}
                  type="file"
                />
              </div>
              {sample.recording && (
                <p aria-live="polite" className={HINT_TEXT}>
                  {t.recordingHint}
                </p>
              )}
              {sample.busy && !sample.recording && (
                <p aria-live="polite" className={HINT_TEXT}>
                  {t.uploadingAudio}
                </p>
              )}
              {selectedSample && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className={HINT_TEXT}>{t.sampleDuration(Math.round(selectedSample.duration))}</span>
                  <button
                    className={BTN_SUBTLE}
                    disabled={blocked}
                    onClick={() => void previewAudio(selectedSample.url)}
                    type="button"
                  >
                    {t.previewOriginal}
                  </button>
                  <button
                    className={BTN_PRIMARY}
                    disabled={blocked}
                    onClick={() =>
                      void runOperation(async (id, live) => {
                        const result = await cloneVoice(requestGateway, selectedSample.sample_id, sampleLine(name), id)

                        if (live()) {
                          setClonePreview(result)
                          void previewAudio(result.trialAudioDataUrl)
                        }
                      })
                    }
                    type="button"
                  >
                    {t.clonePreview}
                  </button>
                </div>
              )}
              {sample.error && (
                <p className="text-xs text-danger-fg" role="alert">
                  {sample.error}
                </p>
              )}
              {clonePreview &&
                actions(
                  clonePreview.voiceId,
                  t.cloneHeading,
                  () => void previewAudio(clonePreview.trialAudioDataUrl),
                  clonePreview
                )}
            </SettingCard>
          ) : (
            <p className={HINT_TEXT}>{t.cloneUnavailable}</p>
          )}
        </>
      )}
      {busy && (
        <div aria-live="polite" className="flex items-center gap-3">
          <p className={HINT_TEXT}>{confirming ? t.saving : t.designGenerating}</p>
          {!confirming && (
            <button
              className={BTN_SUBTLE}
              onClick={() => {
                cancelOperation()
                setOperationState('idle')
              }}
              type="button"
            >
              {dict.common.cancel}
            </button>
          )}
        </div>
      )}
      {error && (
        <p className="text-xs text-danger-fg" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
