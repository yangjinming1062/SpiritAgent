export { $focusContext, $screenLocked, reportInteractionStat, startActivityMonitor } from './activity'
export { startAutonomyProvision, stopAutonomyProvision } from './autonomy'
export {
  clearDraftRefImage,
  loadDraftRefImage,
  pickAvatarImage,
  type PickedImage,
  resolvePortraitUrl,
  saveDraftRefImage
} from './avatar-image'
export { awaitAvatarRegeneration, resolveAvatarRegeneration } from './avatar-regen-store'
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
  setCompanionLifecycle,
  setDisturbanceTier,
  setSpriteState,
  type SpriteEmotion,
  type SpriteStateName
} from './companion-store'
export { DISTURBANCE_TIERS } from './disturbance-tiers'
export { handleDragEndInteraction, handlePetInteraction, handlePokeInteraction, normalizeRegion } from './interaction'
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
export { bindProactiveLineSpeaker, type ProactiveLineOptions } from './proactive-speak'
export { $renderMode, type RenderMode, setRenderMode } from './render-mode'
export { findWindowByKeyword, performRitualWalk, type WindowGeom } from './ritual-walk'
export {
  $defaultScale,
  $dragVelocity,
  $edgeDockSide,
  $homePosition,
  $isEdgeDocked,
  $spatialLocomotion,
  $spatialPos,
  $spatialScale,
  $spriteContentRect,
  $viewport,
  cancelMovement,
  computeOverlayAnchorBesideSprite,
  endDragAt,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  initSpatial,
  type Locomotion,
  resetToHomePosition,
  setDefaultScale,
  setLocale,
  startDrag,
  undockFromEdge,
  updateDragPosition
} from './spatial'
export { SpriteStatusBadge } from './sprite-status-badge'
export { $contextMenuOpen, $contextMenuPos, closeContextMenu, openContextMenu } from './sprite/context-menu-store'
export { FootGlow, triggerFootGlowPulse } from './sprite/foot-glow'
export { clearVfx, emitVfx, Mesh2DVfxOverlay } from './vfx'
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
