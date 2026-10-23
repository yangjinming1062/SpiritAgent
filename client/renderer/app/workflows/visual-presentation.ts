import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { useEffect } from 'react'

import { $stageOutfitPresented, observeCharacterStageVisibility, setStageOutfitSource } from '@/modules/character'
import {
  $visualsPresentation,
  claimVisualPlay,
  refreshVisualPresentation,
  sendVisualReceipt,
  type VisualActualSnapshot,
  type VisualsPresentation
} from '@/modules/visuals'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { isCompanionStageVisible } from '@/shared/store/surfaces'

export const $windowVisualProjection = atom<VisualActualSnapshot | null>(null)
const clientId = crypto.randomUUID()
const settledPlays = new Set<string>()
let stopProjection: (() => void) | null = null
let projectingPlay: string | null = null

function applySources(actual: VisualActualSnapshot | null): void {
  $windowVisualProjection.set(actual)
  setStageOutfitSource(actual?.outfit_path ? { path: actual.outfit_path, name: actual.outfit_description } : null)
}

function waitForStage(path: string, current: () => boolean): Promise<void> {
  return new Promise((resolve, reject) => {
    let stop = (): void => undefined

    const timeout = window.setTimeout(() => {
      stop()
      reject(new Error('Static outfit did not reach the visible stage'))
    }, 15000)

    const check = (): void => {
      if (!current() || $stageOutfitPresented.get() === path) {
        stop()
        window.clearTimeout(timeout)

        if (current()) {
          resolve()
        } else {
          reject(new Error('Presentation changed before display'))
        }
      }
    }

    stop = $stageOutfitPresented.listen(check)
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
    !isCompanionStageVisible() ||
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
    isCompanionStageVisible() &&
    $visualsPresentation.get()?.pending_play?.play_id === pending.play_id

  let claimed = false

  try {
    claimed = await claimVisualPlay(pending.play_id, clientId)

    if (!claimed || !current()) {
      return
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

    applySources({
      ...response.actual,
      visual_id: pending.visual_id,
      visual_version: pending.visual_version,
      visual_description: '',
      outfit_path: pending.outfit.path,
      outfit_hash: pending.outfit.content_hash,
      outfit_description: pending.outfit.description,
      scene_path: pending.scene.path,
      scene_hash: pending.scene.content_hash,
      scene_description: pending.scene.description,
      ready: true
    })
    await waitForStage(pending.outfit.path, current)
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
    projectingPlay = null

    if (scope()) {
      await refreshVisualPresentation()
      applySources($visualsPresentation.get()?.actual ?? null)
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
      void settlePendingPlay(response)
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
  applySources(null)
})
