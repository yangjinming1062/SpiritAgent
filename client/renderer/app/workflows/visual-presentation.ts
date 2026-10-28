import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { useEffect } from 'react'

import {
  $motionLevel,
  $stageOutfitPresented,
  $windowActorMedia,
  $windowActorPlayback,
  isCharacterMediaVisible,
  observeCharacterStageVisibility,
  setStageOutfitSource
} from '@/modules/character'
import {
  $visualsPresentation,
  bindPresentationDirector,
  claimVisualPlay,
  refreshVisualPresentation,
  resolveAmbientLoop,
  sendStaticReceipt,
  sendVisualReceipt,
  staticPresentationKey,
  type VisualActualSnapshot,
  type VisualPlayCommand,
  visualsApi,
  type VisualsPresentation
} from '@/modules/visuals'
import { $stagePlan, deriveStagePlan } from '@/modules/visuals'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $gateway } from '@/shared/store/gateway'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceRole } from '@/shared/store/surfaces'

export const $windowVisualProjection = atom<VisualActualSnapshot | null>(null)
export const $windowScenePresented = atom<string | null>(null)
export const $windowStaticProjection = atom(false)
const clientId = crypto.randomUUID()
const settledPlays = new Set<string>()
let windowCommand: VisualPlayCommand | null = null
let stopProjection: (() => void) | null = null
let stopDirector: (() => void) | null = null
let projectingPlay: string | null = null
let projectionConsumers = 0

function applySources(actual: VisualActualSnapshot | null): void {
  $windowVisualProjection.set(actual)
  setStageOutfitSource(actual?.outfit_path ? { path: actual.outfit_path, name: actual.outfit_description } : null)
}

function releaseVisualProjection(): void {
  stopProjection?.()
  stopProjection = null
  stopDirector?.()
  stopDirector = null
  projectingPlay = null
  windowCommand = null
  $windowStaticProjection.set(false)
  $windowActorMedia.set(null)
  $windowScenePresented.set(null)
  applySources(null)
}

