import { captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource } from '@/shared/lib/resource-request'
import { getStrings } from '@/shared/strings'

import {
  $pendingScene,
  $sceneRegenerating,
  $sceneTaskSlow,
  $sceneTaskStatus,
  cacheSceneDetails,
  detailCache,
  detailReads,
  resolveScene,
  type SceneAsset,
  sceneIO,
  startPoll,
  stopPoll,
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

/** 刷新单个场景的最新状态；不等待场景列表或图片字节，业务错误交给页面显示。 */
export async function refreshSceneStatus(sceneId: string): Promise<SceneAsset | null> {
  const scope = captureAuthScope()
  const detail = await fetchSceneDetail(sceneId, 'defer')

  if (!scope?.() || !detail) {
    return null
  }

  // 单场景终态立即解除它的忙碌标记；更早的全局读取不能再覆盖本次查询。
  sceneIO.stateRequest++

  if (detail.status === 'pending' || $pendingScene.get()?.id === sceneId) {
    $pendingScene.set(detail.status === 'pending' ? detail : null)
  }

  if (detail.regeneration?.status === 'pending' || $sceneRegenerating.get()?.id === sceneId) {
    $sceneRegenerating.set(detail.regeneration?.status === 'pending' ? detail : null)
  }

  const pending = $pendingScene.get()
  const taskStatus = !pending ? 'none' : pending.stage === 'waiting_upload' ? 'waiting_upload' : 'pending'
  $sceneTaskStatus.set(taskStatus)
  stopPoll()
  $sceneTaskSlow.set(false)

  if (taskStatus === 'pending' || $sceneRegenerating.get()) {
    startPoll()
  }

  return detail
}

/** 刷新场景状态；已自行读取列表的调用方使用 skipLibraryRefresh 消去重复读取。返回值只确认本次状态快照有效。 */
