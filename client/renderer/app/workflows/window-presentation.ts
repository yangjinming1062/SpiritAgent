import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import {
  $motionLevel,
  $spatialLocomotion,
  $spritePointerHeld,
  $stageOutfitPresented,
  $windowActorMedia,
  $windowActorPlayback,
  isCharacterMediaVisible,
  observeCharacterStageVisibility,
  setStageOutfitSource
} from '@/features/character'
import {
  $ambientLoopChoice,
  $failedStageMedia,
  $performance,
  $stagePlan,
  $visualsPresentation,
  $wakePerformance,
  bindPresentationDirector,
  claimVisualPlay,
  deriveStagePlan,
  pendingStaticPresentation,
  recordLocalStagePlayback,
  refreshVisualPresentation,
  sendStaticReceipt,
  sendVisualReceipt,
  staticPresentationKey,
  type VisualActualSnapshot,
  visualPerformancePorts,
  type VisualPlayCommand,
  type VisualsPresentation
} from '@/features/creation/visuals'
import { RENDERER_TIMINGS } from '@/shared/constants'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import { $gateway } from '@/shared/store/gateway'
import { $presentation } from '@/shared/store/presentation'
import { visualSurface } from '@/shared/store/surfaces'

const clientId = crypto.randomUUID()
const settledPlays = new Set<string>()
let windowCommand: VisualPlayCommand | null = null
let stopProjection: (() => void) | null = null
let stopDirector: (() => void) | null = null
let projectingPlay: string | null = null
let projectionConsumers = 0
let staticProjection = false

function applySources(actual: VisualActualSnapshot | null): void {
  setStageOutfitSource(actual?.outfit_path ? { path: actual.outfit_path, name: actual.outfit_description } : null)
}

function applyBaseSources(response: VisualsPresentation | null): void {
  const actual = response?.actual
  const still = $stagePlan.get().actorStillUrl

  applySources(actual && still ? { ...actual, outfit_path: still } : (actual ?? null))
}

function releaseVisualProjection(): void {
  stopProjection?.()
  stopProjection = null
  stopDirector?.()
  stopDirector = null
  projectingPlay = null
  windowCommand = null
  staticProjection = false
  $windowActorMedia.set(null)
  applySources(null)
}

function waitForStage(path: string, current: () => boolean): Promise<void> {
  return new Promise((resolve, reject) => {
    let stop = (): void => undefined
    let subscribed = false

    const timeout = window.setTimeout(() => {
      stop()
      reject(new Error('Static outfit did not reach the visible stage'))
    }, RENDERER_TIMINGS.visualStageWaitTimeoutMs)

    const check = (): void => {
      if (!subscribed) {
        return
      }

      const sourcesReady = $stageOutfitPresented.get() === path

      if (!current() || sourcesReady) {
        stop()
        window.clearTimeout(timeout)

        if (current()) {
          resolve()
        } else {
          reject(new Error('Presentation changed before display'))
        }
      }
    }

    const stops = [
      $stageOutfitPresented.listen(check),
      observeCharacterStageVisibility(check),
      $auth.listen(check),
      $presentation.listen(check),
      $visualsPresentation.listen(check),
      $performance.listen(check),
      $motionLevel.listen(check)
    ]

    stop = () => stops.forEach(dispose => dispose())
    subscribed = true
    check()
  })
}

