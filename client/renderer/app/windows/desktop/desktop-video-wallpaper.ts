import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { hydrateWardrobe } from '@/modules/character'
import {
  $desktopVideoContextRevision,
  $desktopVideoPlayback,
  $desktopVideoState,
  acknowledgeDesktopVideoPlay,
  claimDesktopVideoPlay,
  type DesktopVideoPlayCommand,
  type DesktopVideoReceiptStatus,
  ensureDesktopVideoCurrent,
  playDesktopVideoAction,
  refreshDesktopVideos
} from '@/modules/desktop-videos'
import { $activeScene } from '@/modules/scene'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceScreenLocked } from '@/shared/store/surfaces'
import { $theme } from '@/shared/store/theme'

function hasTerminalReceipt(statuses: ReadonlySet<DesktopVideoReceiptStatus> | undefined): boolean {
  return statuses !== undefined && [...statuses].some(status => status !== 'started')
}

export function useDesktopVideoWallpaper(): void {
  const auth = useStore($auth)
  const presentation = useStore($presentation)
  const locked = useStore($surfaceScreenLocked)
  const theme = useStore($theme)
  const scene = useStore($activeScene)
  const state = useStore($desktopVideoState)
  const requested = useStore($desktopVideoPlayback)
  const contextRevision = useStore($desktopVideoContextRevision)
  const observedContextRevision = useRef(contextRevision)
  const observedContextHash = useRef<string | null | undefined>(undefined)
  const preparedEntry = useRef<string | null>(null)
  const active = presentation.effectiveMode === 'desktop' && presentation.status === 'active'
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const [clientId] = useState(() => crypto.randomUUID())
  const [claimed, setClaimed] = useState<DesktopVideoPlayCommand | null>(null)
  const [reduceMotion, setReduceMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const defaultPlay = useRef('')
  const claimedPlays = useRef(new Map<string, Set<DesktopVideoReceiptStatus>>())
  const receiptQueues = useRef(new Map<string, Promise<void>>())
  const paused = locked || presentation.fullscreen || reduceMotion

  const current = state?.current && state.current.context_hash === state.desired_context_hash ? state.current : null

  const currentLoop =
    current?.actions.find(
      item => item.id === state?.selected_action_id && item.kind === 'loop' && item.enabled && item.video_url
    ) ??
    current?.actions.find(
      item => item.id === state?.loop_action_id && item.kind === 'loop' && item.enabled && item.video_url
    ) ??
    current?.actions.find(item => item.key === 'idle' && item.enabled && item.video_url) ??
    null

  const claimedAction = current?.actions.find(action => action.id === claimed?.action_id)

  const activeCommand =
    claimed &&
    claimed.set_id === current?.id &&
    claimed.set_epoch === state?.set_epoch &&
    (claimed.kind !== 'loop' || !claimedAction || claimedAction.version <= claimed.version)
      ? claimed
      : null

  const recordReceipt = useCallback(
    (playId: string, status: DesktopVideoReceiptStatus, error?: string): void => {
      const scope = captureAuthScope()
      const statuses = claimedPlays.current.get(playId)

      if (!scope || !statuses || statuses.has(status) || hasTerminalReceipt(statuses)) {
        return
      }

      statuses.add(status)

      if (status !== 'started') {
        setClaimed(command => (command?.play_id === playId ? null : command))
      }

      const previous = receiptQueues.current.get(playId) ?? Promise.resolve()

      const request = previous
        .then(async () => {
          if (scope()) {
            await acknowledgeDesktopVideoPlay(playId, clientId, status, error)
          }
        })
        .catch(failure => log.warn('desktop-video', 'Playback receipt failed', failure))
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
    },
    [clientId]
  )

  const preparationInProgress =
    state?.current?.actions.some(action => action.status === 'processing' && action.stage !== 'paused') ?? false

  useEffect(() => {
    if (state && observedContextHash.current === undefined) {
      observedContextHash.current = state.desired_context_hash
    }
  }, [state])

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

    void hydrateWardrobe()
    void refreshDesktopVideos()
  }, [authSessionId])

  useEffect(() => {
    if (!active || !authSessionId) {
      observedContextRevision.current = contextRevision

      return
    }

    const entryKey = `${authSessionId}:${presentation.stageEpoch}`
    const explicitEntry = presentation.prepareDesktopMedia && preparedEntry.current !== entryKey

    if (!explicitEntry && contextRevision === observedContextRevision.current) {
      return
    }

    let disposed = false
    const scope = captureAuthScope()
    // 等首轮挂载稳定，StrictMode 的预清理不提交制作请求。
    queueMicrotask(() => {
      if (!disposed) {
        const previousHash = observedContextHash.current
        void refreshDesktopVideos()
          .then(async () => {
            if (disposed || !scope?.()) {
              return
            }

            const hash = $desktopVideoState.get()?.desired_context_hash
            const changed = previousHash !== undefined && hash !== undefined && hash !== previousHash
            observedContextRevision.current = contextRevision
            observedContextHash.current = hash

            if (explicitEntry || changed) {
              if (explicitEntry) {
                preparedEntry.current = entryKey
              }

              await ensureDesktopVideoCurrent(explicitEntry ? 'entry' : 'context_change')
            }
          })
          .catch(error => log.warn('desktop-video', 'Current scene preparation failed', error))
      }
    })

    return () => {
      disposed = true
    }
  }, [active, authSessionId, presentation.prepareDesktopMedia, presentation.stageEpoch, contextRevision])

  useEffect(() => {
    if (!active || !preparationInProgress) {
      return
    }

    const timer = window.setInterval(() => void refreshDesktopVideos(), 5000)

    return () => window.clearInterval(timer)
  }, [active, preparationInProgress])

  useEffect(() => {
    if (!active || !authSessionId || !state || !current || !currentLoop) {
      return
    }

    const ongoingOnce = (command: DesktopVideoPlayCommand | null): boolean => {
      if (
        !command ||
        command.kind !== 'once' ||
        command.set_id !== current.id ||
        command.set_epoch !== state.set_epoch
      ) {
        return false
      }

      const statuses = claimedPlays.current.get(command.play_id)

      if (hasTerminalReceipt(statuses)) {
        return false
      }

      return statuses?.has('started') === true || Date.parse(command.expires_at) > Date.now()
    }

    if (ongoingOnce(requested) || ongoingOnce(claimed)) {
      return
    }

    const key = `${state.set_epoch}:${currentLoop.id}:${currentLoop.video_url}`

    const receiptStatuses = requested ? claimedPlays.current.get(requested.play_id) : undefined

    const settled = hasTerminalReceipt(receiptStatuses)

    if (
      requested &&
      requested.set_id === current.id &&
      requested.set_epoch === state.set_epoch &&
      Date.parse(requested.expires_at) > Date.now() &&
      !settled
    ) {
      if (requested.kind === 'loop' && requested.action_id === currentLoop.id) {
        defaultPlay.current = key
      }

      return
    }

    if (defaultPlay.current === key) {
      return
    }

    defaultPlay.current = key

    void playDesktopVideoAction(currentLoop.id, current.id, 'desktop-entry').catch(error =>
      log.warn('desktop-video', 'Initial playback failed', error)
    )
  }, [active, authSessionId, state, current, currentLoop, requested, claimed])

  useEffect(() => {
    if (!active || !authSessionId || !requested) {
      return
    }

    const scope = captureAuthScope()
    let disposed = false

    const claim = async (): Promise<void> => {
      if (claimedPlays.current.has(requested.play_id)) {
        return
      }

      if (Date.parse(requested.expires_at) <= Date.now() || requested.set_epoch < (state?.set_epoch ?? 0)) {
        return
      }

      if (await claimDesktopVideoPlay(requested.play_id, clientId)) {
        if (disposed || !scope?.()) {
          if (scope?.()) {
            await acknowledgeDesktopVideoPlay(requested.play_id, clientId, 'interrupted')
          }

          return
        }

        claimedPlays.current.set(requested.play_id, new Set())

        if (Date.parse(requested.expires_at) <= Date.now()) {
          recordReceipt(requested.play_id, 'failed', 'Playback request expired before publication')

          return
        }

        setClaimed(requested)
      }
    }

    void claim().catch(error => log.warn('desktop-video', 'Playback claim failed', error))

    return () => {
      disposed = true
    }
  }, [active, authSessionId, requested, clientId, state?.set_epoch, recordReceipt])

  useEffect(() => {
    if (claimed && (!active || !authSessionId || claimed !== activeCommand)) {
      recordReceipt(claimed.play_id, 'interrupted')
    }
  }, [active, authSessionId, claimed, activeCommand, recordReceipt])

  useEffect(() => {
    const scope = captureAuthScope()

    return window.spiritagent.presentation.onBackgroundPlayback(receipt => {
      if (scope?.() && receipt.status !== 'first-frame') {
        recordReceipt(receipt.playId, receipt.status, receipt.error)
      }
    })
  }, [authSessionId, recordReceipt])

  const firstPoster = state?.current?.actions.find(action => action.key === 'idle')?.poster_url ?? null

  const fallbackAction = state?.fallback?.actions.find(
    action => action.kind === 'loop' && action.enabled && action.video_url
  )

  // 保底渲染独立于播放指令与回执。
  const baseline = currentLoop ?? fallbackAction ?? null

  const videoUrl = activeCommand?.video_url ?? baseline?.video_url ?? null
  const posterUrl = activeCommand?.poster_url ?? baseline?.poster_url ?? firstPoster ?? scene?.assetUrl ?? null

  useEffect(() => {
    if (!active || !authSessionId) {
      return
    }

    const scope = captureAuthScope()
    let disposed = false

    void window.spiritagent.presentation
      .setBackground({
        authSessionId,
        video: videoUrl ? { url: videoUrl } : null,
        poster: posterUrl ? { url: posterUrl } : null,
        playId: activeCommand?.play_id ?? null,
        setId: activeCommand?.set_id ?? baseline?.set_id ?? null,
        actionId: activeCommand?.action_id ?? baseline?.id ?? null,
        setEpoch: activeCommand?.set_epoch ?? state?.set_epoch ?? 0,
        expiresAt: activeCommand?.expires_at ?? null,
        kind: activeCommand?.kind ?? 'loop',
        theme,
        reduceMotion,
        paused,
        clear: false
      })
      .catch(error => {
        if (disposed || !scope?.()) {
          return
        }

        log.warn('desktop-video', 'Background publish failed', error)

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
    baseline?.set_id,
    videoUrl,
    posterUrl,
    scene?.assetUrl,
    state?.set_epoch,
    theme,
    reduceMotion,
    paused,
    recordReceipt
  ])

  useEffect(() => {
    const scope = captureAuthScope()
    const plays = claimedPlays.current
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
                await acknowledgeDesktopVideoPlay(playId, clientId, 'interrupted')
              }
            })
            .catch(error => log.warn('desktop-video', 'Shutdown receipt failed', error))
        }
      }
    }
  }, [clientId, authSessionId])
}
