import { useStore } from '@nanostores/react'
import { type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { $companionVoiceId, applyServerCompanionVoiceId, setCompanionVoiceId } from '@/features/character'
import { playDataUrl } from '@/features/speech'
import { speakScripted } from '@/features/speech'
import {
  $voiceCatalogRevision,
  fetchVoiceCatalogRaw,
  matchVoicePreference,
  sampleLine,
  type VoiceCatalog,
  type VoiceDesignPreview,
  voiceSelectionId
} from '@/features/speech'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { failureText, isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { backendApi } from '@/shared/platform/desktop'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { $surfaceRole, $surfaceScreenLocked, $surfaceSpriteVisible } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'
import {
  type CreationFeedbackState
} from '@/ui'

import { useVoiceSample } from '../use-voice-sample'
import {
  $voiceCreation,
  beginVoiceConfirmation,
  cancelVoiceCreation,
  createDesignedVoice,
  updateVoiceCreation,
  type VoiceTab
} from '../voice-creation-store'

export interface VoiceManagerProps {
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

export function useVoiceManager({
  compact = false,
  disabled = false,
  name,
  onVoiceSelected,
  preference
}: VoiceManagerProps) {
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
        updateVoiceCreation({ error: t.loadFailed, errorCode: 'catalog' })

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

      if ($voiceCreation.get().errorCode === 'catalog') {
        updateVoiceCreation({ error: null, errorCode: null })
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
        setPlaybackError(failureText(failure, t.previewFailed))
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
          error: failureText(failure, t.saveFailed),
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

  const status: { state: CreationFeedbackState; title?: string; description?: string } | null = busy
    ? { state: 'working', description: confirming ? undefined : dict.creation.canLeave }
    : outcome === 'unknown'
      ? {
          state: 'unknown',
          title: dict.creation.unknown,
          description: creation.confirmation
            ? t.confirmUnknown
            : creation.errorCode === 'cancel'
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

  return {
    dict,
    t,
    locale,
    gateway,
    catalogRevision,
    currentVoice,
    panelActive,
    screenLocked,
    surfaceRole,
    spriteVisible,
    documentVisible,
    active,
    activeRef,
    viewEligible,
    begin,
    catalog,
    catalogStatus,
    reload,
    setReload,
    creation,
    tab,
    prompt,
    feedback,
    adjusting,
    designPreview,
    clonePreview,
    operationState,
    error,
    outcome,
    startedAt,
    confirmationChecked,
    genderFilter,
    setGenderFilter,
    showAll,
    setShowAll,
    playbackError,
    setPlaybackError,
    playing,
    previewAbort,
    playbackGeneration,
    operation,
    inputRef,
    sample,
    selectedSample,
    busy,
    confirming,
    blocked,
    playbackBlocked,
    stopPlayback,
    beginPlayback,
    playPreview,
    previewAudio,
    previewFixed,
    stopOperation,
    runOperation,
    previewCreation,
    runDesign,
    applyVoice,
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
  }
}