/** 回执来自播放器的实际播放事件；取得字节和首帧预载都不能代替开播。 */
function waitForActorVideo(
  pending: VisualPlayCommand,
  current: () => boolean,
  status: 'started' | 'finished'
): Promise<void> {
  return new Promise((resolve, reject) => {
    let stop = (): void => undefined
    let subscribed = false

    const timeout = window.setTimeout(
      () => {
        stop()
        reject(new Error('Actor video playback timed out'))
      },
      status === 'started' ? RENDERER_TIMINGS.visualActorStartTimeoutMs : RENDERER_TIMINGS.visualActorFinishTimeoutMs
    )

    const wanted = `${pending.visual_id}:${pending.visual_version}:${pending.play_id}`

    const check = (): void => {
      if (!subscribed) {
        return
      }

      const state = $windowActorPlayback.get()
      const matched = state?.key === wanted ? state : null
      const ready = matched?.status === status || (status === 'started' && matched?.status === 'finished')

      if (!current() || ready || matched?.status === 'failed' || matched?.status === 'interrupted') {
        stop()
        window.clearTimeout(timeout)

        if (!current()) {
          reject(new Error('Presentation changed before display'))
        } else if (matched?.status === 'failed' || matched?.status === 'interrupted') {
          reject(new Error(matched.error || 'Actor video failed'))
        } else {
          resolve()
        }
      }
    }

    const stops = [
      $windowActorPlayback.listen(check),
      observeCharacterStageVisibility(check),
      $auth.listen(check),
      $presentation.listen(check),
      $visualsPresentation.listen(check),
      $performance.listen(check),
      $motionLevel.listen(check)
    ]

    stop = () => stops.forEach(dispose => dispose())
    subscribed = true
    check()
  })
}

async function settlePendingPlay(): Promise<void> {
  const pending = $performance.get().command
  const scope = captureAuthScope()

  if (
    !scope ||
    !pending ||
    !$gateway.get()?.isPresentationHost ||
    pending.asset_kind !== 'actor' ||
    pending.media_kind !== 'video' ||
    visualSurface() !== 'sprite' ||
    $presentation.get().effectiveMode !== 'window' ||
    !isCharacterMediaVisible() ||
    settledPlays.has(pending.play_id) ||
    projectingPlay ||
    Date.parse(pending.expires_at) <= Date.now() ||
    $motionLevel.get() === 'off' ||
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  ) {
    return
  }

  settledPlays.add(pending.play_id)
  projectingPlay = pending.play_id

  let started = false

  const current = (): boolean => {
    return (
      scope() &&
      visualSurface() === 'sprite' &&
      !window.matchMedia('(prefers-reduced-motion: reduce)').matches &&
      $performance.get().command?.play_id === pending.play_id &&
      $motionLevel.get() !== 'off' &&
      $presentation.get().effectiveMode === 'window' &&
      !$presentation.get().fullscreen &&
      isCharacterMediaVisible() &&
      (started || Date.parse(pending.expires_at) > Date.now())
    )
  }

  let claimed = false

  try {
    claimed = await claimVisualPlay(pending.play_id, clientId, 'sprite')

    if (!claimed) {
      return
    }

    if (!current()) {
      throw new Error('Presentation changed after claim')
    }

    windowCommand = pending
    assembleWindowMedia()
    await waitForActorVideo(pending, current, 'started')
    started = true
    await sendVisualReceipt(pending.play_id, { status: 'started', receipt_kind: 'video' })
    await refreshVisualPresentation()

    if (!pending.loop) {
      await waitForActorVideo(pending, current, 'finished')
      await sendVisualReceipt(pending.play_id, { status: 'finished', receipt_kind: 'video' })
    }
  } catch (error) {
    if (scope() && claimed) {
      await sendVisualReceipt(pending.play_id, {
        status:
          !current() ||
          ($windowActorPlayback.get()?.key === `${pending.visual_id}:${pending.visual_version}:${pending.play_id}` &&
            $windowActorPlayback.get()?.status === 'interrupted')
            ? 'interrupted'
            : 'failed',
        receipt_kind: 'video',
        error: String(error)
      }).catch(failure => log.warn('visual-presentation', 'Receipt failed', failure))
    }

    log.warn('visual-presentation', 'Window presentation failed', error)
  } finally {
    if (windowCommand?.play_id === pending.play_id) {
      windowCommand = null
      assembleWindowMedia()
    }

    if (projectingPlay === pending.play_id) {
      projectingPlay = null
      assembleWindowMedia()
    }

    if (scope()) {
      await refreshVisualPresentation()

      if (scope() && !projectingPlay) {
        applyBaseSources($visualsPresentation.get())
      }
    }
  }
}

