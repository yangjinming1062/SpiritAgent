import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { type OnboardingAnswers, setCompanionVoiceId } from '@/modules/character'
import {
  $voicePreparing,
  fetchVoiceCatalogRaw,
  matchVoicePreference,
  sampleLine,
  speakScripted,
  stopSpeaking,
  type VoiceOption,
  voiceSelectionId
} from '@/modules/speech'
import { requestGateway } from '@/shared'
import { log } from '@/shared/lib/log'

import type { Phase, VoiceStage } from './back-transition'

interface VoiceOptions {
  phase: Phase
  voiceStage: VoiceStage
  answers: OnboardingAnswers
  begin: () => () => boolean
}

export type OnboardingVoice = ReturnType<typeof useOnboardingVoice>

// 推荐、目录和试听归音色步骤；离开或换号后迟到结果不得改选或出声。
export function useOnboardingVoice({ phase, voiceStage, answers, begin }: VoiceOptions) {
  const voicePreparing = useStore($voicePreparing)
  const [voice, setVoice] = useState<VoiceOption | null>(null)
  const [voiceLoad, setVoiceLoad] = useState<'failed' | 'loading' | 'ready'>('loading')
  const [voiceLoadAttempt, setVoiceLoadAttempt] = useState(0)
  const [voiceCatalog, setVoiceCatalog] = useState<VoiceOption[]>([])
  const [voiceAlternatives, setVoiceAlternatives] = useState<VoiceOption[]>([])

  // 进入目录步骤时加载推荐与目录并试听；离开后迟到结果作废：不改选音色、不上云、不在其他步骤出声。
  useEffect(() => {
    if (phase !== 'voice' || voiceStage !== 'catalog') {
      return
    }

    let cancelled = false
    const isLive = begin()
    stopSpeaking()
    setVoiceLoad('loading')

    void (async () => {
      const [matched, result] = await Promise.all([
        answers.voice?.trim()
          ? matchVoicePreference(requestGateway, answers.voice)
          : Promise.resolve({ ok: true as const, voice: null, alternatives: [] }),
        fetchVoiceCatalogRaw(requestGateway)
      ])

      if (cancelled || !isLive()) {
        return
      }

      // 请求失败不能当作「没有匹配」改选目录首项；保留已选音色，等待重试。
      if (!matched.ok || !result.ok) {
        if (!result.ok) {
          log.warn('onboarding', 'voice catalog request failed')
        }

        setVoiceLoad('failed')

        return
      }

      const catalog = result.catalog.voices
      const selected = matched.voice ?? catalog[0] ?? null
      setVoice(selected)
      setVoiceAlternatives(matched.alternatives)
      setCompanionVoiceId(selected ? voiceSelectionId(selected) : '')
      // matched voice 与 alternatives 已前置，需从目录剔除，否则同音色（如「茉莉」）会在列表重复。
      const priorityVoices = selected ? [selected, ...matched.alternatives] : []
      const priorityIds = new Set(priorityVoices.map(voiceSelectionId))
      const extra = catalog.filter(v => !priorityIds.has(voiceSelectionId(v)))
      setVoiceCatalog([...priorityVoices, ...extra])
      setVoiceLoad('ready')

      if (!selected) {
        return
      }

      void speakScripted(sampleLine(answers.name || ''), voiceSelectionId(selected), 'onboarding.voice.preview')
    })()

    return () => {
      cancelled = true
    }
  }, [phase, voiceStage, answers.voice, answers.name, voiceLoadAttempt, begin])

  const previewVoice = (next: VoiceOption, context: string): void =>
    void speakScripted(sampleLine(answers.name || ''), voiceSelectionId(next) || undefined, context)

  // 选中时总要试听：标签本身说明不了声音听起来什么样。
  const selectVoice = (next: VoiceOption, context: string): void => {
    setVoice(next)
    setCompanionVoiceId(voiceSelectionId(next))
    previewVoice(next, context)
  }

  const otherVoices = voice ? voiceCatalog.filter(option => voiceSelectionId(option) !== voiceSelectionId(voice)) : []
  const voiceCandidates = voice ? [voice, ...(voiceAlternatives.length ? voiceAlternatives : otherVoices)] : []

  return {
    voice,
    voicePreparing,
    voiceLoad,
    voiceCatalog,
    otherVoices,
    voiceCandidates,
    previewVoice,
    selectVoice,
    retry: () => setVoiceLoadAttempt(attempt => attempt + 1)
  }
}
