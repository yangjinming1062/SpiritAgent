import { useStore } from '@nanostores/react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { $motionLevel, isCharacterMediaVisible, observeCharacterStageVisibility } from '@/features/character'
import {
  $ambientLoopChoice,
  $failedStageMedia,
  $performance,
  $visualsPresentation,
  $wakePerformance,
  bindPresentationDirector,
  claimVisualPlay,
  commandTargetsSurface,
  pendingStaticPresentation,
  recordDisplayedCompleteVideo,
  recordLocalStagePlayback,
  recordStageMediaFailure,
  refreshVisualPresentation,
  reportLocalFrame,
  sendStaticReceipt,
  sendVisualReceipt,
  staticPresentationKey,
  visualPerformancePorts,
  type VisualPlayCommand
} from '@/features/creation/visuals'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { visualSurface } from '@/shared/store/surfaces'
import { $lifeStageMedia, type LifeFrameStatus } from '@/shared/store/window-stage'

import { createCompletePlaybackAttempt, deriveFullPresentationTarget } from './visual-presentation'

function observeLifeStage(notify: () => void): () => void {
  const stopVisibility = observeCharacterStageVisibility(notify)
  const stopMotion = $motionLevel.listen(notify)
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  motion.addEventListener('change', notify)

  return () => {
    stopVisibility()
    stopMotion()
    motion.removeEventListener('change', notify)
  }
}

function visibleLifeStage(): boolean {
  return visualSurface() === 'life' && isCharacterMediaVisible()
}

