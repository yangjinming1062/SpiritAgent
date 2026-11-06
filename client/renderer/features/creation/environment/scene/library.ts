import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { authedResource } from '@/shared/lib/resource-request'
import { currentClearEpoch } from '@/shared/lib/storage'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

import { loadSceneDetail } from './detail'
import {
  $pendingScene,
  $sceneHasMore,
  $sceneLibrary,
  $sceneLibraryStatus,
  $scenePage,
  $scenePolicy,
  $sceneQuery,
  $sceneRegenerating,
  $sceneTaskSlow,
  $sceneTaskStatus,
  $sceneTotal,
  cacheSceneDetails,
  detailCache,
  PAGE_SIZE,
  resolveScene,
  sceneIO,
  scenePoll
} from './state'
import { startPoll, stopPoll } from './state'

interface SceneLibraryRequest {
  /** 发起时的列表读序号；被更新的读取代后不再复用。 */
  requestId: number
  promise: Promise<boolean>
}

const sceneLibraryRequests = new Map<string, SceneLibraryRequest>()

export async function loadSceneLibrary(query = $sceneQuery.get(), page = $scenePage.get()): Promise<boolean> {
  const scope = captureAuthScope()

  if (!scope) {
    return false
  }

  if (query !== $sceneQuery.get() || page === 0) {
    page = 0
    sceneIO.cursors = [undefined]
  } else if (page >= sceneIO.cursors.length) {
    page = $scenePage.get()
  }

  // 同参数并发读共享一次请求：重复进入创建页不再重发同样的列表读。
  // 已被更新的读取代的请求不复用——复用它只会拿到 false，这次查询就停在旧结果上。
  const key = `${query}\0${page}`
  const pending = sceneLibraryRequests.get(key)

  if (pending && pending.requestId === sceneIO.listRequest) {
    return pending.promise
  }

  const read = ++sceneIO.nextRead
  const requestId = ++sceneIO.listRequest
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => scope() && requestId === sceneIO.listRequest && epoch === currentClearEpoch()

  const request = (async (): Promise<boolean> => {
    $sceneQuery.set(query)
    $scenePage.set(page)
    $sceneLibraryStatus.set('loading')

    const result = await authedResource('companion_scenes.scene_list_route', {
      query: { q: query, cursor: sceneIO.cursors[page], include_total: true, limit: PAGE_SIZE }
    })

    if (!apiSucceeded(result, 'scene', 'Scene library could not be loaded:') || !result.value) {
      if (isCurrent()) {
        $sceneLibraryStatus.set('error')
      }

      return false
    }

    const value = result.value
    const entries = await Promise.all(value.scenes.map(row => resolveScene(row)))

    if (!isCurrent()) {
      return false
    }

    cacheSceneDetails(entries, read)
    $sceneLibrary.set(entries.map(entry => detailCache.get(entry.id) ?? entry))
    $sceneTotal.set(value.total ?? 0)
    $sceneHasMore.set(value.has_more)
    sceneIO.cursors = sceneIO.cursors.slice(0, page + 1)

    if (value.next_cursor) {
      sceneIO.cursors.push(value.next_cursor)
    }

    $sceneLibraryStatus.set('loaded')

    if (page > 0 && entries.length === 0) {
      void loadSceneLibrary(query, page - 1)
    }

    return true
  })()

  sceneLibraryRequests.set(key, { promise: request, requestId })

  try {
    return await request
  } finally {
    if (sceneLibraryRequests.get(key)?.promise === request) {
      sceneLibraryRequests.delete(key)
    }
  }
}

scenePoll.tick = hydrateScene

export async function hydrateScene(options: { skipLibraryRefresh?: boolean } = {}): Promise<boolean> {
  const scope = captureAuthScope()

  if (!scope) {
    return false
  }

  const read = ++sceneIO.nextRead
  const request = ++sceneIO.stateRequest
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => scope() && request === sceneIO.stateRequest && epoch === currentClearEpoch()
  const result = await authedResource('companion_scenes.read_scene_state')

  if (!apiSucceeded(result, 'scene', 'Scene state could not be loaded:') || !result.value) {
    return false
  }

  const state = result.value
  const stale = (): boolean => !isCurrent()

  if (stale()) {
    return false
  }

  try {
    const pendingMaterial = state.materials.find(item => item.status === 'pending') ?? null
    const regeneratingMaterial = state.materials.find(item => item.regeneration?.status === 'pending') ?? null
    const pending = pendingMaterial ? await resolveScene(pendingMaterial) : null
    const regenerating = regeneratingMaterial ? await resolveScene(regeneratingMaterial) : null

    if (stale()) {
      return false
    }

    const oldPending = $pendingScene.get()
    const oldRegenerating = $sceneRegenerating.get()
    $pendingScene.set(pending)
    $sceneRegenerating.set(regenerating)
    cacheSceneDetails([...(pending ? [pending] : []), ...(regenerating ? [regenerating] : [])], read)
    $scenePolicy.set(state.policy)
    const taskStatus = !pending ? 'none' : pending.stage === 'waiting_upload' ? 'waiting_upload' : 'pending'
    $sceneTaskStatus.set(taskStatus)

    if (taskStatus === 'pending' || regenerating) {
      startPoll()
    } else {
      stopPoll()
      $sceneTaskSlow.set(false)
    }

    if (oldPending && !pending) {
      const detail = await loadSceneDetail(oldPending.id)

      if (isCurrent() && detail?.status === 'ready') {
        notify({ kind: 'success', message: getStrings().living.toasts.sceneReady })
      }
    }

    if (oldRegenerating && (!regenerating || oldRegenerating.id !== regenerating.id)) {
      const finished = await loadSceneDetail(oldRegenerating.id)

      if (isCurrent()) {
        if (finished?.regeneration?.status === 'ready') {
          notify({ kind: 'success', message: getStrings().living.toasts.sceneImageRegenerated })
        } else if (finished?.regeneration?.status === 'failed') {
          notify({
            kind: 'warning',
            message: finished.regeneration.error || getStrings().living.toasts.sceneRegenerateFailed
          })
        }
      }
    }

    if (isCurrent() && !options.skipLibraryRefresh) {
      await loadSceneLibrary()
    }

    return isCurrent()
  } catch (error) {
    log.warn('scene', 'Scene hydration failed:', error)

    return false
  }
}

export function onSceneEvent(): void {
  sceneIO.eventVersion += 1
  void hydrateScene()
}
