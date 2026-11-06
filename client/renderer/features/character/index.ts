export { $screenLocked, startActivityMonitor } from './activity'
export { pickAvatarImage, type PickedImage, resolvePortraitUrl } from './avatar-image'
export { $avatarSeeds, hydrateAvatarSeeds, refreshAvatarSeeds } from './avatar-seeds-store'
export {
  $companionLifecycle,
  $effectiveTier,
  $quietUntil,
  $spriteState,
  $userPreferredTier,
  type DisturbanceTier,
  endQuiet,
  ensureCompanionHydrated,
  pushEffectiveDisturbanceTier,
  QUIET_MINUTES,
  reportUserActivity,
  selectDisturbanceTier,
  setCompanionLifecycle,
  setSpriteState,
  startQuiet
} from './companion-store'
export { FullbodyReferencePanel } from './fullbody-reference-panel'
export { applyServerVoiceUpdate, onCharacterEvent } from './gateway-events'
export { GenerationActionsGroup } from './generation-actions'
export {
  assembleCharacterPersona,
  assemblePersona,
  MAX_IMAGE_DESCRIPTION,
  MAX_USER_TEXT,
  type OnboardingAnswers
} from './persona'
export {
  CHARACTER_GENDER_PRESETS,
  PERSONALITY_PRESETS,
  RELATIONSHIP_PRESETS,
  SPEAKING_STYLE_PRESETS,
  SPECIES_PRESETS,
  USER_GENDER_PRESETS,
  VOICE_PRESETS
} from './persona-presets'
export { $persona, applyPersonaResponse, hydratePersona } from './persona-store'
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
  $autoplayVoice,
  $companionVoiceId,
  $motionLevel,
  $postsEnabled,
  $responsePreference,
  applyServerCompanionVoiceId,
  autoplayVoicePref,
  initCompanionPrefsSync,
  type MotionLevel,
  postsEnabledPref,
  type ResponsePreference,
  setCompanionVoiceId,
  setMotionLevel,
  setResponsePreference
} from './prefs'
export { AccessibilityPermissionButton } from './presentation/accessibility-permission-button'
export { PresentationModeButton, usePresentationModeSwitch } from './presentation/mode-switch'
export { SelfSourceImageFlow } from './self-source-image'
export {
  $defaultScale,
  $spatialLocomotion,
  $spatialPos,
  $spatialScale,
  $spriteContentRect,
  $spriteHeadRect,
  $viewport,
  computeOverlayAnchorBesideSprite,
  initSpatial,
  resetToHomePosition,
  returnToHomePosition,
  setDefaultScale,
  syncDefaultScale
} from './spatial'
export { SPRITE_REGION_ID, SpriteStage } from './sprite-stage'
export { $contextMenuPos, closeContextMenu, openContextMenu } from './sprite/context-menu-store'
export { triggerFootGlowPulse } from './sprite/foot-glow'
export { $spritePointerHeld } from './sprite/gesture'
export { isCharacterMediaVisible, isCharacterStageVisible, observeCharacterStageVisibility } from './stage-visibility'
export { clearVfx, emitVfx } from './vfx'
export { $stageOutfit, $stageOutfitPresented, setStageOutfitSource } from './wardrobe/stage-source'
export { $windowActorMedia, $windowActorPlayback } from './window-media'
