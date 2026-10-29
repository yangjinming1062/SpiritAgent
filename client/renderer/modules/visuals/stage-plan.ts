import { atom } from 'nanostores'

import { currentDayPhase, type DayPhase } from '@/shared/lib/day-phase'

import type { ApiSchemas } from '../../../../shared/api.generated'

import { automaticLoops, contextAllowed, motionAllowed } from './selection'

type LayerMedia = ApiSchemas['StageLayerMedia']
type ActorBody = ApiSchemas['ActorClipBody']
export type StageLayout = ApiSchemas['StageCompositionLayout']
type SceneReference = ApiSchemas['SceneMediaRef']

/** 窗口/桌面共用的主体层计划：透明视频成品 + 主体元数据。 */
export interface StageActorPlan {
  visualId: string
  version: number
  url: string
  posterUrl: string | null
  width: number
  height: number
  loop: boolean
  body: ActorBody | null
}

/** 环境层计划：静态图或循环视频；与主体层独立更换。 */
export interface StageScenePlan {
  visualId: string | null
  version: number | null
  url: string
  posterUrl: string | null
  width: number
  height: number
  isVideo: boolean
  /** 冻结环境及正式基础引用；只在同一基础环境内延续时段画面。 */
  environmentKey: string
  /** 已使用时段版本，保留媒体自身光线。 */
  phaseMatched: boolean
}

/** 从同一份呈现状态解析出的统一舞台计划；窗口取主体层，桌面取完整或分层组合。 */
export interface StagePlan {
  mode: 'layered' | 'cinematic' | 'static' | 'empty'
  actor: StageActorPlan | null
  scene: StageScenePlan | null
  cinematic: LayerMedia | null
  layout: StageLayout
}

const DEFAULT_LAYOUT: StageLayout = {
  layer_order: ['scene', 'actor', 'effects'],
  fit: 'contain',
  actor_position: [0.5, 1],
  safe_area: [0, 0, 1, 1]
}

const EMPTY_PLAN: StagePlan = { mode: 'empty', actor: null, scene: null, cinematic: null, layout: DEFAULT_LAYOUT }

function stageLayout(
  presentation: ApiSchemas['VisualPresentationResponse'] | null,
  play: ApiSchemas['VisualPlayCommand'] | null
): StageLayout {
  return presentation?.composition?.layout ?? play?.composition?.layout ?? DEFAULT_LAYOUT
}

function sceneFromLayer(layer: LayerMedia | null | undefined, phaseMatched = false): StageScenePlan | null {
  if (!layer || layer.asset_kind !== 'scene') {
    return null
  }

  return {
    visualId: layer.visual_id,
    version: layer.version,
    url: layer.media_url,
    posterUrl: layer.poster_url ?? null,
    width: layer.width,
    height: layer.height,
    isVideo: layer.media_kind === 'video',
    environmentKey: '',
    phaseMatched
  }
}

/**
 * 环境层时段选择：当前环境存在与本地时段匹配的版本时优先使用，没有则保持
 * 组合里的现有环境（不限时段版本也视为可用）。纯视觉表现，不改变生活事实，
 * 也不因时段变化生成新媒体。
 */
export function sceneForPhase(
  catalog: readonly LayerMedia[],
  current: LayerMedia | null | undefined,
  phase: DayPhase,
  material?: SceneReference | null,
  previous?: StageScenePlan | null
): StageScenePlan | null {
  const hash = material?.content_hash ?? current?.scene_content_hash
  const base = material && current?.scene_content_hash !== hash ? null : current
  const environmentKey = `${hash ?? ''}:${base?.visual_id ?? 'static'}:${base?.version ?? ''}`
  const matched = hash ? catalog.find(entry => entry.scene_content_hash === hash && entry.day_phase === phase) : null

  const selected =
    sceneFromLayer(matched, true) ??
    (previous?.environmentKey === environmentKey ? previous : null) ??
    sceneFromLayer(base, Boolean(base?.day_phase)) ??
    (material
      ? {
          visualId: null,
          version: null,
          url: material.path,
          posterUrl: null,
          width: 0,
          height: 0,
          isVideo: false,
          environmentKey,
          phaseMatched: false
        }
      : null)

  return selected ? { ...selected, environmentKey } : null
}

