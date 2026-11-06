import { deriveStagePlan, resolveAmbientLoop } from '@/features/creation/visuals'
import type { StagePlan, VisualPlayCommand, VisualsPresentation } from '@/features/creation/visuals'
import type { DesktopBackgroundRequest } from '@ipc/contracts'

interface VisualPresentationInput {
  authSessionId: string
  projection: VisualsPresentation | null
  activeCommand: VisualPlayCommand | null
  staticTarget: VisualsPresentation['static_presentation'] | undefined
  stagePlan: StagePlan
  failedCinematic: string | null
  theme: DesktopBackgroundRequest['theme']
  subtle: boolean
  motionEnabled: boolean
  reduceMotion: boolean
  paused: boolean
}

function firstNonBlank(...values: readonly (string | null | undefined)[]): string | null {
  return values.find(value => typeof value === 'string' && value.trim().length > 0) ?? null
}

function assetPath(url: string | null | undefined): string | null {
  return url ? url.split('?')[0] : null
}

/**
 * 桌面呈现目标的唯一推导处：常驻分层（环境 + 主体）优先，短动作只经认领指令进入，
 * 静态投影仅在无指令时补位。推导不写状态，认领与回执仍归 performance。
 */
export function deriveVisualPresentationTarget({
  authSessionId,
  projection,
  activeCommand,
  staticTarget,
  stagePlan,
  failedCinematic,
  theme,
  subtle,
  motionEnabled,
  reduceMotion,
  paused
}: VisualPresentationInput): DesktopBackgroundRequest {
  const baseline = projection?.loop_visual ?? projection?.current_visual ?? null
  const plan = deriveStagePlan(projection, activeCommand, resolveAmbientLoop(projection), subtle, stagePlan.scene)
  const layerActor = plan.actor
  const layerScene = plan.scene
  const full = activeCommand?.asset_kind === 'cinematic' ? activeCommand : null
  const cinematic = plan.cinematic

  const cinematicKey = full
    ? `${full.visual_id}:${full.visual_version}`
    : cinematic
      ? `${cinematic.visual_id}:${cinematic.version}`
      : null

  const layered = !cinematicKey || cinematicKey === failedCinematic
  const sceneCommand = activeCommand?.asset_kind === 'scene' ? activeCommand : null

  const videoUrl = !motionEnabled
    ? null
    : !layered
      ? assetPath(
          full?.media_kind === 'video'
            ? full.media_path
            : cinematic?.media_kind === 'video'
              ? cinematic.media_url
              : null
        )
      : assetPath(
          sceneCommand?.media_kind === 'video' ? sceneCommand.media_path : layerScene?.isVideo ? layerScene.url : null
        )

  const posterUrl = !layered
    ? firstNonBlank(
        assetPath(full?.media_kind === 'image' ? full.media_path : full?.poster_path),
        assetPath(cinematic?.media_kind === 'image' ? cinematic.media_url : cinematic?.poster_url)
      )
    : firstNonBlank(
        assetPath(sceneCommand?.media_kind === 'image' ? sceneCommand.media_path : sceneCommand?.poster_path),
        assetPath(layerScene?.isVideo ? layerScene.posterUrl : layerScene?.url),
        assetPath(staticTarget?.scene?.path),
        assetPath(projection?.composition?.scene_material?.path),
        assetPath(projection?.actual.scene_path)
      )

  const actorUrl = motionEnabled && layered ? assetPath(layerActor?.url) : null

  const outfitUrl = layered
    ? firstNonBlank(
        assetPath(activeCommand?.outfit?.path),
        assetPath(staticTarget?.outfit?.path),
        assetPath(projection?.composition?.outfit_material?.path),
        assetPath(projection?.actual.outfit_path)
      )
    : null

  const backgroundId = layered
    ? (sceneCommand?.visual_id ?? layerScene?.visualId ?? null)
    : (full?.visual_id ?? cinematic?.visual_id ?? null)

  const backgroundVersion = layered
    ? (sceneCommand?.visual_version ?? layerScene?.version ?? null)
    : (full?.visual_version ?? cinematic?.version ?? null)

  const phaseMedia = layered && (layerScene?.phaseMatched ?? false)

  return {
    authSessionId,
    video: videoUrl ? { url: videoUrl } : null,
    poster: posterUrl ? { url: posterUrl } : null,
    outfit: outfitUrl ? { url: outfitUrl } : null,
    actor: actorUrl ? { url: actorUrl } : null,
    playId:
      activeCommand?.play_id ??
      staticTarget?.request_id ??
      (!layered && cinematicKey ? `render:cinematic:${cinematicKey}` : null),
    playTarget: activeCommand?.asset_kind === 'actor' ? 'actor' : 'background',
    actorBaseKey: stagePlan.actor ? `${stagePlan.actor.visualId}:${stagePlan.actor.version}` : null,
    layout: {
      layerOrder: stagePlan.layout.layer_order,
      fit: stagePlan.layout.fit,
      actorPosition: stagePlan.layout.actor_position,
      safeArea: stagePlan.layout.safe_area
    },
    visualId: backgroundId,
    visualVersion: backgroundVersion,
    actorVisualId: layered ? (layerActor?.visualId ?? null) : null,
    actorVisualVersion: layered ? (layerActor?.version ?? null) : null,
    presentationEpoch: staticTarget?.presentation_epoch ?? projection?.presentation_epoch ?? 0,
    expiresAt: staticTarget ? null : (activeCommand?.expires_at ?? null),
    kind: activeCommand && activeCommand.asset_kind !== 'actor' ? (activeCommand.loop ? 'loop' : 'once') : 'loop',
    // 基础主体层只含循环成品（stage-plan 已滤除 once），仅指令主体可有一次动作。
    actorKind: activeCommand?.asset_kind === 'actor' ? (activeCommand.loop ? 'loop' : 'once') : 'loop',
    // 短动作结束后返回保留的持续画面；独立播放不关联基准。
    continuity: !layered && (activeCommand?.input_image_hash ?? baseline?.input_image_hash) ? 'base' : 'independent',
    transition: staticTarget ? 'fade' : (activeCommand?.transition ?? 'natural'),
    geometryMode: 'target',
    inputImageHash: layered ? null : (activeCommand?.input_image_hash ?? baseline?.input_image_hash ?? null),
    theme,
    reduceMotion: reduceMotion || !motionEnabled,
    paused: paused || !motionEnabled,
    backgroundKind: layered ? 'scene' : 'cinematic',
    phaseMedia,
    clear: !videoUrl && !posterUrl && !actorUrl
  }
}
