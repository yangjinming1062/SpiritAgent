import { atom } from 'nanostores'

import type { ApiSchemas } from '../../../../shared/api.generated'

type LayerMedia = ApiSchemas['StageLayerMedia']
type ActorBody = ApiSchemas['ActorClipBody']
export type StageLayout = ApiSchemas['StageCompositionLayout']

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
  visualId: string
  version: number
  url: string
  posterUrl: string | null
  width: number
  height: number
  isVideo: boolean
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
  return play?.composition?.layout ?? presentation?.composition?.layout ?? DEFAULT_LAYOUT
}

function actorFromLayer(layer: LayerMedia | null | undefined): StageActorPlan | null {
  if (!layer || layer.asset_kind !== 'actor' || layer.media_kind !== 'video') {
    return null
  }

  return {
    visualId: layer.visual_id,
    version: layer.version,
    url: layer.media_url,
    posterUrl: layer.poster_url ?? null,
    width: layer.width,
    height: layer.height,
    loop: layer.clip_kind !== 'once',
    body: layer.body ?? null
  }
}

function sceneFromLayer(layer: LayerMedia | null | undefined): StageScenePlan | null {
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
    isVideo: layer.media_kind === 'video'
  }
}

/** 静态投影（无媒体）与空呈现都归入 static/empty；窗口据此退回冻结造型。 */
export function deriveStagePlan(
  presentation: ApiSchemas['VisualPresentationResponse'] | null,
  play: ApiSchemas['VisualPlayCommand'] | null,
  directedAmbient: CatalogEntry | null = null
): StagePlan {
  if (!presentation && !play) {
    return EMPTY_PLAN
  }

  const baseMedia = presentation?.composition?.media
  const ambient = ambientActor(presentation, directedAmbient)
  const layout = stageLayout(presentation, play)

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
        description: ''
      },
      layout
    }
  }

  if (play?.asset_kind === 'scene') {
    return {
      mode: 'layered',
      actor: actorFromLayer(baseMedia?.actor) ?? ambient,
      scene: {
        visualId: play.visual_id,
        version: play.visual_version,
        url: play.media_path,
        posterUrl: play.poster_path ?? null,
        width: 0,
        height: 0,
        isVideo: play.media_kind === 'video'
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

    const scene = baseMedia?.scene ?? null
    const sceneLayer = sceneFromLayer(scene)

    return { mode: 'layered', actor, scene: sceneLayer, cinematic: null, layout }
  }

  if (!presentation) {
    return EMPTY_PLAN
  }

  const media = presentation.composition?.media ?? null

  // 动态素材可用时，旧静态目标只作为回退；重新开启动态无需等待下一次生活变化。
  if (presentation.static_presentation && !ambient) {
    return { mode: 'static', actor: ambient, scene: null, cinematic: null, layout }
  }

  if (media?.mode === 'cinematic' && media.cinematic) {
    return { mode: 'cinematic', actor: ambient, scene: null, cinematic: media.cinematic, layout }
  }

  if (media?.mode === 'layered') {
    const actor = actorFromLayer(media.actor) ?? ambient

    if (actor || media.scene) {
      return { mode: 'layered', actor, scene: sceneFromLayer(media.scene), cinematic: null, layout }
    }
  }

  return ambient ? { mode: 'layered', actor: ambient, scene: null, cinematic: null, layout } : EMPTY_PLAN
}

/** 解析后的统一舞台计划；窗口与桌面消费者共享同一份。 */
export const $stagePlan = atom<StagePlan>(EMPTY_PLAN)

type CatalogEntry = NonNullable<ApiSchemas['VisualPresentationResponse']['ambient_catalog']>[number]

function actorFromCatalog(entry: CatalogEntry | null | undefined): StageActorPlan | null {
  if (!entry || entry.asset_kind !== 'actor' || entry.media_kind !== 'video') {
    return null
  }

  return {
    visualId: entry.visual_id,
    version: entry.version,
    url: entry.media_url,
    posterUrl: entry.poster_url ?? null,
    width: entry.width,
    height: entry.height,
    loop: entry.clip_kind !== 'once',
    body: entry.body ?? null
  }
}

/**
 * 导演选择的常驻循环优先于服务端 ambient 投影：同为已冻结身份/造型下的只读轮换，
 * 不建立播放账本；目录缺项（如换装后旧选择失效）自动回落 ambient。
 */
function ambientActor(
  presentation: ApiSchemas['VisualPresentationResponse'] | null,
  directed: CatalogEntry | null
): StageActorPlan | null {
  return actorFromCatalog(directed) ?? actorFromLayer(presentation?.ambient_actor)
}