/** 静态投影（无媒体）与空呈现都归入 static/empty；窗口据此退回冻结造型。 */
export function deriveStagePlan(
  presentation: ApiSchemas['VisualPresentationResponse'] | null,
  play: ApiSchemas['VisualPlayCommand'] | null,
  directedAmbient: CatalogEntry | null = null,
  subtle = false,
  previousScene: StageScenePlan | null = null
): StagePlan {
  if (!presentation && !play) {
    return EMPTY_PLAN
  }

  const baseMedia = presentation?.composition?.media
  const ambient = ambientActor(presentation, directedAmbient, subtle)
  const layout = stageLayout(presentation, play)

  const phaseScene = () =>
    sceneForPhase(
      presentation?.ambient_scene ?? [],
      baseMedia?.scene,
      currentDayPhase(),
      presentation?.static_presentation?.scene ?? presentation?.composition?.scene_material,
      previousScene
    )

  if (play?.asset_kind === 'cinematic') {
    return {
      mode: 'cinematic',
      actor: ambient,
      scene: null,
      cinematic: {
        visual_id: play.visual_id,
        version: play.visual_version,
        asset_kind: 'cinematic',
        media_kind: play.media_kind,
        media_url: play.media_path,
        poster_url: play.poster_path,
        clip_kind: play.clip_kind,
        width: 0,
        height: 0,
        duration_ms: null,
        body: null,
        description: '',
        day_phase: null,
        scene_content_hash: null
      },
      layout
    }
  }

  if (play?.asset_kind === 'scene') {
    return {
      mode: 'layered',
      actor: ambient,
      scene: {
        visualId: play.visual_id,
        version: play.visual_version,
        url: play.media_path,
        posterUrl: play.poster_path ?? null,
        width: 0,
        height: 0,
        isVideo: play.media_kind === 'video',
        environmentKey: `command:${play.visual_id}:${play.visual_version}`,
        phaseMatched: Boolean(play.day_phase)
      },
      cinematic: null,
      layout
    }
  }

  if (play && play.asset_kind === 'actor' && play.media_kind === 'video') {
    const actor: StageActorPlan = {
      visualId: play.visual_id,
      version: play.visual_version,
      url: play.media_path,
      posterUrl: play.poster_path ?? null,
      width: 0,
      height: 0,
      loop: play.loop,
      body: play.body ?? null
    }

    const scene = phaseScene()

    return { mode: 'layered', actor, scene, cinematic: null, layout }
  }

  if (!presentation) {
    return EMPTY_PLAN
  }

  const media = presentation.composition?.media ?? null

  // 动态素材可用时，旧静态目标只作为回退；重新开启动态无需等待下一次生活变化。
  if (presentation.static_presentation) {
    return { mode: ambient ? 'layered' : 'static', actor: ambient, scene: phaseScene(), cinematic: null, layout }
  }

  const cinematicEntry = presentation.ambient_catalog.find(
    entry => entry.visual_id === media?.cinematic?.visual_id && entry.version === media?.cinematic?.version
  )

  if (
    media?.mode === 'cinematic' &&
    media.cinematic &&
    media.cinematic.clip_kind !== 'once' &&
    cinematicEntry &&
    motionAllowed(cinematicEntry, subtle) &&
    contextAllowed(cinematicEntry, presentation.intent)
  ) {
    return { mode: 'cinematic', actor: ambient, scene: null, cinematic: media.cinematic, layout }
  }

  if (media?.mode === 'layered') {
    const actor = ambient
    const scene = phaseScene()

    if (actor || scene) {
      return { mode: 'layered', actor, scene, cinematic: null, layout }
    }
  }

  return ambient ? { mode: 'layered', actor: ambient, scene: null, cinematic: null, layout } : EMPTY_PLAN
}

/** 解析后的统一舞台计划；窗口与桌面消费者共享同一份。 */
export const $stagePlan = atom<StagePlan>(EMPTY_PLAN)

type CatalogEntry = NonNullable<ApiSchemas['VisualPresentationResponse']['ambient_catalog']>[number]

function actorFromCatalog(entry: CatalogEntry | null | undefined): StageActorPlan | null {
  if (!entry || entry.asset_kind !== 'actor' || entry.media_kind !== 'video' || entry.clip_kind === 'once') {
    return null
  }

  return {
    visualId: entry.visual_id,
    version: entry.version,
    url: entry.media_url,
    posterUrl: entry.poster_url ?? null,
    width: entry.width,
    height: entry.height,
    loop: true,
    body: entry.body ?? null
  }
}

/**
 * 导演选择的常驻循环优先于服务端 ambient 投影：同为已冻结身份/造型下的只读轮换，
 * 不建立播放账本；目录缺项（如换装后旧选择失效）自动回落 ambient。
 */
function ambientActor(
  presentation: ApiSchemas['VisualPresentationResponse'] | null,
  directed: CatalogEntry | null,
  subtle: boolean
): StageActorPlan | null {
  if (!presentation) {
    return null
  }

  const candidates = automaticLoops(presentation, subtle)

  const selected = candidates.find(
    entry => entry.visual_id === directed?.visual_id && entry.version === directed.version
  )

  const base = candidates.find(
    entry =>
      entry.visual_id === presentation.composition?.media?.actor?.visual_id &&
      entry.version === presentation.composition?.media?.actor?.version
  )

  const idle = candidates.find(
    entry =>
      entry.visual_id === presentation.ambient_actor?.visual_id && entry.version === presentation.ambient_actor.version
  )

  return actorFromCatalog(selected ?? base ?? idle ?? candidates[0])
}
