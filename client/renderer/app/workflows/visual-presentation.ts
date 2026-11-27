import { deriveStagePlan, staticPresentationKey } from '@/features/creation/visuals'
import type {
  $ambientLoopChoice,
  $failedStageMedia,
  $wakePerformance,
  VisualPlayCommand,
  VisualsPresentation
} from '@/features/creation/visuals'
import { DEFAULT_STAGE_LAYOUT, DESKTOP_EMPTY_PLAY_PREFIX, type DesktopBackgroundRequest } from '@ipc/contracts'

interface FullPresentationInput {
  projection: VisualsPresentation | null
  activeCommand: VisualPlayCommand | null
  staticTarget: VisualsPresentation['static_presentation'] | undefined
  surface: 'desktop' | 'life'
  subtle: boolean
  motionEnabled: boolean
  paused: boolean
  failedMedia: ReturnType<typeof $failedStageMedia.get>
  ambient: ReturnType<typeof $ambientLoopChoice.get>
  wake: ReturnType<typeof $wakePerformance.get>
}

interface DesktopPresentationInput extends Omit<FullPresentationInput, 'surface'> {
  authSessionId: string
  theme: DesktopBackgroundRequest['theme']
  reduceMotion: boolean
}

/** 为同一连续目标复用播放身份；再次进入本地基础画面时另发起一次播放。 */
export function createCompletePlaybackAttempt(): (targetId: string | null) => string | null {
  let previousTarget: string | null = null
  let playId: string | null = null

  return targetId => {
    if (targetId !== previousTarget) {
      previousTarget = targetId

      const prefix = targetId?.startsWith(DESKTOP_EMPTY_PLAY_PREFIX)
        ? DESKTOP_EMPTY_PLAY_PREFIX
        : targetId?.startsWith('render:complete:')
          ? 'render:complete:'
          : null

      playId = prefix ? `${prefix}${crypto.randomUUID()}` : targetId
    }

    return playId
  }
}

export function deriveFullPresentationTarget({
  projection,
  activeCommand,
  staticTarget,
  surface,
  subtle,
  motionEnabled,
  paused,
  failedMedia,
  ambient,
  wake
}: FullPresentationInput) {
  const plan = deriveStagePlan(projection, activeCommand, {
    surface,
    subtle,
    motionEnabled,
    ambient,
    wake: paused ? null : wake,
    failedMedia
  })

  if (staticTarget && !activeCommand) {
    return {
      video: null,
      poster: staticTarget.scene?.path ?? null,
      playId: staticPresentationKey(staticTarget),
      visualId: null,
      visualVersion: null,
      localSceneHash: null,
      sceneHash: staticTarget.scene?.content_hash ?? null,
      presentationEpoch: staticTarget.presentation_epoch,
      backgroundKind: 'scene' as const,
      phaseMedia: false,
      dayPhase: plan.dayPhase,
      expiresAt: null,
      kind: 'loop' as const,
      pauseMotion: true,
      transition: 'fade' as const,
      layout: {
        fit: DEFAULT_STAGE_LAYOUT.fit,
        actor_position: DEFAULT_STAGE_LAYOUT.actorPosition,
        safe_area: DEFAULT_STAGE_LAYOUT.safeArea
      },
      inputImageHash: null
    }
  }

  const fullCommand = activeCommand?.asset_kind === 'cinematic' ? activeCommand : null
  const sceneCommand = activeCommand?.asset_kind === 'scene' ? activeCommand : null
  const cinematic = sceneCommand ? null : plan.cinematic
  const scene = plan.scene
  const backgroundKind: DesktopBackgroundRequest['backgroundKind'] = fullCommand || cinematic ? 'cinematic' : 'scene'
  const isCinematic = backgroundKind === 'cinematic'
  const selected = fullCommand || sceneCommand ? null : cinematic
  const displayVideo = motionEnabled && (activeCommand !== null || !cinematic || plan.cinematicMotion)

  const video = !displayVideo
    ? null
    : isCinematic
      ? fullCommand
        ? fullCommand.media_kind === 'video'
          ? fullCommand.media_path
          : null
        : cinematic?.media_kind === 'video'
          ? cinematic.media_url
          : null
      : scene?.isVideo
        ? scene.url
        : null

  const fallbackScene = projection?.static_presentation?.scene ?? projection?.composition?.scene_material
  const scenePoster = scene?.isVideo ? scene.posterUrl : scene?.url

  const poster = isCinematic
    ? fullCommand
      ? fullCommand.media_kind === 'image'
        ? fullCommand.media_path
        : fullCommand.poster_path
      : cinematic?.media_kind === 'image'
        ? cinematic.media_url
        : (cinematic?.poster_url ?? null)
    : (scenePoster ?? fallbackScene?.path ?? null)

  const rawScene =
    !activeCommand && !video && !isCinematic && poster ? (scenePoster ? scene?.fallbackScene : fallbackScene) : null

  const visualId = rawScene ? null : (activeCommand?.visual_id ?? selected?.visual_id ?? scene?.visualId ?? null)
  const visualVersion = rawScene ? null : (activeCommand?.visual_version ?? selected?.version ?? scene?.version ?? null)
  const presentationEpoch = projection?.presentation_epoch ?? 0

  const localPlayId = rawScene
    ? `render:complete:scene:${presentationEpoch}:${rawScene.content_hash}:${rawScene.path.split('?')[0]}`
    : visualId && visualVersion
      ? `render:complete:${visualId}:${visualVersion}:${video ? 'video' : 'image'}:${presentationEpoch}`
      : null

  return {
    video,
    poster,
    playId: activeCommand?.play_id ?? plan.localPlay?.id ?? localPlayId,
    visualId,
    visualVersion,
    localSceneHash: rawScene?.content_hash ?? null,
    sceneHash: isCinematic
      ? (fullCommand?.scene?.content_hash ?? cinematic?.scene_content_hash ?? null)
      : (rawScene?.content_hash ?? scene?.sceneContentHash ?? null),
    presentationEpoch,
    backgroundKind,
    phaseMedia: !isCinematic && !rawScene && (scene?.phaseMatched ?? false),
    dayPhase: plan.dayPhase,
    expiresAt: activeCommand?.expires_at ?? plan.localPlay?.expiresAt ?? null,
    kind: (activeCommand?.loop === false || plan.localPlay?.id.startsWith('render:wake:') ? 'once' : 'loop') as
      | 'loop'
      | 'once',
    pauseMotion: !displayVideo || paused,
    transition: activeCommand?.transition ?? 'natural',
    layout: plan.layout,
    inputImageHash: activeCommand?.input_image_hash ?? null
  }
}

