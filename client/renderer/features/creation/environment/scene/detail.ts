import { captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource } from '@/shared/lib/resource-request'
import { getStrings } from '@/shared/strings'

import {
  cacheSceneDetails,
  detailCache,
  detailReads,
  resolveScene,
  type SceneAsset,
  sceneIO,
  toScene
} from './state'

export async function fetchSceneDetail(
  sceneId: string,
  media: 'defer' | 'resolve' | 'reload' = 'resolve'
): Promise<SceneAsset | null> {
  const scope = captureAuthScope()

  if (!scope) {
    return null
  }

  const read = ++sceneIO.nextRead
  const startMutation = sceneIO.mutationRevision
  const startEvent = sceneIO.eventVersion

  const isCurrent = (): boolean =>
    scope() &&
    startMutation === sceneIO.mutationRevision &&
    startEvent === sceneIO.eventVersion &&
    read >= (detailReads.get(sceneId) ?? -1)

  const result = await authedResource('companion_scenes.scene_detail_route', { path: { scene_id: sceneId } })

  if (!isCurrent()) {
    return null
  }

  if (!result.ok) {
    if (result.reason === 'unauth') {
      return null
    }

    throw new Error(backendDetailMessage(result.error, getStrings().living.toasts.sceneStatusRefreshFailed))
  }

  if (!result.value || result.value.id !== sceneId) {
    throw new Error(getStrings().living.toasts.sceneStatusRefreshFailed)
  }

  const row = result.value
  const cached = detailCache.get(sceneId)
  const deferMedia = media === 'defer'
  const sameImage = row.url.split(/[?#]/)[0] === cached?.assetUrl.split(/[?#]/)[0]

  const scene = deferMedia
    ? toScene(row, row.url && sameImage ? (cached?.url ?? '') : '', Boolean(sameImage && cached?.imageLoadFailed))
    : await resolveScene(row, media === 'reload')

  if (!isCurrent()) {
    return null
  }

  cacheSceneDetails([scene], read)

  if (deferMedia) {
    void resolveScene(row).then(resolved => {
      if (isCurrent() && detailReads.get(sceneId) === read) {
        cacheSceneDetails([resolved], read)
      }
    })
  }

  return scene
}

export async function loadSceneDetail(sceneId: string): Promise<SceneAsset | null> {
  try {
    return await fetchSceneDetail(sceneId)
  } catch (error) {
    log.warn('scene', 'Scene detail could not be loaded:', error)

    return null
  }
}

export async function reloadSceneImage(sceneId: string): Promise<SceneAsset | null> {
  return fetchSceneDetail(sceneId, 'reload')
}

