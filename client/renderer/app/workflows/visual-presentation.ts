import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { useEffect } from 'react'

import {
  $stageOutfitPresented,
  isCharacterStageVisible,
  observeCharacterStageVisibility,
  setStageOutfitSource
} from '@/modules/character'
import {
  $visualsPresentation,
  claimVisualPlay,
  refreshVisualPresentation,
  sendStaticReceipt,
  sendVisualReceipt,
  staticPresentationKey,
  type VisualActualSnapshot,
  type VisualsPresentation
} from '@/modules/visuals'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceRole } from '@/shared/store/surfaces'

export const $windowVisualProjection = atom<VisualActualSnapshot | null>(null)
export const $windowScenePresented = atom<string | null>(null)
const clientId = crypto.randomUUID()
const settledPlays = new Set<string>()
let stopProjection: (() => void) | null = null
let projectingPlay: string | null = null

function applySources(actual: VisualActualSnapshot | null): void {
  $windowVisualProjection.set(actual)
  setStageOutfitSource(actual?.outfit_path ? { path: actual.outfit_path, name: actual.outfit_description } : null)
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

async function settlePendingPlay(response: VisualsPresentation): Promise<void> {
  const pending = response.pending_play
  const scope = captureAuthScope()

  if (
    !scope ||
    !pending ||
    $presentation.get().effectiveMode !== 'window' ||
    !isCharacterStageVisible() ||
    settledPlays.has(pending.play_id) ||
    projectingPlay ||
    Date.parse(pending.expires_at) <= Date.now()
  ) {
    return
  }

  settledPlays.add(pending.play_id)
  projectingPlay = pending.play_id

  const current = (): boolean =>
    scope() &&
    $presentation.get().effectiveMode === 'window' &&
    isCharacterStageVisible() &&
    Date.parse(pending.expires_at) > Date.now() &&
    $visualsPresentation.get()?.pending_play?.play_id === pending.play_id

  let claimed = false

  try {
    claimed = await claimVisualPlay(pending.play_id, clientId)

    if (!claimed) {
      return
    }

    if (!current()) {
      throw new Error('Presentation changed after claim')
    }

    // 窗口静态投影需要冻结的造型与环境素材；缺少时说明该指令不适合窗口投影。
    if (!pending.outfit || !pending.scene) {
      throw new Error('Pending play lacks frozen static sources')
    }

    for (const path of [pending.outfit.path, pending.scene.path]) {
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

    const { outfit, scene } = pending
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
        receipt_kind: 'window_static',
        error: String(error)
      }).catch(failure => log.warn('visual-presentation', 'Receipt failed', failure))
    }

    log.warn('visual-presentation', 'Static projection failed', error)
    settledPlays.delete(pending.play_id)
  } finally {
    if (projectingPlay === pending.play_id) {
      projectingPlay = null
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

  const apply = (response: VisualsPresentation | null): void => {
    if (!scope()) {
      return
    }

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

  const stopState = $visualsPresentation.listen(apply)
  const stopVisibility = observeCharacterStageVisibility(() => apply($visualsPresentation.get()))

  stopProjection = () => {
    stopState()
    stopVisibility()
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
    !isCharacterStageVisible() ||
    $presentation.get().effectiveMode !== 'window'
  ) {
    return
  }

  projectingPlay = key

  const current = (): boolean => {
    const latest = $visualsPresentation.get()

    return (
      scope() &&
      isCharacterStageVisible() &&
      $presentation.get().effectiveMode === 'window' &&
      !latest?.pending_play &&
      Boolean(latest?.static_presentation && staticPresentationKey(latest.static_presentation) === key)
    )
  }

  try {
    let renderError: string | null = null

    try {
      if (target.outfit && target.scene) {
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
    }

    if (scope()) {
      await refreshVisualPresentation()
    }
  }
}

export function useVisualProjection(): void {
  const auth = useStore($auth)
  const presentation = useStore($presentation)
  useEffect(() => {
    if (auth.kind === 'authenticated' && presentation.effectiveMode === 'window') {
      ensureVisualProjection()
    }
  }, [auth, presentation.effectiveMode])
}

registerStorageClearHandler(() => {
  stopProjection?.()
  stopProjection = null
  projectingPlay = null
  settledPlays.clear()
  $windowScenePresented.set(null)
  applySources(null)
})
