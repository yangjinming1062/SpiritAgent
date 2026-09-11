// 绑定 chat 可用的呈现端口（副作用导入，必须先于任何 chat 使用端口）。
import './presentation-bind'

export { $focusContext, $screenLocked, reportInteractionStat } from './activity'
export { isLatestGen, nextGen, playDataUrl, registerAmplitudeSink, stopAudio, warmAudioContext } from './audio-track'
export { startAutonomyProvision, stopAutonomyProvision } from './autonomy'
export {
  clearDraftRefImage,
  loadDraftRefImage,
  pickAvatarImage,
  type PickedImage,
  resolvePortraitUrl,
  saveDraftRefImage
} from './avatar-image'
export { awaitAvatarRegeneration } from './avatar-regen-store'
export {
  $clipOverride,
  $companionLifecycle,
  $effectiveTier,
  $gazeTarget,
  $spriteAction,
  $spriteActionQueue,
  $spriteEmotion,
  $spriteState,
  $userPreferredTier,
  type DisturbanceTier,
  ensureCompanionHydrated,
  playSpriteActionSequence,
  pushEffectiveDisturbanceTier,
  reportUserActivity,
  resolveCompanionRenderLayer,
  setDisturbanceTier,
  setSpriteState,
  type SpriteEmotion,
  type SpriteStateName
} from './companion-store'
export { DISTURBANCE_TIERS } from './disturbance-tiers'
export { handlePetInteraction } from './interaction'
export { MediaViewerOverlay, openMediaViewer } from './media-viewer-overlay'
export { $memoryBrowserTab, type MemoryTab, setMemoryBrowserTab } from './memory-browser-store'
export {
  assembleCharacterPersona,
  assemblePersona,
  MAX_APPEARANCE,
  MAX_USER_TEXT,
  type OnboardingAnswers
} from './persona'
export {
  APPEARANCE_PRESETS,
  CHARACTER_GENDER_PRESETS,
  PERSONALITY_PRESETS,
  type PersonalityPreset,
  RELATIONSHIP_PRESETS,
  type RelationshipPreset,
  SPEAKING_STYLE_PRESETS,
  type SpeakingStylePreset,
  SPECIES_PRESETS,
  USER_AGE_BUCKET_PRESETS,
  USER_GENDER_PRESETS,
  VOICE_PRESETS
} from './persona-presets'
export { $companionMood, $persona, hydratePersona } from './persona-store'
export {
  $activeAvatarId,
  $portraitHistory,
  $portraitSelectedIdx,
  $portraitUrl,
  $regenFeedback,
  applyPortrait,
  clearPortraitHistory,
  hydratePortrait,
  hydratePortraitHistory,
  type PortraitEntry,
  pushPortraitEntry,
  selectAvatar,
  selectPortraitEntry
} from './portrait-store'
export {
  $autonomousMedia,
  $autonomousVoice,
  $companionVoiceId,
  $llmAffect,
  $llmAutonomy,
  $llmReactions,
  $responseMode,
  autonomousMediaPref,
  autonomousVoicePref,
  initCompanionPrefsSync,
  llmAffectPref,
  llmAutonomyPref,
  llmReactionsPref,
  type ResponseMode,
  setCompanionVoiceId,
  setResponseMode
} from './prefs'
export { $renderMode, type RenderMode, setRenderMode } from './render-mode'
export {
  $defaultScale,
  $dragVelocity,
  $edgeDockSide,
  $isEdgeDocked,
  $spatialLocomotion,
  $spatialPos,
  $spriteContentRect,
  $viewport,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  type Locomotion,
  setDefaultScale
} from './spatial'
export { SpriteStatusBadge } from './sprite-status-badge'
export { $contextMenuOpen, openContextMenu } from './sprite/context-menu-store'
export { FootGlow } from './sprite/foot-glow'
export { requestSynth, speakChatMessage, speakScripted, stopSpeaking } from './tts'
export { clearVfx, emitVfx, Mesh2DVfxOverlay } from './vfx'
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
export { VoiceProviderBadge } from './voice-provider-badge'
export { $voicePreparing, beginVoicePreparing, endVoicePreparing } from './voice-state'
export { useOutfitDesignSession } from './wardrobe/design-session'
export {
  $outfitPolicy,
  $outfits,
  activateOutfit,
  deleteOutfit,
  hydrateWardrobe,
  type OutfitPolicy,
  setOutfitPolicy
} from './wardrobe/wardrobe-store'
export { probeInteractiveRegions, useInteractiveRegion } from '@/shared/lib/interactive-regions'
