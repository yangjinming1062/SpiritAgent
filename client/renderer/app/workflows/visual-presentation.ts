import {
  $ambientLoopChoice,
  $failedStageMedia,
  $wakePerformance,
  deriveStagePlan,
  staticPresentationKey
} from '@/features/creation/visuals'
import type { StagePlan, VisualPlayCommand, VisualsPresentation } from '@/features/creation/visuals'
import { DEFAULT_STAGE_LAYOUT, type DesktopBackgroundRequest } from '@ipc/contracts'

interface VisualPresentationInput {
  authSessionId: string
  projection: VisualsPresentation | null
  activeCommand: VisualPlayCommand | null
  staticTarget: VisualsPresentation['static_presentation'] | undefined
  stagePlan: StagePlan
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
 * 桌面先呈现已认领指令或待确认的冻结静态目标，再接续当前活动的基础画面。
 */
export function deriveVisualPresentationTarget({
  authSessionId,
  projection,
  activeCommand,
  staticTarget,
  stagePlan,
  theme,
  subtle,
  motionEnabled,
  reduceMotion,
  paused
}: VisualPresentationInput): DesktopBackgroundRequest {
  const baseline = projection?.loop_visual ?? projection?.current_visual ?? null

  const plan = deriveStagePlan(projection, activeCommand, {
    desktop: true,
    subtle,
    motionEnabled,
    ambient: $ambientLoopChoice.get(),
    wake: paused ? null : $wakePerformance.get(),
    failedMedia: $failedStageMedia.get()
  })

  const common = {
    authSessionId,
    theme,
    dayPhase: plan.dayPhase,
    reduceMotion: reduceMotion || !motionEnabled,
    paused: paused || !motionEnabled,
    geometryMode: 'target' as const
  }

  if (staticTarget && !activeCommand) {
    const complete = Boolean(staticTarget.outfit && staticTarget.scene)
    const scene = complete ? assetPath(staticTarget.scene?.path) : null
    const outfit = complete ? assetPath(staticTarget.outfit?.path) : null

    return {
      ...common,
      poster: scene ? { url: scene } : null,
      outfit: outfit ? { url: outfit } : null,
      outfitPose: false,
      video: null,
      actor: null,
      actorBaseKey: null,
      playId: staticPresentationKey(staticTarget),
      playTarget: 'background',
      presentationEpoch: staticTarget.presentation_epoch,
      visualId: null,
      visualVersion: null,
      actorVisualId: null,
      actorVisualVersion: null,
      kind: 'loop',
      actorKind: 'loop',
      continuity: 'independent',
      transition: 'fade',
      expiresAt: null,
      inputImageHash: null,
      backgroundKind: 'scene',
      phaseMedia: false,
      sceneKey: scene,
      clear: !complete,
      layout: DEFAULT_STAGE_LAYOUT
    }
  }

  const layerActor = plan.actor
  const layerScene = plan.scene
  const full = activeCommand?.asset_kind === 'cinematic' ? activeCommand : null
  const cinematic = plan.cinematic

  const cinematicKey = full
    ? `${full.visual_id}:${full.visual_version}`
    : cinematic
      ? `${cinematic.visual_id}:${cinematic.version}`
      : null

  const layered = !cinematicKey
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
        assetPath(projection?.static_presentation?.scene?.path),
        assetPath(projection?.composition?.scene_material?.path),
        assetPath(projection?.actual.scene_path)
      )

  const actorUrl = motionEnabled && layered ? assetPath(layerActor?.url) : null

  const useQuietScene =
    projection?.autonomous_enabled &&
    projection.rhythm?.state === 'asleep' &&
    !activeCommand &&
    !plan.actorStillUrl &&
    posterUrl

  const outfitUrl =
    layered && !useQuietScene
      ? firstNonBlank(
          assetPath(activeCommand?.outfit?.path),
          assetPath(plan.actorStillUrl),
          assetPath(projection?.static_presentation?.outfit?.path),
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

  const actorBase =
    activeCommand?.asset_kind === 'actor' || plan.localPlay?.id.startsWith('render:wake:')
      ? deriveStagePlan(projection, null, { desktop: true, subtle, ambient: $ambientLoopChoice.get() }).actor
      : stagePlan.actor

  return {
    ...common,
    video: videoUrl ? { url: videoUrl } : null,
    poster: posterUrl ? { url: posterUrl } : null,
    outfit: outfitUrl ? { url: outfitUrl } : null,
    outfitPose: Boolean(outfitUrl && !activeCommand?.outfit && outfitUrl === assetPath(plan.actorStillUrl)),
    actor: actorUrl ? { url: actorUrl } : null,
    playId:
      activeCommand?.play_id ??
      plan.localPlay?.id ??
      (!layered && cinematicKey ? `render:cinematic:${cinematicKey}` : null),
    playTarget: activeCommand?.asset_kind === 'actor' || plan.localPlay?.target === 'actor' ? 'actor' : 'background',
    actorBaseKey: actorBase ? `${actorBase.visualId}:${actorBase.version}` : null,
    layout: {
      layerOrder: plan.layout.layer_order,
      fit: plan.layout.fit,
      actorPosition: plan.layout.actor_position,
      safeArea: plan.layout.safe_area
    },
    visualId: backgroundId,
    visualVersion: backgroundVersion,
    actorVisualId: layered ? (layerActor?.visualId ?? null) : null,
    actorVisualVersion: layered ? (layerActor?.version ?? null) : null,
    presentationEpoch: projection?.presentation_epoch ?? 0,
    expiresAt: activeCommand?.expires_at ?? plan.localPlay?.expiresAt ?? null,
    kind: activeCommand && activeCommand.asset_kind !== 'actor' ? (activeCommand.loop ? 'loop' : 'once') : 'loop',
    // 基础主体层只含循环成品（stage-plan 已滤除 once），仅指令主体可有一次动作。
    actorKind: layerActor?.loop === false ? 'once' : 'loop',
    // 短动作结束后返回保留的持续画面；独立播放不关联基准。
    continuity: !layered && (activeCommand?.input_image_hash ?? baseline?.input_image_hash) ? 'base' : 'independent',
    transition: activeCommand?.transition ?? 'natural',
    inputImageHash: layered ? null : (activeCommand?.input_image_hash ?? baseline?.input_image_hash ?? null),
    backgroundKind: layered ? 'scene' : 'cinematic',
    phaseMedia,
    sceneKey: videoUrl ?? posterUrl,
    clear: !videoUrl && !posterUrl && !actorUrl && !outfitUrl
  }
}
