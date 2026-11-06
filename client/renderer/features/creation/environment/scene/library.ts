import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { authedResource } from '@/shared/lib/resource-request'
import { currentClearEpoch } from '@/shared/lib/storage'

import {
  $sceneHasMore,
  $sceneLibrary,
  $sceneLibraryStatus,
  $scenePage,
  $sceneQuery,
  $sceneTotal,
  cacheSceneDetails,
  detailCache,
  PAGE_SIZE,
  resolveScene,
  sceneIO
} from './state'

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
