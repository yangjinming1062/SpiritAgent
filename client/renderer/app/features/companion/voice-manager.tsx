import { useStore } from '@nanostores/react'
import { type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { $companionVoiceId, applyServerCompanionVoiceId, setCompanionVoiceId } from '@/modules/character'
import {
  $voiceCatalogRevision,
  fetchVoiceCatalogRaw,
  matchVoicePreference,
  playDataUrl,
  sampleLine,
  speakScripted,
  type VoiceCatalog,
  type VoiceDesignPreview,
  voiceSelectionId
} from '@/modules/speech'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { Check, Loader2 } from '@/shared/lib/icons'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  CreationActionBar,
  CreationFeedback,
  type CreationFeedbackState,
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
import {
  $voiceCreation,
  beginVoiceConfirmation,
  cancelVoiceCreation,
  createClonedVoice,
  createDesignedVoice,
  updateVoiceCreation,
  type VoiceTab
} from './voice-creation-store'

interface VoiceManagerProps {
  name: string
  intro?: ReactNode
  compact?: boolean
  preference?: string
  disabled?: boolean
  onVoiceSelected?: (
    voiceId: string,
    label: string,
    serverConfirmed: boolean,
    isSelectionCurrent?: () => boolean
  ) => Promise<boolean | void>
}

export function VoiceManager(props: VoiceManagerProps): React.JSX.Element {
  const auth = useStore($auth)
  const identity = auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind

  return <VoiceManagerSession key={identity} {...props} />
}

function VoiceManagerSession({
  name,
  intro,
  compact = false,
  preference,
  disabled = false,
  onVoiceSelected
}: VoiceManagerProps): React.JSX.Element {
  const dict = useStrings()
  const t = dict.voice
  const locale = useStore($locale)
  const gateway = useStore($gatewayState)
  const catalogRevision = useStore($voiceCatalogRevision)
  const currentVoice = useStore($companionVoiceId)
  const panelActive = usePanelActivity()
  const screenLocked = useStore($surfaceScreenLocked)
  const surfaceRole = useStore($surfaceRole)
  const spriteVisible = useStore($surfaceSpriteVisible)
  const [documentVisible, setDocumentVisible] = useState(document.visibilityState === 'visible')
  const active = panelActive && documentVisible && !screenLocked && (surfaceRole !== 'sprite' || spriteVisible)
  const activeRef = useRef(active)

  const viewEligible = (): boolean =>
    activeRef.current &&
    document.visibilityState === 'visible' &&
    !$surfaceScreenLocked.get() &&
    ($surfaceRole.get() !== 'sprite' || $surfaceSpriteVisible.get())

  useLayoutEffect(() => {
    activeRef.current = active

    return () => {
      activeRef.current = false
    }
  }, [active])
  const begin = useAsyncGuard()
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null)
  const [catalogStatus, setCatalogStatus] = useState<'loading' | 'error' | 'ready'>('loading')
  const [reload, setReload] = useState(0)
  const creation = useStore($voiceCreation)

  const {
    tab,
    prompt,
    feedback,
    adjusting,
    designPreview,
    clonePreview,
    phase: operationState,
    error,
    outcome,
    startedAt,
    confirmationChecked
  } = creation

  const [genderFilter, setGenderFilter] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [playbackError, setPlaybackError] = useState<string | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const previewAbort = useRef<AbortController | null>(null)
  const playbackGeneration = useRef(0)
  const operation = useRef<ReturnType<typeof beginVoiceConfirmation>>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const sample = useVoiceSample(active && tab === 'clone')
  const selectedSample = sample.sample
  const busy = operationState !== 'idle'
  const confirming = operationState === 'confirming'
  const blocked = disabled || busy || outcome === 'unknown' || sample.busy || !active
  const playbackBlocked = disabled || sample.recording || !active || operationState === 'cancelling'

  useEffect(() => {
    const onVisibility = (): void => setDocumentVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', onVisibility)

    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [])

  const stopPlayback = useCallback(() => {
    playbackGeneration.current += 1
    previewAbort.current?.abort()
    previewAbort.current = null
    setPlaying(null)
  }, [])

  useEffect(() => {
    if (!active) {
      stopPlayback()
      operation.current?.invalidate()
      operation.current = null
    }

    return () => {
      stopPlayback()
      operation.current?.invalidate()
      operation.current = null
    }
  }, [active, stopPlayback])

  useEffect(() => {
    if (!active || gateway !== 'open') {
      return
    }

    let cancelled = false
    const mounted = begin()
    setCatalogStatus(status => (status === 'ready' ? status : 'loading'))

    void (async () => {
      const [result, matched] = await Promise.all([
        fetchVoiceCatalogRaw(locale),
        preference?.trim() ? matchVoicePreference(preference, locale) : Promise.resolve(null)
      ])

      if (cancelled || !mounted()) {
        return
      }

      if (!result.ok) {
        setCatalogStatus(status => (status === 'ready' ? status : 'error'))
        updateVoiceCreation({ error: t.loadFailed })

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

      if ($voiceCreation.get().error === t.loadFailed) {
        updateVoiceCreation({ error: null })
      }

      if (!$voiceCreation.get().initializedTab) {
        const selected = next.customVoices.find(voice => voice.voice_id === $companionVoiceId.get())

        updateVoiceCreation({ initializedTab: true, ...(selected ? { tab: selected.kind } : {}) })
      }

      if (!$voiceCreation.get().initializedPrompt) {
        const saved = next.customVoices.find(voice => voice.kind === 'design')

        updateVoiceCreation({
          initializedPrompt: true,
          ...(saved ? { prompt: saved.prompt, provider: saved.provider } : {})
        })
      }
    })()

    return () => {
      cancelled = true
    }
  }, [active, gateway, locale, reload, preference, catalogRevision, t.loadFailed, begin])

  const beginPlayback = (): AbortSignal | null => {
    if (!viewEligible()) {
      return null
    }

    stopPlayback()
    const controller = new AbortController()
    previewAbort.current = controller

    return controller.signal
  }

  const playPreview = async (
    key: string,
    play: (signal: AbortSignal, live: () => boolean) => Promise<void>
  ): Promise<void> => {
    if (playing === key) {
      stopPlayback()

      return
    }

    const signal = beginPlayback()

    if (!signal) {
      return
    }

    const mounted = begin()
    const live = (): boolean => !signal.aborted && mounted() && viewEligible()
    setPlaybackError(null)
    sample.clearError()
    setPlaying(key)

    try {
      await play(signal, live)
    } catch (failure) {
      if (live()) {
        setPlaybackError(errorMessage(failure, t.previewFailed))
      }
    } finally {
      if (live()) {
        setPlaying(null)
      }
    }
  }

  const previewAudio = (url: string): Promise<void> =>
    playPreview(url, async (signal, live) => {
      const dataUrl = url.startsWith('data:') ? url : await backendApi().apiAsset({ url, preferCache: true })

      if (!dataUrl) {
        throw new Error(t.previewFailed)
      }

      if (live() && (await playDataUrl(dataUrl, { signal })) === 'failed') {
        throw new Error(t.previewFailed)
      }
    })

  const previewFixed = (id: string): Promise<void> =>
    playPreview(id, async signal => {
      await speakScripted(sampleLine(name), id, 'voice.preview', signal)
    })

  const stopOperation = async (): Promise<void> => {
    stopPlayback()
    await cancelVoiceCreation()
  }

  const runOperation = async (action: (live: () => boolean) => Promise<void>, recovering = false): Promise<void> => {
    if (disabled || sample.busy || !viewEligible() || operation.current) {
      return
    }

    const claim = beginVoiceConfirmation(recovering)

    if (!claim) {
      return
    }

    operation.current = claim
    const mounted = begin()
    const live = (): boolean => mounted() && viewEligible() && operation.current === claim && claim.live()
    stopPlayback()
    setPlaybackError(null)
    sample.clearError()

    try {
      await action(live)
    } catch (failure) {
      log.warn('voice-manager', 'Voice confirmation failed', failure)

      if (live()) {
        const definiteRejection = isClientErrorIpc(failure)
        const unknown = !definiteRejection && $voiceCreation.get().confirmation !== null
        const rejected = definiteRejection ? $voiceCreation.get().confirmation?.preview : null

        updateVoiceCreation({
          outcome: unknown ? 'unknown' : null,
          error: errorMessage(failure, t.saveFailed),
          ...(rejected
            ? {
                confirmation: null,
                confirmationChecked: false,
                ...(rejected.kind === 'design' ? { designPreview: null } : { clonePreview: null, cloneSampleId: null })
              }
            : {})
        })
      }
    } finally {
      claim.finish()

      if (operation.current === claim) {
        operation.current = null
      }
    }
  }

  const previewCreation = (create: () => Promise<VoiceDesignPreview | null>): void => {
    if (blocked) {
      return
    }

    stopPlayback()
    setPlaybackError(null)
    sample.clearError()
    const mounted = begin()
    const playbackVersion = playbackGeneration.current
    void create().then(preview => {
      if (preview && mounted() && viewEligible() && playbackVersion === playbackGeneration.current) {
        void previewAudio(preview.trialAudioDataUrl)
      }
    })
  }

  const runDesign = (mode: 'auto' | 'feedback' | 'preview'): void => {
    previewCreation(() => createDesignedVoice(mode, name, preference))
  }

  const applyVoice = async (
    id: string,
    label: string,
    serverConfirmed: boolean,
    live: () => boolean
  ): Promise<void> => {
    if (onVoiceSelected) {
      if ((await onVoiceSelected(id, label, serverConfirmed, live)) === false) {
        throw new Error(t.saveFailed)
      }
    } else if (serverConfirmed) {
      await applyServerCompanionVoiceId(id)
    } else {
      setCompanionVoiceId(id)
    }
  }

  const adoptVoice = (id: string, label: string, preview?: VoiceDesignPreview, recovering = false): void => {
    if (preview) {
      const latest = $voiceCreation.get()

      const candidate = recovering
        ? latest.confirmation?.preview
        : preview.kind === 'design'
          ? latest.designPreview
          : latest.clonePreview

      const matchingInput =
        recovering ||
        (preview.kind === 'design'
          ? latest.prompt.trim() === preview.prompt
          : Boolean(latest.cloneSampleId && latest.cloneSampleId === latest.sample?.sample_id))

      if (
        !matchingInput ||
        candidate?.candidateId !== preview.candidateId ||
        candidate.expectedVersion !== preview.expectedVersion ||
        candidate.voiceId !== id
      ) {
        return
      }
    }

    void runOperation(async live => {
      let voiceId = id

      if (preview) {
        updateVoiceCreation({ confirmation: { id, label, preview } })

        const result = await requestResource('voices.post_voice_confirmation', {
          path: { candidate_id: preview.candidateId },
          body: {
            expected_version: preview.expectedVersion
          }
        })

        if (!live()) {
          return
        }

        voiceId = result.voice_id
      }

      await applyVoice(voiceId, label, Boolean(preview), live)

      if (live()) {
        updateVoiceCreation({
          confirmation: null,
          outcome: 'saved',
          ...(preview?.kind === 'design'
            ? { designPreview: null }
            : preview?.kind === 'clone'
              ? { clonePreview: null }
              : {})
        })

        setReload(value => value + 1)
      }
    }, recovering)
  }

  const checkConfirmation = async (): Promise<void> => {
    const pending = $voiceCreation.get().confirmation

    if (!pending) {
      return
    }

    await runOperation(async live => {
      updateVoiceCreation({ confirmationChecked: false })
      const result = await fetchVoiceCatalogRaw(locale)

      if (!live()) {
        return
      }

      if (!result.ok) {
        throw new Error(t.loadFailed)
      }

      setCatalog(result.catalog)

      if (result.catalog.customVoices.some(voice => voice.voice_id === pending.id)) {
        await requestResource('voices.post_voice_confirmation', {
          path: { candidate_id: pending.preview.candidateId },
          body: { expected_version: pending.preview.expectedVersion }
        })

        if (!live()) {
          return
        }

        await applyVoice(pending.id, pending.label, true, live)

        if (live()) {
          updateVoiceCreation({ confirmation: null, designPreview: null, clonePreview: null, outcome: 'saved' })
        }
      } else {
        updateVoiceCreation({ confirmationChecked: true, outcome: 'unknown' })
      }
    }, true)
  }

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

  const chooseTab = (value: VoiceTab): void => {
    if (value === tab || confirming || operationState === 'cancelling') {
      return
    }

    stopPlayback()
    setPlaybackError(null)

    const latest = $voiceCreation.get()
    updateVoiceCreation({
      tab: value,
      initializedTab: true,
      ...(latest.phase === 'idle' && latest.outcome !== 'unknown' ? { outcome: null, error: null } : {})
    })
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

  const candidate =
    tab === 'design'
      ? designPreview?.prompt === prompt.trim()
        ? designPreview
        : null
      : tab === 'clone' && creation.cloneSampleId === selectedSample?.sample_id
        ? clonePreview
        : null

  const designStep = operationState === 'describing' || !prompt.trim() ? 0 : candidate ? 2 : 1

  const operationTitle =
    operationState === 'confirming'
      ? t.saving
      : operationState === 'cancelling'
        ? t.cancelling
        : operationState === 'describing'
          ? t.designingDescription
          : t.generatingTrial

  const status: { state: CreationFeedbackState; title: string; description?: string } | null = busy
    ? { state: 'working', title: operationTitle, description: confirming ? undefined : dict.creation.canLeave }
    : outcome === 'unknown'
      ? {
          state: 'unknown',
          title: dict.creation.unknown,
          description: creation.confirmation
            ? t.confirmUnknown
            : error === t.cancelFailed
              ? t.cancelFailed
              : t.generationUnknown
        }
      : error || (tab === 'clone' && sample.error)
        ? { state: 'error', title: error || sample.error || t.designFailed }
        : sample.busy
          ? {
              state: sample.recording ? 'waiting' : 'working',
              title: sample.recording ? t.recordingHint : t.uploadingAudio
            }
          : outcome === 'saved'
            ? null
            : candidate
              ? { state: 'review', title: t.trialReady, description: t.trialReadyHint }
              : outcome === 'cancelled'
                ? { state: 'cancelled', title: dict.creation.cancelled }
                : tab === 'clone' && selectedSample
                  ? { state: 'waiting', title: t.sampleReady, description: t.sampleReadyHint }
                  : null

  const customAvailable =
    catalogStatus === 'ready' &&
    ((tab === 'design' && catalog?.supportsVoiceDesign) || (tab === 'clone' && catalog?.supportsVoiceClone))

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
              <p className={SETTINGS_ROW_DESC}>{locale === 'en' ? 'Current voice' : '当前声音'}</p>
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
                  <details>
                    <summary className="cursor-pointer text-xs text-body">{t.designGuide}</summary>
                    <p className="mt-2 whitespace-pre-line text-xs leading-relaxed text-body">
                      {catalog.voiceDesignGuide}
                    </p>
                  </details>
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
          {(customAvailable || busy || outcome === 'unknown') &&
            (busy ? (
              <>
                <button className={BTN_PRIMARY} disabled type="button">
                  <Loader2 className="size-4 motion-safe:animate-spin" />
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
                  {error === t.cancelFailed ? t.retryCancel : t.cancelUnknown}
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
        </CreationActionBar>
      )}
    </div>
  )
}