function ensureVisualProjection(): void {
  if (stopProjection) {
    return
  }

  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  const apply = (response: VisualsPresentation | null, synced = false): void => {
    if (!scope()) {
      return
    }

    if (synced && !projectingPlay) {
      staticProjection = false
    }

    assembleWindowMedia()

    if (!projectingPlay) {
      applyBaseSources(response)
    }

    if (response) {
      if (pendingStaticPresentation(response) && !$performance.get().command) {
        void settleStaticProjection(response)
      } else {
        void settlePendingPlay()
      }
    }
  }

  if ($gateway.get()?.isPresentationHost) {
    const stageEpoch = $presentation.get().stageEpoch
    stopDirector = bindPresentationDirector(visualPerformancePorts('sprite'), () => $visualsPresentation.get(), {
      surface: 'sprite',
      isMotionEnabled: () =>
        $motionLevel.get() !== 'off' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      isEligible: () =>
        visualSurface() === 'sprite' &&
        $auth.get().kind === 'authenticated' &&
        $motionLevel.get() !== 'off' &&
        $presentation.get().effectiveMode === 'window' &&
        $presentation.get().stageEpoch === stageEpoch &&
        !$presentation.get().fullscreen &&
        isCharacterMediaVisible() &&
        !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      isSubtle: () => $motionLevel.get() === 'subtle',
      isMechanical: () => $spatialLocomotion.get() !== 'still' || $spritePointerHeld.get(),
      onChanged: notify => {
        const stops = [
          $motionLevel.listen(notify),
          $presentation.listen(notify),
          $spatialLocomotion.listen(notify),
          $spritePointerHeld.listen(notify),
          observeCharacterStageVisibility(notify)
        ]

        const query = window.matchMedia('(prefers-reduced-motion: reduce)')
        query.addEventListener('change', notify)

        return () => {
          stops.forEach(stop => stop())
          query.removeEventListener('change', notify)
        }
      }
    })
  }

  const stopState = $visualsPresentation.listen(response => apply(response, true))

  const stopPlan = $stagePlan.listen(() => {
    assembleWindowMedia()

    if (!projectingPlay) {
      applyBaseSources($visualsPresentation.get())
    }
  })

  const stopLocalPlayback = $windowActorPlayback.listen(playback => {
    const media = $windowActorMedia.get()

    if (media?.localPlayId && media.key === playback?.key) {
      recordLocalStagePlayback(media.localPlayId, playback.status)
    }
  })

  const stopPerformance = $performance.listen(() => {
    if (windowCommand && $performance.get().command?.play_id !== windowCommand.play_id) {
      windowCommand = null
    }

    apply($visualsPresentation.get())
  })

  const stopMotion = $motionLevel.listen(() => apply($visualsPresentation.get()))
  const stopMovement = $spatialLocomotion.listen(assembleWindowMedia)
  const stopPointer = $spritePointerHeld.listen(assembleWindowMedia)
  const stopVisibility = observeCharacterStageVisibility(() => apply($visualsPresentation.get()))
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  const motionChanged = (): void => apply($visualsPresentation.get())
  motion.addEventListener('change', motionChanged)

  stopProjection = () => {
    stopState()
    stopPlan()
    stopLocalPlayback()
    stopPerformance()
    stopMotion()
    stopMovement()
    stopPointer()
    stopVisibility()
    motion.removeEventListener('change', motionChanged)
  }

  apply($visualsPresentation.get())
  void refreshVisualPresentation()
}

