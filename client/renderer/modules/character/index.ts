export {
  $actionCatalog,
  $actionCatalogStatus,
  $activePlayInstance,
  acceptPlayCommand,
  actionCatalogChanged,
  type ActionClipEntry,
  type ActionHitmask,
  type ActionPlayCommand,
  type ActionPlayInstance,
  hydrateActionCatalog,
  isActionStageVisible,
  markPlayInstanceStarted,
  type NormalizedRect,
  observeActionStageVisibility,
  reportReceipt,
  resolveActionClipUrl,
  resolveHitmask,
  settlePlayInstance,
  shouldStartInstance,
  type VideoActionWire
} from './actions'
export { $screenLocked, applyStageActivity, reportInteractionStat, startActivityMonitor } from './activity'
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
export { $avatarSeeds, hydrateAvatarSeeds, patchAvatarSeeds, refreshAvatarSeeds } from './avatar-seeds-store'
export {
  $characterCard,
  BODY_FEATURE_KEYS,
  extractCharacterCard,
  hydrateCharacterCard,
  PORTRAIT_FEATURE_KEYS,
  saveCharacterCard
} from './character-card-store'
export type { CharacterOverrides } from './character-card-store'
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
  $autoplayVoice,
  $companionVoiceId,
  $llmAffect,
  $llmAutonomy,
  $postsEnabled,
  $responsePreference,
  autoplayVoicePref,
  initCompanionPrefsSync,
  llmAffectPref,
  llmAutonomyPref,
  postsEnabledPref,
  type ResponsePreference,
  setCompanionVoiceId,
  setResponsePreference
} from './prefs'
export {
  resolveCompanionPresentation,
  resolveVideoAction,
  VIDEO_ACTION_KEYS,
  VIDEO_GEN_STAGE_TEXT_KEYS,
  type VideoActionKey,
  videoActionNames
} from './presentation'
export { bindProactiveLineSpeaker } from './proactive-speak'
export { handleDragEndInteraction } from './reactions/reaction-audio'
export { EggStage } from './rendering/fallback/egg-stage'
export {
  $videoGenError,
  $videoGenScope,
  $videoGenStage,
  $videoGenState,
  $videoPacks,
  activateVideoPack,
  generateVideoPack,
  hydrateVideoPack,
  videoGenFailed,
  videoGenProgress,
  videoGenReady,
  videoGenScopeMatches,
  videoPackEventReceived
} from './rendering/video/video-pack-store'
export { findWindowByKeyword, performRitualWalk } from './ritual-walk'
export { SelfSourceImageFlow, type SelfSourceReferenceImage } from './self-source-image'
export {
  $defaultScale,
  $expressionBoost,
  $homePosition,
  $peekPreparation,
  $spatialLocomotion,
  $spatialPeek,
  $spatialPos,
  $spatialScale,
  $spriteCanvasRect,
  $spriteContentRect,
  $spriteHeadRect,
  $viewport,
  baseSpriteSize,
  cancelMovement,
  cancelPeekPreparation,
  commitPeekPreparation,
  computeOverlayAnchorBesideSprite,
  endDragAt,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  initSpatial,
  leavePeekForExpression,
  resetToHomePosition,
  restorePeekAfterExpression,
  setDefaultScale,
  setSpatialInsets,
  setSpatialLocale,
  startDrag,
  syncDefaultScale,
  updateDragPosition
} from './spatial'
export { peekMaskRects } from './spatial-peek'
export { SpriteStatusBadge } from './sprite-status-badge'
export { $contextMenuPos, closeContextMenu, openContextMenu } from './sprite/context-menu-store'
export { FootGlow, triggerFootGlowPulse } from './sprite/foot-glow'
export { playSpriteGesture } from './sprite/gesture'
export { SpriteTargetCue, useSpriteBodyGesture } from './sprite/gesture-layer'
export { clearVfx, emitVfx, SpriteVfxOverlay } from './vfx'
export { useOutfitDesignSession } from './wardrobe/design-session'

export { $outfitPolicy, $outfits, deleteOutfit, hydrateWardrobe, setOutfitPolicy } from './wardrobe/wardrobe-store'
