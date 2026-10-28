import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $motionLevel } from '@/modules/character'
import {
  $performance,
  $stagePlan,
  $visualsJobs,
  $visualsPresentation,
  bindPresentationDirector,
  claimVisualPlay,
  deriveStagePlan,
  refreshVisualPresentation,
  refreshVisuals,
  resolveAmbientLoop,
  sendStaticReceipt,
  sendVisualReceipt,
  staticPresentationKey,
  type VisualMediaKind,
  visualPerformancePorts,
  type VisualPlayCommand
} from '@/modules/visuals'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceScreenLocked } from '@/shared/store/surfaces'
import { $theme } from '@/shared/store/theme'
import type { DesktopBackgroundRequest } from '@ipc/contracts'

function hasTerminalReceipt(statuses: ReadonlySet<ReceiptStatus> | undefined): boolean {
  return statuses !== undefined && [...statuses].some(status => status !== 'started')
}

function firstNonBlank(...values: readonly (string | null | undefined)[]): string | null {
  return values.find(value => typeof value === 'string' && value.trim().length > 0) ?? null
}

/** 成品展示地址只用于取数；缓存与预加载身份认稳定 ID、成品版本与剥除签名后的不可变路径。 */
function assetPath(url: string | null | undefined): string | null {
  return url ? url.split('?')[0] : null
}

type ReceiptStatus = 'started' | 'finished' | 'failed' | 'interrupted'

