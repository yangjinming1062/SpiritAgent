import { atom } from 'nanostores'

import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { authedResource } from '@/shared/lib/resource-request'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

import { fetchSceneDetail, loadSceneDetail } from './detail'
import { loadSceneLibrary } from './library'
import { $scenePolicy, cacheSceneDetails, resolveScene, type SceneAsset, sceneIO } from './state'

export const $pendingScene = atom<SceneAsset | null>(null)
export const $sceneRegenerating = atom<SceneAsset | null>(null)
export const $sceneTaskStatus = atom<'none' | 'pending' | 'waiting_upload'>('none')
export const $sceneTaskSlow = atom(false)

const POLL_INTERVAL_MS = 3500
const SLOW_POLL_COUNT = 40

let pollTimer: ReturnType<typeof setInterval> | null = null
let pollCount = 0

/** 轮询只在存在待处理任务或重做时运行，慢速阶段降频但不停止。 */
export function startPoll(): void {
  if (pollTimer !== null) {
    return
  }

  pollTimer = setInterval(() => {
    pollCount++

    if (pollCount > SLOW_POLL_COUNT) {
      $sceneTaskSlow.set(true)

      if (pollCount % 4 !== 0) {
        return
      }
    }

    void hydrateScene()
  }, POLL_INTERVAL_MS)
}

function stopPoll(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
  }

  pollTimer = null
  pollCount = 0
}

function applyTaskState(pending: SceneAsset | null, regenerating: SceneAsset | null): void {
  $pendingScene.set(pending)
  $sceneRegenerating.set(regenerating)

  const taskStatus = !pending ? 'none' : pending.stage === 'waiting_upload' ? 'waiting_upload' : 'pending'
  $sceneTaskStatus.set(taskStatus)

  if (taskStatus === 'pending' || regenerating) {
    startPoll()
  } else {
    stopPoll()
    $sceneTaskSlow.set(false)
  }
}

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
    applyTaskState(pending, regenerating)
    cacheSceneDetails([...(pending ? [pending] : []), ...(regenerating ? [regenerating] : [])], read)
    $scenePolicy.set(state.policy)

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

/** 刷新单个场景的最新状态；不等待场景列表或图片字节，业务错误交给页面显示。 */
export async function refreshSceneStatus(sceneId: string): Promise<SceneAsset | null> {
  const scope = captureAuthScope()
  const detail = await fetchSceneDetail(sceneId, 'defer')

  if (!scope?.() || !detail) {
    return null
  }

  // 单场景终态立即解除它的忙碌标记；更早的全局读取不能再覆盖本次查询。
  sceneIO.stateRequest++

  const currentPending = $pendingScene.get()
  const currentRegenerating = $sceneRegenerating.get()

  if (detail.status === 'pending' || currentPending?.id === sceneId) {
    $pendingScene.set(detail.status === 'pending' ? detail : null)
  }

  if (detail.regeneration?.status === 'pending' || currentRegenerating?.id === sceneId) {
    $sceneRegenerating.set(detail.regeneration?.status === 'pending' ? detail : null)
  }

  const taskStatus = !$pendingScene.get() ? 'none' : $pendingScene.get()?.stage === 'waiting_upload' ? 'waiting_upload' : 'pending'
  $sceneTaskStatus.set(taskStatus)
  stopPoll()
  $sceneTaskSlow.set(false)

  if (taskStatus === 'pending' || $sceneRegenerating.get()) {
    startPoll()
  }

  return detail
}

export function onSceneEvent(): void {
  sceneIO.eventVersion += 1
  void hydrateScene()
}

registerStorageClearHandler(() => {
  stopPoll()
  $pendingScene.set(null)
  $sceneRegenerating.set(null)
  $sceneTaskStatus.set('none')
  $sceneTaskSlow.set(false)
})
