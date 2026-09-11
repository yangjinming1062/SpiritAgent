export { isLatestGen, nextGen, playDataUrl, registerAmplitudeSink, stopAudio, warmAudioContext } from './audio-track'
export { requestSynth, speak, speakChatMessage, speakScripted, stopSpeaking } from './tts'
export {
  designVoice,
  fetchVoiceCatalogRaw,
  GENDER_OPTIONS,
  isCustomVoiceSelectionId,
  matchVoicePreference,
  nextVoice,
  sampleLine,
  type VoiceCatalog,
  type VoiceDesignPreview,
  type VoiceOption,
  voiceProviderLabel,
  voiceSelectionId,
  voiceSelectionProvider
} from './voice'
export {
  bindVoiceBarListeners,
  bindVoiceBarProjection,
  cancelVoiceBar,
  estimateVoiceDuration,
  getCachedVoiceDuration,
  isLivingVoiceBarActive,
  resolveVoiceBarDuration,
  setCachedVoiceDuration,
  synthesizeVoiceBar,
  toggleVoiceBar,
  TTS_MAX_TEXT_CHARS,
  type VoiceBarProjection
} from './voice-bar'
export { VoiceProviderBadge } from './voice-provider-badge'
export { $voicePreparing, beginVoicePreparing, endVoicePreparing } from './voice-state'
export { checkVoiceValidity, type VoiceValidityResult } from './voice-validity'
