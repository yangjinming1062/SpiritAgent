import { atom } from 'nanostores'

import { currentDayPhase, type DayPhase } from '@/shared/lib/day-phase'

import type { ApiSchemas } from '../../../../../shared/api.generated'

import { type CatalogEntry, motionAllowed } from './selection'
import {
  commandTargetsSurface,
  type VisualPlayCommand,
  type VisualsPresentation,
  type VisualTargetSurface
} from './types'

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
  fallbackScene: SceneReference | null
  sceneContentHash: string | null
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
  cinematicMotion: boolean
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
  clip: CatalogEntry
  scene: StageScenePlan | null
}

interface StageOptions {
  surface?: VisualTargetSurface
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
  subtle: boolean,
  failed?: ReadonlySet<string>,
  surface: VisualTargetSurface = 'sprite'
): CatalogEntry[] {
  const plan = presentation.stage_plan

  if (!plan || !presentation.autonomous_enabled) {
    return []
  }

  const usable = (entry: CatalogEntry): boolean => !failed?.has(mediaKey(entry)) && motionAllowed(entry, subtle)

  if (surface !== 'sprite') {
    return plan.cinematic_variants.filter(
      entry =>
        entry.asset_kind === 'cinematic' && entry.media_kind === 'video' && entry.clip_kind === 'loop' && usable(entry)
    )
  }

  const actors = plan.actor_variants.filter(usable)

  return actors.length > 0 ? actors : plan.fallback_actor_variants.filter(usable)
}

function phaseAllowed(media: LayerMedia, phase: DayPhase): boolean {
  return media.day_phase === null || media.day_phase === phase
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
      fallbackScene: media.poster_url ? null : (material ?? null),
      sceneContentHash: media.scene_content_hash,
      isVideo: media.media_kind === 'video',
      phaseMatched: media.day_phase === phase
    }
  }

  return material
    ? {
        visualId: null,
        version: null,
        url: material.path,
        posterUrl: null,
        fallbackScene: material,
        sceneContentHash: material.content_hash,
        isVideo: false,
        phaseMatched: false
      }
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
  {
    surface = 'sprite',
    subtle = false,
    motionEnabled = true,
    ambient = null,
    wake = null,
    failedMedia
  }: StageOptions = {}
): StagePlan {
  const target = presentation?.stage_plan
  const dayPhase = target?.day_phase ?? currentDayPhase()
  const composition = presentation?.composition
  const media = composition?.media
  const staticTarget = presentation?.static_presentation
  const fallbackMaterial = staticTarget?.scene ?? composition?.scene_material
  const full = surface !== 'sprite'
  const wallpaper = surface === 'desktop' ? presentation?.default_wallpaper?.composition : null
  const favorite = wallpaper?.media
  const enabled = presentation?.autonomous_enabled === true
  const candidates = presentation && !full ? ambientCandidates(presentation, subtle, failedMedia) : []
  const selected = candidates.find(entry => ambient && mediaKey(entry) === `${ambient.visualId}:${ambient.version}`)
  const choice = selected ?? candidates[0]

  const regularActor =
    !full && motionEnabled
      ? actorFromEntry(choice?.asset_kind === 'actor' ? choice : enabled ? null : media?.actor)
      : null

  const isUsable = (entry: LayerMedia | null | undefined): entry is LayerMedia =>
    Boolean(entry && !failedMedia?.has(mediaKey(entry)))

  const scene = sceneForStage(
    isUsable(target?.scene_media) ? target.scene_media : null,
    target?.scene ?? fallbackMaterial,
    dayPhase
  )

  const sceneFallback = sceneForStage(
    isUsable(favorite?.scene) ? favorite.scene : null,
    wallpaper?.scene_material,
    dayPhase
  )

  const committedScene = sceneForStage(isUsable(media?.scene) ? media.scene : null, fallbackMaterial, dayPhase)
  const regularScene = enabled ? (sceneFallback ?? scene) : (committedScene ?? sceneFallback ?? scene)
  const fullCandidates = full && enabled ? (target?.cinematic_variants.filter(isUsable) ?? []) : []
  const fullStill = fullCandidates.find(entry => entry.media_kind === 'image')

  const selectedLoop =
    full && presentation && ambient
      ? ambientCandidates(presentation, subtle, failedMedia, surface).find(
          entry => entry.visual_id === ambient.visualId && entry.version === ambient.version
        )
      : null

  const preferredFull = motionEnabled
    ? (selectedLoop ??
      fullCandidates.find(entry => entry.media_kind === 'image' || motionAllowed(entry, subtle)) ??
      fullCandidates.find(entry => entry.poster_url))
    : (fullStill ?? fullCandidates.find(entry => entry.poster_url))

  const favoriteCinematic =
    isUsable(favorite?.cinematic) && (!enabled || phaseAllowed(favorite.cinematic, dayPhase))
      ? favorite.cinematic
      : null

  const cinematic = full
    ? (preferredFull ?? (!enabled && isUsable(media?.cinematic) ? media.cinematic : favoriteCinematic) ?? null)
    : null

  const layout = cinematic
    ? ((cinematic === favorite?.cinematic
        ? wallpaper?.layout
        : composition?.mode === 'cinematic'
          ? composition.layout
          : null) ?? DEFAULT_LAYOUT)
    : ((composition?.mode === 'layered' ? composition.layout : null) ?? DEFAULT_LAYOUT)

  const actorStillUrl = enabled && !full ? (actorFromEntry(choice)?.posterUrl ?? target?.actor_still_url ?? null) : null

  const base: StagePlan = {
    actor: regularActor,
    actorStillUrl,
    scene: regularScene,
    cinematic,
    cinematicMotion:
      motionEnabled && (cinematic !== preferredFull || !preferredFull || motionAllowed(preferredFull, subtle)),
    layout,
    dayPhase,
    localPlay: null
  }

  if (command && !commandTargetsSurface(command, surface)) {
    command = null
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
        fallbackScene: null,
        sceneContentHash: command.scene?.content_hash ?? null,
        isVideo: command.media_kind === 'video',
        phaseMatched: command.day_phase === dayPhase
      }
    }
  }

  if (command?.asset_kind === 'cinematic') {
    return { ...base, actor: null }
  }

  if (wake && motionEnabled && wake.clip.asset_kind === (full ? 'cinematic' : 'actor')) {
    if (full) {
      return {
        ...base,
        cinematic: wake.clip,
        cinematicMotion: true,
        localPlay: {
          id: wake.id,
          visualId: wake.clip.visual_id,
          version: wake.clip.version,
          target: 'background',
          expiresAt: wake.expiresAt
        }
      }
    }

    return {
      ...base,
      actor: actorFromEntry(wake.clip),
      actorStillUrl: actorStillUrl ?? wake.clip.body?.last_frame_path ?? wake.clip.body?.first_frame_path ?? null,
      scene: wake.scene ?? scene,
      cinematic: null,
      localPlay: {
        id: wake.id,
        visualId: wake.clip.visual_id,
        version: wake.clip.version,
        target: 'actor',
        expiresAt: wake.expiresAt
      }
    }
  }

  if (selected && choice && ambient && motionEnabled && regularActor) {
    base.localPlay = {
      id: ambient.id,
      visualId: choice.visual_id,
      version: choice.version,
      target: 'actor',
      expiresAt: null
    }
  }

  return base
}
