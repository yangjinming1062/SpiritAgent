import { atom } from 'nanostores'

import { currentDayPhase, type DayPhase } from '@/shared/lib/day-phase'

import type { ApiSchemas } from '../../../../../shared/api.generated'

import { type CatalogEntry, motionAllowed } from './selection'
import type { VisualPlayCommand, VisualsPresentation } from './types'

type LayerMedia = ApiSchemas['StageLayerMedia']
type StageLayout = ApiSchemas['StageCompositionLayout']
type SceneReference = ApiSchemas['SceneMediaRef']

interface StageActorPlan {
  visualId: string
  version: number
  url: string
  posterUrl: string | null
  width: number
  height: number
  loop: boolean
  body: ApiSchemas['ActorClipBody'] | null
}

export interface StageScenePlan {
  visualId: string | null
  version: number | null
  url: string
  posterUrl: string | null
  isVideo: boolean
  phaseMatched: boolean
}

interface LocalStagePlay {
  id: string
  visualId: string
  version: number
  target: 'actor' | 'background'
  expiresAt: string | null
}

export interface StagePlan {
  actor: StageActorPlan | null
  actorStillUrl: string | null
  scene: StageScenePlan | null
  cinematic: LayerMedia | null
  layout: StageLayout
  dayPhase: DayPhase
  localPlay: LocalStagePlay | null
}

export interface AmbientChoice {
  id: string
  visualId: string
  version: number
}

export interface WakePerformance {
  id: string
  expiresAt: string
  actor: CatalogEntry
  scene: StageScenePlan | null
}

interface StageOptions {
  desktop?: boolean
  subtle?: boolean
  motionEnabled?: boolean
  ambient?: AmbientChoice | null
  wake?: WakePerformance | null
  failedMedia?: ReadonlySet<string>
}

const DEFAULT_LAYOUT: StageLayout = {
  layer_order: ['scene', 'actor', 'effects'],
  fit: 'contain',
  actor_position: [0.5, 1],
  safe_area: [0, 0, 1, 1]
}

export const $stagePlan = atom<StagePlan>(deriveStagePlan(null, null))

export function mediaKey(entry: Pick<CatalogEntry, 'visual_id' | 'version'>): string {
  return `${entry.visual_id}:${entry.version}`
}

export function pendingStaticPresentation(presentation: VisualsPresentation | null) {
  const target = presentation?.static_presentation

  return target &&
    !presentation?.pending_play &&
    target.receipt_kind === null &&
    (!target.retry_at || Date.parse(target.retry_at) <= Date.now())
    ? target
    : null
}

export function ambientCandidates(
  presentation: VisualsPresentation,
  desktop: boolean,
  subtle: boolean,
  failed?: ReadonlySet<string>
): CatalogEntry[] {
  const plan = presentation.stage_plan

  if (!plan || !presentation.autonomous_enabled) {
    return []
  }

  const usable = (entry: CatalogEntry): boolean => !failed?.has(mediaKey(entry)) && motionAllowed(entry, subtle)
  const actors = plan.actor_variants.filter(usable)
  const cinematic = desktop ? plan.cinematic_variants.filter(usable) : []
  const wallpaper = presentation.default_wallpaper?.composition.media?.cinematic

  const useFavorite =
    desktop &&
    wallpaper &&
    !failed?.has(mediaKey(wallpaper)) &&
    phaseAllowed(wallpaper, plan.day_phase) &&
    !needsCharacter(presentation)

  if (useFavorite && plan.activity_type === 'idle' && cinematic.length === 0) {
    return []
  }

  const matched = [...actors, ...cinematic]

  if (matched.length > 0) {
    return matched
  }

  if (useFavorite) {
    return []
  }

  return plan.fallback_actor_variants.filter(usable)
}

function phaseAllowed(media: LayerMedia, phase: DayPhase): boolean {
  return media.day_phase === null || media.day_phase === phase
}

function needsCharacter(presentation: VisualsPresentation | null): boolean {
  return presentation?.rhythm?.state === 'asleep' || presentation?.rhythm?.interacting === true
}

export function sceneForStage(
  media: LayerMedia | null | undefined,
  material: SceneReference | null | undefined,
  phase: DayPhase
): StageScenePlan | null {
  if (media?.asset_kind === 'scene' && phaseAllowed(media, phase)) {
    return {
      visualId: media.visual_id,
      version: media.version,
      url: media.media_url,
      posterUrl: media.poster_url ?? material?.path ?? null,
      isVideo: media.media_kind === 'video',
      phaseMatched: media.day_phase === phase
    }
  }

  return material
    ? { visualId: null, version: null, url: material.path, posterUrl: null, isVideo: false, phaseMatched: false }
    : null
}

function actorFromEntry(entry: LayerMedia | null | undefined): StageActorPlan | null {
  if (!entry || entry.asset_kind !== 'actor' || entry.media_kind !== 'video') {
    return null
  }

  return {
    visualId: entry.visual_id,
    version: entry.version,
    url: entry.media_url,
    posterUrl: entry.body?.first_frame_path ?? entry.poster_url ?? null,
    width: entry.width,
    height: entry.height,
    loop: entry.clip_kind !== 'once',
    body: entry.body ?? null
  }
}