async function settleStaticProjection(response: VisualsPresentation): Promise<void> {
  const target = pendingStaticPresentation(response)
  const scope = captureAuthScope()
  const key = target ? staticPresentationKey(target) : ''

  if (
    !target ||
    !scope ||
    projectingPlay ||
    settledPlays.has(key) ||
    !isCharacterMediaVisible() ||
    visualSurface() !== 'sprite'
  ) {
    return
  }

  projectingPlay = key

  const current = (): boolean => {
    const latest = pendingStaticPresentation($visualsPresentation.get())

    return (
      scope() &&
      isCharacterMediaVisible() &&
      visualSurface() === 'sprite' &&
      !$performance.get().command &&
      Boolean(latest && staticPresentationKey(latest) === key)
    )
  }

  let refresh = true

  try {
    let renderError: string | null = null

    try {
      staticProjection = true
      assembleWindowMedia()

      if (target.outfit) {
        applySources({
          ...response.actual,
          visual_id: null,
          visual_version: null,
          media_kind: null,
          visual_description: '',
          visual_path: null,
          outfit_path: target.outfit.path,
          outfit_hash: target.outfit.content_hash,
          outfit_description: target.outfit.description,
          scene_path: null,
          scene_hash: null,
          scene_description: '',
          identity_path: target.identity_path,
          identity_hash: target.identity_hash,
          ready: true
        })
        await waitForStage(target.outfit.path, current)
      } else {
        throw new Error('Frozen sprite outfit is unavailable')
      }

      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    } catch (error) {
      renderError = String(error)
    }

    if (!current()) {
      return
    }

    const accepted = await sendStaticReceipt({
      request_id: target.request_id,
      presentation_epoch: target.presentation_epoch,
      state_revision: target.state_revision,
      receipt_kind: 'window_static',
      status: renderError ? 'failed' : 'started',
      error: renderError
    })

    if (accepted) {
      settledPlays.add(key)
    }
  } catch (error) {
    refresh = false

    if (scope()) {
      log.warn('visual-presentation', 'Static receipt failed', error)
    }
  } finally {
    if (projectingPlay === key) {
      projectingPlay = null
      staticProjection = false
      applyBaseSources($visualsPresentation.get())
      assembleWindowMedia()
    }

    if (scope() && refresh) {
      await refreshVisualPresentation()
    }
  }
}

/** 窗口模式的呈现装配：影像只提供计划、认领与回执，舞台事实由角色模块提供。 */
export function useWindowPresentation(): void {
  const auth = useStore($auth)
  const presentation = useStore($presentation)
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const active = authSessionId !== null && presentation.effectiveMode === 'window'
  useEffect(() => {
    if (!active) {
      return
    }

    projectionConsumers += 1
    ensureVisualProjection()
    assembleWindowMedia()

    return () => {
      projectionConsumers = Math.max(0, projectionConsumers - 1)

      if (projectionConsumers === 0) {
        releaseVisualProjection()
      }
    }
  }, [active, authSessionId, presentation.stageEpoch])
}

/** 只把认领成功的指令交给主体层；其他窗口只能播放有效基础组合（含导演的本地常驻选择）。 */
function assembleWindowMedia(): void {
  const auth = $auth.get()
  const presentation = $visualsPresentation.get()

  const motion = $motionLevel.get()

  const plan = deriveStagePlan(presentation, windowCommand, {
    ambient: $ambientLoopChoice.get(),
    wake: $wakePerformance.get(),
    failedMedia: $failedStageMedia.get(),
    subtle: motion === 'subtle',
    motionEnabled: motion !== 'off' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })

  const actor = $spatialLocomotion.get() !== 'still' || $spritePointerHeld.get() ? null : plan.actor

  const base = deriveStagePlan(presentation, null, {
    ambient: $ambientLoopChoice.get(),
    subtle: motion === 'subtle'
  }).actor

  if (staticProjection || auth.kind !== 'authenticated' || $presentation.get().effectiveMode !== 'window' || !actor) {
    $windowActorMedia.set(null)

    return
  }

  const baseKey = base ? `${base.visualId}:${base.version}` : null
  $windowActorMedia.set({
    key: `${actor.visualId}:${actor.version}${windowCommand ? `:${windowCommand.play_id}` : plan.localPlay ? `:${plan.localPlay.id}` : ''}`,
    localPlayId: plan.localPlay?.id ?? null,
    owner: auth.snapshot.sessionId,
    url: actor.url,
    width: actor.width,
    height: actor.height,
    loop: actor.loop,
    baseKey,
    layout: plan.layout,
    body: actor.body
      ? {
          footAnchor: actor.body.foot_anchor ?? null,
          headAnchor: actor.body.head_anchor ?? null,
          bodyBounds: actor.body.body_bounds ?? null,
          hitBounds: actor.body.hit_bounds ?? null
        }
      : null
  })
}

registerStorageClearHandler(() => {
  releaseVisualProjection()
  settledPlays.clear()
})