export function useDesktopVisualWallpaper(): void {
  const auth = useStore($auth)
  const motionLevel = useStore($motionLevel)
  const presentation = useStore($presentation)
  const locked = useStore($surfaceScreenLocked)
  const theme = useStore($theme)
  const projection = useStore($visualsPresentation)
  const jobs = useStore($visualsJobs)
  const performance = useStore($performance)
  const active = presentation.effectiveMode === 'desktop' && presentation.status === 'active'
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const [clientId] = useState(() => crypto.randomUUID())
  const [claimed, setClaimed] = useState<VisualPlayCommand | null>(null)
  const [failedCinematic, setFailedCinematic] = useState<string | null>(null)
  const [reduceMotion, setReduceMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const claimedPlays = useRef(new Map<string, Set<ReceiptStatus>>())
  const claimedKinds = useRef(new Map<string, VisualMediaKind>())
  const receiptQueues = useRef(new Map<string, Promise<void>>())
  const publishedKey = useRef('')
  const paused = locked || presentation.fullscreen || reduceMotion

  // 持续画面优先于当前影像；图片即持续画面，视频按 clip_kind 循环或短时播放。
  const baseline = projection?.loop_visual ?? projection?.current_visual ?? null

  const pendingPlay = performance.command
  const staticTarget = pendingPlay ? null : projection?.static_presentation
  const staticReceipts = useRef(new Set<string>())

  const recordReceipt = useCallback((playId: string, status: ReceiptStatus, error?: string): void => {
    const scope = captureAuthScope()
    const statuses = claimedPlays.current.get(playId)

    if (!scope || !statuses || statuses.has(status) || hasTerminalReceipt(statuses)) {
      return
    }

    statuses.add(status)

    if (status !== 'started') {
      setClaimed((command: VisualPlayCommand | null) => (command?.play_id === playId ? null : command))
    }

    const kind = claimedKinds.current.get(playId) ?? 'image'
    const previous = receiptQueues.current.get(playId) ?? Promise.resolve()

    const request = previous
      .then(async () => {
        if (scope()) {
          await sendVisualReceipt(playId, {
            status,
            receipt_kind: kind,
            ...(error ? { error } : {})
          })
        }
      })
      .catch(failure => log.warn('desktop-visual', 'Playback receipt failed', failure))
      .finally(() => {
        if (scope()) {
          void refreshVisualPresentation()
        }

        if (receiptQueues.current.get(playId) === request) {
          receiptQueues.current.delete(playId)
        }
      })

    receiptQueues.current.set(playId, request)

    if (claimedPlays.current.size > 128) {
      for (const [recordedId, recorded] of claimedPlays.current) {
        if (recordedId !== playId && hasTerminalReceipt(recorded)) {
          claimedPlays.current.delete(recordedId)

          if (claimedPlays.current.size <= 128) {
            break
          }
        }
      }
    }
  }, [])

  useEffect(() => {
    publishedKey.current = ''
    claimedPlays.current.clear()
    claimedKinds.current.clear()
    receiptQueues.current.clear()
    staticReceipts.current.clear()
    setClaimed(null)
    setFailedCinematic(null)
  }, [authSessionId])

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const change = (): void => setReduceMotion(query.matches)
    query.addEventListener('change', change)

    return () => query.removeEventListener('change', change)
  }, [])

  useEffect(() => {
    if (!authSessionId) {
      return
    }

    void refreshVisuals()
    void refreshVisualPresentation()
  }, [authSessionId])

  // 桌面宿主装配导演：常驻轮换本地完成，互动/意图演出走指令管线（回执沿本文件既有通道）。
  useEffect(() => {
    if (!active || !authSessionId) {
      return
    }

    const stageEpoch = $presentation.get().stageEpoch

    return bindPresentationDirector(visualPerformancePorts(), () => $visualsPresentation.get(), {
      isEligible: () =>
        $auth.get().kind === 'authenticated' &&
        $motionLevel.get() !== 'off' &&
        $presentation.get().effectiveMode === 'desktop' &&
        $presentation.get().status === 'active' &&
        $presentation.get().stageEpoch === stageEpoch &&
        !$presentation.get().fullscreen &&
        !$surfaceScreenLocked.get() &&
        !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      isSubtle: () => $motionLevel.get() === 'subtle',
      isMechanical: () => false,
      onChanged: notify => {
        const stops = [$motionLevel.listen(notify), $presentation.listen(notify), $surfaceScreenLocked.listen(notify)]
        const query = window.matchMedia('(prefers-reduced-motion: reduce)')
        query.addEventListener('change', notify)

        return () => {
          stops.forEach(stop => stop())
          query.removeEventListener('change', notify)
        }
      }
    })
  }, [active, authSessionId, presentation.stageEpoch])

  const preparationInProgress = jobs.some(job => job.state === 'running' || job.state === 'queued')

  useEffect(() => {
    if (!active || !preparationInProgress) {
      return
    }

    const timer = window.setInterval(() => {
      void refreshVisuals()
      void refreshVisualPresentation()
    }, 5000)

    return () => window.clearInterval(timer)
  }, [active, preparationInProgress])

  // 播放认领：仅桌面承载认领待播指令；`queued` 只表示受理，实际回执才更新呈现事实。
  useEffect(() => {
    if (!active || !authSessionId || !pendingPlay || motionLevel === 'off') {
      return
    }

    const scope = captureAuthScope()
    let disposed = false

    const claim = async (): Promise<void> => {
      if (claimedPlays.current.has(pendingPlay.play_id)) {
        return
      }

      if (Date.parse(pendingPlay.expires_at) <= Date.now()) {
        return
      }

      if (await claimVisualPlay(pendingPlay.play_id, clientId)) {
        if (disposed || !scope?.()) {
          if (scope?.()) {
            await sendVisualReceipt(pendingPlay.play_id, {
              status: 'interrupted',
              receipt_kind: pendingPlay.media_kind,
              error: 'Presentation host changed'
            })
          }

          return
        }

        claimedPlays.current.set(pendingPlay.play_id, new Set())
        claimedKinds.current.set(pendingPlay.play_id, pendingPlay.media_kind)

        if (Date.parse(pendingPlay.expires_at) <= Date.now()) {
          recordReceipt(pendingPlay.play_id, 'failed', 'Playback request expired before publication')

          return
        }

        setClaimed(pendingPlay)
      }
    }

    void claim().catch(error => log.warn('desktop-visual', 'Playback claim failed', error))

    return () => {
      disposed = true
    }
  }, [active, authSessionId, pendingPlay, clientId, motionLevel, recordReceipt])

  useEffect(() => {
    const scope = captureAuthScope()

    return shellApi().presentation.onBackgroundPlayback(receipt => {
      if (!scope?.()) {
        return
      }

      if (receipt.playId.startsWith('render:cinematic:')) {
        if (receipt.status === 'failed') {
          setFailedCinematic(receipt.playId.slice('render:cinematic:'.length))
        }

        return
      }

      if (receipt.status === 'failed' && claimed?.play_id === receipt.playId && claimed.asset_kind === 'cinematic') {
        setFailedCinematic(`${claimed.visual_id}:${claimed.visual_version}`)
      }

      if (staticTarget?.request_id === receipt.playId) {
        const currentPresentation = $presentation.get()
        const key = staticPresentationKey(staticTarget)

        if (
          currentPresentation.effectiveMode !== 'desktop' ||
          currentPresentation.status !== 'active' ||
          $surfaceScreenLocked.get() ||
          (staticTarget.retry_at !== null && Date.parse(staticTarget.retry_at) > Date.now())
        ) {
          return
        }

        if ((receipt.status === 'started' || receipt.status === 'failed') && !staticReceipts.current.has(key)) {
          staticReceipts.current.add(key)
          void sendStaticReceipt({
            request_id: receipt.playId,
            presentation_epoch: staticTarget.presentation_epoch,
            state_revision: staticTarget.state_revision,
            receipt_kind: 'desktop_static',
            status: receipt.status,
            error: receipt.error ?? null
          })
            .catch(error => {
              if (scope()) {
                staticReceipts.current.delete(key)
                log.warn('desktop-visual', 'Static receipt failed', error)
              }
            })
            .finally(() => {
              if (scope()) {
                void refreshVisualPresentation()
              }
            })
        }

        return
      }

      const status: ReceiptStatus | null =
        receipt.status === 'first-frame'
          ? null
          : receipt.status === 'started'
            ? 'started'
            : receipt.status === 'completed'
              ? 'finished'
              : receipt.status === 'interrupted'
                ? 'interrupted'
                : 'failed'

      if (status) {
        recordReceipt(receipt.playId, status, receipt.error)
      }
    })
  }, [authSessionId, recordReceipt, staticTarget, claimed])

  const activeCommand =
    claimed &&
    motionLevel !== 'off' &&
    !paused &&
    performance.command?.play_id === claimed.play_id &&
    (performance.state === 'playing' || Date.parse(claimed.expires_at) > Date.now()) &&
    claimed.presentation_epoch >= (projection?.presentation_epoch ?? 0) &&
    (claimed.state_revision >= (projection?.state_revision ?? 0) ||
      (claimed.visual_id === projection?.current_visual?.id &&
        claimed.visual_version === projection.current_visual.version))
      ? claimed
      : null

  // 同一成品的新播放请求应重新尝试完整场景。失败标记只针对上一次
  // 播放，不能让后续手动或导演重试永久继承分层降级。
  useEffect(() => {
    if (activeCommand?.asset_kind === 'cinematic') {
      setFailedCinematic(null)
    }
  }, [activeCommand?.asset_kind, activeCommand?.play_id])

  useEffect(() => {
    if (claimed && (!active || !authSessionId || !activeCommand)) {
      recordReceipt(claimed.play_id, 'interrupted', 'Playback interrupted')
    }
  }, [active, authSessionId, claimed, activeCommand, recordReceipt])

  const stagePlan = useStore($stagePlan)

  const selectedPlan = deriveStagePlan(
    projection,
    activeCommand,
    resolveAmbientLoop(projection),
    motionLevel === 'subtle'
  )

  const layerActor = selectedPlan.actor
  const layerScene = selectedPlan.scene
  const full = activeCommand?.asset_kind === 'cinematic' ? activeCommand : null
  const cinematic = selectedPlan.cinematic

  const cinematicKey = full
    ? `${full.visual_id}:${full.visual_version}`
    : cinematic
      ? `${cinematic.visual_id}:${cinematic.version}`
      : null

  const layered = !cinematicKey || cinematicKey === failedCinematic
  const sceneCommand = activeCommand?.asset_kind === 'scene' ? activeCommand : null
  const motionEnabled = motionLevel !== 'off'

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

  useEffect(() => {
    if (!active || !authSessionId) {
      return
    }

    const scope = captureAuthScope()
    let disposed = false

    if (staticTarget?.retry_at && Date.parse(staticTarget.retry_at) > Date.now()) {
      return
    }

    const request: DesktopBackgroundRequest = {
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
      clear: !videoUrl && !posterUrl && !actorUrl
    }

    const key = JSON.stringify(request)

    if (publishedKey.current === key) {
      return
    }

    publishedKey.current = key

    void shellApi()
      .presentation.setBackground(request)
      .catch(error => {
        if (disposed || !scope?.()) {
          return
        }

        log.warn('desktop-visual', 'Background publish failed', error)
        publishedKey.current = ''

        if (activeCommand) {
          recordReceipt(activeCommand.play_id, 'failed', String(error))
        }
      })

    return () => {
      disposed = true
    }
  }, [
    active,
    authSessionId,
    activeCommand,
    baseline?.input_image_hash,
    projection?.presentation_epoch,
    stagePlan,
    cinematicKey,
    backgroundId,
    backgroundVersion,
    videoUrl,
    posterUrl,
    outfitUrl,
    actorUrl,
    layered,
    layerActor?.visualId,
    layerActor?.version,
    layerActor?.loop,
    layerScene?.visualId,
    layerScene?.version,
    staticTarget,
    theme,
    motionEnabled,
    motionLevel,
    reduceMotion,
    paused,
    recordReceipt
  ])

  useEffect(() => {
    const scope = captureAuthScope()
    const plays = claimedPlays.current
    const kinds = claimedKinds.current
    const queues = receiptQueues.current

    return () => {
      if (!scope?.()) {
        return
      }

      for (const [playId, statuses] of plays) {
        if (!hasTerminalReceipt(statuses)) {
          const previous = queues.get(playId) ?? Promise.resolve()
          void previous
            .then(async () => {
              if (scope()) {
                await sendVisualReceipt(playId, {
                  status: 'interrupted',
                  receipt_kind: kinds.get(playId) ?? 'image',
                  error: 'Presentation host released'
                })
              }
            })
            .catch(error => log.warn('desktop-visual', 'Shutdown receipt failed', error))
        }
      }
    }
  }, [clientId, authSessionId])
}