function allowsLifeMotion(): boolean {
  return $motionLevel.get() !== 'off' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** 主窗口只在可见生活区认领整景指令，正式回执由实际展示的首帧驱动。 */
export function useLifeVisualPresentation(): void {
  const auth = useStore($auth)
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const stageEpoch = useStore($presentation).stageEpoch
  const projection = useStore($visualsPresentation)
  const pending = useStore($performance).command
  const motion = useStore($motionLevel)
  const [visible, setVisible] = useState(false)
  const [motionEnabled, setMotionEnabled] = useState(false)
  const [claimed, setClaimed] = useState<VisualPlayCommand | null>(null)
  const [playbackAttempt] = useState(createCompletePlaybackAttempt)
  const claimClient = useRef(crypto.randomUUID())
  const settled = useRef(new Set<string>())
  const failedMedia = useStore($failedStageMedia)
  const ambient = useStore($ambientLoopChoice)
  const wake = useStore($wakePerformance)

  useEffect(() => {
    const refresh = (): void => {
      setVisible(visibleLifeStage())
      setMotionEnabled(allowsLifeMotion())
    }

    return observeLifeStage(refresh)
  }, [])

  useEffect(() => {
    settled.current.clear()
    setClaimed(null)

    if (!sessionId || !visible) {
      return
    }

    const stop = bindPresentationDirector(visualPerformancePorts('life'), () => $visualsPresentation.get(), {
      surface: 'life',
      isEligible: visibleLifeStage,
      isMotionEnabled: allowsLifeMotion,
      isSubtle: () => $motionLevel.get() === 'subtle',
      isMechanical: () => false,
      onChanged: observeLifeStage
    })

    void refreshVisualPresentation()

    return stop
  }, [sessionId, stageEpoch, visible])

  useEffect(() => {
    if (
      !sessionId ||
      !visible ||
      !pending ||
      !commandTargetsSurface(pending, 'life') ||
      (pending.media_kind === 'video' && !motionEnabled) ||
      Date.parse(pending.expires_at) <= Date.now()
    ) {
      return
    }

    const scope = captureAuthScope()
    let active = true
    void claimVisualPlay(pending.play_id, claimClient.current, 'life')
      .then(async accepted => {
        if (!accepted || !scope?.()) {
          return
        }

        if (!active || !visibleLifeStage() || $performance.get().command?.play_id !== pending.play_id) {
          await sendVisualReceipt(pending.play_id, { status: 'interrupted', receipt_kind: pending.media_kind })

          return
        }

        setClaimed(pending)
      })
      .catch(error => log.warn('life-stage', 'Playback claim failed', error))

    return () => {
      active = false
    }
  }, [sessionId, visible, pending, motionEnabled])

  const command =
    visible && claimed && claimed.play_id === pending?.play_id && (claimed.media_kind === 'image' || motionEnabled)
      ? claimed
      : null

  useEffect(() => {
    if (visible && claimed?.media_kind === 'video' && claimed.play_id === pending?.play_id && !motionEnabled) {
      void sendVisualReceipt(claimed.play_id, { status: 'interrupted', receipt_kind: 'video' }).catch(error =>
        log.warn('life-stage', 'Playback receipt failed', error)
      )
    }
  }, [claimed, motionEnabled, pending?.play_id, visible])
  const staticTarget = pendingStaticPresentation(projection)

  const target = deriveFullPresentationTarget({
    projection,
    activeCommand: command,
    staticTarget,
    surface: 'life',
    subtle: motion === 'subtle',
    motionEnabled,
    paused: !visible,
    failedMedia,
    ambient,
    wake
  })

  const url = (target.video ?? target.poster)?.split('?')[0] ?? null
  const mediaKind = target.video ? 'video' : 'image'
  const visualKey = target.visualId && target.visualVersion ? `${target.visualId}:${target.visualVersion}` : null
  const targetKey = target.playId ?? url

  const selected = useRef<{ key: string | null; playback: (status: LifeFrameStatus, error?: string) => void } | null>(
    null
  )

  useLayoutEffect(() => {
    const key = playbackAttempt(sessionId && visible && url ? targetKey : null)

    if (!sessionId || !visible || !url || !key) {
      if (visible && staticTarget && !url) {
        void acknowledgeStatic('failed', 'Frozen scene is unavailable')
      }

      selected.current = null
      $lifeStageMedia.set(null)
      recordDisplayedCompleteVideo(null)

      return
    }

    const scope = captureAuthScope()
    const isCurrent = (): boolean => Boolean(scope?.() && visibleLifeStage() && selected.current?.key === key)
    const wakePlayId = target.playId?.startsWith('render:wake:') ? target.playId : null

    const playback = (status: LifeFrameStatus, error?: string): void => {
      if (!isCurrent() && !((command || wakePlayId) && status === 'interrupted' && scope?.())) {
        return
      }

      if (error) {
        log.warn('life-stage', 'Media playback failed', error)
      }

      if (status === 'failed' && visualKey) {
        recordStageMediaFailure(visualKey)
      }

      if (status === 'started') {
        recordDisplayedCompleteVideo(
          mediaKind === 'video' && target.backgroundKind === 'cinematic' && visualKey
            ? { key: visualKey, sceneHash: target.sceneHash }
            : null
        )
      }

      const receiptKey = `${key}:${status}`

      if (settled.current.has(receiptKey)) {
        return
      }

      settled.current.add(receiptKey)
      const oldest = settled.current.values().next().value

      if (oldest && settled.current.size > 64) {
        settled.current.delete(oldest)
      }

      if (command) {
        void sendVisualReceipt(command.play_id, { status, receipt_kind: command.media_kind, error })
          .catch(error => log.warn('life-stage', 'Playback receipt failed', error))
          .finally(() => {
            if (scope?.()) {
              void refreshVisualPresentation()
            }
          })

        return
      }

      if (wakePlayId) {
        recordLocalStagePlayback(wakePlayId, status)
      }

      if (staticTarget && target.playId === staticPresentationKey(staticTarget)) {
        if (status === 'started' || status === 'failed') {
          void acknowledgeStatic(status, error)
        }
      } else if (status === 'started') {
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
            receipt_kind: mediaKind,
            target_surface: 'life'
          },
          isCurrent
        ).catch(error => log.warn('life-stage', 'Local frame receipt failed', error))
      }
    }

    selected.current = { key, playback }
    const previous = $lifeStageMedia.get()

    if (previous?.owner !== sessionId || previous.key !== key || previous.url !== url) {
      $lifeStageMedia.set({
        key,
        owner: sessionId,
        kind: mediaKind,
        url,
        loop: target.kind === 'loop',
        natural: !command && target.transition === 'natural',
        mediaKey: visualKey ?? key,
        sceneHash: target.sceneHash,
        presentationEpoch: target.presentationEpoch,
        expiresAt: target.expiresAt ? Date.parse(target.expiresAt) : null,
        playback: (status, error) => {
          if (selected.current?.key === key) {
            selected.current.playback(status, error)
          } else if (status === 'interrupted') {
            playback(status, error)
          }
        },
        cleared: isEmpty => {
          const stillEmpty = (): boolean => Boolean(scope?.() && visibleLifeStage() && isEmpty())

          if (stillEmpty()) {
            recordDisplayedCompleteVideo(null)
            void reportLocalFrame(
              { presentation_epoch: target.presentationEpoch, receipt_kind: 'empty', target_surface: 'life' },
              stillEmpty
            ).catch(error => log.warn('life-stage', 'Local frame receipt failed', error))
          }
        }
      })
    }

    async function acknowledgeStatic(status: 'started' | 'failed', error?: string): Promise<void> {
      if (!staticTarget || !visibleLifeStage()) {
        return
      }

      try {
        await sendStaticReceipt({
          request_id: staticTarget.request_id,
          presentation_epoch: staticTarget.presentation_epoch,
          state_revision: staticTarget.state_revision,
          receipt_kind: 'window_static',
          status,
          error: error ?? null
        })
        await refreshVisualPresentation()
      } catch (error) {
        log.warn('life-stage', 'Static stage receipt failed', error)
      }
    }
  }, [
    sessionId,
    stageEpoch,
    visible,
    url,
    targetKey,
    playbackAttempt,
    mediaKind,
    visualKey,
    command,
    staticTarget,
    target.playId,
    target.kind,
    target.transition,
    target.backgroundKind,
    target.visualId,
    target.visualVersion,
    target.localSceneHash,
    target.sceneHash,
    target.expiresAt,
    target.presentationEpoch
  ])

  useLayoutEffect(
    () => () => {
      selected.current = null
      $lifeStageMedia.set(null)
      playbackAttempt(null)
      recordDisplayedCompleteVideo(null)
    },
    [sessionId, stageEpoch, playbackAttempt]
  )
}
