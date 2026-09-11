import { $locale } from '@/shared/store/locale'
import type { RequestGateway, VoiceOption } from '@/shared/voice-catalog'
import { customVoiceSelectionId, voiceSelectionId } from '@/shared/voice-catalog'

// 音色目录、匹配与设计由后端 `tts.*` JSON-RPC 方法支撑。目录项携带供应商，
// 持久化与合成使用 voiceSelectionId 生成的唯一引用，避免供应商私有 id 冲突。

export type { VoiceOption } from '@/shared/voice-catalog'
export {
  GENDER_OPTIONS,
  isCustomVoiceSelectionId,
  voiceProviderLabel,
  voiceSelectionId,
  voiceSelectionProvider
} from '@/shared/voice-catalog'

interface VoiceMatch {
  voice: VoiceOption | null
  alternatives: VoiceOption[]
}

export interface VoiceCatalog {
  providers: string[]
  voices: VoiceOption[]
  supportsVoiceDesign: boolean
  voiceDesignGuide: string
}

export interface VoiceDesignPreview {
  voiceId: string
  trialAudioDataUrl: string
}

interface CatalogResponse {
  providers: string[]
  voices: VoiceOption[]
  supports_voice_design: boolean
  voice_design_guide: string
}

interface MatchResponse {
  voice: VoiceOption | null
  alternatives: VoiceOption[]
}

interface DesignResponse {
  provider: string
  voice_id: string
  trial_audio_base64: string
  trial_audio_mime: string
}

type FetchResult = { ok: true; catalog: VoiceCatalog } | { ok: false; reason: 'fetch_failed' }

export async function fetchVoiceCatalogRaw(
  requestGateway: RequestGateway,
  language: string | null = $locale.get()
): Promise<FetchResult> {
  try {
    const res = await requestGateway<CatalogResponse>('tts.list_voices', {
      language: language ?? null
    })

    return {
      ok: true,
      catalog: {
        providers: res.providers,
        voices: res.voices,
        supportsVoiceDesign: res.supports_voice_design,
        voiceDesignGuide: res.voice_design_guide
      }
    }
  } catch {
    return { ok: false, reason: 'fetch_failed' }
  }
}

export async function matchVoicePreference(
  requestGateway: RequestGateway,
  preference: string,
  language: string | null = $locale.get()
): Promise<VoiceMatch> {
  try {
    const res = await requestGateway<MatchResponse>('tts.match_voice', { language, preference })

    return { voice: res.voice, alternatives: res.alternatives }
  } catch {
    return { voice: null, alternatives: [] }
  }
}

export async function designVoice(
  requestGateway: RequestGateway,
  prompt: string,
  previewText = ''
): Promise<VoiceDesignPreview> {
  const res = await requestGateway<DesignResponse>('tts.design_voice', { prompt, preview_text: previewText })

  return {
    voiceId: customVoiceSelectionId(res.provider, res.voice_id),
    trialAudioDataUrl: `data:${res.trial_audio_mime};base64,${res.trial_audio_base64}`
  }
}

export function nextVoice(currentId: string, catalog: readonly VoiceOption[]): VoiceOption | null {
  if (catalog.length <= 1) {
    return catalog[0] ?? null
  }

  const idx = catalog.findIndex(v => voiceSelectionId(v) === currentId)

  return catalog[(idx + 1) % catalog.length] ?? catalog[0]
}

export function sampleLine(name: string, language = $locale.get()): string {
  return language === 'en'
    ? `Hi, I'm ${name || 'your companion'}. This is my voice.`
    : `你好呀，我是${name || ''}。这是我的声音～`
}