/** 以服务端候选为基准，正在播放的点播和新鲜起身事件才临时覆盖。 */
export function deriveStagePlan(
  presentation: VisualsPresentation | null,
  command: VisualPlayCommand | null,
  { desktop = false, subtle = false, motionEnabled = true, ambient = null, wake = null, failedMedia }: StageOptions = {}
): StagePlan {
  const target = presentation?.stage_plan
  const dayPhase = target?.day_phase ?? currentDayPhase()
  const composition = presentation?.composition
  const media = composition?.media
  const staticTarget = presentation?.static_presentation
  const fallbackMaterial = staticTarget?.scene ?? composition?.scene_material
  const wallpaper = desktop ? presentation?.default_wallpaper?.composition : null
  const favorite = wallpaper?.media
  const enabled = presentation?.autonomous_enabled === true
  const showCharacter = enabled && needsCharacter(presentation)

  const favoriteCinematic =
    favorite?.cinematic && (!enabled || phaseAllowed(favorite.cinematic, dayPhase)) ? favorite.cinematic : null

  const candidates = presentation ? ambientCandidates(presentation, desktop, subtle, failedMedia) : []
  const selected = candidates.find(entry => ambient && mediaKey(entry) === `${ambient.visualId}:${ambient.version}`)
  const choice = selected ?? candidates[0]

  const regularActor = motionEnabled
    ? actorFromEntry(choice?.asset_kind === 'actor' ? choice : enabled ? null : media?.actor)
    : null

  const scene = sceneForStage(target?.scene_media, target?.scene ?? fallbackMaterial, dayPhase)
  const sceneFallback = sceneForStage(favorite?.scene, wallpaper?.scene_material, dayPhase)
  const committedScene = sceneForStage(media?.scene, fallbackMaterial, dayPhase)
  const matchesActivity = Boolean(choice && target?.actor_variants.includes(choice))

  const regularScene =
    enabled && (matchesActivity || showCharacter)
      ? (scene ?? sceneFallback)
      : (sceneFallback ?? (enabled ? scene : committedScene))

  // 整片自带环境和角色，只有该版本实际可用时才接管背景。
  const desiredCinematic = desktop
    ? choice?.asset_kind === 'cinematic'
      ? choice
      : !(showCharacter || choice)
        ? enabled
          ? favoriteCinematic
          : (media?.cinematic ?? favoriteCinematic)
        : null
    : null

  const cinematic = desiredCinematic && !failedMedia?.has(mediaKey(desiredCinematic)) ? desiredCinematic : null

  const layout = cinematic
    ? ((cinematic === favorite?.cinematic
        ? wallpaper?.layout
        : composition?.mode === 'cinematic'
          ? composition.layout
          : null) ?? DEFAULT_LAYOUT)
    : ((composition?.mode === 'layered' ? composition.layout : null) ?? DEFAULT_LAYOUT)

  const actorStillUrl = enabled ? (actorFromEntry(choice)?.posterUrl ?? target?.actor_still_url ?? null) : null

  const base: StagePlan = {
    actor: regularActor,
    actorStillUrl,
    scene: regularScene,
    cinematic,
    layout,
    dayPhase,
    localPlay: null
  }

  if (command?.asset_kind === 'actor' && command.media_kind === 'video') {
    return {
      ...base,
      actor: {
        visualId: command.visual_id,
        version: command.visual_version,
        url: command.media_path,
        posterUrl: command.body?.first_frame_path ?? command.poster_path ?? null,
        width: 0,
        height: 0,
        loop: command.loop,
        body: command.body ?? null
      },
      cinematic: null,
      scene: regularScene ?? committedScene
    }
  }

  if (command?.asset_kind === 'scene') {
    return {
      ...base,
      cinematic: null,
      scene: {
        visualId: command.visual_id,
        version: command.visual_version,
        url: command.media_path,
        posterUrl: command.poster_path ?? null,
        isVideo: command.media_kind === 'video',
        phaseMatched: command.day_phase === dayPhase
      }
    }
  }

  if (command?.asset_kind === 'cinematic') {
    return { ...base, actor: null }
  }

  if (wake && motionEnabled) {
    return {
      ...base,
      actor: actorFromEntry(wake.actor),
      actorStillUrl: actorStillUrl ?? wake.actor.body?.last_frame_path ?? wake.actor.body?.first_frame_path ?? null,
      scene: wake.scene ?? scene,
      cinematic: null,
      localPlay: {
        id: wake.id,
        visualId: wake.actor.visual_id,
        version: wake.actor.version,
        target: 'actor',
        expiresAt: wake.expiresAt
      }
    }
  }

  if (selected && choice && ambient && motionEnabled && (cinematic || regularActor)) {
    base.localPlay = {
      id: ambient.id,
      visualId: choice.visual_id,
      version: choice.version,
      target: cinematic ? 'background' : 'actor',
      expiresAt: null
    }
  }

  return base
}
