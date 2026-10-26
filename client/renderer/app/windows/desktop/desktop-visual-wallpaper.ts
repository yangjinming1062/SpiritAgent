import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  $visualPlayback,
  $visualsJobs,
  $visualsPresentation,
  claimVisualPlay,
  type PlaybackStatus,
  refreshVisualPresentation,
  refreshVisuals,
  sendVisualReceipt,
  type VisualMediaKind,
  type VisualPlayCommand,
  visualsApi
} from '@/modules/visuals'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceScreenLocked } from '@/shared/store/surfaces'
import { $theme } from '@/shared/store/theme'
import type { DesktopBackgroundRequest } from '@ipc/contracts'

function hasTerminalReceipt(statuses: ReadonlySet<PlaybackStatus> | undefined): boolean {
  return statuses !== undefined && [...statuses].some(status => status !== 'started')
}

function firstNonBlank(...values: readonly (string | null | undefined)[]): string | null {
  return values.find(value => typeof value === 'string' && value.trim().length > 0) ?? null
}

/** 成品展示地址只用于取数；缓存与预加载身份认稳定 ID、成品版本与剥除签名后的不可变路径。 */
function assetPath(url: string | null | undefined): string | null {
  return url ? url.split('?')[0] : null
}

type ReceiptStatus = 'started' | 'finished' | 'failed'

export function useDesktopVisualWallpaper(): void {
  const auth = useStore($auth)
  const presentation = useStore($presentation)
  const locked = useStore($surfaceScreenLocked)
  const theme = useStore($theme)
  const projection = useStore($visualsPresentation)
  const jobs = useStore($visualsJobs)
  const requested = useStore($visualPlayback)
  const active = presentation.effectiveMode === 'desktop' && presentation.status === 'active'
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const [clientId] = useState(() => crypto.randomUUID())
  const [claimed, setClaimed] = useState<VisualPlayCommand | null>(null)
  const [reduceMotion, setReduceMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const claimedPlays = useRef(new Map<string, Set<PlaybackStatus>>())
  const claimedKinds = useRef(new Map<string, VisualMediaKind>())
  const receiptQueues = useRef(new Map<string, Promise<void>>())
  const publishedKey = useRef('')
  const paused = locked || presentation.fullscreen || reduceMotion

  // 持续画面优先于当前影像；图片即持续画面，视频按 clip_kind 循环或短时播放。
  const baseline = projection?.loop_visual ?? projection?.current_visual ?? null

  const pendingPlay = requested ?? projection?.pending_play ?? null
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
    if (!active || !authSessionId || !pendingPlay) {
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
              status: 'failed',
              receipt_kind: pendingPlay.media_kind,
              error: 'interrupted'
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
  }, [active, authSessionId, pendingPlay, clientId, recordReceipt])

  useEffect(() => {
    const scope = captureAuthScope()

    return shellApi().presentation.onBackgroundPlayback(receipt => {
      if (!scope?.()) {
        return
      }

      if (staticTarget?.request_id === receipt.playId) {
        if (
          (receipt.status === 'started' || receipt.status === 'failed') &&
          !staticReceipts.current.has(receipt.playId)
        ) {
          staticReceipts.current.add(receipt.playId)
          void visualsApi
            .receiptStatic({
              request_id: receipt.playId,
              presentation_epoch: staticTarget.presentation_epoch,
              state_revision: staticTarget.state_revision,
              receipt_kind: 'desktop_static',
              status: receipt.status,
              error: receipt.error ?? null
            })
            .then(() => refreshVisualPresentation())
            .catch(error => {
              staticReceipts.current.delete(receipt.playId)
              log.warn('desktop-visual', 'Static receipt failed', error)
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
              : 'failed'

      if (status) {
        recordReceipt(receipt.playId, status, receipt.error)
      }
    })
  }, [authSessionId, recordReceipt, staticTarget])

  const activeCommand =
    claimed &&
    Date.parse(claimed.expires_at) > Date.now() &&
    claimed.presentation_epoch >= (projection?.presentation_epoch ?? 0) &&
    (claimed.state_revision >= (projection?.state_revision ?? 0) ||
      (claimed.visual_id === projection?.current_visual?.id &&
        claimed.visual_version === projection.current_visual.version))
      ? claimed
      : null

  useEffect(() => {
    if (claimed && (!active || !authSessionId || !activeCommand)) {
      recordReceipt(claimed.play_id, 'failed', 'Playback interrupted')
    }
  }, [active, authSessionId, claimed, activeCommand, recordReceipt])

  const selectedMediaKind = activeCommand?.media_kind ?? baseline?.media_kind

  const videoUrl =
    !staticTarget && selectedMediaKind === 'video'
      ? firstNonBlank(assetPath(activeCommand?.media_path), assetPath(baseline?.media_url))
      : null

  const posterUrl = staticTarget
    ? assetPath(staticTarget.scene?.path)
    : firstNonBlank(
        assetPath(activeCommand?.media_kind === 'image' ? activeCommand.media_path : null),
        assetPath(activeCommand?.poster_path),
        assetPath(baseline?.poster_url),
        assetPath(baseline?.media_kind === 'image' ? baseline?.media_url : null)
      )

  const outfitUrl = staticTarget ? assetPath(staticTarget.outfit?.path) : null

  useEffect(() => {
    if (!active || !authSessionId) {
      return
    }

    const scope = captureAuthScope()
    let disposed = false

    const request: DesktopBackgroundRequest = {
      authSessionId,
      video: videoUrl ? { url: videoUrl } : null,
      poster: posterUrl ? { url: posterUrl } : null,
      outfit: outfitUrl ? { url: outfitUrl } : null,
      playId: staticTarget?.request_id ?? activeCommand?.play_id ?? null,
      visualId: staticTarget ? null : (activeCommand?.visual_id ?? baseline?.id ?? null),
      visualVersion: staticTarget ? null : (activeCommand?.visual_version ?? baseline?.version ?? null),
      presentationEpoch: staticTarget?.presentation_epoch ?? projection?.presentation_epoch ?? 0,
      expiresAt: staticTarget ? null : (activeCommand?.expires_at ?? null),
      kind: !staticTarget && activeCommand ? (activeCommand.loop ? 'loop' : 'once') : 'loop',
      // 短动作结束后返回保留的持续画面；独立播放不关联基准。
      continuity:
        !staticTarget && (activeCommand?.input_image_hash ?? baseline?.input_image_hash) ? 'base' : 'independent',
      transition: staticTarget ? 'fade' : (activeCommand?.transition ?? 'natural'),
      geometryMode: 'target',
      inputImageHash: staticTarget ? null : (activeCommand?.input_image_hash ?? baseline?.input_image_hash ?? null),
      theme,
      reduceMotion,
      paused,
      clear: !videoUrl && !posterUrl
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
    baseline?.id,
    baseline?.version,
    baseline?.input_image_hash,
    projection?.presentation_epoch,
    videoUrl,
    posterUrl,
    outfitUrl,
    staticTarget,
    theme,
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
                  status: 'failed',
                  receipt_kind: kinds.get(playId) ?? 'image',
                  error: 'interrupted'
                })
              }
            })
            .catch(error => log.warn('desktop-visual', 'Shutdown receipt failed', error))
        }
      }
    }
  }, [clientId, authSessionId])
}