function waitForStage(path: string, scenePath: string, current: () => boolean): Promise<void> {
  return new Promise((resolve, reject) => {
    let stop = (): void => undefined
    let subscribed = false

    const timeout = window.setTimeout(() => {
      stop()
      reject(new Error('Static outfit did not reach the visible stage'))
    }, 15000)

    const check = (): void => {
      if (!subscribed) {
        return
      }

      const sourcesReady =
        $stageOutfitPresented.get() === path &&
        ($surfaceRole.get() !== 'main' || $windowScenePresented.get() === scenePath)

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
      $windowScenePresented.listen(check),
      observeCharacterStageVisibility(check),
      $auth.listen(check),
      $presentation.listen(check),
      $visualsPresentation.listen(check)
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
      status === 'started' ? 15000 : 75000
    )

    const wanted = `${pending.visual_id}:${pending.visual_version}:${pending.play_id}`

    const check = (): void => {
      if (!subscribed) {
        return
      }

      const state = $windowActorPlayback.get()
      const matched = state?.key === wanted ? state : null
      const ready = matched?.status === status || (status === 'started' && matched?.status === 'finished')

      if (!current() || ready || matched?.status === 'failed') {
        stop()
        window.clearTimeout(timeout)

        if (!current()) {
          reject(new Error('Presentation changed before display'))
        } else if (matched?.status === 'failed') {
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
      $visualsPresentation.listen(check)
    ]

    stop = () => stops.forEach(dispose => dispose())
    subscribed = true
    check()
  })
}

async function settlePendingPlay(response: VisualsPresentation): Promise<void> {
  const pending = response.pending_play
  const scope = captureAuthScope()

  if (
    !scope ||
    !pending ||
    $presentation.get().effectiveMode !== 'window' ||
    !isCharacterMediaVisible() ||
    settledPlays.has(pending.play_id) ||
    projectingPlay ||
    Date.parse(pending.expires_at) <= Date.now() ||
    $motionLevel.get() === 'off' ||
    (pending.asset_kind === 'actor' &&
      (window.matchMedia('(prefers-reduced-motion: reduce)').matches || $presentation.get().fullscreen))
  ) {
    return
  }

  settledPlays.add(pending.play_id)
  projectingPlay = pending.play_id

  let started = false

  const current = (): boolean => {
    const latest = $visualsPresentation.get()

    return (
      scope() &&
      $presentation.get().effectiveMode === 'window' &&
      isCharacterMediaVisible() &&
      (started
        ? latest?.current_visual?.id === pending.visual_id &&
          latest.current_visual.version === pending.visual_version &&
          (!latest.pending_play || latest.pending_play.play_id === pending.play_id)
        : Date.parse(pending.expires_at) > Date.now() && latest?.pending_play?.play_id === pending.play_id)
    )
  }

  let claimed = false

  try {
    claimed = await claimVisualPlay(pending.play_id, clientId)

    if (!claimed) {
      return
    }

    if (!current()) {
      throw new Error('Presentation changed after claim')
    }

    // 透明主体视频指令：等待舞台实际呈现后按视频回执；一次性动作在 ended 后完成。
    if (pending.asset_kind === 'actor' && pending.media_kind === 'video') {
      if (!current()) {
        throw new Error('Presentation changed before display')
      }

      windowCommand = pending
      assembleWindowMedia()
      await waitForActorVideo(pending, current, 'started')
      started = true
      await sendVisualReceipt(pending.play_id, { status: 'started', receipt_kind: 'video' })
      await refreshVisualPresentation()

      if (!pending.loop) {
        await waitForActorVideo(pending, current, 'finished')
      }

      await sendVisualReceipt(pending.play_id, { status: 'finished', receipt_kind: 'video' })

      return
    }

    // 窗口静态投影需要冻结的造型与环境素材；缺少时说明该指令不适合窗口投影。
    const outfit = pending.outfit ?? response.composition?.outfit_material ?? response.static_presentation?.outfit
    const scene = pending.scene ?? response.composition?.scene_material ?? response.static_presentation?.scene

    if (!outfit || !scene) {
      throw new Error('Pending play lacks frozen static sources')
    }

    for (const path of [outfit.path, scene.path]) {
      const url = await backendApi().apiAsset({ url: path, preferCache: true })

      if (!url) {
        throw new Error('Frozen static source is unavailable')
      }

      const image = new Image()
      image.src = url
      await image.decode()
    }

    if (!current()) {
      throw new Error('Presentation changed before display')
    }

    $windowStaticProjection.set(true)
    assembleWindowMedia()
    applySources({
      ...response.actual,
      visual_id: pending.visual_id,
      visual_version: pending.visual_version,
      visual_description: '',
      outfit_path: outfit.path,
      outfit_hash: outfit.content_hash,
      outfit_description: outfit.description,
      scene_path: scene.path,
      scene_hash: scene.content_hash,
      scene_description: scene.description,
      ready: true
    })
    await waitForStage(outfit.path, scene.path, current)
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))

    if (!current()) {
      throw new Error('Presentation changed before acknowledgment')
    }

    await sendVisualReceipt(pending.play_id, { status: 'started', receipt_kind: 'window_static' })
    await sendVisualReceipt(pending.play_id, { status: 'finished', receipt_kind: 'window_static' })
  } catch (error) {
    if (scope() && claimed) {
      await sendVisualReceipt(pending.play_id, {
        status: 'failed',
        receipt_kind: pending.asset_kind === 'actor' ? 'video' : 'window_static',
        error: String(error)
      }).catch(failure => log.warn('visual-presentation', 'Receipt failed', failure))
    }

    log.warn('visual-presentation', 'Window presentation failed', error)
    settledPlays.delete(pending.play_id)
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
        applySources($visualsPresentation.get()?.actual ?? null)
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

  // 演出的点播走完整指令管线：受理→认领→窗口回执→组合推进；导演只决定“播什么”。
  // 只有精灵窗（presentation host）绑定导演：useVisualProjection 也被主窗生活区调用，
  // 主窗的互动请求经 storage 信号转发到这里，避免双宿主并发点播。
  // 资格：已登录、未关闭动态表现且处于窗口模式（桌面模式由桌面宿主的导演负责）。
  if ($gateway.get()?.isPresentationHost) {
    stopDirector = bindPresentationDirector(
      async visualId => {
        await visualsApi.playVisual(visualId)
        await refreshVisualPresentation()
      },
      () => $visualsPresentation.get(),
      {
        isEligible: () =>
          $auth.get().kind === 'authenticated' &&
          $motionLevel.get() !== 'off' &&
          $presentation.get().effectiveMode === 'window',
        isSubtle: () => $motionLevel.get() === 'subtle',
        onChanged: notify => {
          const onChanged = (): void => {
            notify()
            assembleWindowMedia()
            apply($visualsPresentation.get())
          }

          const stopMotion = $motionLevel.listen(onChanged)
          const stopMode = $presentation.listen(onChanged)

          return () => {
            stopMotion()
            stopMode()
          }
        }
      }
    )
  }

  const apply = (response: VisualsPresentation | null, synced = false): void => {
    if (!scope()) {
      return
    }

    if (synced && !projectingPlay) {
      $windowStaticProjection.set(false)
    }

    assembleWindowMedia()

    if (!projectingPlay) {
      applySources(response?.actual ?? null)
    }

    if (response) {
      if (response.static_presentation && !response.pending_play) {
        void settleStaticProjection(response)
      } else {
        void settlePendingPlay(response)
      }
    }
  }

  const stopState = $visualsPresentation.listen(response => apply(response, true))
  const stopPlan = $stagePlan.listen(assembleWindowMedia)
  const stopVisibility = observeCharacterStageVisibility(() => apply($visualsPresentation.get()))
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  const motionChanged = (): void => apply($visualsPresentation.get())
  motion.addEventListener('change', motionChanged)

  stopProjection = () => {
    stopState()
    stopPlan()
    stopVisibility()
    motion.removeEventListener('change', motionChanged)
  }

  apply($visualsPresentation.get())
  void refreshVisualPresentation()
}

async function settleStaticProjection(response: VisualsPresentation): Promise<void> {
  const target = response.static_presentation
  const scope = captureAuthScope()
  const key = target ? staticPresentationKey(target) : ''

  if (
    !target ||
    !scope ||
    projectingPlay ||
    settledPlays.has(key) ||
    (target.retry_at !== null && Date.parse(target.retry_at) > Date.now()) ||
    !isCharacterMediaVisible() ||
    $presentation.get().effectiveMode !== 'window'
  ) {
    return
  }

  projectingPlay = key

  const current = (): boolean => {
    const latest = $visualsPresentation.get()

    return (
      scope() &&
      isCharacterMediaVisible() &&
      $presentation.get().effectiveMode === 'window' &&
      !latest?.pending_play &&
      Boolean(latest?.static_presentation && staticPresentationKey(latest.static_presentation) === key)
    )
  }

  try {
    let renderError: string | null = null

    try {
      if (target.outfit && target.scene) {
        $windowStaticProjection.set(true)
        assembleWindowMedia()
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
          scene_path: target.scene.path,
          scene_hash: target.scene.content_hash,
          scene_description: target.scene.description,
          identity_path: target.identity_path,
          identity_hash: target.identity_hash,
          ready: true
        })
        await waitForStage(target.outfit.path, target.scene.path, current)
      } else {
        applySources(null)
      }

      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    } catch (error) {
      renderError = String(error)
    }

    if (!current()) {
      return
    }

    settledPlays.add(key)
    await sendStaticReceipt({
      request_id: target.request_id,
      presentation_epoch: target.presentation_epoch,
      state_revision: target.state_revision,
      receipt_kind: 'window_static',
      status: renderError ? 'failed' : 'started',
      error: renderError
    })
  } catch (error) {
    if (scope()) {
      log.warn('visual-presentation', 'Static receipt failed', error)
    }
  } finally {
    if (projectingPlay === key) {
      projectingPlay = null
      assembleWindowMedia()
    }

    if (scope()) {
      await refreshVisualPresentation()
    }
  }
}

export function useVisualProjection(): void {
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
  }, [active, authSessionId])
}

/** 只把认领成功的指令交给主体层；其他窗口只能播放有效基础组合（含导演的本地常驻选择）。 */
function assembleWindowMedia(): void {
  const auth = $auth.get()
  const presentation = $visualsPresentation.get()
  const plan = deriveStagePlan(presentation, windowCommand, resolveAmbientLoop(presentation))
  const actor = $motionLevel.get() === 'off' || plan.mode === 'static' ? null : plan.actor
  const base = $stagePlan.get().actor

  if (
    $windowStaticProjection.get() ||
    auth.kind !== 'authenticated' ||
    $presentation.get().effectiveMode !== 'window' ||
    !actor
  ) {
    $windowActorMedia.set(null)

    return
  }

  const baseKey = base ? `${base.visualId}:${base.version}` : null
  $windowActorMedia.set({
    key: `${actor.visualId}:${actor.version}${windowCommand ? `:${windowCommand.play_id}` : ''}`,
    owner: auth.snapshot.sessionId,
    url: actor.url,
    width: actor.width,
    height: actor.height,
    loop: actor.loop,
    baseKey,
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
