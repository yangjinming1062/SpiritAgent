import type { RequestGateway } from '@/shared/voice-catalog'

import { $companionVoiceId, setCompanionVoiceId } from './prefs'
import { fetchVoiceCatalogRaw, isCustomVoiceSelectionId, voiceSelectionId, voiceSelectionProvider } from './voice'

type VoiceValidityResult = { valid: true } | { valid: false; name: string; reason: 'catalog_miss' }

export async function checkCompanionVoiceValidity(requestGateway: RequestGateway): Promise<VoiceValidityResult> {
  const id = $companionVoiceId.get()

  if (!id) {
    return { valid: true }
  }

  const result = await fetchVoiceCatalogRaw(requestGateway)

  if (!result.ok) {
    return { valid: true }
  }

  const exact = result.catalog.voices.find(v => voiceSelectionId(v) === id)

  const customProvider = isCustomVoiceSelectionId(id) ? voiceSelectionProvider(id) : ''
  const configuredCustom = customProvider && result.catalog.providers.includes(customProvider)

  if (exact || configuredCustom) {
    return { valid: true }
  }

  // 清除过期的 id，使下次 speak() 不再带 voice 参数
  setCompanionVoiceId('')

  return { valid: false, name: id, reason: 'catalog_miss' }
}
