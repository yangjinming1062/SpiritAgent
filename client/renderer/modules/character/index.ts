export { $screenLocked, reportInteractionStat, startActivityMonitor } from './activity'
export { pickAvatarImage, type PickedImage, resolvePortraitUrl } from './avatar-image'
export { $avatarSeeds, hydrateAvatarSeeds, patchAvatarSeeds, refreshAvatarSeeds } from './avatar-seeds-store'
export {
  $characterCard,
  BODY_FEATURE_KEYS,
  extractCharacterCard,
  hydrateCharacterCard,
  PORTRAIT_FEATURE_KEYS,
  saveCharacterCard
} from './character-card-store'
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
  setCompanionLifecycle,
  setDisturbanceTier,
  setSpriteState,
  startQuiet
} from './companion-store'
export { FullbodyReferencePanel } from './fullbody-reference-panel'
export { hydrateFullbodyReference } from './fullbody-reference-store'
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
export { $companionMood, $persona, applyPersonaResponse, hydratePersona } from './persona-store'
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
  $informationDiscoveryEnabled,
  $postsEnabled,
  $responsePreference,
  applyServerCompanionVoiceId,
  autoplayVoicePref,
  informationDiscoveryPref,
  initCompanionPrefsSync,
  postsEnabledPref,
  type ResponsePreference,
  setCompanionVoiceId,
  setResponsePreference
} from './prefs'
export { handleDragEndInteraction } from './reactions/reaction-audio'
export { SelfSourceImageFlow, type SelfSourceReferenceImage } from './self-source-image'
export {
  $defaultScale,
  $expressionBoost,
  $homePosition,
  $spatialLocomotion,
  $spatialPos,
  $spatialScale,
  $spriteCanvasRect,
  $spriteContentRect,
  $spriteHeadRect,
  $viewport,
  baseSpriteSize,
  cancelMovement,
  computeOverlayAnchorBesideSprite,
  endDragAt,
  EXPRESSION_BOOST_SCALE,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  initSpatial,
  resetToHomePosition,
  returnToHomePosition,
  setDefaultScale,
  startDrag,
  syncDefaultScale,
  updateDragPosition
} from './spatial'
export { $contextMenuPos, closeContextMenu, openContextMenu } from './sprite/context-menu-store'
export { FootGlow, triggerFootGlowPulse } from './sprite/foot-glow'
export { playSpriteGesture } from './sprite/gesture'
export { useSpriteBodyGesture } from './sprite/gesture-layer'
export { isCharacterStageVisible, observeCharacterStageVisibility } from './stage-visibility'
export { clearVfx, emitVfx, SpriteVfxOverlay } from './vfx'
export { useOutfitDesignSession } from './wardrobe/design-session'

export {
  $stageOutfit,
  $stageOutfitPresented,
  $stageOutfitStatus,
  retryStageOutfit,
  setStageOutfitSource
} from './wardrobe/stage-source'
export type { StageOutfitSource } from './wardrobe/stage-source'
export {
  $outfitPolicy,
  $outfits,
  $wardrobeStatus,
  deleteOutfit,
  hydrateWardrobe,
  retryOutfitDescription,
  setOutfitPolicy
} from './wardrobe/wardrobe-store'
export type { CharacterOverrides } from '@protocol'