export function deriveVisualPresentationTarget(
  input: DesktopPresentationInput
): DesktopBackgroundRequest & { localSceneHash: string | null; sceneHash: string | null } {
  const target = deriveFullPresentationTarget({ ...input, surface: 'desktop' })
  const video = target.video?.split('?')[0] ?? null
  const poster = target.poster?.split('?')[0] ?? null
  const clear = !video && !poster

  const emptyPlay =
    clear && !input.paused && input.projection ? `${DESKTOP_EMPTY_PLAY_PREFIX}${target.presentationEpoch}` : null

  return {
    authSessionId: input.authSessionId,
    theme: input.theme,
    video: video ? { url: video } : null,
    poster: poster ? { url: poster } : null,
    outfit: null,
    outfitPose: false,
    actor: null,
    actorBaseKey: null,
    playId: target.playId ?? emptyPlay,
    playTarget: 'background',
    layout: {
      layerOrder: target.backgroundKind === 'cinematic' ? ['cinematic', 'effects'] : ['scene', 'effects'],
      fit: target.layout.fit,
      actorPosition: target.layout.actor_position,
      safeArea: target.layout.safe_area
    },
    visualId: target.visualId,
    visualVersion: target.visualVersion,
    localSceneHash: target.localSceneHash,
    sceneHash: target.sceneHash,
    actorVisualId: null,
    actorVisualVersion: null,
    presentationEpoch: target.presentationEpoch,
    expiresAt: target.expiresAt,
    kind: target.kind,
    actorKind: 'loop',
    continuity: target.inputImageHash ? 'base' : 'independent',
    transition: target.transition,
    geometryMode: 'target',
    inputImageHash: target.inputImageHash,
    reduceMotion: input.reduceMotion || !input.motionEnabled,
    paused: target.pauseMotion,
    backgroundKind: target.backgroundKind,
    phaseMedia: target.phaseMedia,
    dayPhase: target.dayPhase,
    sceneKey: video ?? poster,
    clear
  }
}
