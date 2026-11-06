import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { $companionVoiceId } from '@/features/character'
import {
  $voiceCatalogRevision,
  fetchVoiceCatalogRaw,
  matchVoicePreference,
  type VoiceCatalog,
  type VoiceDesignPreview,
  voiceSelectionId
} from '@/features/speech'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { $surfaceRole, $surfaceScreenLocked, $surfaceSpriteVisible } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'
import { type CreationFeedbackState } from '@/ui'

import { useVoiceSample } from '../use-voice-sample'
import { $voiceCreation, createDesignedVoice, updateVoiceCreation, type VoiceTab } from '../voice-creation-store'

import { useVoiceOperations, type VoiceSelectionHandler } from './use-voice-operations'
import { useVoicePreview } from './use-voice-preview'

export interface VoiceManagerProps {
  name: string
  intro?: ReactNode
  compact?: boolean
  preference?: string
  disabled?: boolean
  onVoiceSelected?: VoiceSelectionHandler
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
  const inputRef = useRef<HTMLInputElement>(null)
  const sample = useVoiceSample(active && tab === 'clone')
  const selectedSample = sample.sample
  const busy = operationState !== 'idle'
  const confirming = operationState === 'confirming'
  const blocked = disabled || busy || outcome === 'unknown' || sample.busy || !active
  const playbackBlocked = disabled || sample.recording || !active || operationState === 'cancelling'

  const preview = useVoicePreview({
    active,
    name,
    onBeforePlay: sample.clearError,
    viewEligible
  })

  const operations = useVoiceOperations({
    active,
    begin,
    clearPlaybackError: () => preview.setPlaybackError(null),
    clearSampleError: sample.clearError,
    disabled,
    locale,
    onCatalog: setCatalog,
    onSaved: () => setReload(value => value + 1),
    onVoiceSelected,
    sampleBusy: sample.busy,
    stopPreview: preview.stop,
    t,
    viewEligible
  })

  useEffect(() => {
    const onVisibility = (): void => setDocumentVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', onVisibility)

    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [])

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

  const runDesign = (mode: 'auto' | 'feedback' | 'preview'): void => {
    preview.previewCreation(blocked, () => createDesignedVoice(mode, name, preference))
  }

  const chooseTab = (value: VoiceTab): void => {
    if (value === tab || confirming || operationState === 'cancelling') {
      return
    }

    preview.stop()
    preview.setPlaybackError(null)

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
    playbackError: preview.playbackError,
    setPlaybackError: preview.setPlaybackError,
    playing: preview.playing,
    inputRef,
    sample,
    selectedSample,
    busy,
    confirming,
    blocked,
    playbackBlocked,
    stopPlayback: preview.stop,
    previewAudio: preview.previewAudio,
    previewFixed: preview.previewFixed,
    previewCreation: (create: () => Promise<VoiceDesignPreview | null>) => preview.previewCreation(blocked, create),
    stopOperation: operations.stopOperation,
    runDesign,
    adoptVoice: operations.adoptVoice,
    checkConfirmation: operations.checkConfirmation,
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
