import type { RequestGateway } from '@/shared/voice-catalog'

import { fetchVoiceCatalogRaw, voiceSelectionId } from './voice'

type VoiceValidityResult = { valid: true } | { valid: false; name: string; reason: 'catalog_miss' }

/** 检查固定目录或已确认方案；失效音色由调用方清除。目录请求失败时保留当前选择。 */
export async function checkVoiceValidity(
  voiceId: string,
  requestGateway: RequestGateway
): Promise<VoiceValidityResult> {
  if (!voiceId) {
    return { valid: true }
  }

  const result = await fetchVoiceCatalogRaw(requestGateway)

  if (!result.ok) {
    return { valid: true }
  }

  const exact = result.catalog.voices.some(voice => voiceSelectionId(voice) === voiceId)
  const savedCustom = result.catalog.customVoices.some(voice => voice.voice_id === voiceId && voice.available === true)

  if (exact || savedCustom) {
    return { valid: true }
  }

  return { valid: false, name: voiceId, reason: 'catalog_miss' }
}

export type { VoiceValidityResult }
