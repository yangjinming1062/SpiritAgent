import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { $locale } from '@/shared/store/locale'
import { getStrings } from '@/shared/strings'
import type { VoiceOption } from '@/shared/voice-catalog'

import type { ApiSchemas } from '../../../../shared/api.generated'

// 音色目录、匹配与设计由后端声音 REST 资源支撑。目录项携带供应商，持久化与合成使用 voiceSelectionId 生成的唯一引用，避免供应商私有 id 冲突。

export type { VoiceOption } from '@/shared/voice-catalog'
export { voiceProviderLabel, voiceSelectionId } from '@/shared/voice-catalog'

// 请求失败与「没有匹配」分开报告，调用方不能把失败当作无匹配而改选默认音色。
type VoiceMatch = { ok: true; voice: VoiceOption | null; alternatives: VoiceOption[] } | { ok: false; error: unknown }

export interface VoiceCatalog {
  providers: string[]
  voices: VoiceOption[]
  supportsVoiceDesign: boolean
  voiceDesignGuide: string
  supportsVoicePromptGeneration: boolean
  supportsVoiceClone: boolean
  customVoices: CustomVoiceSummary[]
}

export type CustomVoiceSummary = ApiSchemas['CustomVoiceSummary']

export interface VoiceDesignPreview {
  candidateId: string
  voiceId: string
  trialAudioDataUrl: string
  expectedVersion: number
  kind: 'design' | 'clone'
  prompt: string
  provider: string
}

type DesignResponse = ApiSchemas['VoicePreviewResponse']

type FetchResult = { ok: true; catalog: VoiceCatalog } | { ok: false; reason: 'fetch_failed' }

export const $voiceCatalogRevision = atom(0)

export function invalidateVoiceCatalog(): void {
  $voiceCatalogRevision.set($voiceCatalogRevision.get() + 1)
}

export async function fetchVoiceCatalogRaw(language: string | null = $locale.get()): Promise<FetchResult> {
  try {
    const res = await requestResource('GET /api/voices', { query: { language } })

    return {
      ok: true,
      catalog: {
        providers: res.providers,
        voices: res.voices,
        supportsVoiceDesign: res.supports_voice_design,
        voiceDesignGuide: res.voice_design_guide,
        supportsVoicePromptGeneration: res.supports_voice_prompt_generation,
        supportsVoiceClone: res.supports_voice_clone,
        customVoices: res.custom_voices
      }
    }
  } catch {
    return { ok: false, reason: 'fetch_failed' }
  }
}

export async function matchVoicePreference(
  preference: string,
  language: string | null = $locale.get()
): Promise<VoiceMatch> {
  try {
    const res = await requestResource('POST /api/voices/match', { body: { language, preference } })

    return { ok: true, voice: res.voice, alternatives: res.alternatives }
  } catch (error) {
    log.warn('voice', 'Voice match failed', error)

    return { ok: false, error }
  }
}

export async function designVoice(
  prompt: string,
  previewText: string,
  operationId: string,
  provider?: string
): Promise<VoiceDesignPreview> {
  const res = await requestResource('POST /api/voices/candidates', {
    body: {
      kind: 'design',
      operation_id: operationId,
      prompt,
      preview_text: previewText,
      provider
    }
  })

  return voicePreview(res)
}

export async function cloneVoice(
  sampleId: string,
  previewText: string,
  operationId: string
): Promise<VoiceDesignPreview> {
  const res = await requestResource('POST /api/voices/candidates', {
    body: {
      kind: 'clone',
      operation_id: operationId,
      sample_id: sampleId,
      preview_text: previewText
    }
  })

  return voicePreview(res)
}

function voicePreview(res: DesignResponse): VoiceDesignPreview {
  return {
    candidateId: res.candidate_id,
    voiceId: res.voice_id,
    trialAudioDataUrl: `data:${res.trial_audio_mime};base64,${res.trial_audio_base64}`,
    expectedVersion: res.expected_version,
    kind: res.kind,
    prompt: res.prompt,
    provider: res.provider
  }
}

export function sampleLine(name: string): string {
  return getStrings().voice.sampleLine(name.trim())
}
