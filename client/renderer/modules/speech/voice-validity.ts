import type { RequestGateway } from '@/shared/voice-catalog'

import { fetchVoiceCatalogRaw, isCustomVoiceSelectionId, voiceSelectionId, voiceSelectionProvider } from './voice'

type VoiceValidityResult = { valid: true } | { valid: false; name: string; reason: 'catalog_miss' }

/** 校验音色 id 是否仍在云端目录中（供应商裁剪 / 改名 / 换源）。
 *  纯检查：过期 id 的清除由调用方写回偏好，语音模块不持有音色偏好状态。 */
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

  const exact = result.catalog.voices.find(v => voiceSelectionId(v) === voiceId)

  const customProvider = isCustomVoiceSelectionId(voiceId) ? voiceSelectionProvider(voiceId) : ''
  const configuredCustom = customProvider && result.catalog.providers.includes(customProvider)

  if (exact || configuredCustom) {
    return { valid: true }
  }

  return { valid: false, name: voiceId, reason: 'catalog_miss' }
}

export type { VoiceValidityResult }
