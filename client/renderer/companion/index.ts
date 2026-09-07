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
export { useVoiceRecorder } from './hooks/use-voice-recorder'
export { handlePetInteraction } from './interaction'
export { probeInteractiveRegions, useInteractiveRegion } from './interactive-regions'
export { openMediaViewer } from './media-viewer-overlay'
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
export { $personaSkin, initPersonaSkin, type PersonaSkin, refreshPersonaSkin } from './persona-skin'
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
  $companionVoiceId,
  $llmAffect,
  $llmAutonomy,
  $llmReactions,
  $responseMode,
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
export { $contextMenuOpen, openContextMenu } from './sprite/context-menu-store'
export { FootGlow } from './sprite/foot-glow'
export { requestSynth, speakChatMessage, speakScripted, stopSpeaking } from './tts'
export { clearVfx, emitVfx, Mesh2DVfxOverlay } from './vfx'
export {
  designVoice,
  fetchVoiceCatalogRaw,
  GENDER_OPTIONS,
  LANGUAGE_LABELS,
  matchVoicePreference,
  nextVoice,
  sampleLine,
  type VoiceCatalog,
  type VoiceDesignPreview,
  type VoiceOption
} from './voice'
export { $voicePreparing, beginVoicePreparing, endVoicePreparing } from './voice-state'
export { useOutfitDesignSession } from './wardrobe/design-session'
export { $outfits, activateOutfit, deleteOutfit, hydrateWardrobe } from './wardrobe/wardrobe-store'
