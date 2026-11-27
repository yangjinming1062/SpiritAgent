import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { createCompletePlaybackAttempt, deriveVisualPresentationTarget } from '@/app/workflows/visual-presentation'
import { $motionLevel } from '@/features/character'
import {
  $ambientLoopChoice,
  $failedStageMedia,
  $performance,
  $stagePlan,
  $visualsError,
  $visualsJobs,
  $visualsPresentation,
  $visualsPresentationError,
  $wakePerformance,
  bindPresentationDirector,
  claimVisualPlay,
  commandTargetsSurface,
  pendingStaticPresentation,
  recordDisplayedCompleteVideo,
  recordLocalStagePlayback,
  recordStageMediaFailure,
  refreshVisualJobs,
  refreshVisualPresentation,
  refreshVisuals,
  reportLocalFrame,
  retryStageMedia,
  sendStaticReceipt,
  sendVisualReceipt,
  staticPresentationKey,
  visualPerformancePorts,
  type VisualPlayCommand
} from '@/features/creation/visuals'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceScreenLocked, visualSurface } from '@/shared/store/surfaces'
import { $theme } from '@/shared/store/theme'
import { useStrings } from '@/shared/strings'
import { DESKTOP_EMPTY_PLAY_PREFIX } from '@ipc/contracts'
import type { VisualMediaKind } from '@protocol'

function hasTerminalReceipt(statuses: ReadonlySet<ReceiptStatus> | undefined): boolean {
  return statuses !== undefined && [...statuses].some(status => status !== 'started')
}

type ReceiptStatus = 'started' | 'finished' | 'failed' | 'interrupted'

// 桌面壁纸状态由呈现事实与任务进度推导，桌面根组件只消费结果。
export function useDesktopWallpaperStatus(): { error: boolean; status: string | null } {
  const strings = useStrings()
  const presentation = useStore($visualsPresentation)
  const presentationError = useStore($visualsPresentationError)
  const libraryError = useStore($visualsError)
  const jobs = useStore($visualsJobs)
  const error = presentationError ?? libraryError
  const busy = jobs.filter(job => job.state === 'running' || job.state === 'queued' || job.state === 'paused')
  const stage = useStore($stagePlan)
  const idle = stage.scene || stage.cinematic || presentation?.current_visual

  const status = error
    ? error
    : busy.length > 0
      ? busy.some(job => job.state === 'paused')
        ? strings.visuals.paused
        : strings.common.actionVariants.statuses.processing
      : !idle
        ? strings.visuals.emptyTitle
        : null

  return { error: Boolean(error), status }
}

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
  const [reduceMotion, setReduceMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const claimedPlays = useRef(new Map<string, Set<ReceiptStatus>>())
  const claimedKinds = useRef(new Map<string, VisualMediaKind>())
  const receiptQueues = useRef(new Map<string, Promise<void>>())
  const publishedKey = useRef('')
  const displayedTarget = useRef<ReturnType<typeof deriveVisualPresentationTarget> | null>(null)
  const [playbackAttempt] = useState(createCompletePlaybackAttempt)
  const visible = active && presentation.stageAvailable && !locked && !presentation.fullscreen
  const paused = !visible
  const motionEnabled = motionLevel !== 'off' && !reduceMotion

  const pendingPlay = performance.command
  const staticTarget = pendingPlay || !visible ? null : pendingStaticPresentation(projection)
  const lastStaticReceipt = useRef<string | null>(null)

  const settleStatic = useCallback(
    async (
      target: NonNullable<ReturnType<typeof pendingStaticPresentation>>,
      status: 'started' | 'failed',
      error?: string | null
    ): Promise<void> => {
      const scope = captureAuthScope()
      const key = staticPresentationKey(target)
      const current = $presentation.get()

      if (
        !scope ||
        visualSurface() !== 'desktop' ||
        current.effectiveMode !== 'desktop' ||
        current.status !== 'active' ||
        $surfaceScreenLocked.get() ||
        lastStaticReceipt.current === key
      ) {
        return
      }

      lastStaticReceipt.current = key

      try {
        await sendStaticReceipt({
          request_id: target.request_id,
          presentation_epoch: target.presentation_epoch,
          state_revision: target.state_revision,
          receipt_kind: 'desktop_static',
          status,
          error: error ?? null
        })
      } catch (failure) {
        if (scope()) {
          if (lastStaticReceipt.current === key) {
            lastStaticReceipt.current = null
          }

          log.warn('desktop-visual', 'Static receipt failed', failure)
        }
      } finally {
        if (scope()) {
          void refreshVisualPresentation()
        }
      }
    },
    []
  )

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
    displayedTarget.current = null
    playbackAttempt(null)
    claimedPlays.current.clear()
    claimedKinds.current.clear()
    receiptQueues.current.clear()
    lastStaticReceipt.current = null
    setClaimed(null)

    return () => {
      displayedTarget.current = null
    }
  }, [authSessionId, playbackAttempt])

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

  // 桌面宿主消费服务端的完整场景选择并认领正式演出。
  useEffect(() => {
    if (!active || !authSessionId) {
      return
    }

    const stageEpoch = $presentation.get().stageEpoch

    return bindPresentationDirector(visualPerformancePorts('desktop'), () => $visualsPresentation.get(), {
      surface: 'desktop',
      isMotionEnabled: () =>
        $motionLevel.get() !== 'off' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      isEligible: () =>
        $auth.get().kind === 'authenticated' &&
        visualSurface() === 'desktop' &&
        $presentation.get().effectiveMode === 'desktop' &&
        $presentation.get().status === 'active' &&
        $presentation.get().stageEpoch === stageEpoch &&
        !$presentation.get().fullscreen &&
        !$surfaceScreenLocked.get(),
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
      void refreshVisualJobs()
      void refreshVisualPresentation()
    }, 5000)

    return () => window.clearInterval(timer)
  }, [active, preparationInProgress])

  // 播放认领：仅桌面承载认领待播指令；`queued` 只表示受理，实际回执才更新呈现事实。
  useEffect(() => {
    if (
      !visible ||
      !authSessionId ||
      !pendingPlay ||
      !commandTargetsSurface(pendingPlay, 'desktop') ||
      (pendingPlay.media_kind === 'video' && !motionEnabled)
    ) {
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

      if (await claimVisualPlay(pendingPlay.play_id, clientId, 'desktop')) {
        if (disposed || !scope?.() || visualSurface() !== 'desktop') {
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
  }, [visible, authSessionId, pendingPlay, clientId, motionEnabled, recordReceipt])

  useEffect(() => {
    const scope = captureAuthScope()

    return shellApi().presentation.onBackgroundPlayback(receipt => {
      if (!scope?.()) {
        return
      }

      if (receipt.playId.startsWith('render:')) {
        const target = displayedTarget.current

        const sameTarget = (): boolean => displayedTarget.current?.playId === receipt.playId
        const current = (): boolean => visualSurface() === 'desktop' && sameTarget()

        if (receipt.playId.startsWith(DESKTOP_EMPTY_PLAY_PREFIX)) {
          if (target?.clear && receipt.status === 'completed' && current()) {
            recordDisplayedCompleteVideo(null)
            void reportLocalFrame(
              { presentation_epoch: target.presentationEpoch, receipt_kind: 'empty', target_surface: 'desktop' },
              current
            ).catch(error => log.warn('desktop-visual', 'Empty frame receipt failed', error))
          }

          return
        }

        const key = target?.visualId && target.visualVersion ? `${target.visualId}:${target.visualVersion}` : null

        if (current() && receipt.status === 'failed' && key) {
          recordStageMediaFailure(key)
        }

        if (receipt.playId.startsWith('render:wake:') && receipt.status !== 'first-frame') {
          recordLocalStagePlayback(receipt.playId, receipt.status === 'completed' ? 'finished' : receipt.status)
        }

        if (receipt.status === 'started' && target && sameTarget()) {
          recordDisplayedCompleteVideo(
            target.video && target.backgroundKind === 'cinematic' && key ? { key, sceneHash: target.sceneHash } : null
          )

          if (!current()) {
            return
          }

          const frame = target.localSceneHash
            ? { scene_hash: target.localSceneHash }
            : target.visualId && target.visualVersion
              ? { visual_id: target.visualId, visual_version: target.visualVersion }
              : null

          if (!frame) {
            return
          }

          void reportLocalFrame(
            {
              ...frame,
              presentation_epoch: target.presentationEpoch,
              receipt_kind: target.video ? 'video' : 'image',
              target_surface: 'desktop'
            },
            current
          ).catch(error => log.warn('desktop-visual', 'Local frame receipt failed', error))
        }

        return
      }

      if (receipt.status === 'started' && claimed?.play_id === receipt.playId && visualSurface() === 'desktop') {
        recordDisplayedCompleteVideo(
          claimed.asset_kind === 'cinematic' && claimed.media_kind === 'video'
            ? { key: `${claimed.visual_id}:${claimed.visual_version}`, sceneHash: claimed.scene?.content_hash ?? null }
            : null
        )
      }

      if (receipt.status === 'failed' && claimed?.play_id === receipt.playId && claimed.asset_kind === 'cinematic') {
        recordStageMediaFailure(`${claimed.visual_id}:${claimed.visual_version}`)
      }

      if (staticTarget && receipt.playId === staticPresentationKey(staticTarget)) {
        if (receipt.status === 'started' && visualSurface() === 'desktop') {
          recordDisplayedCompleteVideo(null)
        }

        if (receipt.status === 'started' || receipt.status === 'failed') {
          void settleStatic(staticTarget, receipt.status, receipt.error)
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
  }, [authSessionId, recordReceipt, settleStatic, staticTarget, claimed])

  const activeCommand =
    claimed &&
    (claimed.media_kind === 'image' || motionEnabled) &&
    visible &&
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
      retryStageMedia(`${activeCommand.visual_id}:${activeCommand.visual_version}`)
    }
  }, [activeCommand?.asset_kind, activeCommand?.play_id, activeCommand?.visual_id, activeCommand?.visual_version])

  useEffect(() => {
    if (claimed && (!active || !authSessionId || !activeCommand)) {
      recordReceipt(claimed.play_id, 'interrupted', 'Playback interrupted')
    }
  }, [active, authSessionId, claimed, activeCommand, recordReceipt])

  const failedMedia = useStore($failedStageMedia)
  const ambient = useStore($ambientLoopChoice)
  const wake = useStore($wakePerformance)

  const request = useMemo(
    () =>
      active && authSessionId
        ? deriveVisualPresentationTarget({
            authSessionId,
            projection,
            activeCommand,
            staticTarget,
            theme,
            subtle: motionLevel === 'subtle',
            motionEnabled,
            reduceMotion,
            paused,
            failedMedia,
            ambient,
            wake
          })
        : null,
    [
      active,
      authSessionId,
      projection,
      activeCommand,
      staticTarget,
      theme,
      motionLevel,
      motionEnabled,
      reduceMotion,
      paused,
      failedMedia,
      ambient,
      wake
    ]
  )

  // 发布只在目标真正变化时发生；失败保留当前画面并把回执记到认领指令上。
  useEffect(() => {
    if (!active || !authSessionId || !request) {
      publishedKey.current = ''
      displayedTarget.current = null
      playbackAttempt(null)

      return
    }

    const key = JSON.stringify(request)

    if (publishedKey.current === key) {
      return
    }

    const scope = captureAuthScope()
    publishedKey.current = key
    const attempt = { ...request, playId: playbackAttempt(request.playId) }
    displayedTarget.current = attempt

    // 暂停时播放器可保留同一槽位，恢复也不会重发它的首帧。
    if (!visible && request.clear) {
      recordDisplayedCompleteVideo(null)
    }

    const { localSceneHash: _localSceneHash, sceneHash: _sceneHash, ...background } = attempt
    void shellApi()
      .presentation.setBackground(background)
      .catch(error => {
        if (displayedTarget.current !== attempt || !scope?.()) {
          return
        }

        log.warn('desktop-visual', 'Background publish failed', error)
        publishedKey.current = ''

        if (activeCommand) {
          recordReceipt(activeCommand.play_id, 'failed', String(error))
        } else if (staticTarget && attempt.playId === staticPresentationKey(staticTarget)) {
          void settleStatic(staticTarget, 'failed', String(error))
        } else if (attempt.playId?.startsWith('render:')) {
          if (attempt.visualId && attempt.visualVersion) {
            recordStageMediaFailure(`${attempt.visualId}:${attempt.visualVersion}`)
          }

          recordLocalStagePlayback(attempt.playId, 'failed')
        }
      })
  }, [
    active,
    authSessionId,
    request,
    staticTarget,
    activeCommand,
    recordReceipt,
    settleStatic,
    visible,
    playbackAttempt
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
